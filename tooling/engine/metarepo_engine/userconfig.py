"""Per-user local configuration ``~/.config/metarepo/<project-id>.yaml`` (006/R5).

``{actor, metarepo_root, repos: {<name>: <abs path>}, harness?}``. The file belongs to one member on one
machine and is never inside the synced tree. ``METAREPO_CONFIG_DIR`` overrides the folder (tests, portable use).
"""
from __future__ import annotations

import os
import re
import sys
from pathlib import Path
from typing import Any

from .fsops import atomic_write_text
from .jsonout import EXIT_COMPAT, EngineError

_SAFE_ID = re.compile(r"^[A-Za-z0-9._-]{1,128}$")
KEYS = ("actor", "metarepo_root", "repos", "harness")


def config_dir() -> Path:
    override = os.environ.get("METAREPO_CONFIG_DIR")
    if override:
        return Path(override)
    if sys.platform == "win32":
        base = os.environ.get("APPDATA")
        return (Path(base) if base else Path.home() / "AppData" / "Roaming") / "metarepo"
    base = os.environ.get("XDG_CONFIG_HOME")
    return (Path(base) if base and os.path.isabs(base) else Path.home() / ".config") / "metarepo"


def user_config_path(project_id: str) -> Path:
    if not _SAFE_ID.match(project_id or ""):
        raise ValueError(f"unsafe project id for config path: {project_id!r}")
    return config_dir() / f"{project_id}.yaml"


def load(project_id: str, *, strict: bool = False) -> dict[str, Any]:
    """The stored config, or ``{}`` when absent. Unreadable/invalid: ``{}`` unless ``strict`` (then EngineError)."""
    path = user_config_path(project_id)
    try:
        import yaml

        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except Exception as exc:  # noqa: BLE001 - unreadable file, bad YAML or missing PyYAML
        if strict:
            raise EngineError("user_config_invalid", f"cannot read {path}: {exc}", EXIT_COMPAT) from exc
        return {}
    if not isinstance(data, dict) or not isinstance(data.get("repos", {}) or {}, dict):
        if strict:
            raise EngineError("user_config_invalid", f"{path} must hold a mapping with 'repos' as a mapping",
                              EXIT_COMPAT)
        return {}
    data["repos"] = dict(data.get("repos") or {})
    return data


def save(project_id: str, data: dict[str, Any]) -> Path:
    from .config import dump_yaml

    path = user_config_path(project_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    ordered = {k: data[k] for k in KEYS if data.get(k) not in (None, {}, "")}
    ordered.update({k: v for k, v in data.items() if k not in KEYS})  # keep keys we do not know
    atomic_write_text(path, dump_yaml(ordered))
    return path


def stored_actor(project_id: str | None) -> str | None:
    if not project_id:
        return None
    try:
        actor = load(project_id).get("actor")
    except ValueError:
        return None
    return str(actor) if actor else None
