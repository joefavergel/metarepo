"""``init``: scaffold a metarepository from interview answers (spec 005, R1-R9).

``init <dir> --answers <file.json>`` is what the /create-metarepo skill calls after the interview;
``init <dir> --interactive`` asks the same questions in the terminal (R8). Both build one answers mapping,
turn it into a ``project.yaml`` configuration and call ``scaffold.scaffold`` (idempotent, never overwrites).
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from .. import compat, scaffold
from ..command import Command, Context
from ..config import CONTRACT_FOLDERS, load_schema, validate_project
from ..events import append_event, now_iso, resolve_actor
from ..jsonout import EXIT_COMPAT, EXIT_PRECONDITION, EXIT_USAGE, EngineError, Result
from ..naming import slugify
from ..syncinfo import detect_provider
from ..root import find_root
from ..validate import validate

# Files that cloud providers and operating systems drop into a folder; they do not make it "non-empty" (R1).
PROVIDER_METADATA = frozenset({
    ".ds_store", "desktop.ini", "thumbs.db", ".dropbox", ".dropbox.attr", ".dropbox.cache", ".tmp.driveupload",
    ".tmp.drivedownload", ".localized", ".spotlight-v100", ".trashes", ".fseventsd", ".icon", "icon\r",
})
# Names a partially scaffolded folder may contain (R6).
SCAFFOLD_NAMES = frozenset(CONTRACT_FOLDERS) | {"project.yaml", "AGENTS.md", "README.md"}

ANSWER_KEYS = frozenset(load_schema("answers")["properties"])


def detect_sync_provider(path: Path) -> str | None:
    """Heuristic from the path; the fragments live in templates/sync-providers.yaml (shared with 006)."""
    return detect_provider(path)


def inside_git_worktree(path: Path) -> Path | None:
    for candidate in (Path(path).resolve(), *Path(path).resolve().parents):
        if (candidate / ".git").exists():
            return candidate
    return None


def repo_name_from_url(url: str) -> str:
    """Proposal for a repository name: the last path segment of the URL without ``.git`` (R3d)."""
    text = url.strip().rstrip("/")
    if "://" not in text and ":" in text:  # scp-like: git@host:org/name.git
        text = text.split(":", 1)[1]
    else:
        text = urlparse(text).path or text
    last = text.rstrip("/").rsplit("/", 1)[-1]
    if last.endswith(".git"):
        last = last[:-4]
    return slugify(last) if last else "repo"


def default_language() -> str:
    """The operator's language: from the environment when it names one, else ``en``. The skill passes it explicitly."""
    for var in ("LC_ALL", "LC_MESSAGES", "LANG"):
        m = re.match(r"^([A-Za-z]{2,3})(?:[_.@-]|$)", os.environ.get(var, ""))
        if m and m.group(1).lower() not in ("c", "po"):
            return m.group(1).lower()
    return "en"


def load_answers(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except OSError as exc:
        raise EngineError("answers_unreadable", f"cannot read answers file: {exc}", EXIT_USAGE) from exc
    except ValueError as exc:
        raise EngineError("answers_invalid", f"answers file is not valid JSON: {exc}", EXIT_USAGE) from exc
    return data


def check_answers(answers: Any) -> dict[str, Any]:
    """Validate against ``answers.schema.json``; unknown keys are errors (a typo must not be silently ignored)."""
    findings = validate(answers, load_schema("answers"))
    problems = [f"{p}: {m}" for p, m in findings.errors]
    if isinstance(answers, dict):
        problems += [f"{k}: unknown answer" for k in answers if k not in ANSWER_KEYS]
    if problems:
        raise EngineError("answers_invalid", "answers file is invalid: " + "; ".join(problems), EXIT_USAGE)
    return answers


def build_config(answers: dict[str, Any], *, version: str, sync_provider: str) -> dict[str, Any]:
    """Map interview answers to ``project.yaml`` (R3 defaults, R5)."""
    name = answers["name"].strip()
    description = answers["description"].strip()
    config: dict[str, Any] = {
        "schema_version": 1,
        "id": scaffold.new_project_id(),
        "name": name,
        "slug": answers.get("slug") or slugify(name),
        "description": description,
        "objective": (answers.get("objective") or description).strip(),
        "scope": {"in": list((answers.get("scope") or {}).get("in", [])),
                  "out": list((answers.get("scope") or {}).get("out", []))},
        "language": {"content": answers.get("language") or default_language()},
        "toolkit": {"requires": compat.requires_for(version), "created_with": version},
        "sync": {"provider": sync_provider, "notes": ""},
        "repos": [],
        "team": list(answers.get("team") or []),
        "governance": {"wiki_write_mode": answers.get("wiki_write_mode", "lease")},
        "intake": {"confirm": answers.get("intake_confirm", "always")},
    }
    if answers.get("wiki_owners"):
        config["governance"]["wiki_owners"] = list(answers["wiki_owners"])
    for repo in answers.get("repos") or []:
        entry: dict[str, Any] = {"name": repo.get("name") or repo_name_from_url(repo["url"]), "url": repo["url"].strip()}
        if repo.get("default_branch"):
            entry["default_branch"] = repo["default_branch"]
        entry["role"] = repo.get("role") or ("primary" if not config["repos"] else "secondary")
        config["repos"].append(entry)
    return config


def _foreign_entries(target: Path) -> list[str]:
    return sorted(
        e.name for e in target.iterdir()
        if e.name.lower() not in PROVIDER_METADATA and e.name not in SCAFFOLD_NAMES
    )


def check_target(target: Path) -> Path | None:
    """R1 preconditions. Returns the root when ``target`` is already a metarepository root (idempotent re-run)."""
    if not target.exists():
        raise EngineError("target_missing", f"target folder does not exist: {target}", EXIT_PRECONDITION)
    if not target.is_dir():
        raise EngineError("target_not_a_folder", f"target is not a folder: {target}", EXIT_PRECONDITION)
    if not os.access(target, os.W_OK | os.X_OK):
        raise EngineError("target_not_writable", f"target folder is not writable: {target}", EXIT_PRECONDITION)
    root = find_root(target)
    if root is not None and root != target.resolve():
        raise EngineError(
            "already_in_metarepo",
            f"{target} is inside the metarepository at {root}. To work with it use /join-metarepo; "
            "to change its settings edit project.yaml.",
            EXIT_PRECONDITION,
        )
    if root is None:
        foreign = _foreign_entries(target)
        if foreign:
            raise EngineError(
                "target_not_empty",
                f"{target} is not empty (found: {', '.join(foreign[:5])}{', ...' if len(foreign) > 5 else ''}). "
                "Choose an empty folder; only provider metadata files may be present.",
                EXIT_PRECONDITION,
            )
    return root


# ---- interactive mode (R8): same questions as the skill's basic interview, plain terminal prompts
def _ask(prompt: str, default: str | None = None, *, required: bool = False) -> str:
    suffix = f" [{default}]" if default else ""
    while True:
        sys.stderr.write(f"{prompt}{suffix}: ")
        sys.stderr.flush()
        line = sys.stdin.readline()
        if line == "":  # EOF: no way to ask
            raise EngineError("no_input", "interactive mode needs input on stdin; use --answers instead", EXIT_USAGE)
        value = line.strip() or (default or "")
        if value or not required:
            return value
        sys.stderr.write("A value is required.\n")


def _yes(prompt: str) -> bool:
    return _ask(prompt + " (y/N)").lower() in ("y", "yes")


def interview(preset_name: str | None) -> dict[str, Any]:
    answers: dict[str, Any] = {}
    answers["name"] = preset_name or _ask("Project name", required=True)
    answers["description"] = _ask("Short description of the project", required=True)
    notes = _ask("Specific instructions for agents working in this project (optional)")
    if notes:
        answers["notes"] = notes
    if _yes("Does the project have associated code repositories?"):
        repos = []
        while True:
            url = _ask("Repository URL (empty to finish)")
            if not url:
                break
            name = _ask("Repository name", repo_name_from_url(url))
            branch = _ask("Default branch (empty = repository default)")
            repos.append({"url": url, "name": name, **({"default_branch": branch} if branch else {})})
        if repos:
            answers["repos"] = repos
    if _yes("Configure advanced settings now?"):
        objective = _ask("Objective (empty = same as the description)")
        if objective:
            answers["objective"] = objective
        scope_in = _ask("Scope, in (comma separated, optional)")
        scope_out = _ask("Scope, out (comma separated, optional)")
        if scope_in or scope_out:
            answers["scope"] = {"in": [s.strip() for s in scope_in.split(",") if s.strip()],
                                "out": [s.strip() for s in scope_out.split(",") if s.strip()]}
        answers["language"] = _ask("Content language code", default_language())
        answers["wiki_write_mode"] = _ask("Wiki write mode (lease/owner)", "lease")
        answers["intake_confirm"] = _ask("Intake confirmation (always/when-uncertain)", "always")
    return answers


def _args(p: argparse.ArgumentParser) -> None:
    p.add_argument("dir", help="target folder (must exist and be empty, or a partial scaffold)")
    src = p.add_mutually_exclusive_group()
    src.add_argument("--answers", metavar="FILE", help="JSON file with the interview answers (answers.schema.json)")
    src.add_argument("--interactive", action="store_true", help="ask the questions in the terminal")
    p.add_argument("--name", default=None, help="project name (used with --interactive to skip that question)")


def run(ctx: Context, args: argparse.Namespace) -> Result:
    if not args.answers and not args.interactive:
        raise EngineError(
            "answers_required",
            "nothing to create from: pass --answers <file.json> or --interactive (no folder was changed)",
            EXIT_USAGE,
        )
    target = Path(args.dir).expanduser()
    existing_root = check_target(target)
    target = target.resolve()
    answers = interview(args.name) if args.interactive else load_answers(Path(args.answers).expanduser())
    answers = check_answers(answers)

    result = Result()
    detected = detect_sync_provider(target)
    provider = answers.get("sync_provider") or detected or "none"
    if not answers.get("sync_provider") and detected is None:
        result.warn("sync_undetected", "the folder is not recognised as inside a sync root; sync provider recorded "
                    "as 'none' (set sync_provider in the answers or sync.provider in project.yaml)")
    git_root = inside_git_worktree(target)
    if git_root is not None:
        result.warn("inside_git_worktree", f"{target} is inside the git working tree at {git_root}; a metarepository "
                    "lives on a synced drive and must not contain git (P9)")

    config = build_config(answers, version=ctx.version, sync_provider=provider)
    findings = validate_project(config)
    if findings.errors:
        raise EngineError("answers_invalid", "answers give an invalid project.yaml: "
                          + "; ".join(f"{p}: {m}" for p, m in findings.errors), EXIT_COMPAT)
    if existing_root is not None:
        result.warn("already_initialized", f"{target} is already a metarepository root; only missing pieces are created")

    planned = scaffold.planned_paths(config)
    if ctx.dry_run:
        created = [p for p in planned if not (target / p).exists()]
    else:
        try:
            created = scaffold.scaffold(target, config, version=ctx.version, notes=answers.get("notes"))
        except ValueError as exc:
            raise EngineError("answers_invalid", str(exc), EXIT_USAGE) from exc
    kept = [p for p in planned if p not in set(created)]
    # Folders that scaffold returns without a trailing slash are listed as-is; files and folders are both paths.
    result.data = {
        "dry_run": ctx.dry_run,
        "root": target.as_posix(),
        "created": created,
        "kept": kept,
        "project": {"name": config["name"], "slug": config["slug"], "objective": config["objective"],
                    "language": config["language"]["content"], "toolkit_requires": config["toolkit"]["requires"],
                    "repos": [r["name"] for r in config["repos"]],
                    "wiki_write_mode": config["governance"]["wiki_write_mode"],
                    "intake_confirm": config["intake"]["confirm"]},
        "sync": {"provider": provider, "detected": detected},
        "next_steps": ["/join-metarepo", "/init-wiki", "00_inbox/"],
    }
    result.targets = created
    result.log_event = False  # the dispatcher has no project for init; the event is appended below
    result.text = _summary(result.data)
    if created and not ctx.dry_run:
        try:
            append_event(target, command="metarepo.created", targets=created, result="ok",
                         actor=resolve_actor(ctx.actor), extra={"project_id": config["id"], "at": now_iso()})
        except OSError as exc:
            result.warn("event_log_failed", f"could not append to _system/events.log: {exc}")
    return result


def _summary(data: dict[str, Any]) -> str:
    head = "Would create" if data["dry_run"] else "Created"
    lines = [f"{head} {len(data['created'])} path(s) in {data['root']}; kept {len(data['kept'])} existing."]
    lines += [f"  + {p}" for p in data["created"]]
    lines += [f"  = {p}" for p in data["kept"]]
    lines.append("Next: /join-metarepo (members), /init-wiki (after a first /intake), drop material in 00_inbox/.")
    return "\n".join(lines)


COMMAND = Command(
    name="init",
    help="Scaffold a metarepo from interview answers (--answers FILE or --interactive)",
    run=run,
    add_arguments=_args,
    mutating=True,
    writes_project=False,
    needs_project=False,
    gate=False,
    owner="005",
)
