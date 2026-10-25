"""``render-templates``: re-render AGENTS.md, README.md and code/README.md (002/R17, AC2)."""
from __future__ import annotations

import argparse

from ..command import Command, Context
from .. import leases
from ..config import validate_project
from ..events import resolve_actor
from ..fsops import atomic_write_text
from ..jsonout import EXIT_COMPAT, EngineError, Result
from ..templates import extract_notes, has_repos, render_project_files

FORBIDDEN_INSTRUCTION_FILES = ("CLAUDE.md", ".claude/CLAUDE.md", "CLAUDE.local.md")  # 002/R2b


def _args(p: argparse.ArgumentParser) -> None:
    p.add_argument("--lease", default=None, help="nonce of a config lease already held (default: acquire one)")


def _write_all(root, handle, pending: dict[str, str]) -> None:
    handle.ensure()
    for rel, text in pending.items():
        atomic_write_text(root / rel, text)


def run(ctx: Context, args: argparse.Namespace) -> Result:
    project = ctx.require_project()
    config = project.config()
    findings = validate_project(config)
    if findings.errors:
        raise EngineError("schema_invalid", "project.yaml is invalid; run `config validate`: "
                          + "; ".join(f"{p}: {m}" for p, m in findings.errors), EXIT_COMPAT, "project.yaml")
    root = project.root

    def read_existing(rel: str) -> str | None:
        path = root / rel
        return path.read_text(encoding="utf-8") if path.is_file() else None

    rendered = render_project_files(config, read_existing)
    result = Result()
    changed: list[str] = []
    pending: dict[str, str] = {}
    for rel, text in rendered.items():
        existing = read_existing(rel)
        if existing is not None and extract_notes(existing) is None:
            result.warn("project_notes_missing", f"{rel} has no project-notes block; a default block was added", rel)
        if existing is not None and existing.replace("\r\n", "\n") == text:
            continue
        changed.append(rel)
        pending[rel] = text
    if pending and not ctx.dry_run:
        if args.lease:  # the caller already holds the config lease (004/R1)
            handle = leases.attach(root, "config", args.lease)
            _write_all(root, handle, pending)
        else:
            with leases.held(root, "config", resolve_actor(ctx.actor), "render-templates") as handle:
                _write_all(root, handle, pending)
    for rel in FORBIDDEN_INSTRUCTION_FILES:
        if (root / rel).exists():
            result.warn("forbidden_instruction_file",
                        f"{rel} must not exist: the project uses AGENTS.md as its only instruction file (002/R2b)", rel)
    if not has_repos(config) and (root / "code" / "README.md").exists():
        result.warn("code_readme_without_repos", "code/README.md exists but project.yaml lists no repos",
                    "code/README.md")
    result.data = {"dry_run": ctx.dry_run, "rendered": sorted(rendered), "changed": changed,
                   "unchanged": sorted(set(rendered) - set(changed))}
    result.targets = [] if ctx.dry_run else changed
    result.log_event = bool(changed)
    verb = "would change" if ctx.dry_run else "changed"
    result.text = f"{verb}: {', '.join(changed) if changed else 'nothing (already up to date)'}"
    return result


COMMAND = Command(
    name="render-templates",
    help="Re-render AGENTS.md, README.md and code/README.md from project.yaml (project-notes blocks are kept)",
    run=run,
    add_arguments=_args,
    mutating=True,
    writes_project=True,
    owner="002",
)
