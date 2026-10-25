"""``extractors``: list adapters, availability and install hints (008, P13). Read-only; installs nothing."""
from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

from ..command import Command, Context
from ..config import effective
from ..extract.registry import all_adapters
from ..jsonout import EngineError, Result
from ..root import Project, find_root, is_metarepo_root


def _extraction_config(root_arg: str | None, result: Result) -> tuple[dict[str, Any], bool]:
    try:
        root = Path(root_arg).expanduser().resolve() if root_arg else find_root(Path.cwd())
        if root is None or not is_metarepo_root(root):
            return {}, False
        return effective(Project(root).config())["extraction"], True
    except (EngineError, OSError) as exc:
        result.warn("config_invalid", f"project.yaml could not be read; showing built-in adapters only: {exc}")
        return {}, False


def run(ctx: Context, args: argparse.Namespace) -> Result:
    result = Result()
    cfg, in_project = _extraction_config(getattr(args, "root", None), result)
    allow_cloud = bool(cfg.get("allow_cloud"))
    rows = []
    for a in all_adapters(cfg):
        available = a.available()
        usable = available and (a.mode != "cloud" or allow_cloud)
        rows.append({"name": a.name, "kind": a.kind, "mode": a.mode, "available": available, "usable": usable,
                     "version": a.version() if available else None, "media_types": list(a.media_summary),
                     "install": None if available else a.install_hint,
                     "note": "cloud extractor disabled (extraction.allow_cloud is false)" if available and not usable
                     else None})
    rows.append({"name": "agent", "kind": "skill", "mode": "agent", "available": True, "usable": True,
                 "version": None, "media_types": ["image/*", "application/pdf", "audio/*"], "install": None,
                 "note": "the extract skill writes the body and records it with `extract-record`"})
    result.data = {"in_metarepo": in_project, "allow_cloud": allow_cloud, "extractors": rows,
                   "preferences": cfg.get("preferences") or {}}
    result.text = "\n".join(f"{r['name']:<12} {r['mode']:<6} {'usable' if r['usable'] else 'unavailable'}"
                            + (f"  ({r['install']})" if r["install"] else "") for r in rows)
    return result


COMMAND = Command(
    name="extractors",
    help="List extractor adapters, availability and install hints",
    run=run,
    needs_project=False,
    needs_yaml=False,
    owner="008",
)
