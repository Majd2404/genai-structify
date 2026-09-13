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
messy, unstructured text, propose a clean tabular schema that captures the \
meaningful, repeated entities in it.

Rules:
- Only propose fields that actually appear or can be reliably inferred.
- Prefer fewer, well-defined columns over many speculative ones.
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
- Return ONLY a JSON array of objects. Each object is one row.
- Every object must have exactly the keys given in the schema, in that order.
- If a field's value isn't present for a given record, use null -- never invent data.
- Dates should be normalized to YYYY-MM-DD when a date type is specified.
- Do not include rows that are pure noise/headers/duplicates.
- Do not include any explanation, markdown, or text outside the JSON array.
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


def extract_rows(client: LLMClient, text: str, schema: Schema) -> list[dict]:
    """
    Extracts rows across the whole document, chunking long input and
    de-duplicating rows that show up in the overlap between chunks.
    """
    all_rows: list[dict] = []
    seen = set()

    for chunk in chunk_text(text):
        prompt = (
            f"Schema:\n{schema.to_prompt_spec()}\n\n"
            f"Text to extract from:\n\n{chunk}"
        )
        rows = client.extract_json(EXTRACTION_SYSTEM_PROMPT, prompt)
        if not isinstance(rows, list):
            continue

        for row in rows:
            key = tuple(sorted(row.items(), key=lambda kv: kv[0]))
            if key not in seen:
                seen.add(key)
                all_rows.append(row)

    return all_rows
