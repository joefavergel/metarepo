"""Golden-file helper for the public JSON contract (003/ADR-001).

Set ``UPDATE_GOLDEN=1`` to (re)write the expected files; review the diff before committing it, because a
change to an envelope is a MAJOR version change.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

GOLDEN_DIR = Path(__file__).resolve().parent / "golden"


def normalize(value: Any, replacements: dict[str, str] | None = None) -> Any:
    """Replace machine-specific strings (paths, versions) so golden files are portable."""
    replacements = replacements or {}
    if isinstance(value, str):
        for old, new in sorted(replacements.items(), key=lambda kv: -len(kv[0])):
            value = value.replace(old, new)
        return value
    if isinstance(value, list):
        return [normalize(v, replacements) for v in value]
    if isinstance(value, dict):
        return {k: normalize(v, replacements) for k, v in value.items()}
    return value


def assert_golden(name: str, actual: Any, replacements: dict[str, str] | None = None) -> None:
    actual = normalize(actual, replacements)
    path = GOLDEN_DIR / name
    rendered = json.dumps(actual, indent=2, ensure_ascii=False, sort_keys=False) + "\n"
    if os.environ.get("UPDATE_GOLDEN") == "1":
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(rendered, encoding="utf-8", newline="\n")
        return
    assert path.is_file(), f"missing golden file {path}; run with UPDATE_GOLDEN=1 once and review it"
    expected = json.loads(path.read_text(encoding="utf-8"))
    assert actual == expected, f"output differs from golden/{name}; if intended, regenerate with UPDATE_GOLDEN=1"
