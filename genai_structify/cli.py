"""
Command-line interface.

Usage:
    python -m genai_structify.cli path/to/input.pdf -o output.xlsx
    python -m genai_structify.cli path/to/input.txt --schema-only
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .excel_writer import write_excel
from .extractor import Field, Schema, extract_rows, infer_schema
from .llm_client import LLMClient, LLMError
from .readers import UnsupportedFileType, read_input


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="genai-structify",
        description="Turn unstructured data into a clean, structured Excel file.",
    )
    parser.add_argument("input", help="Path to input file (.txt, .md, .csv, .pdf, .docx)")
    parser.add_argument(
        "-o", "--output", default="output.xlsx", help="Path to write the .xlsx file to"
    )
    parser.add_argument(
        "--schema-only",
        action="store_true",
        help="Only infer and print the proposed schema, don't extract rows",
    )
    parser.add_argument(
        "--schema-file",
        help="Path to a JSON file describing the schema to use, skipping inference",
    )
    parser.add_argument(
        "--model", default="llama3.2:1b", help="Ollama model name to use for extraction"
    )
    return parser


def load_schema_file(path: str) -> Schema:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    fields = [Field(**f) for f in data["fields"]]
    return Schema(fields=fields, sheet_name=data.get("sheet_name", "Data"))


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    try:
        text = read_input(args.input)
    except (UnsupportedFileType, FileNotFoundError, ValueError) as exc:
        print(f"Error reading input: {exc}", file=sys.stderr)
        return 1

    try:
        client = LLMClient(model=args.model)

        if args.schema_file:
            schema = load_schema_file(args.schema_file)
        else:
            print("Inferring schema from input...", file=sys.stderr)
            schema = infer_schema(client, text)

        print("\nProposed schema:", file=sys.stderr)
        print(f"  sheet_name: {schema.sheet_name}", file=sys.stderr)
        for f in schema.fields:
            print(f"  - {f.name} ({f.type}): {f.description}", file=sys.stderr)

        if args.schema_only:
            print(
                json.dumps(
                    {
                        "sheet_name": schema.sheet_name,
                        "fields": [f.__dict__ for f in schema.fields],
                    },
                    indent=2,
                )
            )
            return 0

        print("\nExtracting rows (this may take a moment for large inputs)...", file=sys.stderr)
        rows, warnings = extract_rows(client, text, schema)
        for warning in warnings:
            print(f"Warning: {warning}", file=sys.stderr)
        print(f"Extracted {len(rows)} rows.", file=sys.stderr)

        output_path = write_excel(rows, schema, args.output)
        print(f"Done. Wrote: {output_path}")
        return 0

    except LLMError as exc:
        print(f"LLM error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
