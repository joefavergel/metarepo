"""``repos path <name>``: this member's recorded clone path (006/AC3)."""
from __future__ import annotations

import argparse
from pathlib import Path

from .. import repos, userconfig
from ..command import Command, Context
from ..jsonout import EXIT_PRECONDITION, EngineError, Result


def _args(p: argparse.ArgumentParser) -> None:
    p.add_argument("name", help="repository name from project.yaml → repos")


def run(ctx: Context, args: argparse.Namespace) -> Result:
    project = ctx.require_project()
    repos.find_declared(project.config(), args.name)
    recorded = (userconfig.load(project.project_id).get("repos") or {}).get(args.name)
    if not recorded:
        raise EngineError(
            "repo_not_recorded",
            f"no local clone of {args.name!r} is recorded for this user. Run: cli.py repos clone {args.name} "
            "(or /join-metarepo).", EXIT_PRECONDITION)
    if not Path(recorded).is_dir():
        raise EngineError(
            "repo_path_missing",
            f"the recorded path for {args.name!r} does not exist: {recorded}. Run: cli.py repos clone {args.name} "
            "--dest <path> to clone it again or point to the right folder.", EXIT_PRECONDITION)
    return Result(data={"name": args.name, "path": recorded}, text=recorded)


COMMAND = Command(name="repos path", help="Print this member's local clone path of a repository", run=run,
                  add_arguments=_args, owner="006")
