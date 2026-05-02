"""
parse_universe.py
-----------------
Parses exported SAP BusinessObjects universe XML files into structured JSON.

Usage:
    python parse_universe.py --input ./universe_extracted --output ./parsed

Expects the .unx to have been renamed to .zip and extracted first:
    cp universe.unx universe.zip && unzip universe.zip -d universe_extracted/
"""

import argparse
import json
import os
import re
from pathlib import Path
from xml.etree import ElementTree as ET


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def find_xml_files(directory: str) -> list[Path]:
    return list(Path(directory).rglob("*.xml"))


def safe_text(element, tag: str, default="") -> str:
    child = element.find(tag)
    return child.text.strip() if child is not None and child.text else default


def safe_attr(element, attr: str, default="") -> str:
    return element.attrib.get(attr, default).strip()


# ---------------------------------------------------------------------------
# Table extraction
# ---------------------------------------------------------------------------

def extract_tables(root: ET.Element) -> list[dict]:
    tables = []

    # Physical tables
    for tbl in root.iter("Table"):
        tables.append({
            "name": safe_attr(tbl, "Name") or safe_text(tbl, "Name"),
            "type": "physical",
            "sql": None,
            "source_table": None,
            "schema": safe_attr(tbl, "Schema"),
            "owner": safe_attr(tbl, "Owner"),
        })

    # Alias tables
    for tbl in root.iter("AliasTable"):
        tables.append({
            "name": safe_attr(tbl, "Name") or safe_text(tbl, "Name"),
            "type": "alias",
            "sql": None,
            "source_table": safe_attr(tbl, "SourceTable") or safe_text(tbl, "SourceTable"),
            "schema": None,
            "owner": None,
        })

    # Derived tables (DerivedTable or TableExpression)
    for tbl in root.iter("DerivedTable"):
        sql_el = tbl.find("Expression") or tbl.find("SQL")
        tables.append({
            "name": safe_attr(tbl, "Name") or safe_text(tbl, "Name"),
            "type": "derived",
            "sql": sql_el.text.strip() if sql_el is not None and sql_el.text else None,
            "source_table": None,
            "schema": None,
            "owner": None,
        })

    # Deduplicate by name (same table may appear in multiple XML files)
    seen = set()
    deduped = []
    for t in tables:
        if t["name"] and t["name"] not in seen:
            seen.add(t["name"])
            deduped.append(t)

    return deduped


# ---------------------------------------------------------------------------
# Join extraction
# ---------------------------------------------------------------------------

CARDINALITY_MAP = {
    "OneToOne":  "1..1",
    "OneToMany": "1..N",
    "ManyToOne": "N..1",
    "ManyToMany": "N..N",
    # fallback patterns
    "11": "1..1",
    "1N": "1..N",
    "N1": "N..1",
    "NN": "N..N",
}


def normalise_cardinality(raw: str) -> str:
    return CARDINALITY_MAP.get(raw, raw or "unknown")


def extract_joins(root: ET.Element) -> list[dict]:
    joins = []
    for join in root.iter("Join"):
        cardinality_raw = (
            safe_attr(join, "Cardinality")
            or safe_text(join, "Cardinality")
        )
        joins.append({
            "table1": safe_attr(join, "Table1") or safe_text(join, "Table1"),
            "table2": safe_attr(join, "Table2") or safe_text(join, "Table2"),
            "expression": safe_attr(join, "Expression") or safe_text(join, "Expression"),
            "cardinality": normalise_cardinality(cardinality_raw),
            "outer_join": safe_attr(join, "OuterJoin", "false").lower() == "true",
            "contexts": [],  # populated in context pass
        })
    return joins


# ---------------------------------------------------------------------------
# Context extraction
# ---------------------------------------------------------------------------

def extract_contexts(root: ET.Element, joins: list[dict]) -> list[dict]:
    contexts = []
    for ctx in root.iter("Context"):
        name = safe_attr(ctx, "Name") or safe_text(ctx, "Name")
        included_joins = []

        # Collect join references inside this context
        for join_ref in ctx.iter("JoinPath"):
            ref = safe_attr(join_ref, "JoinRef") or join_ref.text
            if ref:
                included_joins.append(ref.strip())

        # Also look for direct join name references
        for join_el in ctx.iter("Join"):
            jname = safe_attr(join_el, "Name") or safe_attr(join_el, "Ref")
            if jname:
                included_joins.append(jname.strip())

        contexts.append({
            "name": name,
            "join_refs": list(set(included_joins)),
            "tables": [],   # populated in post-processing
            "joins": [],    # populated in post-processing
        })

    # Tag joins with their context membership
    for ctx in contexts:
        for join in joins:
            expr = join.get("expression", "")
            t1, t2 = join.get("table1", ""), join.get("table2", "")
            # Match by expression fragment or table names appearing in join refs
            for ref in ctx["join_refs"]:
                if ref and (ref in expr or ref == f"{t1}_{t2}" or ref == f"{t2}_{t1}"):
                    if ctx["name"] not in join["contexts"]:
                        join["contexts"].append(ctx["name"])
                    if t1 not in ctx["tables"]:
                        ctx["tables"].append(t1)
                    if t2 not in ctx["tables"]:
                        ctx["tables"].append(t2)
                    if join not in ctx["joins"]:
                        ctx["joins"].append({
                            "table1": t1,
                            "table2": t2,
                            "cardinality": join["cardinality"],
                            "outer_join": join["outer_join"],
                        })

    return contexts


# ---------------------------------------------------------------------------
# Aggregate awareness extraction
# ---------------------------------------------------------------------------

def extract_aggregate_tables(root: ET.Element) -> list[dict]:
    agg_tables = []
    for agg in root.iter("AggregateAwareness"):
        for tbl in agg.iter("AggregateTable"):
            agg_tables.append({
                "name": safe_attr(tbl, "Name") or safe_text(tbl, "Name"),
                "base_fact_table": safe_attr(tbl, "BaseFact") or safe_text(tbl, "BaseFact"),
                "grain": safe_attr(tbl, "Grain") or safe_text(tbl, "Grain") or "unknown",
                "compatible_objects": [
                    safe_attr(obj, "Name") or obj.text
                    for obj in tbl.iter("CompatibleObject")
                ],
            })
    return agg_tables


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def parse_universe(input_dir: str, output_dir: str):
    xml_files = find_xml_files(input_dir)
    if not xml_files:
        print(f"No XML files found in {input_dir}")
        return

    all_tables, all_joins, all_contexts, all_agg_tables = [], [], [], []

    for xml_path in xml_files:
        print(f"Parsing {xml_path.name} ...")
        try:
            tree = ET.parse(xml_path)
            root = tree.getroot()
        except ET.ParseError as e:
            print(f"  Skipping {xml_path.name} — parse error: {e}")
            continue

        tables   = extract_tables(root)
        joins    = extract_joins(root)
        contexts = extract_contexts(root, joins)
        agg_tbls = extract_aggregate_tables(root)

        all_tables.extend(tables)
        all_joins.extend(joins)
        all_contexts.extend(contexts)
        all_agg_tables.extend(agg_tbls)

    # Deduplicate tables across multiple XML files
    seen = set()
    deduped_tables = []
    for t in all_tables:
        if t["name"] and t["name"] not in seen:
            seen.add(t["name"])
            deduped_tables.append(t)

    os.makedirs(output_dir, exist_ok=True)

    outputs = {
        "tables.json":            deduped_tables,
        "joins.json":             all_joins,
        "contexts.json":          all_contexts,
        "aggregate_tables.json":  all_agg_tables,
    }

    for filename, data in outputs.items():
        path = os.path.join(output_dir, filename)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        print(f"  Written: {path}  ({len(data)} records)")

    print("\nParsing complete.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Parse BO universe XML to JSON")
    parser.add_argument("--input",  required=True, help="Directory of extracted universe XML files")
    parser.add_argument("--output", required=True, help="Output directory for JSON files")
    args = parser.parse_args()
    parse_universe(args.input, args.output)
