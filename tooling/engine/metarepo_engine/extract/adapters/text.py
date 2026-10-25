"""Stdlib adapter for plain text, Markdown, CSV/TSV, HTML, transcripts (VTT/SRT) and structured text.

No optional dependency; it is what makes extraction work on a bare installation (ADR-002).
"""
from __future__ import annotations

import csv
import io
import re
from html.parser import HTMLParser
from pathlib import Path

from .. import tables
from ..base import Adapter, AdapterError, ExtractOptions, ExtractResult, normalize_body
from ...fsops import fs_path

ADAPTER_VERSION = "1"

_PLAIN = {"text/plain": None}
_MARKDOWN = {"text/markdown"}
_TABULAR = {"text/csv": ",", "text/tab-separated-values": "\t"}
_HTML = {"text/html", "application/xhtml+xml"}
_TRANSCRIPT = {"text/vtt", "application/x-subrip"}
_FENCED = {"application/json": "json", "application/yaml": "yaml", "application/xml": "xml", "text/xml": "xml",
           "text/calendar": "", "message/rfc822": ""}


def decode(data: bytes) -> tuple[str, list[str]]:
    notes: list[str] = []
    if data.startswith(b"\xef\xbb\xbf"):
        data = data[3:]
    elif data.startswith((b"\xff\xfe", b"\xfe\xff")):
        try:
            return data.decode("utf-16"), notes
        except UnicodeDecodeError:
            pass
    try:
        return data.decode("utf-8"), notes
    except UnicodeDecodeError:
        text = data.decode("utf-8", errors="replace")
        notes.append("the file is not valid UTF-8; undecodable bytes were replaced")
        return text, notes


def first_heading(markdown: str) -> str | None:
    for line in markdown.split("\n"):
        m = re.match(r"^#{1,6}\s+(.+?)\s*#*\s*$", line)
        if m:
            return m.group(1).strip()
    return None


# ----------------------------------------------------------------------------- HTML
class _HtmlToMarkdown(HTMLParser):
    SKIP = {"script", "style", "head", "noscript", "template", "svg"}
    BLOCK = {"p", "div", "section", "article", "header", "footer", "main", "aside", "nav", "blockquote", "figure",
             "figcaption", "ul", "ol", "dl", "table", "tr", "form", "address"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.out: list[str] = []
        self.title = ""
        self._skip = 0
        self._in_title = False
        self._href: list[str | None] = []
        self._lists: list[list[int]] = []
        self._pre = 0
        self._row: list[str] | None = None
        self._rows: list[list[str]] = []
        self._cell: list[str] | None = None

    def _emit(self, text: str) -> None:
        if self._cell is not None:
            self._cell.append(text)
        else:
            self.out.append(text)

    def handle_starttag(self, tag, attrs):
        if tag == "title":
            self._in_title = True
            return
        if tag in self.SKIP:
            self._skip += 1
            return
        if self._skip:
            return
        a = dict(attrs)
        if tag in ("h1", "h2", "h3", "h4", "h5", "h6"):
            self._emit("\n\n" + "#" * int(tag[1]) + " ")
        elif tag == "br":
            self._emit("\n")
        elif tag == "hr":
            self._emit("\n\n---\n\n")
        elif tag in ("ul", "ol"):
            self._lists.append([0, 1 if tag == "ol" else 0])
            self._emit("\n")
        elif tag == "li":
            depth = max(len(self._lists) - 1, 0)
            marker = "- "
            if self._lists and self._lists[-1][1]:
                self._lists[-1][0] += 1
                marker = f"{self._lists[-1][0]}. "
            self._emit("\n" + "  " * depth + marker)
        elif tag == "a":
            self._href.append(a.get("href"))
            self._emit("[")
        elif tag in ("strong", "b"):
            self._emit("**")
        elif tag in ("em", "i"):
            self._emit("_")
        elif tag == "pre":
            self._pre += 1
            self._emit("\n\n```\n")
        elif tag == "code" and not self._pre:
            self._emit("`")
        elif tag == "img":
            alt = (a.get("alt") or "").strip()
            if alt:
                self._emit(f"> [Figure: {alt}]")
        elif tag == "table":
            self._rows = []
        elif tag == "tr":
            self._row = []
        elif tag in ("td", "th"):
            self._cell = []
        elif tag in self.BLOCK:
            self._emit("\n\n")

    def handle_endtag(self, tag):
        if tag == "title":
            self._in_title = False
            return
        if tag in self.SKIP:
            self._skip = max(self._skip - 1, 0)
            return
        if self._skip:
            return
        if tag in ("h1", "h2", "h3", "h4", "h5", "h6"):
            self._emit("\n\n")
        elif tag in ("ul", "ol"):
            if self._lists:
                self._lists.pop()
            self._emit("\n\n")
        elif tag == "a":
            href = self._href.pop() if self._href else None
            self._emit(f"]({href})" if href else "]")
        elif tag in ("strong", "b"):
            self._emit("**")
        elif tag in ("em", "i"):
            self._emit("_")
        elif tag == "pre":
            self._pre = max(self._pre - 1, 0)
            self._emit("\n```\n\n")
        elif tag == "code" and not self._pre:
            self._emit("`")
        elif tag in ("td", "th"):
            if self._cell is not None and self._row is not None:
                self._row.append(re.sub(r"\s+", " ", "".join(self._cell)).strip())
            self._cell = None
        elif tag == "tr":
            if self._row is not None:
                self._rows.append(self._row)
            self._row = None
        elif tag == "table":
            if self._rows:
                width = max(len(r) for r in self._rows)
                rows = [r + [""] * (width - len(r)) for r in self._rows]
                self._emit("\n\n" + tables.md_table(rows[0], rows[1:]) + "\n\n")
            self._rows = []
        elif tag in self.BLOCK:
            self._emit("\n\n")

    def handle_data(self, data):
        if self._in_title:
            self.title += data
            return
        if self._skip:
            return
        if self._pre:
            self._emit(data)
        else:
            self._emit(re.sub(r"\s+", " ", data))


def html_to_markdown(source: str) -> tuple[str, str | None]:
    parser = _HtmlToMarkdown()
    parser.feed(source)
    parser.close()
    text = "".join(parser.out)
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip(), (parser.title.strip() or None)


# ----------------------------------------------------------------------------- transcripts
_CUE = re.compile(r"(?:(\d+):)?(\d{1,2}):(\d{2})[.,](\d{3})\s*-->\s*(?:(\d+):)?(\d{1,2}):(\d{2})[.,](\d{3})")


def _secs(h, m, s, ms) -> float:
    return int(h or 0) * 3600 + int(m) * 60 + int(s) + int(ms) / 1000


def _stamp(sec: float) -> str:
    sec = int(sec)
    return f"[{sec // 3600:02d}:{sec % 3600 // 60:02d}:{sec % 60:02d}]"


def transcript_to_markdown(text: str) -> tuple[str, float, float]:
    """Cues -> ``[hh:mm:ss] text`` lines. Returns (markdown, duration_s, covered_s)."""
    cues: list[tuple[float, float, str]] = []
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    i = 0
    while i < len(lines):
        m = _CUE.search(lines[i])
        if not m:
            i += 1
            continue
        start, end = _secs(*m.group(1, 2, 3, 4)), _secs(*m.group(5, 6, 7, 8))
        i += 1
        buf = []
        while i < len(lines) and lines[i].strip():
            buf.append(re.sub(r"<[^>]+>", "", lines[i]).strip())
            i += 1
        if buf:
            cues.append((start, end, " ".join(buf)))
    covered, cursor = 0.0, 0.0
    for start, end, _ in sorted(cues):
        lo = max(start, cursor)
        if end > lo:
            covered += end - lo
            cursor = end
    duration = max((c[1] for c in cues), default=0.0)
    return "\n".join(f"{_stamp(s)} {t}" for s, _, t in cues), duration, covered


# ----------------------------------------------------------------------------- adapter
class TextAdapter(Adapter):
    name = "text"
    mode = "local"
    kind = "builtin"
    media_summary = ("text/plain", "text/markdown", "text/csv", "text/tab-separated-values", "text/html",
                     "text/vtt", "application/x-subrip", "application/json", "application/yaml", "application/xml")

    def version(self) -> str:
        return ADAPTER_VERSION

    def handles(self, media_type: str) -> int:
        mt = media_type.lower()
        known = set(_PLAIN) | _MARKDOWN | set(_TABULAR) | _HTML | _TRANSCRIPT | set(_FENCED)
        if mt in known:
            return 50
        return 10 if mt.startswith("text/") else 0

    def extract(self, path: Path, options: ExtractOptions) -> ExtractResult:
        try:
            with open(fs_path(path), "rb") as fh:
                data = fh.read()
        except OSError as exc:
            raise AdapterError(f"cannot read the file: {exc}") from exc
        text, notes = decode(data)
        mt = (options.media_type or "text/plain").lower()
        result = ExtractResult(body="", notes=notes)
        if mt in _TABULAR:
            rows = list(csv.reader(io.StringIO(text, newline=""), delimiter=_TABULAR[mt]))
            rows = [r for r in rows if any(c.strip() for c in r)]
            result.body = tables.render_table(rows, options.large_table_rows) if rows else ""
            result.structure = {"rows": max(len(rows) - 1, 0), "columns": len(rows[0]) if rows else 0}
        elif mt in _HTML:
            result.body, result.title = html_to_markdown(text)
        elif mt in _TRANSCRIPT:
            body, duration, covered = transcript_to_markdown(text)
            result.body = body
            if duration:
                result.structure = {"duration_s": int(duration)}
                result.signals = {"duration_s": duration, "covered_s": covered}
        elif mt in _FENCED:
            fence = "````" if "```" in text else "```"
            result.body = f"{fence}{_FENCED[mt]}\n{text.strip(chr(10))}\n{fence}"
        else:
            result.body = text
        if result.title is None and mt in _MARKDOWN:
            result.title = first_heading(text)
        if result.title is None and mt in _PLAIN:
            first = next((ln.strip() for ln in text.split("\n") if ln.strip()), "")
            result.title = first[:120] if first and len(first) <= 120 else None
        result.body = normalize_body(result.body)
        return result

