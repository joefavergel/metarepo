"""Skeleton scaffolding (002 section 2). Idempotent and never overwrites (P12, 003/R8).

This is the building block that ``init`` (spec 005) wraps with the interview answers; tests and the
no-artifact CI rule use it directly. A scaffolded project holds only project files: folders,
``project.yaml``, ``_system/state.json`` and the rendered ``AGENTS.md`` / ``README.md`` / ``code/README.md``.
"""
from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Any

from . import __version__, compat
from .config import dump_yaml, validate_project
from .events import now_iso
from .fsops import write_if_absent
from .templates import has_repos, load_skeleton, render_project_files


def new_project_id() -> str:
    return str(uuid.uuid4())


def new_state(version: str = __version__, now: str | None = None) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "metarepo": {"configured": True, "created_at": now or now_iso(), "created_with": version},
        "wiki": {"configured": False, "configured_at": None, "bundle": "30_wiki"},
        "toolkit": {"created_with": version, "last_written_with": version, "migrations": []},
        "last_doctor": None,
        "last_lint": None,
    }


def fixture_config(version: str = __version__) -> dict[str, Any]:
    """A complete, valid, neutral configuration for tests and CI."""
    return {
        "schema_version": 1,
        "id": "7f3c1b2a-5d4e-4f6a-9b8c-0123456789ab",
        "name": "Example Project",
        "slug": "example-project",
        "description": "One paragraph describing the example project.",
        "objective": "What the example project must achieve.",
        "scope": {"in": ["topic A", "topic B"], "out": ["topic C"]},
        "language": {"content": "en"},
        "toolkit": {"requires": compat.requires_for(version), "created_with": version},
        "sync": {"provider": "none", "notes": ""},
        "repos": [{"name": "app", "url": "https://example.com/org/app.git", "default_branch": "main",
                   "role": "primary"}],
        "team": [{"id": "member-a", "display_name": "Member A", "roles": ["author", "reviewer"]}],
    }


def scaffold(target: Path, config: dict[str, Any], *, version: str = __version__,
             now: str | None = None) -> list[str]:
    """Create the skeleton under ``target``. Returns the root-relative paths created (empty if nothing)."""
    findings = validate_project(config)
    if findings.errors:
        raise ValueError("invalid project configuration: " + "; ".join(f"{p}: {m}" for p, m in findings.errors))
    target = Path(target)
    created: list[str] = []

    def mark(path: Path) -> None:
        created.append(path.relative_to(target).as_posix())

    skeleton = load_skeleton()
    folders = list(skeleton["folders"]) + [
        f for f in skeleton.get("optional_folders", []) if f.get("when") != "repos" or has_repos(config)
    ]
    target.mkdir(parents=True, exist_ok=True)
    for entry in folders:
        folder = target / entry["path"]
        if not folder.exists():
            folder.mkdir(parents=True)
            mark(folder)
        if entry.get("keep") and write_if_absent(folder / ".keep", b""):
            mark(folder / ".keep")
    if write_if_absent(target / "project.yaml", dump_yaml(config)):
        mark(target / "project.yaml")
    state_text = json.dumps(new_state(version, now), indent=2, ensure_ascii=False) + "\n"
    if write_if_absent(target / "_system" / "state.json", state_text):
        mark(target / "_system" / "state.json")

    def read_existing(rel: str) -> str | None:
        p = target / rel
        return p.read_text(encoding="utf-8") if p.is_file() else None

    for rel, text in render_project_files(config, read_existing).items():
        path = target / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        if write_if_absent(path, text):
            mark(path)
    return sorted(created)
