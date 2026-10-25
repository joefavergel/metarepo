"""Derived registry view over extraction frontmatter (002/ADR-004; 003 T2.1).

The authoritative record of a source is the frontmatter of its extraction (008 section 2). The registry is
computed from those files, never edited by hand, and may be cached in the local cache. Wiki-side fields
(``wiki_status``) arrive with spec 010 through ``EXTRA_COLUMNS``.
"""
from __future__ import annotations

import csv
import io
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable

from . import frontmatter
from .fsops import atomic_write_text, local_cache_dir

EXTRACTS_DIR = "20_extracts"
_PART_RE = re.compile(r"\.part-\d{3}\.md$")

COLUMNS = ("id", "path", "sha256", "size", "media_type", "original_name", "title", "language", "quality",
           "triage", "parent_id", "extractor", "extracted_at", "extract")

# 010 registers computed columns here: name -> function(project root, row) -> value.
EXTRA_COLUMNS: dict[str, Callable[[Path, dict[str, Any]], Any]] = {}


@dataclass
class RegistryView:
    rows: list[dict[str, Any]] = field(default_factory=list)
    warnings: list[tuple[str, str]] = field(default_factory=list)  # (extraction path, message)

    def known_ids(self) -> dict[str, str]:
        return {r["id"]: r["sha256"] for r in self.rows if r.get("id") and r.get("sha256")}

    def duplicates(self) -> list[list[str]]:
        by_hash: dict[str, list[str]] = {}
        for r in self.rows:
            if r.get("sha256"):
                by_hash.setdefault(r["sha256"], []).append(r["path"])
        return [sorted(paths) for paths in by_hash.values() if len(paths) > 1]


def _row(root: Path, md: Path, front: dict[str, Any]) -> dict[str, Any]:
    triage = front.get("triage") if isinstance(front.get("triage"), dict) else {}
    bundle = front.get("bundle") if isinstance(front.get("bundle"), dict) else {}
    extractor = front.get("extractor") if isinstance(front.get("extractor"), dict) else {}
    row = {
        "id": front.get("source_id"),
        "path": front.get("source_path"),
        "sha256": front.get("source_sha256"),
        "size": front.get("source_size"),
        "media_type": front.get("media_type"),
        "original_name": front.get("original_name"),
        "title": front.get("title"),
        "language": front.get("language"),
        "quality": front.get("quality"),
        "triage": triage.get("decision"),
        "parent_id": bundle.get("parent_id"),
        "extractor": extractor.get("name"),
        "extracted_at": str(front["extracted_at"]) if front.get("extracted_at") is not None else None,
        "extract": md.relative_to(root).as_posix(),
    }
    for name, fn in EXTRA_COLUMNS.items():
        row[name] = fn(root, row)
    return row


def build(root: Path) -> RegistryView:
    """Scan ``20_extracts/`` and return one row per extraction, sorted by id then path."""
    view = RegistryView()
    base = Path(root) / EXTRACTS_DIR
    if not base.is_dir():
        return view
    for md in sorted(base.rglob("*.md")):
        rel = md.relative_to(root).as_posix()
        if _PART_RE.search(md.name):
            continue  # continuation parts carry no frontmatter (008/R10)
        try:
            front = frontmatter.read_head(md)
        except (frontmatter.FrontmatterError, OSError) as exc:
            view.warnings.append((rel, f"unreadable extraction: {exc}"))
            continue
        if front.get("type") != "Extract":
            view.warnings.append((rel, "not an extraction (frontmatter 'type' is not 'Extract')"))
            continue
        missing = [k for k in ("source_id", "source_path", "source_sha256") if not front.get(k)]
        if missing:
            view.warnings.append((rel, "extraction lacks " + ", ".join(missing)))
            continue
        view.rows.append(_row(Path(root), md, front))
    view.rows.sort(key=lambda r: (str(r["id"]), r["extract"]))
    return view


class FilterError(ValueError):
    pass


def parse_filters(specs: Iterable[str]) -> dict[str, set[str]]:
    """``key=v1,v2`` clauses (repeatable); keys are ANDed, values of one key are ORed."""
    out: dict[str, set[str]] = {}
    allowed = set(COLUMNS) | set(EXTRA_COLUMNS)
    for spec in specs:
        key, sep, values = spec.partition("=")
        key = key.strip()
        if not sep or not key or not values.strip():
            raise FilterError(f"bad filter {spec!r}: expected key=value[,value]")
        if key not in allowed:
            hint = " (wiki_status arrives with spec 010)" if key == "wiki_status" else ""
            raise FilterError(f"unknown filter key {key!r}{hint}; available: {', '.join(sorted(allowed))}")
        out.setdefault(key, set()).update(v.strip() for v in values.split(",") if v.strip())
    return out


def apply_filters(rows: list[dict[str, Any]], filters: dict[str, set[str]]) -> list[dict[str, Any]]:
    def keep(row: dict[str, Any]) -> bool:
        return all(str(row.get(k)) in vals for k, vals in filters.items())

    return [r for r in rows if keep(r)]


def to_csv(rows: list[dict[str, Any]]) -> str:
    columns = list(COLUMNS) + [c for c in EXTRA_COLUMNS if c not in COLUMNS]
    buf = io.StringIO(newline="")
    writer = csv.writer(buf, lineterminator="\n")
    writer.writerow(columns)
    for row in rows:
        writer.writerow(["" if row.get(c) is None else row.get(c) for c in columns])
    return buf.getvalue()


def write_cache(project_id: str, rows: list[dict[str, Any]]) -> list[Path]:
    """Persist the view in the local cache (002/R6a). Disposable: ``registry`` rebuilds it."""
    cache = local_cache_dir(project_id, create=True)
    json_path, csv_path = cache / "registry.json", cache / "registry.csv"
    atomic_write_text(json_path, json.dumps(rows, ensure_ascii=False, indent=2) + "\n")
    atomic_write_text(csv_path, to_csv(rows))
    return [json_path, csv_path]
