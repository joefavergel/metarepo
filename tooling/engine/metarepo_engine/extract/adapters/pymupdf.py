"""PyMuPDF adapter (optional): one ``## Page N`` section per PDF page.

Uses ``pymupdf4llm`` for Markdown layout (tables, headings) when it is installed, else PyMuPDF's plain
text. The library API used here (``open``, ``page_count``, ``load_page``, ``get_text``, ``metadata``;
``pymupdf4llm.to_markdown(doc, pages=[i])``) is the documented one. [TO VERIFY against the installed
versions when a real library is available; the unit tests inject a fake module.]
"""
from __future__ import annotations

from pathlib import Path

from ..base import AdapterError, ExtractOptions, ExtractResult, module_available, normalize_body
from ._optional import OptionalAdapter

PDF = "application/pdf"


class PyMuPdfAdapter(OptionalAdapter):
    name = "pymupdf"
    modules = ("pymupdf", "fitz")
    dist = "PyMuPDF"
    pip_name = "pymupdf pymupdf4llm"
    media_summary = (PDF,)

    def handles(self, media_type: str) -> int:
        return 70 if media_type.lower() == PDF else 0

    def extract(self, path: Path, options: ExtractOptions) -> ExtractResult:
        lib = self.load()
        md_lib = self.load("pymupdf4llm") if module_available("pymupdf4llm") else None
        try:
            doc = lib.open(str(path))
        except Exception as exc:  # noqa: BLE001
            raise AdapterError(f"cannot open the PDF: {exc}") from exc
        try:
            pages, empty = [], 0
            total = doc.page_count
            for index in range(total):
                try:
                    if md_lib is not None:
                        text = md_lib.to_markdown(doc, pages=[index])
                    else:
                        text = doc.load_page(index).get_text("text")
                except Exception as exc:  # noqa: BLE001
                    raise AdapterError(f"page {index + 1} could not be read: {exc}") from exc
                text = normalize_body(text)
                if not text.strip():
                    empty += 1
                pages.append(f"## Page {index + 1}\n\n{text.rstrip()}".rstrip())
            meta = getattr(doc, "metadata", None) or {}
            title = (meta.get("title") or "").strip() or None
        finally:
            close = getattr(doc, "close", None)
            if close:
                close()
        return ExtractResult(body=normalize_body("\n\n".join(pages)), title=title, structure={"pages": total},
                             signals={"pages": total, "empty_pages": empty})
