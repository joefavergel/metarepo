"""metarepo engine: deterministic operations for a knowledge metarepository."""
from __future__ import annotations

from pathlib import Path


def _read_version() -> str:
    # Layout-independent: tooling/ (canonical), plugin/ (build copy) and the portable bundle all
    # keep VERSION next to the engine/ directory. See tooling/engine/CONTRIBUTING-commands.md.
    here = Path(__file__).resolve()
    for candidate in (here.parents[2] / "VERSION", here.parents[1] / "VERSION"):
        try:
            text = candidate.read_text(encoding="utf-8").strip()
        except OSError:
            continue
        if text:
            return text
    return "0.0.0+unknown"


__version__ = _read_version()
