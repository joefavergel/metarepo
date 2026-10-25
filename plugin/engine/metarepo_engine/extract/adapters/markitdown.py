"""MarkItDown adapter (optional): Office files, HTML, EPUB and a PDF fallback.

``markitdown.MarkItDown().convert(path).text_content`` is the documented call. Spreadsheet tables are
post-processed with the large-table rule so a huge sheet never lands in the body (AC2).
[TO VERIFY against the installed version when a real library is available; unit tests inject a fake.]
"""
from __future__ import annotations

import re
from pathlib import Path

from .. import tables
from ..base import AdapterError, ExtractOptions, ExtractResult, normalize_body
from ._optional import OptionalAdapter

XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
PPTX = "application/vnd.openxmlformats-officedocument.presentationml.presentation"
_MEDIA = {
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": 60,
    PPTX: 60,
    XLSX: 40,
    "application/vnd.ms-excel": 40,
    "application/pdf": 40,
    "application/epub+zip": 60,
    "text/html": 30,
    "application/msword": 30,
    "application/vnd.ms-powerpoint": 30,
}
_SLIDE = re.compile(r"<!--\s*Slide number:\s*(\d+)\s*-->")


class MarkItDownAdapter(OptionalAdapter):
    name = "markitdown"
    modules = ("markitdown",)
    dist = "markitdown"
    media_summary = tuple(_MEDIA)

    def handles(self, media_type: str) -> int:
        return _MEDIA.get(media_type.lower(), 0)

    def extract(self, path: Path, options: ExtractOptions) -> ExtractResult:
        lib = self.load()
        try:
            text = lib.MarkItDown().convert(str(path)).text_content
        except Exception as exc:  # noqa: BLE001
            raise AdapterError(f"conversion failed: {exc}") from exc
        text = text or ""
        structure: dict = {}
        if options.media_type in (XLSX, "application/vnd.ms-excel"):
            text = re.sub(r"(?m)^## (?!Sheet: )", "## Sheet: ", text)
            text = tables.summarize_pipe_tables(text.replace("\r\n", "\n"), options.large_table_rows)
            structure = {"sheets": re.findall(r"(?m)^## Sheet: (.+)$", text)}
        elif options.media_type == PPTX:
            text = _SLIDE.sub(lambda m: f"## Slide {m.group(1)}", text)
        return ExtractResult(body=normalize_body(text), structure=structure)
