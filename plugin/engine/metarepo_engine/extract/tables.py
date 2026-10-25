"""Markdown tables and the large-table rule (008 section 2, AC2).

Up to ``large_table_rows`` data rows a table is rendered inline. Above that the body gets a schema
(columns, inferred types), the row count, summary statistics and the first and last rows, with a note
that the full data is in the original. Output is deterministic (R9).
"""
from __future__ import annotations

import re
from typing import Sequence

EDGE_ROWS = 5
_INT = re.compile(r"^[+-]?\d+$")
_NUM = re.compile(r"^[+-]?(?:\d+\.\d*|\.\d+|\d+)(?:[eE][+-]?\d+)?$")
_BOOL = {"true", "false"}
_SEPARATOR = re.compile(r"^\|?\s*:?-{3,}:?\s*(\|\s*:?-{3,}:?\s*)*\|?$")


def cell(text: str) -> str:
    return str(text).replace("\\", "\\\\").replace("|", "\\|").replace("\r\n", "\n").replace("\n", "<br>").strip()


def md_table(header: Sequence[str], rows: Sequence[Sequence[str]]) -> str:
    width = len(header)
    lines = ["| " + " | ".join(cell(h) for h in header) + " |", "| " + " | ".join("---" for _ in header) + " |"]
    for row in rows:
        padded = list(row) + [""] * (width - len(row))
        lines.append("| " + " | ".join(cell(c) for c in padded[:width]) + " |")
    return "\n".join(lines)


def _header(first: Sequence[str]) -> list[str]:
    return [h.strip() or f"col_{i + 1}" for i, h in enumerate(first)]


def infer_type(values: Sequence[str]) -> str:
    present = [v.strip() for v in values if v.strip()]
    if not present:
        return "empty"
    if all(_INT.match(v) for v in present):
        return "integer"
    if all(_NUM.match(v) for v in present):
        return "number"
    if all(v.lower() in _BOOL for v in present):
        return "boolean"
    return "text"


def _fmt(x: float) -> str:
    return str(int(x)) if x == int(x) and abs(x) < 1e15 else format(x, ".6g")


def column_summary(name: str, values: Sequence[str]) -> list[str]:
    kind = infer_type(values)
    present = [v for v in values if v.strip()]
    row = [name, kind, str(len(present)), str(len(set(present)))]
    if kind in ("integer", "number"):
        nums = [float(v) for v in present]
        row += [_fmt(min(nums)), _fmt(max(nums)), _fmt(sum(nums) / len(nums))]
    elif present:
        ordered = sorted(present)
        row += [ordered[0][:40], ordered[-1][:40], ""]
    else:
        row += ["", "", ""]
    return row


def render_table(rows: Sequence[Sequence[str]], large_rows: int) -> str:
    """``rows[0]`` is the header. Returns Markdown (the table itself, or the large-table summary)."""
    if not rows:
        return "_(empty)_"
    header = _header(rows[0])
    data = [list(r) + [""] * (len(header) - len(r)) for r in rows[1:]]
    if len(data) <= large_rows:
        return md_table(header, data)
    cols = [[r[i] for r in data] for i in range(len(header))]
    summary = [column_summary(h, c) for h, c in zip(header, cols)]
    parts = [
        f"> Large table: {len(data)} data rows x {len(header)} columns. Only the schema, summary statistics and the "
        f"first and last {EDGE_ROWS} rows are shown; the full data is in the original file.",
        "",
        "**Schema and summary**",
        "",
        md_table(["column", "type", "non-empty", "distinct", "min", "max", "mean"], summary),
        "",
        f"**First {EDGE_ROWS} rows**",
        "",
        md_table(header, data[:EDGE_ROWS]),
        "",
        f"**Last {EDGE_ROWS} rows**",
        "",
        md_table(header, data[-EDGE_ROWS:]),
    ]
    return "\n".join(parts)


def _split_row(line: str) -> list[str]:
    inner = line.strip()
    if inner.startswith("|"):
        inner = inner[1:]
    if inner.endswith("|") and not inner.endswith("\\|"):
        inner = inner[:-1]
    cells: list[str] = []
    cur: list[str] = []
    i = 0
    while i < len(inner):
        ch = inner[i]
        if ch == "\\" and i + 1 < len(inner):
            cur.append(inner[i + 1])
            i += 2
            continue
        if ch == "|":
            cells.append("".join(cur).strip())
            cur = []
        else:
            cur.append(ch)
        i += 1
    cells.append("".join(cur).strip())
    return cells


def summarize_pipe_tables(markdown: str, large_rows: int) -> str:
    """Apply the large-table rule to Markdown pipe tables produced by a third-party adapter."""
    lines = markdown.split("\n")
    out: list[str] = []
    i = 0
    while i < len(lines):
        if lines[i].lstrip().startswith("|"):
            j = i
            while j < len(lines) and lines[j].lstrip().startswith("|"):
                j += 1
            block = lines[i:j]
            if len(block) > 2 and len(block) - 2 > large_rows and _SEPARATOR.match(block[1].strip()):
                rows = [_split_row(block[0])] + [_split_row(b) for b in block[2:]]
                out.append(render_table(rows, large_rows))
            else:
                out.extend(block)
            i = j
        else:
            out.append(lines[i])
            i += 1
    return "\n".join(out)
