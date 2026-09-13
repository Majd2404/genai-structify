"""
Unit tests. Chunking and the Excel writer are tested with no network calls.
LLM-dependent functions (infer_schema, extract_rows) are exercised with a
fake client so the suite runs without an API key.
"""

from __future__ import annotations

from genai_structify.excel_writer import write_excel
from genai_structify.extractor import Field, Schema, chunk_text, extract_rows


class FakeLLMClient:
    """Stands in for LLMClient in tests -- returns canned JSON."""

    def __init__(self, responses):
        self._responses = list(responses)

    def extract_json(self, system_prompt, user_content, max_tokens=4000):
        return self._responses.pop(0)


def test_chunk_text_short_input_returns_single_chunk():
    text = "short text"
    assert chunk_text(text, chunk_size=100) == [text]


def test_chunk_text_splits_long_input_with_overlap():
    text = "a" * 1000
    chunks = chunk_text(text, chunk_size=300, overlap=50)
    assert len(chunks) > 1
    # consecutive chunks should overlap
    assert chunks[0][-50:] == chunks[1][:50]


def test_extract_rows_deduplicates_across_chunks():
    schema = Schema(
        fields=[Field(name="name", type="string", description="")],
        sheet_name="Data",
    )
    fake = FakeLLMClient(
        [
            [{"name": "Alice"}, {"name": "Bob"}],
            [{"name": "Bob"}, {"name": "Carol"}],  # Bob repeated at chunk boundary
        ]
    )
    long_text = "x" * (12000 + 100)  # forces 2 chunks at default CHUNK_SIZE
    rows = extract_rows(fake, long_text, schema)
    names = sorted(r["name"] for r in rows)
    assert names == ["Alice", "Bob", "Carol"]


def test_write_excel_creates_file_with_expected_headers(tmp_path):
    schema = Schema(
        fields=[
            Field(name="vendor", type="string", description=""),
            Field(name="amount", type="number", description=""),
        ],
        sheet_name="Invoices",
    )
    rows = [{"vendor": "Acme", "amount": 4250}, {"vendor": "BrightPay", "amount": 1200}]

    out = write_excel(rows, schema, tmp_path / "out.xlsx")
    assert out.exists()

    from openpyxl import load_workbook

    wb = load_workbook(out)
    ws = wb.active
    assert ws.title == "Invoices"
    assert [c.value for c in ws[1]] == ["vendor", "amount"]
    assert ws.cell(row=2, column=1).value == "Acme"
