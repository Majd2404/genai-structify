"""
Core pipeline: raw text -> proposed schema -> structured rows.

Two-step design on purpose:
  1. infer_schema()  -- ask the model what fields make sense for this data
  2. extract_rows()  -- ask the model to actually pull rows using that schema

Splitting these lets a caller (CLI or future web UI) show the user the
proposed schema and let them edit it before the (more expensive) full
extraction pass runs.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .llm_client import LLMClient

# Rough character budget per chunk. Conservative to leave headroom for
# the prompt + JSON overhead within the model's context window.
CHUNK_SIZE = 12000
CHUNK_OVERLAP = 500


@dataclass
class Field:
    name: str
    type: str  # "string" | "number" | "date" | "boolean"
    description: str


@dataclass
class Schema:
    fields: list[Field] = field(default_factory=list)
    sheet_name: str = "Data"

    def to_prompt_spec(self) -> str:
        lines = [f"- {f.name} ({f.type}): {f.description}" for f in self.fields]
        return "\n".join(lines)


SCHEMA_SYSTEM_PROMPT = """You are a data modeling assistant. Given a sample of \
messy, unstructured text, propose a clean tabular schema for the REPEATING RECORDS \
in it -- the kind of structure you'd use as spreadsheet columns.

Critical distinction:
- CORRECT: fields describe attributes of ONE record type that repeats many times \
(e.g. for invoice notes: vendor, invoice_id, amount, status, due_date). Every row \
in the final table will be one record, with these fields as its columns.
- WRONG: fields that summarize different topics, sections, or entities mentioned in \
the document (e.g. "meeting_notes", "vendor_follow_ups", "acme_logistics", \
"nora_updates"). These are NOT columns -- they're document sections, and would make \
extraction impossible. Never propose a field per company/person/topic mentioned.

If the text describes multiple distinct events about the same kind of entity \
(e.g. several invoices, several log entries, several transactions), the schema \
must have ONE row shape that all of them share -- not one field per entity.

Rules:
- Only propose fields that actually appear or can be reliably inferred.
- Prefer fewer, well-defined columns over many speculative ones (aim for 4-8 fields).
- Use snake_case for field names.
- Valid types are only: string, number, date, boolean.
- Also propose a short, human-readable sheet_name (2-3 words, no spaces -> use underscores).

Respond with ONLY a JSON object of this exact shape, no other text:
{
  "sheet_name": "example_name",
  "fields": [
    {"name": "field_name", "type": "string", "description": "what this captures"}
  ]
}
"""

EXTRACTION_SYSTEM_PROMPT = """You extract structured records from unstructured text \
according to a fixed schema.

Rules:
- Return ONLY a JSON array of objects -- even if there is exactly one matching record, \
or none at all (in which case return []). NEVER return a single JSON object at the top \
level; it must always be wrapped in an array.
- Every object must have exactly the keys given in the schema, in that order.
- If a field's value isn't present for a given record, use null -- never invent data.
- Dates should be normalized to YYYY-MM-DD when a date type is specified.
- Do not include rows that are pure noise/headers/duplicates.
- Do not include any explanation, markdown, or text outside the JSON array.

Example of the required shape (for a schema with fields "name" and "amount"):
[{"name": "Acme", "amount": 100}, {"name": "Globex", "amount": 250}]
"""


def infer_schema(client: LLMClient, text: str) -> Schema:
    sample = text[:6000]  # a representative sample is enough to propose a schema
    result = client.extract_json(
        SCHEMA_SYSTEM_PROMPT,
        f"Sample data:\n\n{sample}",
    )
    fields = [Field(**f) for f in result["fields"]]
    return Schema(fields=fields, sheet_name=result.get("sheet_name", "Data"))


def chunk_text(text: str, chunk_size: int = CHUNK_SIZE, overlap: int = CHUNK_OVERLAP) -> list[str]:
    if len(text) <= chunk_size:
        return [text]

    chunks = []
    start = 0
    while start < len(text):
        end = start + chunk_size
        chunks.append(text[start:end])
        start = end - overlap
    return chunks


def _normalize_to_row_list(rows, schema: Schema):
    """
    Best-effort recovery when the model doesn't return a plain JSON array,
    a common failure mode for small local models. Returns a list (possibly
    empty) -- never None -- so callers don't need a separate "couldn't
    normalize" branch.
    """
    if isinstance(rows, list):
        return rows

    if isinstance(rows, dict):
        # Common wrapper shapes: {"rows": [...]}, {"data": [...]}, etc.
        for key in ("rows", "records", "data", "results", "items"):
            if isinstance(rows.get(key), list):
                return rows[key]

        # A single record returned unwrapped -- if its keys look like our
        # schema's fields, treat it as one row rather than discarding it.
        field_names = {f.name for f in schema.fields}
        if field_names & set(rows.keys()):
            return [rows]

    return []


def extract_rows(client: LLMClient, text: str, schema: Schema) -> tuple[list[dict], list[str]]:
    """
    Extracts rows across the whole document, chunking long input and
    de-duplicating rows that show up in the overlap between chunks.

    Returns (rows, warnings). Warnings are populated when a chunk's response
    couldn't be used or needed best-effort recovery -- this is surfaced
    instead of silently producing an empty result, which is especially
    important with small local models that sometimes fail to follow the
    schema exactly.
    """
    all_rows: list[dict] = []
    warnings: list[str] = []
    seen = set()

    chunks = chunk_text(text)
    for i, chunk in enumerate(chunks, start=1):
        prompt = (
            f"Schema:\n{schema.to_prompt_spec()}\n\n"
            f"Text to extract from:\n\n{chunk}"
        )
        raw = client.extract_json(EXTRACTION_SYSTEM_PROMPT, prompt)
        rows = _normalize_to_row_list(raw, schema)

        if not isinstance(raw, list) and rows:
            warnings.append(
                f"Chunk {i}/{len(chunks)}: model returned a {type(raw).__name__} "
                "instead of a JSON array -- recovered automatically."
            )
        elif not rows:
            warnings.append(
                f"Chunk {i}/{len(chunks)}: no usable rows "
                f"(model returned {type(raw).__name__})."
            )
            continue

        for row in rows:
            if not isinstance(row, dict):
                warnings.append(f"Chunk {i}/{len(chunks)}: skipped a non-object row.")
                continue
            key = tuple(sorted(row.items(), key=lambda kv: kv[0]))
            if key not in seen:
                seen.add(key)
                all_rows.append(row)

    if not all_rows and not warnings:
        warnings.append("No rows were extracted, and no errors were reported by the model.")

    return all_rows, warnings
