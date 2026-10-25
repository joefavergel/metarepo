"""Source pages (010/R3, R4, R8): the wiki-side record of a fed source, and the derived ``wiki_status``.

A Source page lives in ``30_wiki/sources/<source-id>.md`` and records ``source_id``, ``content_sha256`` (the
original's hash when it was fed) and ``extracted_at`` (the extraction it was written from). Comparing those
with the extraction's frontmatter yields the status of a source:

* ``pending``  triaged ``wiki`` and no source page yet;
* ``ingested`` a source page exists and still matches the extraction;
* ``stale``    the page's ``content_sha256`` differs from the extraction's ``source_sha256``, or the extraction
               was regenerated since (``extracted_at`` differs);
* ``None``     not triaged ``wiki`` and never fed.

``wiki_status`` is registered as a registry column through ``registry.EXTRA_COLUMNS``. Reads never write.
"""
from __future__ import annotations

import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .. import frontmatter
from ..okf import OkfPage
from .bundle import WIKI_DIR

SOURCES_DIR = "sources"
SOURCE_TYPE = "Source"
PENDING, INGESTED, STALE = "pending", "ingested", "stale"

_INDEX_CACHE: dict[tuple, dict[str, tuple[str, dict[str, Any]]]] = {}


def page_rel(source_id: str) -> str:
    """Root-relative POSIX path where the source page of ``source_id`` is created."""
    return f"{WIKI_DIR}/{SOURCES_DIR}/{source_id}.md"


def source_pages(root: Path) -> dict[str, tuple[str, dict[str, Any]]]:
    """``source_id -> (root-relative path, frontmatter)`` for every Source page. Cached per folder signature."""
    base = Path(root) / WIKI_DIR / SOURCES_DIR
    if not base.is_dir():
        return {}
    files = sorted(p for p in base.glob("*.md") if p.is_file())
    signature = (str(root), tuple((p.name, p.stat().st_mtime_ns, p.stat().st_size) for p in files))
    cached = _INDEX_CACHE.get(signature)
    if cached is not None:
        return cached
    found: dict[str, tuple[str, dict[str, Any]]] = {}
    for p in files:
        try:
            front = frontmatter.read_head(p)
        except (frontmatter.FrontmatterError, OSError):
            continue
        sid = front.get("source_id")
        if sid and front.get("type") == SOURCE_TYPE:
            found.setdefault(str(sid), (p.relative_to(root).as_posix(), front))
    if len(_INDEX_CACHE) > 8:
        _INDEX_CACHE.clear()
    _INDEX_CACHE[signature] = found
    return found


def _stamp(value: Any) -> str:
    """Timestamps compare as text; YAML may have parsed an unquoted one into a datetime."""
    if isinstance(value, datetime):
        value = value if value.tzinfo else value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    return str(value).strip().replace(" ", "T").replace("+00:00", "Z")


def status_of(page_front: dict[str, Any] | None, row: dict[str, Any]) -> str | None:
    """Status for one registry ``row`` given the frontmatter of its source page (or None when there is none)."""
    if page_front is None:
        return PENDING if row.get("triage") == "wiki" else None
    if str(page_front.get("content_sha256") or "") != str(row.get("sha256") or ""):
        return STALE
    recorded = page_front.get("extracted_at")
    if recorded is not None and row.get("extracted_at") is not None and _stamp(recorded) != _stamp(row["extracted_at"]):
        return STALE
    return INGESTED


def wiki_status(root: Path, row: dict[str, Any]) -> str | None:
    """Registry column function (``registry.EXTRA_COLUMNS['wiki_status']``)."""
    entry = source_pages(root).get(str(row.get("id")))
    return status_of(entry[1] if entry else None, row)


def _iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m-%d")


def _rel_from_page(root: Path, target_rel: str, sid: str) -> str:
    """Relative link from ``30_wiki/sources/<id>.md`` to a root-relative path."""
    return os.path.relpath(Path(root) / target_rel, Path(root) / WIKI_DIR / SOURCES_DIR).replace(os.sep, "/")


def skeleton(root: Path, row: dict[str, Any], *, actor: str, now: str, assisted_by: str | None = None) -> dict[str, Any]:
    """Pre-filled frontmatter for the Source page of registry ``row`` (010/R8, R13). ``description``,
    ``tags`` and the body are completed by the agent."""
    sid = str(row["id"])
    original = Path(root) / str(row["path"])
    try:
        last_modified = _iso(original.stat().st_mtime)
    except OSError:
        last_modified = str(row.get("extracted_at") or now)[:10]
    resource = _rel_from_page(root, str(row["path"]), sid)
    by = actor if actor.startswith("human:") else f"human:{actor}"
    generated: dict[str, Any] = {"by": by, "at": now}
    if assisted_by:
        generated["assisted_by"] = assisted_by
    title = row.get("title") or row.get("original_name") or sid
    return {
        "type": SOURCE_TYPE,
        "title": str(title),
        "description": "",
        "source_id": sid,
        "resource": resource,
        "extract": _rel_from_page(root, str(row["extract"]), sid),
        "content_sha256": row.get("sha256"),
        "extracted_at": row.get("extracted_at"),
        "last_modified": last_modified,
        "tags": [],
        "generated": generated,
        "sources": [{"id": sid, "title": str(title), "path": resource}],
    }


def render(front: dict[str, Any]) -> str:
    """Markdown text of a skeleton page: frontmatter plus a title heading (the agent writes the body)."""
    doc = frontmatter.Document(front=front, body=f"# {front['title']}\n", has_front=True)
    return doc.dump()


def read_page(path: Path) -> OkfPage:
    return OkfPage.load(path)
