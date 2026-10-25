"""Conflict-copy detection and recovery data (004/R10, R11).

Detection never changes anything. Recovery is the caller's job (``/doctor``): show both versions, propose
a merge for wiki pages, and remove the losing copy only through ``remove --confirm`` (P6, 002/R6). The
engine never merges on its own.
"""
from __future__ import annotations

import difflib
import os
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from .events import read_events
from .fsops import TEMP_MARKER, fs_path, nfc
from .readable import classify

SKIP_DIRS = {".git", ".hg", ".svn", "__pycache__", "node_modules"}
DIFF_MAX_BYTES = 1024 * 1024
KINDS = ("pattern", "heuristic")


@dataclass
class Conflict:
    path: str  # root-relative POSIX path of the suspected copy
    original: str | None  # root-relative path of the file it copies, when known
    kind: str  # "pattern" | "heuristic"
    rule: str  # pattern id or "same-stem"
    provider: str | None = None
    confidence: str = "likely"  # "likely" (pattern) | "possible" (heuristic only)

    def as_dict(self) -> dict[str, Any]:
        return {"path": self.path, "original": self.original, "kind": self.kind, "rule": self.rule,
                "provider": self.provider, "confidence": self.confidence}


@dataclass
class _Rule:
    id: str
    provider: str
    regex: re.Pattern[str]
    requires: tuple[str, ...]


def load_providers(path: Path | None = None) -> dict[str, Any]:
    from .config import yaml_module
    from .templates import templates_dir

    yaml = yaml_module()
    file = path or templates_dir() / "sync-providers.yaml"
    return yaml.safe_load(Path(file).read_text(encoding="utf-8"))


def _rules(spec: dict[str, Any], providers: Iterable[str] | None) -> list[_Rule]:
    table = spec.get("providers") or {}
    names = ["common"] + [n for n in (providers if providers is not None else table) if n != "common"]
    rules: list[_Rule] = []
    for name in names:
        for pat in (table.get(name) or {}).get("patterns") or []:
            rules.append(_Rule(pat["id"], name, re.compile(pat["regex"]), tuple(pat.get("requires") or ())))
    return rules


def known_hosts(root: Path) -> set[str]:
    import platform

    hosts = {platform.node().lower()} if platform.node() else set()
    try:
        for ev in read_events(root):
            if ev.get("host"):
                hosts.add(str(ev["host"]).lower())
    except (OSError, ValueError):
        pass
    return hosts


def _iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _walk(root: Path) -> Iterable[tuple[str, list[str]]]:
    for dirpath, dirs, files in os.walk(root):
        dirs[:] = sorted(d for d in dirs if d not in SKIP_DIRS)
        yield dirpath, sorted(f for f in files if TEMP_MARKER not in f)


def detect(root: Path, *, providers: Iterable[str] | None = None, spec: dict[str, Any] | None = None,
           hosts: set[str] | None = None, heuristic: bool = True) -> list[Conflict]:
    """Conflict copies under ``root``. ``providers``: names from sync-providers.yaml (default: all of them;
    pass ``[project sync.provider]`` to narrow). Sorted by path."""
    root = Path(root)
    spec = spec or load_providers()
    rules = _rules(spec, providers)
    hosts = hosts if hosts is not None else known_hosts(root)
    window = float((spec.get("heuristic") or {}).get("window_seconds", 3600))
    strips = [re.compile(s, re.IGNORECASE) for s in (spec.get("heuristic") or {}).get("strip") or []]
    found: dict[str, Conflict] = {}

    for dirpath, files in _walk(root):
        here = Path(dirpath)
        names = {nfc(f): f for f in files}
        rel_dir = here.relative_to(root).as_posix()

        def rel(name: str) -> str:
            return name if rel_dir == "." else f"{rel_dir}/{name}"

        for name in sorted(names):
            for rule in rules:
                m = rule.regex.match(name)
                if not m:
                    continue
                groups = m.groupdict()
                original_name = f"{groups['stem']}{groups.get('ext') or ''}"
                has_original = original_name in names and original_name != name
                if "original" in rule.requires and not has_original:
                    continue
                if "known_host" in rule.requires and (groups.get("host") or "").lower() not in hosts:
                    continue
                found[rel(name)] = Conflict(rel(name), rel(original_name) if has_original else None, "pattern",
                                            rule.id, rule.provider, "likely")
                break

        if not heuristic:
            continue
        groups_by_key: dict[tuple[str, str], list[str]] = {}
        for name in names:
            if rel(name) in found:
                continue
            stem, ext = os.path.splitext(name)
            for h in hosts:  # drop a trailing "-<known host>" before normalising
                if h and stem.lower().endswith("-" + h):
                    stem = stem[: -(len(h) + 1)]
            for _ in range(2):
                for rx in strips:
                    stem = rx.sub("", stem)
            groups_by_key.setdefault((stem.lower(), ext.lower()), []).append(name)
        for members in groups_by_key.values():
            if len(members) < 2:
                continue
            _flag_group(here, members, rel, window, found)
    return sorted(found.values(), key=lambda c: c.path)


def _flag_group(here: Path, members: list[str], rel, window: float, found: dict[str, Conflict]) -> None:
    from .fsops import sha256_file

    info = []
    for name in members:
        p = here / name
        if not classify(p).usable:
            continue
        st = os.stat(fs_path(p))
        info.append((name, st.st_mtime, st.st_size, p))
    if len(info) < 2:
        return
    info.sort(key=lambda t: (t[1], t[0]))
    original = info[0]
    for name, mtime, size, p in info[1:]:
        if abs(mtime - original[1]) > window:
            continue
        if size == original[2] and sha256_file(p) == sha256_file(original[3]):
            continue  # identical content: a duplicate, not a conflict
        found[rel(name)] = Conflict(rel(name), rel(original[0]), "heuristic", "same-stem", None, "possible")


def _version(root: Path, rel_path: str | None, with_hash: bool) -> dict[str, Any] | None:
    if rel_path is None:
        return None
    p = Path(root) / rel_path
    if not p.is_file():
        return {"path": rel_path, "exists": False}
    st = os.stat(fs_path(p))
    verdict = classify(p)
    out: dict[str, Any] = {"path": rel_path, "exists": True, "size": st.st_size, "modified": _iso(st.st_mtime),
                           "readability": verdict.status}
    if with_hash and verdict.usable:
        from .fsops import sha256_file

        out["sha256"] = sha256_file(p)
    return out


def describe(root: Path, conflict: Conflict, *, with_hash: bool = True) -> dict[str, Any]:
    """Both versions' metadata and, for wiki pages and other small text files, a unified diff (R11)."""
    root = Path(root)
    copy = _version(root, conflict.path, with_hash)
    orig = _version(root, conflict.original, with_hash)
    is_wiki_page = conflict.path.startswith("30_wiki/") and conflict.path.endswith(".md")
    out: dict[str, Any] = {**conflict.as_dict(), "copy": copy, "original_version": orig,
                           "proposal": "merge" if is_wiki_page else "choose-one",
                           "identical": bool(copy and orig and copy.get("sha256") and copy.get("sha256") == orig.get("sha256")),
                           "diff": None}
    if orig and orig.get("exists") and copy and copy.get("exists"):
        out["diff"] = text_diff(root / conflict.original, root / conflict.path)  # type: ignore[arg-type]
    return out


def text_diff(original: Path, copy: Path) -> str | None:
    """Unified diff of two small UTF-8 files, or None when either is binary or too large."""
    try:
        if os.stat(fs_path(original)).st_size > DIFF_MAX_BYTES or os.stat(fs_path(copy)).st_size > DIFF_MAX_BYTES:
            return None
        a = Path(original).read_text(encoding="utf-8").splitlines(keepends=True)
        b = Path(copy).read_text(encoding="utf-8").splitlines(keepends=True)
    except (OSError, UnicodeDecodeError):
        return None
    return "".join(difflib.unified_diff(a, b, fromfile=Path(original).name, tofile=Path(copy).name))
