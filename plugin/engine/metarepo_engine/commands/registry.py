"""``registry [--filter k=v] [--format json|csv] [--write]``: derived view over extractions (002/ADR-004)."""
from __future__ import annotations

import argparse
import json

from .. import registry as reg
from ..command import Command, Context
from ..jsonout import EXIT_USAGE, EngineError, Result


def _args(p: argparse.ArgumentParser) -> None:
    p.add_argument("--filter", action="append", default=[], metavar="KEY=VALUE[,VALUE]",
                   help="keep rows where KEY is one of the values (repeatable; keys are ANDed)")
    p.add_argument("--format", choices=("json", "csv"), default="json", help="human output format (default json)")
    p.add_argument("--write", action="store_true",
                   help="also write the view to the local cache (registry.json and registry.csv); never to the project")


def run(ctx: Context, args: argparse.Namespace) -> Result:
    project = ctx.require_project()
    try:
        filters = reg.parse_filters(args.filter)
    except reg.FilterError as exc:
        raise EngineError("bad_filter", str(exc), EXIT_USAGE) from None
    view = reg.build(project.root)
    rows = reg.apply_filters(view.rows, filters)
    result = Result()
    for path, message in view.warnings:
        result.warn("extraction_unreadable", f"{path}: {message}", path)
    data = {"count": len(rows), "sources": rows, "duplicates": view.duplicates(), "format": args.format}
    if args.write:
        pid = project.project_id
        if not pid:
            raise EngineError("config_invalid", "project.yaml has no id; cannot locate the local cache", 4)
        data["cache"] = [str(p) for p in reg.write_cache(pid, rows)]
    result.data = data
    if args.format == "csv":
        data["csv"] = reg.to_csv(rows)
        result.text = data["csv"].rstrip("\n")
    else:
        result.text = json.dumps(rows, ensure_ascii=False, indent=2)
    return result


COMMAND = Command(
    name="registry",
    help="Derived registry view over extraction frontmatter (never edited by hand)",
    run=run,
    add_arguments=_args,
    owner="003",
)
