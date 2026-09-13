"""
Turns whatever the user hands us (.txt, .pdf, .docx, or raw pasted text)
into a single plain-text string the extractor can work with.
"""

from __future__ import annotations

from pathlib import Path


class UnsupportedFileType(ValueError):
    pass


def read_input(path: str | Path) -> str:
    path = Path(path)
    suffix = path.suffix.lower()

    if suffix == ".txt" or suffix == ".md" or suffix == ".csv":
        return path.read_text(encoding="utf-8", errors="ignore")

    if suffix == ".pdf":
        return _read_pdf(path)

    if suffix == ".docx":
        return _read_docx(path)

    raise UnsupportedFileType(
        f"'{suffix}' is not supported yet. Supported: .txt, .md, .csv, .pdf, .docx"
    )


def _read_pdf(path: Path) -> str:
    try:
        from pypdf import PdfReader
    except ImportError as exc:
        raise ImportError(
            "PDF support requires pypdf. Install with: pip install pypdf"
        ) from exc

    reader = PdfReader(str(path))
    pages = [page.extract_text() or "" for page in reader.pages]
    text = "\n".join(pages).strip()

    if not text:
        raise ValueError(
            "No extractable text found in this PDF -- it may be a scanned image. "
            "OCR support is on the roadmap (see README)."
        )
    return text


def _read_docx(path: Path) -> str:
    try:
        import docx
    except ImportError as exc:
        raise ImportError(
            "DOCX support requires python-docx. Install with: pip install python-docx"
        ) from exc

    document = docx.Document(str(path))
    return "\n".join(p.text for p in document.paragraphs)
