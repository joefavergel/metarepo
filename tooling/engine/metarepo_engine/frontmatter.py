"""YAML frontmatter I/O shared by extractions, wiki pages and outputs (003/R6, G3).

* ``---``-delimited YAML at the top of a UTF-8 file; the body is everything after the closing line.
* Unknown keys and key order are preserved. When the parsed mapping is unchanged the original frontmatter
  text is written back verbatim (comments included); when it changed it is re-serialized in block style,
  and **YAML comments inside frontmatter are not preserved** in that case.
* The body is never touched: it is carried as the exact decoded text, so a round trip is byte-for-byte
  (line endings, BOM and a missing final newline included).
"""
from __future__ import annotations

import copy
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

from .config import yaml_module
from .fsops import atomic_write_bytes, fs_path

DELIM = "---"


class FrontmatterError(ValueError):
    pass


@dataclass
class Document:
    front: dict[str, Any] = field(default_factory=dict)
    body: str = ""
    has_front: bool = False
    newline: str = "\n"
    bom: bool = False
    _raw_front: str | None = None  # frontmatter text exactly as read (between the delimiter lines)
    _orig_front: dict[str, Any] | None = None

    def changed(self) -> bool:
        return self._orig_front is None or self.front != self._orig_front or list(self.front) != list(self._orig_front)

    def dump(self) -> str:
        if not self.has_front and not self.front:
            return ("﻿" if self.bom else "") + self.body
        nl = self.newline
        if self._raw_front is not None and not self.changed():
            front_text = self._raw_front
        elif not self.front:
            front_text = ""
        else:
            yaml = yaml_module()
            dumped = yaml.safe_dump(self.front, sort_keys=False, allow_unicode=True, default_flow_style=False,
                                    width=4096)
            front_text = dumped.replace("\n", nl)
        if front_text and not front_text.endswith(nl):
            front_text += nl
        return ("﻿" if self.bom else "") + DELIM + nl + front_text + DELIM + nl + self.body

    def to_bytes(self) -> bytes:
        return self.dump().encode("utf-8")


def _decode(data: bytes) -> tuple[str, bool]:
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise FrontmatterError(f"file is not valid UTF-8: {exc}") from exc
    if text.startswith("﻿"):
        return text[1:], True
    return text, False


def _split(text: str) -> tuple[str, str, str] | None:
    """(raw frontmatter, body, newline) when ``text`` starts with a frontmatter block, else None."""
    first_end = text.find("\n")
    if first_end < 0 or text[:first_end].rstrip("\r").rstrip() != DELIM:
        return None
    nl = "\r\n" if text[first_end - 1: first_end] == "\r" else "\n"
    pos = first_end + 1
    while pos <= len(text):
        end = text.find("\n", pos)
        line_end = len(text) if end < 0 else end
        line = text[pos:line_end].rstrip("\r")
        if line.rstrip() == DELIM:
            raw = text[first_end + 1: pos]
            body = text[line_end + 1:] if end >= 0 else ""
            return raw, body, nl
        if end < 0:
            break
        pos = end + 1
    raise FrontmatterError("frontmatter is not terminated by a closing '---' line")


def parse(data: bytes | str) -> Document:
    if isinstance(data, bytes):
        text, bom = _decode(data)
    else:
        text, bom = (data[1:], True) if data.startswith("﻿") else (data, False)
    split = _split(text)
    if split is None:
        return Document(front={}, body=text, has_front=False, bom=bom)
    raw, body, nl = split
    yaml = yaml_module()
    try:
        loaded = yaml.safe_load(raw) if raw.strip() else {}
    except yaml.YAMLError as exc:
        raise FrontmatterError(f"frontmatter is not valid YAML: {exc}") from exc
    if loaded is None:
        loaded = {}
    if not isinstance(loaded, dict):
        raise FrontmatterError("frontmatter must be a YAML mapping")
    return Document(front=loaded, body=body, has_front=True, newline=nl, bom=bom, _raw_front=raw,
                    _orig_front=copy.deepcopy(loaded))


def read(path: Path) -> Document:
    with open(fs_path(path), "rb") as fh:
        return parse(fh.read())


def read_head(path: Path) -> dict[str, Any]:
    """Frontmatter mapping only, reading just the head of the file (fast for large extractions)."""
    chunks: list[bytes] = []
    with open(fs_path(path), "rb") as fh:
        first = fh.readline()
        if first.lstrip(b"\xef\xbb\xbf").rstrip() != DELIM.encode():
            return {}
        chunks.append(first)
        for line in fh:
            chunks.append(line)
            if line.rstrip() == DELIM.encode():
                return parse(b"".join(chunks)).front
    raise FrontmatterError("frontmatter is not terminated by a closing '---' line")


def write(path: Path, doc: Document) -> None:
    atomic_write_bytes(Path(path), doc.to_bytes())


def update(path: Path, updates: dict[str, Any] | None = None, remove: Iterable[str] = ()) -> Document:
    """Change frontmatter keys in place; the body is carried through untouched."""
    doc = read(path)
    for key, value in (updates or {}).items():
        doc.front[key] = value
    for key in remove:
        doc.front.pop(key, None)
    doc.has_front = True
    if doc.changed():
        write(path, doc)
    return doc
