"""``repos list``: repositories declared in project.yaml with this member's recorded clone path (006)."""
from __future__ import annotations

import argparse

from .. import repos, userconfig
from ..command import Command, Context
from ..jsonout import Result


def run(ctx: Context, args: argparse.Namespace) -> Result:
    project = ctx.require_project()
    user = userconfig.load(project.project_id) if project.project_id else {}
    recorded = user.get("repos") or {}
    rows = [{"name": r["name"], "url": r.get("url"), "default_branch": r.get("default_branch"),
             "role": r.get("role"), "path": recorded.get(r["name"])} for r in repos.declared(project.config())]
    text = "\n".join(f"{r['name']}\t{r['url']}\t{r['path'] or '(not cloned on this machine)'}" for r in rows) \
        or "no repositories declared in project.yaml"
    return Result(data={"repos": rows}, text=text)


COMMAND = Command(name="repos list", help="List code repositories referenced by the project", run=run, owner="006")
