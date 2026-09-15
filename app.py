"""
Streamlit web UI for GenAI-Structify.

Run with:
    streamlit run app.py

Three-step flow, mirroring the CLI's design:
  1. Upload a file -> infer a proposed schema
  2. Review/edit the schema before spending time on full extraction
  3. Extract rows and download the resulting .xlsx
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import pandas as pd
import streamlit as st

from genai_structify.excel_writer import write_excel
from genai_structify.extractor import Field, Schema, extract_rows, infer_schema
from genai_structify.llm_client import LLMClient, LLMError
from genai_structify.readers import UnsupportedFileType, read_input

st.set_page_config(page_title="GenAI-Structify", page_icon="📊", layout="centered")

st.title("📊 GenAI-Structify")
st.caption("Upload messy, unstructured data. Get back a clean, organized Excel file.")

if "text" not in st.session_state:
    st.session_state.text = None
if "schema" not in st.session_state:
    st.session_state.schema = None
if "rows" not in st.session_state:
    st.session_state.rows = None

with st.sidebar:
    st.subheader("Settings")
    model = st.text_input("Ollama model", value="llama3.2:1b")
    host = st.text_input("Ollama host", value="http://localhost:11434")
    st.caption("Requires Ollama running locally: `ollama serve`")
    st.caption(
        "Small models (1-2B) may need manual schema edits or produce fewer rows. "
        "Larger models (7B+) generally give more reliable results."
    )

uploaded_file = st.file_uploader(
    "Upload a file", type=["txt", "md", "csv", "pdf", "docx"]
)

if uploaded_file is not None:
    suffix = Path(uploaded_file.name).suffix
    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
        tmp.write(uploaded_file.getvalue())
        tmp_path = tmp.name

    try:
        st.session_state.text = read_input(tmp_path)
    except (UnsupportedFileType, ValueError) as exc:
        st.error(str(exc))
        st.session_state.text = None

if st.session_state.text:
    with st.expander("Preview extracted text", expanded=False):
        st.text(st.session_state.text[:2000] + ("..." if len(st.session_state.text) > 2000 else ""))

    if st.button("1. Infer schema", type="primary"):
        try:
            with st.spinner("Asking the model to propose a schema..."):
                client = LLMClient(model=model, host=host)
                st.session_state.schema = infer_schema(client, st.session_state.text)
                st.session_state.rows = None
        except LLMError as exc:
            st.error(str(exc))

if st.session_state.schema:
    st.subheader("2. Review the proposed schema")
    st.caption("Edit column names, types, or descriptions before extracting. Delete rows you don't want.")

    schema_df = pd.DataFrame(
        [{"name": f.name, "type": f.type, "description": f.description} for f in st.session_state.schema.fields]
    )
    edited_df = st.data_editor(
        schema_df,
        num_rows="dynamic",
        column_config={
            "type": st.column_config.SelectboxColumn(options=["string", "number", "date", "boolean"])
        },
        width="stretch",
    )

    sheet_name = st.text_input("Sheet name", value=st.session_state.schema.sheet_name)

    if st.button("3. Extract & build Excel", type="primary"):
        fields = [Field(**row) for row in edited_df.to_dict("records")]
        schema = Schema(fields=fields, sheet_name=sheet_name)

        try:
            with st.spinner("Extracting rows (this may take a while for long documents)..."):
                client = LLMClient(model=model, host=host)
                rows, warnings = extract_rows(client, st.session_state.text, schema)
                for warning in warnings:
                    st.warning(warning)
                st.session_state.rows = rows
                st.session_state.final_schema = schema
        except LLMError as exc:
            st.error(str(exc))

if st.session_state.rows is not None:
    st.subheader(f"Extracted {len(st.session_state.rows)} rows")
    st.dataframe(pd.DataFrame(st.session_state.rows), width="stretch")

    with tempfile.NamedTemporaryFile(delete=False, suffix=".xlsx") as tmp_out:
        write_excel(st.session_state.rows, st.session_state.final_schema, tmp_out.name)
        with open(tmp_out.name, "rb") as f:
            st.download_button(
                "⬇️ Download Excel file",
                data=f.read(),
                file_name="structured_output.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            )