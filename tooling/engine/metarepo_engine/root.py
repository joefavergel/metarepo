"""Metarepository root discovery and the Project object (002/R1, 003/R1)."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .jsonout import EXIT_COMPAT, EXIT_PRECONDITION, EngineError

PROJECT_FILE = "project.yaml"
STATE_REL = "_system/state.json"


def is_metarepo_root(path: Path) -> bool:
    """A root has both ``project.yaml`` and ``_system/state.json`` (002/R1)."""
    return (path / PROJECT_FILE).is_file() and (path / "_system" / "state.json").is_file()


def find_root(start: Path) -> Path | None:
    start = Path(start).resolve()
    for candidate in (start, *start.parents):
        if is_metarepo_root(candidate):
            return candidate
    return None


class Project:
    """A metarepository on disk. Reads are lazy and cached for the life of the command."""

    def __init__(self, root: Path):
        self.root = Path(root)
        self._config: dict[str, Any] | None = None
        self._state: dict[str, Any] | None = None

    @classmethod
    def discover(cls, explicit_root: str | None, cwd: Path | None = None) -> "Project":
        if explicit_root:
            root = Path(explicit_root).expanduser().resolve()
            if not is_metarepo_root(root):
                raise EngineError(
                    "not_a_metarepo",
                    f"{root} is not a metarepository root (needs project.yaml and _system/state.json)",
                    EXIT_PRECONDITION,
                )
            return cls(root)
        found = find_root(cwd or Path.cwd())
        if found is None:
            raise EngineError(
                "not_a_metarepo",
                "no metarepository found here or in any parent folder; use --root <path>",
                EXIT_PRECONDITION,
            )
        return cls(found)

    @property
    def config_path(self) -> Path:
        return self.root / PROJECT_FILE

    @property
    def state_path(self) -> Path:
        return self.root / STATE_REL

    def config(self) -> dict[str, Any]:
        if self._config is None:
            from .config import load_project_yaml

            self._config = load_project_yaml(self.config_path)
        return self._config

    def state(self) -> dict[str, Any]:
        if self._state is None:
            try:
                data = json.loads(self.state_path.read_text(encoding="utf-8"))
            except (OSError, ValueError) as exc:
                raise EngineError("state_invalid", f"cannot read {STATE_REL}: {exc}", EXIT_COMPAT, STATE_REL) from exc
            if not isinstance(data, dict):
                raise EngineError("state_invalid", f"{STATE_REL} must be a JSON object", EXIT_COMPAT, STATE_REL)
            self._state = data
        return self._state

    def rel(self, path: Path) -> str:
        """Root-relative POSIX path for JSON output (003/R15)."""
        return Path(path).resolve().relative_to(self.root).as_posix()

    @property
    def project_id(self) -> str | None:
        pid = self.config().get("id")
        return str(pid) if pid else None
