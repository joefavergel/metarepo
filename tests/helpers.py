"""Test helpers: a fixture metarepository builder (kept independent of the engine's own scaffolding)."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import yaml

DEFAULT_ID = "7f3c1b2a-5d4e-4f6a-9b8c-0123456789ab"


def base_config(requires: str = ">=0.1,<0.2", **overrides: Any) -> dict[str, Any]:
    cfg: dict[str, Any] = {
        "schema_version": 1,
        "id": DEFAULT_ID,
        "name": "Example Project",
        "slug": "example-project",
        "objective": "Example objective.",
        "scope": {"in": ["topic-a"], "out": []},
        "language": {"content": "en"},
        "toolkit": {"requires": requires, "created_with": "0.1.0"},
    }
    cfg.update(overrides)
    return cfg


def make_project(root: Path, *, requires: str = ">=0.1,<0.2", yaml_text: str | None = None,
                 **overrides: Any) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    text = yaml_text if yaml_text is not None else yaml.safe_dump(base_config(requires, **overrides), sort_keys=False)
    (root / "project.yaml").write_text(text, encoding="utf-8")
    (root / "_system").mkdir(exist_ok=True)
    state = {
        "schema_version": 1,
        "metarepo": {"configured": True, "created_at": "2026-01-01T00:00:00Z", "created_with": "0.1.0"},
        "wiki": {"configured": False, "configured_at": None, "bundle": "30_wiki"},
        "toolkit": {"created_with": "0.1.0", "last_written_with": "0.1.0", "migrations": []},
        "last_doctor": None,
        "last_lint": None,
    }
    (root / "_system" / "state.json").write_text(json.dumps(state, indent=2) + "\n", encoding="utf-8")
    for folder in ("00_inbox", "10_sources", "20_extracts", "30_wiki", "outputs", "shared", "_system/locks"):
        (root / folder).mkdir(parents=True, exist_ok=True)
    return root


def tree_snapshot(root: Path) -> dict[str, bytes]:
    return {p.relative_to(root).as_posix(): p.read_bytes() for p in sorted(root.rglob("*")) if p.is_file()}
