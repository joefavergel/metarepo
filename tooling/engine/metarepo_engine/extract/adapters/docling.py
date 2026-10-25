"""Docling adapter (optional): layout-aware conversion of PDF, Office files and images.

The first conversion may download models (Docling's own behaviour, outside the engine's control). The adapter is
``mode: local``: documents are converted on this machine. The calls used
(``docling.document_converter.DocumentConverter().convert(path).document.export_to_markdown(
page_break_placeholder=...)``) follow Docling's documented API. [TO VERIFY against the installed
version when a real library is available; the unit tests inject a fake module.]
"""
from __future__ import annotations

import importlib
from pathlib import Path

from ..base import AdapterError, ExtractOptions, ExtractResult, normalize_body
from ._optional import OptionalAdapter

PAGE_BREAK = "<<<metarepo-page-break>>>"
_MEDIA = {
    "application/pdf": 75,
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": 70,
    "application/vnd.openxmlformats-officedocument.presentationml.presentation": 70,
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": 50,
    "text/html": 20,
    "image/png": 50, "image/jpeg": 50, "image/tiff": 50, "image/bmp": 50, "image/webp": 50,
}
_PRESENTATION = "application/vnd.openxmlformats-officedocument.presentationml.presentation"


class DoclingAdapter(OptionalAdapter):
    name = "docling"
    modules = ("docling",)
    dist = "docling"
    media_summary = tuple(_MEDIA)

    def handles(self, media_type: str) -> int:
        return _MEDIA.get(media_type.lower(), 0)

    def extract(self, path: Path, options: ExtractOptions) -> ExtractResult:
        self.load()
        converter_mod = self.load_submodule("docling.document_converter")
        try:
            result = converter_mod.DocumentConverter().convert(str(path))
            markdown = result.document.export_to_markdown(page_break_placeholder=PAGE_BREAK)
        except Exception as exc:  # noqa: BLE001
            raise AdapterError(f"conversion failed: {exc}") from exc
        chunks = markdown.split(PAGE_BREAK)
        label = "Slide" if options.media_type == _PRESENTATION else "Page"
        paged = len(chunks) > 1 or options.media_type in ("application/pdf", _PRESENTATION)
        empty = sum(1 for c in chunks if not c.strip())
        if paged:
            body = "\n\n".join(f"## {label} {i + 1}\n\n{c.strip()}".rstrip() for i, c in enumerate(chunks))
            structure = {"pages": len(chunks)}
            signals = {"pages": len(chunks), "empty_pages": empty}
        else:
            body, structure, signals = markdown, {}, {}
        return ExtractResult(body=normalize_body(body), structure=structure, signals=signals)

    def load_submodule(self, name: str):
        try:
            return importlib.import_module(name)
        except Exception as exc:  # noqa: BLE001
            raise AdapterError(f"cannot import {name}: {exc}") from exc
