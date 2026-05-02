"""
validate_er.py
--------------
Cross-checks that every table and join from the parsed universe JSON
is accounted for across the generated Mermaid ER diagrams.

Usage:
    python validate_er.py --parsed ./parsed --er_diagrams ./er_diagrams

Exit codes:
    0 — all checks passed
    1 — one or more validation issues found

Output:
    Prints a structured validation report to stdout.
    Optionally writes a JSON report with --report ./validation_report.json
"""

import argparse
import json
import os
import re
import sys
from collections import defaultdict
from pathlib import Path


# ---------------------------------------------------------------------------
# ANSI colours (gracefully disabled on Windows / non-TTY)
# ---------------------------------------------------------------------------

USE_COLOUR = sys.stdout.isatty() and os.name != "nt"

def green(s):  return f"\033[92m{s}\033[0m" if USE_COLOUR else s
def yellow(s): return f"\033[93m{s}\033[0m" if USE_COLOUR else s
def red(s):    return f"\033[91m{s}\033[0m" if USE_COLOUR else s
def bold(s):   return f"\033[1m{s}\033[0m"  if USE_COLOUR else s
def cyan(s):   return f"\033[96m{s}\033[0m" if USE_COLOUR else s


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def load_json(path: str):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def safe_name(name: str) -> str:
    """Mirror the sanitisation used in generate_er.py."""
    return re.sub(r"[^A-Za-z0-9_]", "_", name or "UNKNOWN")


def find_mmd_files(directory: str) -> list[Path]:
    return sorted(Path(directory).glob("*.mmd"))


# ---------------------------------------------------------------------------
# Parse Mermaid ER files
# ---------------------------------------------------------------------------

def parse_mmd(path: Path) -> dict:
    """
    Returns:
        {
            "context":    str,
            "tables":     set[str],   # sanitised names found as entity blocks
            "joins":      set[tuple], # (t1, t2) pairs, sorted
            "agg_tables": set[str],   # from %% Aggregate table: comments
            "derived":    set[str],   # from %% Derived table: comments
            "aliases":    dict,       # alias_name → source_name
        }
    """
    result = {
        "context":    path.stem,
        "tables":     set(),
        "joins":      set(),
        "agg_tables": set(),
        "derived":    set(),
        "aliases":    {},
    }

    with open(path, "r", encoding="utf-8") as f:
        lines = f.readlines()

    for line in lines:
        stripped = line.strip()

        # Context name from comment
        if stripped.startswith("%% Context:") or stripped.startswith("%%  Context:"):
            result["context"] = stripped.split(":", 1)[1].strip()

        # Aggregate table annotation
        agg_match = re.search(r"%%\s+Aggregate table:\s+(\S+)", stripped)
        if agg_match:
            result["agg_tables"].add(safe_name(agg_match.group(1)))

        # Derived table annotation
        der_match = re.search(r"%%\s+Derived table:\s+(\S+)", stripped)
        if der_match:
            result["derived"].add(safe_name(der_match.group(1)))

        # Alias table annotation
        alias_match = re.search(r"%%\s+Alias table:\s+(\S+)\s+source=(\S+)", stripped)
        if alias_match:
            result["aliases"][safe_name(alias_match.group(1))] = safe_name(alias_match.group(2))

        # Entity block:  TABLE_NAME { ... }
        entity_match = re.match(r"^\s{4}([A-Za-z0-9_]+)\s+\{", line)
        if entity_match:
            result["tables"].add(entity_match.group(1))

        # Relationship line:  T1 ||--o{ T2 : "label"
        rel_match = re.match(
            r"^\s{4}([A-Za-z0-9_]+)\s+[|o}{]+--[|o}{]+\s+([A-Za-z0-9_]+)\s+:",
            line
        )
        if rel_match:
            t1, t2 = rel_match.group(1), rel_match.group(2)
            result["joins"].add(tuple(sorted([t1, t2])))

    return result


# ---------------------------------------------------------------------------
# Build universe index from JSON
# ---------------------------------------------------------------------------

def build_universe_index(parsed_dir: str) -> dict:
    tables_path   = os.path.join(parsed_dir, "tables.json")
    joins_path    = os.path.join(parsed_dir, "joins.json")
    contexts_path = os.path.join(parsed_dir, "contexts.json")
    agg_path      = os.path.join(parsed_dir, "aggregate_tables.json")

    for p in [tables_path, joins_path, contexts_path]:
        if not os.path.exists(p):
            print(red(f"Required file not found: {p}"))
            sys.exit(1)

    all_tables  = load_json(tables_path)
    all_joins   = load_json(joins_path)
    contexts    = load_json(contexts_path)
    agg_tables  = load_json(agg_path) if os.path.exists(agg_path) else []

    # All table names (sanitised)
    table_names = {safe_name(t["name"]) for t in all_tables if t.get("name")}

    # All join pairs (sanitised, sorted)
    join_pairs = {
        tuple(sorted([safe_name(j["table1"]), safe_name(j["table2"])]))
        for j in all_joins
        if j.get("table1") and j.get("table2")
    }

    # Per-context expected tables + joins
    context_index = {}
    for ctx in contexts:
        cname = ctx.get("name", "unnamed")
        ctx_tables = {safe_name(t) for t in ctx.get("tables", []) if t}
        ctx_joins  = {
            tuple(sorted([safe_name(j["table1"]), safe_name(j["table2"])]))
            for j in ctx.get("joins", [])
            if j.get("table1") and j.get("table2")
        }
        context_index[cname] = {"tables": ctx_tables, "joins": ctx_joins}

    # Aggregate table names
    agg_names = {safe_name(a["name"]) for a in agg_tables if a.get("name")}

    # Derived table names
    derived_names = {safe_name(t["name"]) for t in all_tables if t.get("type") == "derived"}

    # Alias table names
    alias_names = {safe_name(t["name"]) for t in all_tables if t.get("type") == "alias"}

    return {
        "all_tables":    table_names,
        "all_joins":     join_pairs,
        "contexts":      context_index,
        "agg_tables":    agg_names,
        "derived_tables": derived_names,
        "alias_tables":  alias_names,
        "context_count": len(contexts),
    }


# ---------------------------------------------------------------------------
# Validation checks
# ---------------------------------------------------------------------------

def check_context_diagram_count(universe: dict, diagrams: list[dict]) -> dict:
    """Every context in contexts.json should have a matching diagram."""
    context_names_in_json = set(universe["contexts"].keys())
    # Exclude shared_dimensions from context count
    diagram_contexts = {
        d["context"] for d in diagrams
        if "shared" not in d["context"].lower()
    }

    missing = context_names_in_json - diagram_contexts
    extra   = diagram_contexts - context_names_in_json

    return {
        "check": "Context diagram count",
        "passed": len(missing) == 0,
        "missing_diagrams": sorted(missing),
        "extra_diagrams":   sorted(extra),
        "detail": (
            f"{len(diagram_contexts)} diagram(s) found, "
            f"{len(context_names_in_json)} context(s) in JSON"
        ),
    }


def check_all_tables_covered(universe: dict, diagrams: list[dict]) -> dict:
    """Every table in tables.json should appear in at least one diagram."""
    all_diagram_tables = set()
    for d in diagrams:
        all_diagram_tables |= d["tables"]

    uncovered = universe["all_tables"] - all_diagram_tables
    return {
        "check": "All tables covered",
        "passed": len(uncovered) == 0,
        "uncovered_tables": sorted(uncovered),
        "detail": (
            f"{len(universe['all_tables'])} tables in universe, "
            f"{len(all_diagram_tables)} found across diagrams, "
            f"{len(uncovered)} uncovered"
        ),
    }


def check_all_joins_covered(universe: dict, diagrams: list[dict]) -> dict:
    """Every join in joins.json should appear in at least one diagram."""
    all_diagram_joins = set()
    for d in diagrams:
        all_diagram_joins |= d["joins"]

    uncovered = universe["all_joins"] - all_diagram_joins
    return {
        "check": "All joins covered",
        "passed": len(uncovered) == 0,
        "uncovered_joins": [list(j) for j in sorted(uncovered)],
        "detail": (
            f"{len(universe['all_joins'])} joins in universe, "
            f"{len(all_diagram_joins)} found across diagrams, "
            f"{len(uncovered)} uncovered"
        ),
    }


def check_context_table_completeness(universe: dict, diagrams: list[dict]) -> dict:
    """For each context, its diagram should contain all expected tables."""
    issues = {}
    diagram_by_context = {d["context"]: d for d in diagrams}

    for ctx_name, ctx_data in universe["contexts"].items():
        diagram = diagram_by_context.get(ctx_name)
        if not diagram:
            continue  # already caught by context diagram count check

        expected = ctx_data["tables"]
        found    = diagram["tables"]
        missing  = expected - found
        if missing:
            issues[ctx_name] = sorted(missing)

    return {
        "check": "Context table completeness",
        "passed": len(issues) == 0,
        "issues": issues,
        "detail": (
            f"{len(issues)} context(s) with missing tables"
            if issues else "All context tables present in diagrams"
        ),
    }


def check_context_join_completeness(universe: dict, diagrams: list[dict]) -> dict:
    """For each context, its diagram should contain all expected joins."""
    issues = {}
    diagram_by_context = {d["context"]: d for d in diagrams}

    for ctx_name, ctx_data in universe["contexts"].items():
        diagram = diagram_by_context.get(ctx_name)
        if not diagram:
            continue

        expected = ctx_data["joins"]
        found    = diagram["joins"]
        missing  = expected - found
        if missing:
            issues[ctx_name] = [list(j) for j in sorted(missing)]

    return {
        "check": "Context join completeness",
        "passed": len(issues) == 0,
        "issues": issues,
        "detail": (
            f"{len(issues)} context(s) with missing joins"
            if issues else "All context joins present in diagrams"
        ),
    }


def check_derived_tables_annotated(universe: dict, diagrams: list[dict]) -> dict:
    """Every derived table should be annotated in at least one diagram."""
    annotated = set()
    for d in diagrams:
        annotated |= d["derived"]

    missing_annotation = universe["derived_tables"] - annotated
    return {
        "check": "Derived tables annotated",
        "passed": len(missing_annotation) == 0,
        "unannotated": sorted(missing_annotation),
        "detail": (
            f"{len(universe['derived_tables'])} derived tables, "
            f"{len(missing_annotation)} missing annotation"
        ),
    }


def check_aggregate_tables_annotated(universe: dict, diagrams: list[dict]) -> dict:
    """Every aggregate table should be annotated in at least one diagram."""
    annotated = set()
    for d in diagrams:
        annotated |= d["agg_tables"]

    missing_annotation = universe["agg_tables"] - annotated
    return {
        "check": "Aggregate tables annotated",
        "passed": len(missing_annotation) == 0,
        "unannotated": sorted(missing_annotation),
        "detail": (
            f"{len(universe['agg_tables'])} aggregate tables, "
            f"{len(missing_annotation)} missing annotation"
        ),
    }


def check_alias_tables_annotated(universe: dict, diagrams: list[dict]) -> dict:
    """Every alias table should be annotated in at least one diagram."""
    annotated = set()
    for d in diagrams:
        annotated |= set(d["aliases"].keys())

    missing_annotation = universe["alias_tables"] - annotated
    return {
        "check": "Alias tables annotated",
        "passed": len(missing_annotation) == 0,
        "unannotated": sorted(missing_annotation),
        "detail": (
            f"{len(universe['alias_tables'])} alias tables, "
            f"{len(missing_annotation)} missing annotation"
        ),
    }


def check_shared_dimensions_diagram(universe: dict, diagrams: list[dict]) -> dict:
    """shared_dimensions.mmd should exist if any tables appear in 2+ contexts."""
    shared_diagram = next(
        (d for d in diagrams if "shared" in d["context"].lower()), None
    )

    # Count tables across contexts
    table_ctx_count = defaultdict(int)
    for ctx_data in universe["contexts"].values():
        for t in ctx_data["tables"]:
            table_ctx_count[t] += 1

    multi_ctx_tables = {t for t, c in table_ctx_count.items() if c >= 2}

    if not multi_ctx_tables:
        return {
            "check": "Shared dimensions diagram",
            "passed": True,
            "detail": "No shared dimensions detected — diagram not required",
        }

    if not shared_diagram:
        return {
            "check": "Shared dimensions diagram",
            "passed": False,
            "detail": (
                f"{len(multi_ctx_tables)} shared dimension(s) detected "
                f"but shared_dimensions.mmd not found"
            ),
            "expected_shared": sorted(multi_ctx_tables),
        }

    in_diagram  = shared_diagram["tables"]
    missing     = {safe_name(t) for t in multi_ctx_tables} - in_diagram
    return {
        "check": "Shared dimensions diagram",
        "passed": len(missing) == 0,
        "detail": (
            f"{len(multi_ctx_tables)} shared dim(s), "
            f"{len(in_diagram)} in diagram, "
            f"{len(missing)} missing"
        ),
        "missing_from_shared": sorted(missing),
    }


# ---------------------------------------------------------------------------
# Report printer
# ---------------------------------------------------------------------------

def print_report(results: list[dict], universe: dict, diagrams: list[dict]):
    passed  = sum(1 for r in results if r["passed"])
    total   = len(results)
    overall = passed == total

    print()
    print(bold("=" * 60))
    print(bold("  BO Universe ER Diagram Validation Report"))
    print(bold("=" * 60))
    print(f"  Universe tables : {len(universe['all_tables'])}")
    print(f"  Universe joins  : {len(universe['all_joins'])}")
    print(f"  Contexts        : {universe['context_count']}")
    print(f"  Diagrams found  : {len(diagrams)}")
    print(bold("=" * 60))
    print()

    for r in results:
        status = green("  PASS") if r["passed"] else red("  FAIL")
        print(f"{status}  {bold(r['check'])}")
        print(f"        {r['detail']}")

        # Print specifics on failures
        if not r["passed"]:
            for key in ["missing_diagrams", "extra_diagrams", "uncovered_tables",
                        "uncovered_joins", "unannotated", "missing_from_shared",
                        "expected_shared"]:
                items = r.get(key)
                if items:
                    label = key.replace("_", " ").title()
                    print(f"        {yellow(label)}:")
                    for item in items[:20]:  # cap output
                        print(f"          - {item}")
                    if len(items) > 20:
                        print(f"          ... and {len(items) - 20} more")

            issues = r.get("issues")
            if issues:
                for ctx, missing in list(issues.items())[:10]:
                    print(f"        {yellow(ctx)}:")
                    for item in missing[:10]:
                        print(f"          - {item}")
                if len(issues) > 10:
                    print(f"        ... and {len(issues) - 10} more contexts")

        print()

    print(bold("=" * 60))
    summary = (
        green(f"  ALL {total} CHECKS PASSED")
        if overall
        else red(f"  {total - passed}/{total} CHECKS FAILED")
    )
    print(summary)
    print(bold("=" * 60))
    print()

    return overall


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def validate(parsed_dir: str, er_dir: str, report_path: str | None):
    mmd_files = find_mmd_files(er_dir)
    if not mmd_files:
        print(red(f"No .mmd files found in {er_dir}"))
        sys.exit(1)

    print(cyan(f"Loading parsed JSON from: {parsed_dir}"))
    universe = build_universe_index(parsed_dir)

    print(cyan(f"Parsing {len(mmd_files)} diagram(s) from: {er_dir}"))
    diagrams = [parse_mmd(f) for f in mmd_files]

    results = [
        check_context_diagram_count(universe, diagrams),
        check_all_tables_covered(universe, diagrams),
        check_all_joins_covered(universe, diagrams),
        check_context_table_completeness(universe, diagrams),
        check_context_join_completeness(universe, diagrams),
        check_derived_tables_annotated(universe, diagrams),
        check_aggregate_tables_annotated(universe, diagrams),
        check_alias_tables_annotated(universe, diagrams),
        check_shared_dimensions_diagram(universe, diagrams),
    ]

    overall = print_report(results, universe, diagrams)

    if report_path:
        report = {
            "summary": {
                "passed": sum(1 for r in results if r["passed"]),
                "total":  len(results),
                "overall_passed": overall,
            },
            "universe": {
                "table_count":   len(universe["all_tables"]),
                "join_count":    len(universe["all_joins"]),
                "context_count": universe["context_count"],
            },
            "checks": results,
        }
        with open(report_path, "w", encoding="utf-8") as f:
            json.dump(report, f, indent=2, ensure_ascii=False)
        print(cyan(f"JSON report written: {report_path}"))

    sys.exit(0 if overall else 1)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Validate Mermaid ER diagrams against parsed universe JSON"
    )
    parser.add_argument("--parsed",     required=True, help="Directory of parsed JSON files")
    parser.add_argument("--er_diagrams",required=True, help="Directory of generated .mmd files")
    parser.add_argument("--report",     default=None,  help="Optional path to write JSON report")
    args = parser.parse_args()
    validate(args.parsed, args.er_diagrams, args.report)
