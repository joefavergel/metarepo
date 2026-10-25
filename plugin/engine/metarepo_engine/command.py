"""Command definition and execution context shared by the dispatcher and every command module."""
from __future__ import annotations

import argparse
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from .jsonout import Result, warning
from .root import Project


@dataclass
class Context:
    """Per-invocation state handed to ``run``. Built by the dispatcher; commands never build it."""

    project: Project | None
    json_mode: bool
    dry_run: bool
    actor: str | None
    version: str
    cwd: Path = field(default_factory=Path.cwd)
    warnings: list[dict[str, Any]] = field(default_factory=list)

    @property
    def root(self) -> Path | None:
        return self.project.root if self.project else None

    def require_project(self) -> Project:
        if self.project is None:  # pragma: no cover - dispatcher guarantees it when needs_project
            raise RuntimeError("command declared needs_project=False but asked for the project")
        return self.project

    def warn(self, code: str, message: str, path: str | None = None) -> None:
        self.warnings.append(warning(code, message, path))


RunFn = Callable[[Context, argparse.Namespace], Result]
ArgsFn = Callable[[argparse.ArgumentParser], None]


@dataclass(frozen=True)
class Command:
    """One CLI command. A module under ``commands/`` exposes exactly one ``COMMAND``.

    ``name`` may contain one space to form a group (``"config validate"``).
    ``mutating``       adds ``--dry-run`` (003/R4).
    ``writes_project`` the command writes inside a project: the compatibility gate refuses it
                       outside ``toolkit.requires`` (001/R7) and the dispatcher logs an event (003/R13).
    ``needs_project``  the dispatcher discovers the root first (exit 3 when there is none).
    ``gate``           run the compatibility check at all (``migrate`` and ``config validate`` opt out).
    ``needs_yaml``     requires PyYAML (003/R16: without it only ``status`` works, others exit 5).
    ``implemented``    False for stubs owned by a later spec: the dispatcher answers
                       ``not_implemented`` (exit 2) without running anything.
    """

    name: str
    help: str
    run: RunFn
    add_arguments: ArgsFn | None = None
    mutating: bool = False
    writes_project: bool = False
    needs_project: bool = True
    gate: bool = True
    needs_yaml: bool = True
    owner: str = "003"
    implemented: bool = True

    def __post_init__(self) -> None:
        if self.writes_project and not self.mutating:
            raise ValueError(f"{self.name}: writes_project implies mutating")
        if len(self.name.split(" ")) > 2:
            raise ValueError(f"{self.name}: at most two words (group + subcommand)")
