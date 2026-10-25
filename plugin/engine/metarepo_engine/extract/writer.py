"""Extraction file writer: frontmatter, body, part splitting, preserve-on-force (008/R7, R10).

Layout: ``20_extracts/<path under 10_sources>.md``; continuation parts of an oversized body are
``<same>.part-002.md`` ... carrying no frontmatter; the first file records ``parts: N``.
"""
from __future__ import annotations

import datetime as _dt
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .. import frontmatter
from ..events import now_iso
from ..fsops import atomic_write_bytes, remove_file

SOURCES = "10_sources"
EXTRACTS = "20_extracts"
# Fields kept across regeneration (R7). Everything not produced by the engine is also kept.
PRESERVED = ("triage", "supersedes", "bundle", "aliases", "original_name")
ORDER = ("type", "schema_version", "source_id", "source_path", "source_sha256", "source_size", "media_type",
         "original_name", "aliases", "title", "language", "extracted_at", "extractor", "quality", "quality_notes",
         "structure", "parts", "triage", "supersedes", "bundle")


def extract_rel(source_rel: str) -> str:
    if not source_rel.startswith(SOURCES + "/"):
        raise ValueError(f"not a source path: {source_rel}")
    return f"{EXTRACTS}/{source_rel[len(SOURCES) + 1:]}.md"


def part_rel(main_rel: str, n: int) -> str:
    return main_rel[:-3] + f".part-{n:03d}.md"


def jsonable(value: Any) -> Any:
    """YAML loads unquoted timestamps as datetime objects; turn them back into ISO strings."""
    if isinstance(value, dict):
        return {str(k): jsonable(v) for k, v in value.items()}
    if isinstance(value, list):
        return [jsonable(v) for v in value]
    if isinstance(value, _dt.datetime):
        if value.tzinfo is None:
            return value.strftime("%Y-%m-%dT%H:%M:%SZ")
        return value.astimezone(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    if isinstance(value, _dt.date):
        return value.isoformat()
    return value


def read_front(root: Path, main_rel: str) -> dict[str, Any] | None:
    path = root / main_rel
    if not path.is_file():
        return None
    return jsonable(frontmatter.read_head(path))


def is_extracted(front: dict[str, Any] | None) -> bool:
    """A placement stub has identity and triage but no ``extractor``/``extracted_at`` yet."""
    return bool(front) and isinstance(front.get("extractor"), dict) and bool(front.get("extracted_at"))


def build_front(existing: dict[str, Any] | None, *, source_id: str, source_rel: str, sha256: str, size: int,
                media_type: str, original_name: str, extractor: dict[str, Any], quality: str, notes: str,
                title: str | None, language: str | None, structure: dict[str, Any], parts: int) -> dict[str, Any]:
    old = existing or {}
    new: dict[str, Any] = {
        "type": "Extract", "schema_version": 1, "source_id": old.get("source_id") or source_id,
        "source_path": source_rel, "source_sha256": sha256, "source_size": size, "media_type": media_type,
        "original_name": old.get("original_name") or original_name,
        "aliases": old.get("aliases") or [],
        "title": title or old.get("title"),
        "language": language or old.get("language"),
        "extracted_at": now_iso(), "extractor": extractor, "quality": quality, "quality_notes": notes,
        "structure": structure or {},
    }
    if parts > 1:
        new["parts"] = parts
    new["triage"] = old.get("triage")
    new["supersedes"] = old.get("supersedes") or []
    new["bundle"] = old.get("bundle") or {"parent_id": None, "group": None, "member_path": None}
    for key, value in old.items():  # unknown keys written by other specs survive
        if key not in new and key not in ORDER:
            new[key] = value
    return new


def split_parts(body: str, max_bytes: int) -> list[str]:
    """Split on line boundaries so no part exceeds ``max_bytes`` UTF-8 bytes (a longer line is cut)."""
    if len(body.encode("utf-8")) <= max_bytes:
        return [body]
    parts: list[str] = []
    cur: list[str] = []
    size = 0
    for line in body.splitlines(keepends=True):
        chunks = [line]
        if len(line.encode("utf-8")) > max_bytes:
            step = max(max_bytes // 4, 1)  # worst case 4 bytes per character
            chunks = [line[i:i + step] for i in range(0, len(line), step)]
        for chunk in chunks:
            n = len(chunk.encode("utf-8"))
            if cur and size + n > max_bytes:
                parts.append("".join(cur))
                cur, size = [], 0
            cur.append(chunk)
            size += n
    if cur:
        parts.append("".join(cur))
    return parts


@dataclass
class WriteOutcome:
    written: list[str] = field(default_factory=list)
    removed: list[str] = field(default_factory=list)
    parts: int = 1


def write_extraction(root: Path, source_rel: str, front: dict[str, Any], body: str, max_bytes: int) -> WriteOutcome:
    main_rel = extract_rel(source_rel)
    pieces = split_parts(body, max_bytes)
    if len(pieces) > 1:
        front = dict(front)
        front["parts"] = len(pieces)
        front = {k: front[k] for k in [*ORDER, *[k for k in front if k not in ORDER]] if k in front}
    elif "parts" in front:
        front = {k: v for k, v in front.items() if k != "parts"}
    out = WriteOutcome(parts=len(pieces))
    for n in range(2, len(pieces) + 1):  # continuation parts first, the main file last
        rel = part_rel(main_rel, n)
        atomic_write_bytes(root / rel, pieces[n - 1].encode("utf-8"))
        out.written.append(rel)
    doc = frontmatter.Document(front=front, body=pieces[0], has_front=True)
    frontmatter.write(root / main_rel, doc)
    out.written.insert(0, main_rel)
    n = len(pieces) + 1
    while (root / part_rel(main_rel, n)).is_file():  # parts left over from a longer earlier extraction
        remove_file(root / part_rel(main_rel, n))
        out.removed.append(part_rel(main_rel, n))
        n += 1
    return out
