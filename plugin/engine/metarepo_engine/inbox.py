"""``inbox scan``: inventory of ``00_inbox/`` (007/R2). Read-only.

Every top-level entry becomes one item. A file carries hash, source id, media type, size and the flags
``duplicate_of``, ``bundle``, ``cloud_native``, ``not_local``; a folder is an uncompressed bundle (007/R5a)
and is only counted here, never hashed (``unbundle`` validates and expands it). Junk is listed apart.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from . import registry
from .bundles import archive_kind, is_junk_path
from .fsops import nfc
from .jsonout import EXIT_PRECONDITION, EngineError
from .hashing import HashCache, source_id
from .mediatype import detect
from .readable import CLOUD_NATIVE, NOT_LOCAL, classify
from .root import Project

INBOX = "00_inbox"


def _folder_stats(folder: Path) -> dict[str, Any]:
    files = junk = size = links = 0
    for dirpath, dirs, names in os.walk(folder, followlinks=False):
        links += sum(1 for d in dirs if (Path(dirpath) / d).is_symlink())
        for n in names:
            full = Path(dirpath) / n
            rel = full.relative_to(folder).parts
            if is_junk_path(rel):
                junk += 1
                continue
            files += 1
            try:
                size += full.lstat().st_size
            except OSError:
                pass
    return {"file_count": files, "junk_count": junk, "size": size, "symlinks": links}


def scan(project: Project, *, cache: HashCache | None = None) -> dict[str, Any]:
    root = project.root
    inbox = root / INBOX
    view = registry.build(root)
    known = view.known_ids()
    by_hash = {r["sha256"]: r for r in view.rows if r.get("sha256")}
    cache = cache if cache is not None else HashCache(project.project_id)
    items: list[dict[str, Any]] = []
    junk: list[str] = []
    seen_hash: dict[str, str] = {}
    if inbox.is_dir():
        for entry in sorted(inbox.iterdir(), key=lambda p: nfc(p.name)):
            rel = f"{INBOX}/{nfc(entry.name)}"
            if is_junk_path([entry.name]):
                junk.append(rel)
                continue
            if entry.is_symlink():
                items.append({"path": rel, "name": nfc(entry.name), "kind": "symlink", "symlink": True,
                              "bundle": False, "cloud_native": False, "not_local": False})
                continue
            if entry.is_dir():
                stats = _folder_stats(entry)
                items.append({"path": rel, "name": nfc(entry.name), "kind": "folder", "bundle": True,
                              "bundle_kind": "folder", "cloud_native": False, "not_local": False,
                              "only_junk": stats["file_count"] == 0, **stats})
                continue
            verdict = classify(entry)
            item: dict[str, Any] = {"path": rel, "name": nfc(entry.name), "kind": "file",
                                    "size": entry.stat().st_size, "cloud_native": verdict.status == CLOUD_NATIVE,
                                    "not_local": verdict.status == NOT_LOCAL, "bundle": False, "duplicate_of": None,
                                    "sha256": None, "id": None}
            if verdict.status in (CLOUD_NATIVE, NOT_LOCAL):
                item["note"] = verdict.reason
                item["media_type"] = detect(entry).media_type if verdict.status == CLOUD_NATIVE else None
                items.append(item)
                continue
            digest = cache.sha256(entry)
            media = detect(entry)
            kind = archive_kind(entry)
            item.update(sha256=digest, id=source_id(digest, known), media_type=media.media_type,
                        bundle=kind is not None, bundle_kind=kind)
            if digest in by_hash:
                item["duplicate_of"] = by_hash[digest]["id"]
                item["duplicate_path"] = by_hash[digest]["path"]
                item["same_name"] = by_hash[digest].get("original_name") == entry.name
            elif digest in seen_hash:
                item["duplicate_in_inbox_of"] = seen_hash[digest]
            seen_hash.setdefault(digest, rel)
            items.append(item)
    cache.save()
    summary = {
        "items": len(items), "junk": len(junk),
        "duplicates": sum(1 for i in items if i.get("duplicate_of") or i.get("duplicate_in_inbox_of")),
        "bundles": sum(1 for i in items if i.get("bundle")),
        "cloud_native": sum(1 for i in items if i.get("cloud_native")),
        "not_local": sum(1 for i in items if i.get("not_local")),
    }
    return {"inbox": INBOX, "items": items, "junk": junk, "summary": summary}


def require_outside_tree(root: Path, path: Path, what: str = "staging directory") -> Path:
    """Staging lives outside the synced tree (007/ADR-002, 002/R6a)."""
    resolved = Path(path).expanduser().resolve()
    try:
        resolved.relative_to(Path(root).resolve())
    except ValueError:
        return resolved
    raise EngineError("staging_inside_tree", f"the {what} must be outside the metarepository (it would sync to "
                      "every member); use a local temp directory", EXIT_PRECONDITION)
