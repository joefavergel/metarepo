"""Append-only audit log ``_system/events.log`` (003/R13).

One JSON object per line, written with a single ``os.write`` on an ``O_APPEND`` descriptor so that
concurrent writers never interleave inside a line.
"""
from __future__ import annotations

import getpass
import json
import os
import platform
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .fsops import fs_path

EVENTS_REL = "_system/events.log"


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def resolve_actor(explicit: str | None) -> str:
    """--actor, then METAREPO_ACTOR, then the OS user name (per-user config arrives with 006)."""
    if explicit:
        return explicit
    env = os.environ.get("METAREPO_ACTOR")
    if env:
        return env
    try:
        return getpass.getuser()
    except Exception:  # noqa: BLE001 - getpass can fail in odd sandboxes
        return "unknown"


def append_event(
    root: Path,
    *,
    command: str,
    targets: list[str],
    result: str,
    actor: str,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    record: dict[str, Any] = {
        "ts": now_iso(),
        "actor": actor,
        "host": platform.node() or "unknown",
        "command": command,
        "targets": list(targets),
        "result": result,
    }
    if extra:
        record.update(extra)
    line = (json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8")
    path = Path(root) / EVENTS_REL
    path.parent.mkdir(parents=True, exist_ok=True)
    flags = os.O_WRONLY | os.O_APPEND | os.O_CREAT | getattr(os, "O_BINARY", 0)
    fd = os.open(fs_path(path), flags, 0o644)
    try:
        os.write(fd, line)  # single write per line
    finally:
        os.close(fd)
    return record


def read_events(root: Path) -> list[dict[str, Any]]:
    path = Path(root) / EVENTS_REL
    if not path.exists():
        return []
    out = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        if raw.strip():
            out.append(json.loads(raw))
    return out
