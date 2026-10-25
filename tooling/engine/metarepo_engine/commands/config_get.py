"""``config get <key>``: read a value from project.yaml with defaults applied (002/R14)."""
from __future__ import annotations

import argparse
import json

from ..command import Command, Context
from ..config import effective, get_path
from ..jsonout import EXIT_USAGE, EngineError, Result


def _args(p: argparse.ArgumentParser) -> None:
    p.add_argument("key", help="dotted key, e.g. governance.lease_ttl_minutes or repos.0.name")
    p.add_argument("--no-defaults", action="store_true", help="read the file as written, without documented defaults")


def run(ctx: Context, args: argparse.Namespace) -> Result:
    project = ctx.require_project()
    config = project.config()
    source = config if args.no_defaults else effective(config)
    try:
        value = get_path(source, args.key)
    except KeyError:
        raise EngineError("key_not_found", f"no such key: {args.key}", EXIT_USAGE) from None
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
    return Result(data={"key": args.key, "value": value}, text=text)


COMMAND = Command(
    name="config get",
    help="Read a key from project.yaml (documented defaults applied)",
    run=run,
    add_arguments=_args,
    owner="002",
)
