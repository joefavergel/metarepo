"""Stdlib reader for ``.xlsx`` workbooks: one ``## Sheet: <name>`` section per sheet (008 section 2).

Cell values are the cached values stored in the file (formulas are not evaluated). Dates are stored as
serial numbers in the format and appear as such; this adapter does not interpret number formats.
"""
from __future__ import annotations

import posixpath
import re
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET

from .. import tables
from ..base import Adapter, AdapterError, ExtractOptions, ExtractResult, normalize_body
from ...fsops import fs_path

ADAPTER_VERSION = "1"
XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
MAX_MEMBER_BYTES = 1024 * 1024 * 1024  # zip-bomb guard per workbook part

_NS = {"m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main",
       "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships",
       "rel": "http://schemas.openxmlformats.org/package/2006/relationships"}
_REF = re.compile(r"^([A-Z]+)(\d+)$")


def _col_index(letters: str) -> int:
    n = 0
    for ch in letters:
        n = n * 26 + (ord(ch) - 64)
    return n - 1


def _number(text: str) -> str:
    try:
        value = float(text)
    except ValueError:
        return text
    if value == int(value) and abs(value) < 1e15:
        return str(int(value))
    return format(value, ".15g")


def _read(zf: zipfile.ZipFile, name: str) -> bytes:
    info = zf.getinfo(name)
    if info.file_size > MAX_MEMBER_BYTES:
        raise AdapterError(f"workbook part {name} is too large to read safely")
    return zf.read(name)


def _shared_strings(zf: zipfile.ZipFile) -> list[str]:
    if "xl/sharedStrings.xml" not in zf.namelist():
        return []
    root = ET.fromstring(_read(zf, "xl/sharedStrings.xml"))
    out = []
    for si in root.findall("m:si", _NS):
        out.append("".join(t.text or "" for t in si.iter(f"{{{_NS['m']}}}t")))
    return out


def _sheet_targets(zf: zipfile.ZipFile) -> list[tuple[str, str]]:
    wb = ET.fromstring(_read(zf, "xl/workbook.xml"))
    rels = ET.fromstring(_read(zf, "xl/_rels/workbook.xml.rels"))
    targets = {r.get("Id"): r.get("Target") for r in rels.findall("rel:Relationship", _NS)}
    out = []
    for sheet in wb.findall("m:sheets/m:sheet", _NS):
        rid = sheet.get(f"{{{_NS['r']}}}id")
        target = targets.get(rid)
        if not target:
            continue
        path = target.lstrip("/") if target.startswith("/") else posixpath.normpath(posixpath.join("xl", target))
        out.append((sheet.get("name") or f"Sheet{len(out) + 1}", path))
    return out


def _sheet_rows(zf: zipfile.ZipFile, part: str, strings: list[str]) -> list[list[str]]:
    root = ET.fromstring(_read(zf, part))
    rows: list[list[str]] = []
    for row in root.iterfind("m:sheetData/m:row", _NS):
        cells: dict[int, str] = {}
        next_col = 0
        for c in row.findall("m:c", _NS):
            m = _REF.match(c.get("r") or "")
            col = _col_index(m.group(1)) if m else next_col
            next_col = col + 1
            kind = c.get("t")
            v = c.find("m:v", _NS)
            if kind == "inlineStr":
                value = "".join(t.text or "" for t in c.iter(f"{{{_NS['m']}}}t"))
            elif v is None or v.text is None:
                continue
            elif kind == "s":
                idx = int(v.text)
                value = strings[idx] if 0 <= idx < len(strings) else ""
            elif kind == "b":
                value = "TRUE" if v.text.strip() == "1" else "FALSE"
            elif kind in ("str", "e"):
                value = v.text
            else:
                value = _number(v.text)
            cells[col] = value
        width = (max(cells) + 1) if cells else 0
        rows.append([cells.get(i, "") for i in range(width)])
    while rows and not any(rows[-1]):
        rows.pop()
    width = max((len(r) for r in rows), default=0)
    return [r + [""] * (width - len(r)) for r in rows]


class XlsxAdapter(Adapter):
    name = "xlsx"
    mode = "local"
    kind = "builtin"
    media_summary = (XLSX,)

    def version(self) -> str:
        return ADAPTER_VERSION

    def handles(self, media_type: str) -> int:
        return 80 if media_type.lower() == XLSX else 0

    def extract(self, path: Path, options: ExtractOptions) -> ExtractResult:
        try:
            with zipfile.ZipFile(fs_path(path)) as zf:
                strings = _shared_strings(zf)
                sections, names = [], []
                for name, part in _sheet_targets(zf):
                    names.append(name)
                    rows = _sheet_rows(zf, part, strings)
                    table = tables.render_table(rows, options.large_table_rows) if rows else "_(empty sheet)_"
                    sections.append(f"## Sheet: {name}\n\n{table}")
        except (zipfile.BadZipFile, KeyError, ET.ParseError, ValueError, OSError) as exc:
            raise AdapterError(f"not a readable workbook: {exc}") from exc
        return ExtractResult(body=normalize_body("\n\n".join(sections)), structure={"sheets": names},
                             notes=["formulas show cached values; dates appear as serial numbers"])
