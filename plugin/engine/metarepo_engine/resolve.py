"""Engine resolution rule (001/R5) and Python interpreter discovery (001 plan section 2).

Skills carry the rule as prose in ``references/engine.md``; this module is the executable reference
used by tests, hooks and tooling. Order:

1. ``METAREPO_ENGINE`` (path to ``cli.py`` or an executable);
2. the engine bundled next to the skill's installation (the path recorded in the skill's
   ``references/engine.md``, else ``<skill>/../../engine/cli.py``);
3. a ``metarepo`` executable on ``PATH``.
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Mapping, Sequence

MARKER_START = "<!-- engine-path:start -->"
MARKER_END = "<!-- engine-path:end -->"
UNRESOLVED = "unresolved"
_PATH_LINE = re.compile(r"^path:\s*(.+?)\s*$", re.MULTILINE)

MIN_PYTHON = (3, 10)
PYTHON_CANDIDATES: tuple[tuple[str, ...], ...] = (("python3",), ("python",), ("py", "-3"))

INSTALL_INSTRUCTIONS = (
    "The metarepo engine was not found. Set METAREPO_ENGINE to the path of cli.py, install the plugin "
    "or the portable bundle (see INSTALL.md), or put a `metarepo` executable on PATH. "
    "Python >= 3.10 with PyYAML is required: python3 -m pip install pyyaml (Windows: py -3 -m pip install pyyaml)."
)


@dataclass(frozen=True)
class EngineLocation:
    source: str  # "env" | "bundled" | "path"
    path: str  # cli.py, or an executable


def recorded_engine_path(skill_dir: Path) -> str | None:
    """The path stored in the marker block of ``references/engine.md`` (None when unresolved)."""
    md = skill_dir / "references" / "engine.md"
    try:
        text = md.read_text(encoding="utf-8")
    except OSError:
        return None
    start, end = text.find(MARKER_START), text.find(MARKER_END)
    if start < 0 or end < start:
        return None
    m = _PATH_LINE.search(text[start:end])
    if not m or m.group(1) == UNRESOLVED:
        return None
    return m.group(1)


def find_engine(
    skill_dir: Path | None = None,
    env: Mapping[str, str] | None = None,
    which: Callable[[str], str | None] = shutil.which,
) -> EngineLocation | None:
    env = os.environ if env is None else env
    override = env.get("METAREPO_ENGINE")
    if override:
        if Path(override).is_file():
            return EngineLocation("env", str(Path(override)))
        found = which(override)
        if found:
            return EngineLocation("env", found)
        # An explicit but broken override is an error the caller should surface, not skip silently.
        return None
    if skill_dir is not None:
        skill_dir = Path(skill_dir)
        recorded = recorded_engine_path(skill_dir)
        candidates = []
        if recorded:
            p = Path(recorded)
            candidates.append(p if p.is_absolute() else (skill_dir / p))
        candidates.append(skill_dir.parent.parent / "engine" / "cli.py")
        for cand in candidates:
            if cand.is_file():
                return EngineLocation("bundled", str(cand.resolve()))
    exe = which("metarepo")
    if exe:
        return EngineLocation("path", exe)
    return None


def _probe_python(cmd: Sequence[str]) -> tuple[int, int] | None:
    try:
        out = subprocess.run(
            [*cmd, "-c", "import sys;print('%d.%d' % sys.version_info[:2])"],
            capture_output=True, text=True, timeout=15, check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if out.returncode != 0:
        return None
    try:
        major, minor = out.stdout.strip().split(".")
        return int(major), int(minor)
    except ValueError:
        return None


def find_python(
    which: Callable[[str], str | None] = shutil.which,
    probe: Callable[[Sequence[str]], tuple[int, int] | None] = _probe_python,
) -> list[str] | None:
    """First of ``python3``, ``python``, ``py -3`` that reports >= 3.10."""
    for cand in PYTHON_CANDIDATES:
        if which(cand[0]) is None:
            continue
        version = probe(cand)
        if version is not None and version >= MIN_PYTHON:
            return list(cand)
    return None


def command_for(location: EngineLocation, python: Sequence[str] | None = None) -> list[str]:
    """Argv prefix that runs the engine. ``cli.py`` needs an interpreter; executables run as-is."""
    if location.path.endswith(".py"):
        interpreter = list(python) if python else [sys.executable]
        return [*interpreter, location.path]
    return [location.path]
