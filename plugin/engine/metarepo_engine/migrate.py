"""Project migrations (001/R8).

A migration file ``engine/migrations/<from>_to_<to>.py`` moves a project between two *compatibility
lines*. A line is ``M`` for toolkit versions >= 1.0 (``1_to_2.py``) and ``0.m`` for the 0.x series
(``0.1_to_0.2.py``); see ``compat.line_of``. Each file defines ``migrate(root, dry_run) -> list[str]``
returning the root-relative POSIX paths it changes (or would change).

Flow: plan the chain, show a dry run, require confirmation, back up every file to be changed into the
member's local cache, apply, bump ``toolkit.requires``, record the migration in ``_system/state.json``.
The ``config`` lease (004) is not available yet: ``LEASE_HOOK`` is the seam 004 fills.
"""
from __future__ import annotations

import importlib.util
import json
import re
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from . import compat
from .events import now_iso
from .fsops import atomic_write_bytes, atomic_write_text, local_cache_dir
from .jsonout import EXIT_COMPAT, EXIT_PRECONDITION, EngineError
from .root import Project

_FILE_RE = re.compile(r"^(\d+(?:\.\d+)?)_to_(\d+(?:\.\d+)?)\.py$")

# 004 replaces this with a function that acquires the ``config`` lease and returns a release callable.
LEASE_HOOK: Callable[[Project], Callable[[], None]] | None = None


def migrations_dir() -> Path:
    return Path(__file__).resolve().parents[1] / "migrations"


def discover(directory: Path) -> dict[str, tuple[str, Path]]:
    """Map ``from`` line -> (``to`` line, file)."""
    found: dict[str, tuple[str, Path]] = {}
    if directory.is_dir():
        for f in sorted(directory.iterdir()):
            m = _FILE_RE.match(f.name)
            if m:
                found[m.group(1)] = (m.group(2), f)
    return found


@dataclass
class Step:
    from_line: str
    to_line: str
    path: Path


def project_line(requires: str) -> str:
    for op, version in compat.parse_requires(requires):
        if op in (">=", "=="):
            major, minor, patch = version
            return compat.line_of(f"{major}.{minor}.{patch}")
    raise EngineError("config_invalid", f"cannot derive the project's compatibility line from '{requires}'",
                      EXIT_COMPAT, "project.yaml")


def plan_chain(from_line: str, to_line: str, available: dict[str, tuple[str, Path]]) -> list[Step]:
    steps: list[Step] = []
    current, guard = from_line, 0
    while current != to_line:
        guard += 1
        if current not in available or guard > 64:
            raise EngineError(
                "no_migration_path",
                f"no migration from line {current} towards {to_line} is shipped with this toolkit",
                EXIT_COMPAT,
            )
        nxt, path = available[current]
        steps.append(Step(current, nxt, path))
        current = nxt
    return steps


def _load(path: Path):
    spec = importlib.util.spec_from_file_location(f"metarepo_migration_{path.stem.replace('.', '_')}", path)
    if spec is None or spec.loader is None:  # pragma: no cover
        raise EngineError("migration_invalid", f"cannot load {path.name}", EXIT_COMPAT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    fn = getattr(module, "migrate", None)
    if not callable(fn):
        raise EngineError("migration_invalid", f"{path.name} defines no migrate(root, dry_run)", EXIT_COMPAT)
    return fn


_REQ_LINE = re.compile(r"^(?P<pre>\s+requires:\s*)(?P<q>[\"']?)(?P<val>[^\"'#\n]*?)(?P=q)(?P<post>\s*(#.*)?)$")


def bump_requires_text(text: str, new_requires: str) -> str:
    """Rewrite ``toolkit.requires`` in place, keeping comments and the rest of the file untouched."""
    lines = text.splitlines(keepends=True)
    in_toolkit = False
    for i, raw in enumerate(lines):
        stripped = raw.rstrip("\r\n")
        eol = raw[len(stripped):]
        if re.match(r"^toolkit:\s*(#.*)?$", stripped):
            in_toolkit = True
            continue
        if in_toolkit:
            if stripped and not stripped[0].isspace() and not stripped.startswith("#"):
                break
            m = _REQ_LINE.match(stripped)
            if m:
                q = m.group("q") or '"'
                lines[i] = f"{m.group('pre')}{q}{new_requires}{q}{m.group('post')}{eol}"
                return "".join(lines)
    raise EngineError("config_unwritable", "could not locate toolkit.requires in project.yaml to bump it "
                      "(block style `toolkit:` / `  requires: ...` is required)", EXIT_COMPAT, "project.yaml")


@dataclass
class Report:
    status: str  # up-to-date | planned | applied
    installed: str
    requires: str
    steps: list[dict[str, Any]] = field(default_factory=list)
    new_requires: str | None = None
    backup_dir: str | None = None
    changed: list[str] = field(default_factory=list)


def run_migrations(
    project: Project,
    *,
    installed: str,
    dry_run: bool,
    confirmed: bool,
    directory: Path | None = None,
) -> Report:
    config = project.config()
    requires = str((config.get("toolkit") or {}).get("requires") or "")
    verdict = compat.check(requires, installed)
    if verdict.status == compat.OK:
        return Report("up-to-date", installed, requires)
    if verdict.status == compat.UPDATE_TOOLKIT:
        raise EngineError("update_toolkit", verdict.message, EXIT_COMPAT)

    steps = plan_chain(project_line(requires), compat.line_of(installed), discover(directory or migrations_dir()))
    new_requires = compat.requires_for(installed)
    root = project.root
    # Pre-flight: the requires bump must be possible before anything is changed.
    original_yaml = project.config_path.read_text(encoding="utf-8")
    bump_requires_text(original_yaml, new_requires)

    report = Report("planned", installed, requires, new_requires=new_requires)
    for step in steps:
        planned = _load(step.path)(root, True)
        report.steps.append({"from": step.from_line, "to": step.to_line, "file": step.path.name,
                             "changes": sorted(planned)})
    if dry_run or not confirmed:
        return report

    release = LEASE_HOOK(project) if LEASE_HOOK else None
    try:
        pid = project.project_id
        if not pid:
            raise EngineError("config_invalid", "project.yaml has no id; cannot locate the local cache", EXIT_COMPAT)
        stamp = now_iso().replace(":", "").replace("-", "")
        backup_root = local_cache_dir(pid, create=True) / "migration-backups" / stamp
        backup_root.mkdir(parents=True, exist_ok=True)
        report.backup_dir = str(backup_root)

        def backup(rel: str) -> None:
            src = root / rel
            if src.is_file():
                dst = backup_root / rel
                dst.parent.mkdir(parents=True, exist_ok=True)
                if not dst.exists():
                    shutil.copy2(src, dst)

        changed: set[str] = set()
        for step in steps:
            fn = _load(step.path)
            to_change = sorted(fn(root, True))
            for rel in to_change:
                backup(rel)
            done = sorted(fn(root, False))
            unexpected = [rel for rel in done if rel not in to_change]
            if unexpected:
                raise EngineError("migration_unplanned_change",
                                  f"{step.path.name} changed files its dry run did not announce: {unexpected}",
                                  EXIT_COMPAT)
            changed.update(done)

        backup("project.yaml")
        backup("_system/state.json")
        atomic_write_text(project.config_path, bump_requires_text(project.config_path.read_text(encoding="utf-8"),
                                                                  new_requires))
        state = json.loads(project.state_path.read_text(encoding="utf-8"))
        tk = state.setdefault("toolkit", {})
        tk["last_written_with"] = installed
        tk.setdefault("migrations", []).append(
            {"from": steps[0].from_line, "to": steps[-1].to_line, "at": now_iso(), "toolkit": installed}
        )
        atomic_write_bytes(project.state_path, (json.dumps(state, indent=2, ensure_ascii=False) + "\n").encode("utf-8"))
        report.changed = sorted(changed | {"project.yaml", "_system/state.json"})
        report.status = "applied"
        return report
    finally:
        if release:
            release()
