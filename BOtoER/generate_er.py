"""
generate_er.py
--------------
Generates context-scoped Mermaid ER diagrams from parsed universe JSON.

Usage:
    python generate_er.py --parsed ./parsed --output ./er_diagrams

Produces:
    er_diagrams/shared_dimensions.mmd
    er_diagrams/context_<name>.mmd   (one per context)
"""

import argparse
import json
import os
import re
from collections import defaultdict
from pathlib import Path


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def load_json(path: str) -> list | dict:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def safe_name(name: str) -> str:
    """Sanitise names for Mermaid identifiers."""
    return re.sub(r"[^A-Za-z0-9_]", "_", name or "UNKNOWN")


# ---------------------------------------------------------------------------
# Cardinality → Mermaid notation
# ---------------------------------------------------------------------------

CARDINALITY_TO_MERMAID = {
    "1..1":    "||--||",
    "1..N":    "||--o{",
    "N..1":    "}o--||",
    "N..N":    "}o--o{",
    "unknown": "||--||",
}


def mermaid_cardinality(cardinality: str, outer_join: bool) -> str:
    base = CARDINALITY_TO_MERMAID.get(cardinality, "||--||")
    return base


def join_label(join: dict) -> str:
    label = join.get("expression", "").strip()
    if not label:
        label = f"{join['table1']} → {join['table2']}"
    # Keep label short for diagram readability
    if len(label) > 60:
        label = label[:57] + "..."
    outer = " (outer)" if join.get("outer_join") else ""
    return label + outer


# ---------------------------------------------------------------------------
# ER diagram builder
# ---------------------------------------------------------------------------

def build_er_diagram(context_name: str, tables: list[str], joins: list[dict],
                     all_tables: list[dict], agg_tables: list[dict]) -> str:

    table_lookup = {t["name"]: t for t in all_tables}
    agg_names = {a["name"] for a in agg_tables}

    # Context-level aggregate tables
    ctx_agg = [a for a in agg_tables if a["name"] in tables or a.get("base_fact_table") in tables]

    lines = [
        f"%%  Context: {context_name}",
    ]
    if ctx_agg:
        for a in ctx_agg:
            lines.append(f"%%  Aggregate table: {a['name']}  grain={a['grain']}  base={a['base_fact_table']}")

    # Flag derived + alias tables
    derived = [t for t in tables if table_lookup.get(t, {}).get("type") == "derived"]
    aliases = [(t, table_lookup[t].get("source_table")) for t in tables
               if table_lookup.get(t, {}).get("type") == "alias"]

    for d in derived:
        lines.append(f"%%  Derived table: {d}  → build as view in Databricks")
    for alias, src in aliases:
        lines.append(f"%%  Alias table: {alias}  source={src}")

    lines.append("")
    lines.append("erDiagram")

    # Emit table blocks (simple — column detail not available from universe XML)
    for tbl in sorted(set(tables)):
        sn = safe_name(tbl)
        ttype = table_lookup.get(tbl, {}).get("type", "physical")
        comment = ""
        if ttype == "derived":
            comment = " %% derived → view"
        elif ttype == "alias":
            src = table_lookup.get(tbl, {}).get("source_table", "")
            comment = f" %% alias of {src}"
        elif tbl in agg_names:
            comment = " %% aggregate table"
        lines.append(f"    {sn} {{{comment}}}")

    lines.append("")

    # Emit relationships
    seen_joins = set()
    for join in joins:
        t1 = safe_name(join.get("table1", ""))
        t2 = safe_name(join.get("table2", ""))
        if not t1 or not t2:
            continue
        key = tuple(sorted([t1, t2]))
        if key in seen_joins:
            continue
        seen_joins.add(key)

        cardinality = join.get("cardinality", "unknown")
        notation = mermaid_cardinality(cardinality, join.get("outer_join", False))
        label = join_label(join)
        lines.append(f'    {t1} {notation} {t2} : "{label}"')

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Shared dimensions detection
# ---------------------------------------------------------------------------

def find_shared_dimensions(contexts: list[dict], all_tables: list[dict]) -> list[str]:
    table_context_count = defaultdict(int)
    table_lookup = {t["name"]: t for t in all_tables}

    for ctx in contexts:
        for tbl in ctx.get("tables", []):
            table_context_count[tbl] += 1

    # Shared = appears in 2+ contexts AND is not clearly a fact table
    # Heuristic: alias tables and tables with "dim" / "date" / "lookup" in name
    # are likely dimensions; tables with "fact" / "trx" / "trans" are likely facts
    fact_hints = {"fact", "trx", "trans", "event", "ledger", "order", "sale", "invoice"}
    dim_hints  = {"dim", "date", "time", "calendar", "lookup", "ref", "code", "type", "cat"}

    shared = []
    for tbl, count in table_context_count.items():
        if count < 2:
            continue
        lower = tbl.lower()
        is_fact = any(h in lower for h in fact_hints)
        if not is_fact:
            shared.append(tbl)

    return sorted(set(shared))


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def generate_er(parsed_dir: str, output_dir: str):
    tables_path     = os.path.join(parsed_dir, "tables.json")
    joins_path      = os.path.join(parsed_dir, "joins.json")
    contexts_path   = os.path.join(parsed_dir, "contexts.json")
    agg_path        = os.path.join(parsed_dir, "aggregate_tables.json")

    for p in [tables_path, joins_path, contexts_path]:
        if not os.path.exists(p):
            print(f"Required file not found: {p}")
            return

    all_tables  = load_json(tables_path)
    all_joins   = load_json(joins_path)
    contexts    = load_json(contexts_path)
    agg_tables  = load_json(agg_path) if os.path.exists(agg_path) else []

    os.makedirs(output_dir, exist_ok=True)

    # --- Context ER diagrams ---
    for ctx in contexts:
        ctx_name  = ctx.get("name", "unnamed")
        ctx_tables = ctx.get("tables", [])
        ctx_joins  = ctx.get("joins", [])

        if not ctx_tables and not ctx_joins:
            print(f"  Skipping context '{ctx_name}' — no tables/joins resolved (check join refs)")
            continue

        diagram = build_er_diagram(ctx_name, ctx_tables, ctx_joins, all_tables, agg_tables)
        safe   = re.sub(r"[^A-Za-z0-9_\-]", "_", ctx_name)
        fname  = os.path.join(output_dir, f"context_{safe}.mmd")
        with open(fname, "w", encoding="utf-8") as f:
            f.write(diagram)
        print(f"  Written: {fname}")

    # --- Shared dimensions ER diagram ---
    shared_dims = find_shared_dimensions(contexts, all_tables)
    if shared_dims:
        # Collect joins between shared dims
        shared_joins = [
            j for j in all_joins
            if j.get("table1") in shared_dims and j.get("table2") in shared_dims
        ]
        diagram = build_er_diagram("shared_dimensions", shared_dims, shared_joins, all_tables, [])
        fname = os.path.join(output_dir, "shared_dimensions.mmd")
        with open(fname, "w", encoding="utf-8") as f:
            f.write(diagram)
        print(f"  Written: {fname}  ({len(shared_dims)} shared dimensions)")
    else:
        print("  No shared dimensions detected.")

    # --- Summary ---
    print(f"\nGenerated {len(contexts)} context diagram(s) + shared_dimensions diagram.")
    print(f"Output directory: {output_dir}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Generate Mermaid ER diagrams from parsed universe JSON")
    parser.add_argument("--parsed", required=True, help="Directory containing parsed JSON files")
    parser.add_argument("--output", required=True, help="Output directory for .mmd files")
    args = parser.parse_args()
    generate_er(args.parsed, args.output)
