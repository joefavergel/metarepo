"""Rendering of the project's root files from the toolkit templates (002/R17, R18).

``AGENTS.md``, ``README.md`` and (when repositories exist) ``code/README.md`` are rendered with
``string.Template`` from ``project.yaml`` values. Output is deterministic (no timestamps), so rendering
twice yields identical bytes. Each file carries one delimited block that regeneration preserves:

    <!-- project-notes:start -->
    ...human additions...
    <!-- project-notes:end -->
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from string import Template
from typing import Any, Callable

from .config import effective

NOTES_START = "<!-- project-notes:start -->"
NOTES_END = "<!-- project-notes:end -->"
DEFAULT_NOTES = "(Add project-specific notes here. This block is preserved when the file is regenerated.)"
_NOTES_RE = re.compile(re.escape(NOTES_START) + r"\n?(.*?)\n?" + re.escape(NOTES_END), re.DOTALL)

TIER_LABEL = {"on-demand": "on demand", "explicit": "explicit request only"}


def templates_dir() -> Path:
    return Path(__file__).resolve().parents[2] / "templates"


def load_skeleton(directory: Path | None = None) -> dict[str, Any]:
    return json.loads(((directory or templates_dir()) / "skeleton.json").read_text(encoding="utf-8"))


def extract_notes(existing: str | None) -> str | None:
    """The text inside the project-notes block of an existing file, or None when there is no block."""
    if existing is None:
        return None
    m = _NOTES_RE.search(existing.replace("\r\n", "\n"))
    return m.group(1) if m else None


def has_repos(config: dict[str, Any]) -> bool:
    return bool(config.get("repos"))


def folder_map(config: dict[str, Any]) -> str:
    rows = [
        ("`AGENTS.md`", "Agent instructions (single source of truth)", "free"),
        ("`README.md`", "Human onboarding", "on demand"),
        ("`project.yaml`", "Project configuration (read through the engine)", "free"),
        ("`00_inbox/`", "Untriaged material; members drop files here, `/intake` moves them out",
         "during `/intake` only"),
        ("`10_sources/`", "Immutable originals, placed by the engine", "explicit request only"),
        ("`20_extracts/`", "One Markdown extraction per source (mirrors `10_sources/`)", "on demand"),
        ("`30_wiki/`", "The project wiki (OKF bundle); entry point `index.md`", "free"),
        ("`registry` (engine command)", "Derived view over the extractions; never edited", "free"),
        ("`outputs/`", "Generated deliverables; `_templates/` for project output templates", "on demand"),
        ("`shared/`", "Shared material that is not a source; `shared/tools/` for team tools; never ingested",
         "explicit request only"),
        ("`personal/<member>/`", "Personal scratch space", "own folder only, unless asked"),
    ]
    if has_repos(config):
        rows.append(("`code/`", "`README.md` only: index of the code repositories (clones live elsewhere)",
                     "free (small)"))
    rows.append(("`_system/`", "Shared project state: gates, leases, event log", "via the engine only"))
    for extra in config.get("extra_folders") or []:
        purpose = extra.get("purpose") or "Project-specific folder"
        rows.append((f"`{extra['path']}/`", purpose, TIER_LABEL.get(extra.get("tier"), "explicit request only")))
    lines = ["| Path | Purpose | Access tier |", "|---|---|---|"]
    lines += [f"| {a} | {b} | {c} |" for a, b, c in rows]
    return "\n".join(lines)


def _cell(text: Any) -> str:
    return str(text).replace("|", "\\|").replace("\n", " ")


def repo_table(config: dict[str, Any]) -> str:
    lines = ["| Name | URL | Default branch | Role |", "|---|---|---|---|"]
    for repo in config.get("repos") or []:
        lines.append(
            f"| {_cell(repo['name'])} | {_cell(repo['url'])} | {_cell(repo.get('default_branch') or '(repository default)')}"
            f" | {_cell(repo.get('role') or 'primary')} |"
        )
    return "\n".join(lines)


def repos_section(config: dict[str, Any]) -> str:
    repos = config.get("repos") or []
    rules = (
        "Rules: clones live **outside** this synced folder; never clone into it and never read code from it. "
        "Find a member's local clone with `repos path <name>` (engine). Documentation about code belongs in "
        "the repository itself, not in the wiki. The index with clone instructions is `code/README.md`."
    )
    if not repos:
        return ("This project has no code repositories registered (`repos` in `project.yaml`). "
                "If it gets some, the engine adds `code/README.md`.\n\n" + rules)
    items = "\n".join(
        f"- `{r['name']}`: {r['url']} (branch: {r.get('default_branch') or 'repository default'}, "
        f"role: {r.get('role') or 'primary'})"
        for r in repos
    )
    return items + "\n\n" + rules


def _join(values: Any) -> str:
    items = [str(v) for v in (values or [])]
    return "; ".join(items) if items else "(not specified)"


def context(config: dict[str, Any], notes: str | None) -> dict[str, str]:
    cfg = effective(config)
    sync = cfg.get("sync") or {}
    provider = sync.get("provider")
    sync_line = f" by {provider}" if provider and provider != "none" else ""
    if sync.get("notes"):
        sync_line += f" ({sync['notes']})"
    scope = cfg.get("scope") or {}
    return {
        "project_name": str(cfg["name"]),
        "slug": str(cfg["slug"]),
        "description": str(cfg.get("description") or "").strip(),
        "objective": " ".join(str(cfg["objective"]).split()),
        "content_language": str((cfg.get("language") or {}).get("content", "")),
        "toolkit_requires": str((cfg.get("toolkit") or {}).get("requires", "")),
        "folder_map": folder_map(cfg),
        "repos_section": repos_section(cfg),
        "repo_table": repo_table(cfg),
        "sync_line": sync_line,
        "scope_in": _join(scope.get("in")),
        "scope_out": _join(scope.get("out")),
        "project_notes": DEFAULT_NOTES if notes is None else notes,
    }


def render_text(template_rel: str, config: dict[str, Any], existing: str | None = None,
                directory: Path | None = None) -> str:
    template = Template((((directory or templates_dir()) / template_rel).read_text(encoding="utf-8")))
    text = template.substitute(context(config, extract_notes(existing)))
    return text.replace("\r\n", "\n")


def render_project_files(
    config: dict[str, Any],
    read_existing: Callable[[str], str | None],
    directory: Path | None = None,
) -> dict[str, str]:
    """Rendered text per root-relative path, for every file that applies to this project."""
    skeleton = load_skeleton(directory)
    out: dict[str, str] = {}
    for entry in skeleton["root_files"]:
        if entry.get("when") == "repos" and not has_repos(config):
            continue
        rel = entry["path"]
        out[rel] = render_text(entry["template"], config, read_existing(rel), directory)
    return out
