"""Streaming SHA-256, a disposable hash cache and content-addressed source ids (002/R12; 003/R10).

Source id: ``src-`` + the first 12 hex characters of the SHA-256 of the file bytes. When the 12-character
id is already taken by a source with a *different* full hash, the newcomer gets a longer prefix
(16, then 20, ...); both full hashes live in the extraction frontmatter, so both are recorded.
"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Iterable, Mapping

from . import fsops
from .fsops import atomic_write_text, fs_path, local_cache_dir
from .readable import ensure_local



def sha256_file(path: Path, chunk: int = 1024 * 1024) -> str:
    """SHA-256 of a local file. Streamed placeholders are refused with ``not_local`` (004/R12)."""
    ensure_local(path)
    return fsops.sha256_file(path, chunk)


SOURCE_ID_RE = re.compile(r"^src-[0-9a-f]{12,64}$")
ID_LENGTHS = (12, 16, 20, 24, 28, 32, 40, 48, 56, 64)


def source_id(full_hash: str, known: Mapping[str, str] | None = None) -> str:
    """Id for ``full_hash``. ``known`` maps existing ids to their full hashes (from the registry)."""
    full_hash = full_hash.lower()
    if not re.fullmatch(r"[0-9a-f]{64}", full_hash):
        raise ValueError("expected a 64-character hex SHA-256")
    known = known or {}
    for length in ID_LENGTHS:
        candidate = f"src-{full_hash[:length]}"
        owner = known.get(candidate)
        if owner is None or owner.lower() == full_hash:
            return candidate
    raise ValueError("no free source id")  # unreachable: 64 characters is the full hash


def group_duplicates(hashes: Mapping[str, str]) -> list[list[str]]:
    """Groups of keys (paths) whose full hash is identical, in input order, groups of two or more only."""
    by_hash: dict[str, list[str]] = {}
    for key, digest in hashes.items():
        by_hash.setdefault(digest, []).append(key)
    return [keys for keys in by_hash.values() if len(keys) > 1]


class HashCache:
    """``(path, size, mtime_ns) -> sha256`` cache stored in the local cache; losing it loses nothing."""

    FILENAME = "hashes.json"

    def __init__(self, project_id: str | None):
        self._path: Path | None = None
        if project_id:
            self._path = local_cache_dir(project_id) / self.FILENAME
        self._data: dict[str, list] = {}
        self._dirty = False
        if self._path is not None and self._path.is_file():
            try:
                loaded = json.loads(self._path.read_text(encoding="utf-8"))
                if isinstance(loaded, dict):
                    self._data = loaded
            except (OSError, ValueError):
                self._data = {}  # corrupt cache: rebuild silently

    def sha256(self, path: Path) -> str:
        path = Path(path)
        ensure_local(path)
        st = os.stat(fs_path(path))
        key = str(path.resolve())
        entry = self._data.get(key)
        if entry and entry[0] == st.st_size and entry[1] == st.st_mtime_ns:
            return entry[2]
        digest = sha256_file(path)
        self._data[key] = [st.st_size, st.st_mtime_ns, digest]
        self._dirty = True
        return digest

    def save(self) -> None:
        if self._path is None or not self._dirty:
            return
        self._path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_text(self._path, json.dumps(self._data, separators=(",", ":")))
        self._dirty = False


def hash_files(paths: Iterable[Path], cache: HashCache | None = None) -> dict[str, str]:
    out: dict[str, str] = {}
    for p in paths:
        out[str(p)] = cache.sha256(p) if cache else sha256_file(Path(p))
    return out
