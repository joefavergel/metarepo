"""``status``: root, versions, compatibility, gates, dependencies, leases (003/R16).

Works without PyYAML (degraded: project fields are omitted and the missing dependency is reported) and
outside a metarepository (reports ``in_metarepo: false`` with a warning). Always read-only.
"""
from __future__ import annotations

import argparse
import importlib.util
import platform
import sys
from pathlib import Path
from typing import Any

from .. import compat
from ..command import Command, Context
from ..config import INSTALL_HINT
from ..jsonout import EXIT_PRECONDITION, EngineError, Result, warning
from ..root import Project, find_root, is_metarepo_root

MIN_PYTHON = (3, 10)

# Optional extractors, detected without importing them (013/008 may extend this table).
EXTRACTOR_PROBES: dict[str, dict[str, str]] = {
    "markitdown": {"module": "markitdown", "install": "python3 -m pip install markitdown"},
    "docling": {"module": "docling", "install": "python3 -m pip install docling"},
    "pymupdf": {"module": "fitz", "install": "python3 -m pip install pymupdf"},
    "pymupdf4llm": {"module": "pymupdf4llm", "install": "python3 -m pip install pymupdf4llm"},
}


def _module_available(name: str) -> bool:
    try:
        return importlib.util.find_spec(name) is not None
    except (ImportError, ValueError):
        return False


def _pyyaml() -> dict[str, Any]:
    try:
        import yaml  # type: ignore
    except ImportError:
        return {"available": False, "version": None, "install": "python3 -m pip install pyyaml"}
    return {"available": True, "version": getattr(yaml, "__version__", None), "install": None}


def _lease_summary(root: Path) -> dict[str, Any]:
    locks = root / "_system" / "locks"
    files = sorted(p.name for p in locks.iterdir() if p.is_file() and not p.name.startswith(".")) if locks.is_dir() else []
    return {"count": len(files), "files": files, "note": "lease contents are interpreted from spec 004 on"}


def run(ctx: Context, args: argparse.Namespace) -> Result:
    result = Result()
    explicit = getattr(args, "root", None)
    root: Path | None
    if explicit:
        root = Path(explicit).expanduser().resolve()
        if not is_metarepo_root(root):
            raise EngineError("not_a_metarepo", f"{root} is not a metarepository root", EXIT_PRECONDITION)
    else:
        root = find_root(Path.cwd())
    yaml_info = _pyyaml()
    python_ok = sys.version_info[:2] >= MIN_PYTHON
    data: dict[str, Any] = {
        "in_metarepo": root is not None,
        "root": root.as_posix() if root else None,
        "toolkit": {
            "version": ctx.version,
            "engine": Path(__file__).resolve().parents[2].joinpath("cli.py").as_posix(),
            "python": platform.python_version(),
            "python_ok": python_ok,
            "platform": sys.platform,
        },
        "dependencies": {
            "pyyaml": yaml_info,
            "extractors": {name: {"available": _module_available(p["module"]), "install": p["install"]}
                           for name, p in EXTRACTOR_PROBES.items()},
        },
        "project": None,
        "compatibility": None,
        "gates": None,
        "leases": None,
    }
    if not python_ok:
        result.warn("python_too_old", f"Python >= {MIN_PYTHON[0]}.{MIN_PYTHON[1]} is required; found "
                    f"{platform.python_version()}")
    if not yaml_info["available"]:
        result.warn("missing_dependency", INSTALL_HINT)
    if root is None:
        result.warn("not_a_metarepo", "no metarepository found here or in a parent folder; use --root <path>")
    elif yaml_info["available"]:
        project = Project(root)
        try:
            config = project.config()
            data["project"] = {"id": config.get("id"), "name": config.get("name"), "slug": config.get("slug")}
            requires = (config.get("toolkit") or {}).get("requires")
            if requires:
                verdict = compat.check(str(requires), ctx.version)
                data["compatibility"] = {"requires": str(requires), "installed": ctx.version,
                                         "status": verdict.status, "message": verdict.message}
                if not verdict.ok:
                    result.warnings.append(warning("toolkit_incompatible", verdict.message))
            else:
                result.warn("compat_unchecked", "project.yaml has no toolkit.requires")
        except (EngineError, compat.CompatError) as exc:
            result.warn("config_invalid", f"project.yaml could not be read: {exc}", "project.yaml")
        try:
            state = project.state()
            data["gates"] = {"metarepo_configured": bool((state.get("metarepo") or {}).get("configured")),
                             "wiki_configured": bool((state.get("wiki") or {}).get("configured"))}
        except EngineError as exc:
            result.warn("state_invalid", exc.message, "_system/state.json")
        data["leases"] = _lease_summary(root)
    result.data = data
    lines = [f"toolkit {ctx.version}, python {platform.python_version()}",
             f"metarepository: {data['root'] or 'none'}"]
    if data["compatibility"]:
        lines.append(f"compatibility: {data['compatibility']['status']} (requires {data['compatibility']['requires']})")
    if data["gates"]:
        lines.append(f"gates: metarepo={data['gates']['metarepo_configured']} wiki={data['gates']['wiki_configured']}")
    result.text = "\n".join(lines)
    return result


COMMAND = Command(
    name="status",
    help="Root, versions, compatibility with toolkit.requires, gates, dependencies, leases",
    run=run,
    needs_project=False,
    needs_yaml=False,
    gate=False,
    owner="003",
)
