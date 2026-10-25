#!/usr/bin/env python3
"""PreToolUse hook (001/R9): deny direct file-writing tool calls into engine-owned folders.

Protected folders inside a metarepository: ``10_sources/``, ``20_extracts/``, ``_system/``.
Outside a metarepository the hook does nothing. Stdlib only; fails open on any problem, because
correctness never depends on hooks (P2): the engine enforces its own rules.

Input: the hook JSON on stdin (``tool_name``, ``tool_input.file_path``, ``cwd``).
Output: a ``permissionDecision: deny`` JSON object on stdout (exit 0), or nothing.
"""
from __future__ import annotations

import json
import os
import re
import sys
from typing import Callable

_HINTS = {
    "10_sources": (
        "Originals in 10_sources/ are immutable and only the engine places them. Drop files in 00_inbox/ and run "
        "/intake (engine command `place`); a correction is a new source that supersedes the old one."
    ),
    "20_extracts": (
        "Extractions in 20_extracts/ are written only by the engine. Use the engine commands `extract` or "
        "`extract-record` (via the extract workflow) instead of editing files directly."
    ),
    "_system": (
        "_system/ holds shared project state (gates, leases, event log) and is written only by the engine. "
        "Use the matching engine command (`lease`, `wiki gate`, `remove`, `migrate`)."
    ),
}
PROTECTED = frozenset(_HINTS)
_PATH_KEYS = ("file_path", "notebook_path", "path")
_SPLIT = re.compile(r"[\\/]+")


def split_parts(path: str) -> list[str]:
    """Split a POSIX or Windows path into its non-empty segments (drive letter stays a segment)."""
    return [p for p in _SPLIT.split(path) if p]


def is_metarepo_root(path: str) -> bool:
    return os.path.isfile(os.path.join(path, "project.yaml")) and os.path.isfile(
        os.path.join(path, "_system", "state.json")
    )


def real_target(path: str) -> str:
    """Absolute, symlink-resolved path even when the file (or its folders) do not exist yet."""
    absolute = os.path.abspath(path)
    tail: list[str] = []
    probe = absolute
    while not os.path.exists(probe):
        parent, name = os.path.split(probe)
        if parent == probe:
            break
        tail.append(name)
        probe = parent
    resolved = os.path.realpath(probe)
    return os.path.join(resolved, *reversed(tail)) if tail else resolved


def find_root(target: str) -> str | None:
    """Nearest ancestor of ``target`` that is a metarepository root, or None."""
    current = os.path.dirname(target)
    while True:
        if is_metarepo_root(current):
            return current
        parent = os.path.dirname(current)
        if parent == current:
            return None
        current = parent


def _target_path(payload: dict) -> str | None:
    tool_input = payload.get("tool_input")
    if not isinstance(tool_input, dict):
        return None
    for key in _PATH_KEYS:
        value = tool_input.get(key)
        if isinstance(value, str) and value:
            if not (os.path.isabs(value) or re.match(r"^[A-Za-z]:[\\/]", value) or value.startswith("\\\\")):
                cwd = payload.get("cwd")
                if isinstance(cwd, str) and cwd:
                    value = cwd.rstrip("\\/") + "/" + value
            return value
    return None


def decide(
    payload: dict,
    resolve: Callable[[str], str] = real_target,
    root_of: Callable[[str], str | None] = find_root,
) -> str | None:
    """Return a denial reason, or None to stay out of the way."""
    target = _target_path(payload)
    if target is None:
        return None
    target = resolve(target)
    root = root_of(target)
    if root is None:
        return None
    root_parts = split_parts(root)
    target_parts = split_parts(target)
    if len(target_parts) <= len(root_parts):
        return None
    if [p.casefold() for p in target_parts[: len(root_parts)]] != [p.casefold() for p in root_parts]:
        return None
    first = target_parts[len(root_parts)].casefold()
    if first in PROTECTED:
        return f"Blocked: {'/'.join(target_parts[len(root_parts):])} is inside {first}/. {_HINTS[first]}"
    return None


def main() -> int:
    try:
        payload = json.loads(sys.stdin.read() or "{}")
        reason = decide(payload) if isinstance(payload, dict) else None
    except Exception:  # noqa: BLE001 - fail open
        return 0
    if reason:
        sys.stdout.write(
            json.dumps(
                {
                    "hookSpecificOutput": {
                        "hookEventName": "PreToolUse",
                        "permissionDecision": "deny",
                        "permissionDecisionReason": reason,
                    }
                }
            )
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
