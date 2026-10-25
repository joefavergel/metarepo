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


def add_source(root: Path, rel: str, content: bytes, *, title: str | None = None, triage: str = "wiki",
               quality: str = "high", body: str | None = None, media_type: str = "text/plain",
               original_name: str | None = None, extra_front: dict[str, Any] | None = None) -> dict[str, str]:
    """Place a source and write its extraction record (the layout of 008 section 2)."""
    import hashlib

    digest = hashlib.sha256(content).hexdigest()
    sid = f"src-{digest[:12]}"
    src = root / "10_sources" / rel
    src.parent.mkdir(parents=True, exist_ok=True)
    src.write_bytes(content)
    front: dict[str, Any] = {
        "type": "Extract", "schema_version": 1, "source_id": sid, "source_path": f"10_sources/{rel}",
        "source_sha256": digest, "source_size": len(content), "media_type": media_type,
        "original_name": original_name or Path(rel).name, "title": title or Path(rel).stem,
        "language": "en", "extracted_at": "2026-10-24T15:00:00Z",
        "extractor": {"name": "plain", "version": "1", "mode": "local"}, "quality": quality,
        "triage": {"decision": triage, "reason": "test", "by": "member-a"},
        "supersedes": [], "bundle": {"parent_id": None, "group": None, "member_path": None},
    }
    front.update(extra_front or {})
    text = "---\n" + yaml.safe_dump(front, sort_keys=False, allow_unicode=True) + "---\n"
    text += body if body is not None else f"# {front['title']}\n\nExtracted text of {rel}.\n"
    md = root / "20_extracts" / f"{rel}.md"
    md.parent.mkdir(parents=True, exist_ok=True)
    md.write_text(text, encoding="utf-8")
    return {"id": sid, "sha256": digest, "path": f"10_sources/{rel}", "extract": f"20_extracts/{rel}.md"}


def add_page(root: Path, rel: str, front: dict[str, Any] | None, body: str) -> Path:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    head = "---\n" + yaml.safe_dump(front, sort_keys=False, allow_unicode=True) + "---\n" if front is not None else ""
    path.write_text(head + body, encoding="utf-8")
    return path


def tree_snapshot(root: Path) -> dict[str, bytes]:
    return {p.relative_to(root).as_posix(): p.read_bytes() for p in sorted(root.rglob("*")) if p.is_file()}
