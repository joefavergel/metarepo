"""``config user``: read or update this member's per-user configuration outside the tree (006/R4, R5).

``config user`` shows it; ``--set actor=<team-id>`` / ``--set harness=<text>`` / ``--unset harness`` change it.
Nothing inside the metarepository is written. Adding a member to ``project.yaml → team`` is not done here.
"""
from __future__ import annotations

import argparse
import re

from .. import userconfig
from ..command import Command, Context
from ..jsonout import EXIT_USAGE, EngineError, Result

SETTABLE = ("actor", "harness")
ID_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")  # same shape as team[].id in project.yaml


def _args(p: argparse.ArgumentParser) -> None:
    p.add_argument("--set", dest="sets", action="append", default=[], metavar="KEY=VALUE",
                   help="set actor or harness (repeatable)")
    p.add_argument("--unset", dest="unsets", action="append", default=[], metavar="KEY",
                   help="remove actor or harness (repeatable)")


def run(ctx: Context, args: argparse.Namespace) -> Result:
    project = ctx.require_project()
    pid = project.project_id
    current = userconfig.load(pid, strict=True)
    new = {**current, "repos": dict(current.get("repos") or {})}
    for item in args.sets:
        key, sep, value = item.partition("=")
        if not sep or key not in SETTABLE or not value.strip():
            raise EngineError("usage", f"--set expects actor=<id> or harness=<text>, got {item!r}", EXIT_USAGE)
        if key == "actor" and not ID_RE.match(value):
            raise EngineError("usage", "actor must be a team id: lowercase letters, digits and hyphens", EXIT_USAGE)
        new[key] = value.strip()
    for key in args.unsets:
        if key not in SETTABLE:
            raise EngineError("usage", f"--unset accepts {', '.join(SETTABLE)}, got {key!r}", EXIT_USAGE)
        new.pop(key, None)
    changing = bool(args.sets or args.unsets)
    if changing:
        new["metarepo_root"] = str(project.root)
    path = userconfig.user_config_path(pid)
    result = Result(data={"path": str(path), "exists": path.exists(), "config": new if (changing or current) else {}},
                    log_event=False)
    team = {t.get("id") for t in project.config().get("team") or [] if isinstance(t, dict)}
    actor = new.get("actor")
    result.data["actor_in_team"] = bool(actor and actor in team)
    if actor and actor not in team:
        result.warn("actor_not_in_team", f"{actor!r} is not listed in project.yaml → team; it is used as a claimed "
                    "identity only. Ask the project owner to add the member under the config lease.")
    if changing and new != current:
        result.data["changed"] = True
        if ctx.dry_run:
            result.data["dry_run"] = True
        else:
            userconfig.save(pid, new)
            result.data["exists"] = True
    else:
        result.data["changed"] = False
    return result


COMMAND = Command(name="config user", help="Show or update this member's per-user configuration (outside the tree)",
                  run=run, add_arguments=_args, mutating=True, owner="006")
