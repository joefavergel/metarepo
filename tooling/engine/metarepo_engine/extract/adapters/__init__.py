"""Built-in adapters, in default order of discovery (priority decides, this order breaks ties)."""
from __future__ import annotations

from ..base import Adapter
from .docling import DoclingAdapter
from .markitdown import MarkItDownAdapter
from .pymupdf import PyMuPdfAdapter
from .text import TextAdapter
from .xlsx import XlsxAdapter


def builtin_adapters() -> list[Adapter]:
    return [TextAdapter(), XlsxAdapter(), DoclingAdapter(), PyMuPdfAdapter(), MarkItDownAdapter()]
