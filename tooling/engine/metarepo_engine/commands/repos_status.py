"""``repos status``: state of this member's local clones (006)."""
from __future__ import annotations

import argparse

from .. import repos, userconfig
from ..command import Command, Context
from ..jsonout import Result


def _args(p: argparse.ArgumentParser) -> None:
    p.add_argument("name", nargs="?", help="limit to one repository")


def run(ctx: Context, args: argparse.Namespace) -> Result:
    project = ctx.require_project()
    config = project.config()
    if args.name:
        repos.find_declared(config, args.name)
    user = userconfig.load(project.project_id)
    rows = repos.status(project.root, config, user, [args.name] if args.name else None)
    result = Result(data={"repos": rows})
    for r in rows:
        if r["state"] == "inside-tree":
            result.warn("repo_inside_tree", f"{r['name']}: recorded path {r['path']} is inside the synced tree")
    result.text = "\n".join(f"{r['name']}\t{r['state']}\t{r['path'] or '-'}\t{r['branch'] or '-'}" for r in rows) \
        or "no repositories declared in project.yaml"
    return result


COMMAND = Command(name="repos status", help="Show the state of local clones", run=run, add_arguments=_args,
                  owner="006")
