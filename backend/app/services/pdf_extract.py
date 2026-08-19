"""
PDF -> plain text extraction, using PyMuPDF (fitz) with pdfplumber as a
fallback for PDFs that PyMuPDF struggles with (e.g. some scanned/complex
layouts). Both are pure-text extractors; OCR is out of scope.
"""
import io


def extract_text(file_bytes: bytes) -> str:
    text = _extract_with_pymupdf(file_bytes)
    if text and len(text.strip()) > 50:
        return text

    text = _extract_with_pdfplumber(file_bytes)
    if text:
        return text

    raise ValueError(
        "Could not extract readable text from this PDF. It may be a scanned "
        "image without a text layer (OCR is not implemented in this build)."
    )


def _extract_with_pymupdf(file_bytes: bytes) -> str:
    try:
        import fitz  # PyMuPDF
    except ImportError:
        return ""
    try:
        doc = fitz.open(stream=file_bytes, filetype="pdf")
        pages = [page.get_text("text") for page in doc]
        doc.close()
        return "\n".join(pages)
    except Exception:
        return ""


def _extract_with_pdfplumber(file_bytes: bytes) -> str:
    try:
        import pdfplumber
    except ImportError:
        return ""
    try:
        pages = []
        with pdfplumber.open(io.BytesIO(file_bytes)) as pdf:
            for page in pdf.pages:
                pages.append(page.extract_text() or "")
        return "\n".join(pages)
    except Exception:
        return ""


def split_sentences(text: str) -> list[str]:
    """Lightweight sentence splitter (avoids a heavy NLTK/spaCy dependency
    for this one step; ner.py uses spaCy's own splitter when a model is
    loaded and this is only the plain-text fallback)."""
    import re
    # Collapse whitespace/line-breaks from PDF extraction artifacts first.
    normalized = re.sub(r"\s+", " ", text).strip()
    # Split on sentence-ending punctuation followed by a capital letter,
    # while trying not to break on common abbreviations / decimal numbers.
    raw_sentences = re.split(r"(?<!\b[A-Z])(?<=[.!?])\s+(?=[A-Z(])", normalized)
    return [s.strip() for s in raw_sentences if s.strip()]
