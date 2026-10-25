"""``repos clone <name>``: clone outside the synced tree and record the path for this member (006/R6).

Network access happens only here, through ``git clone`` with the member's own credentials (003/R14). An existing
correct clone is recorded without cloning; failures are reported, never retried.
"""
from __future__ import annotations

import argparse
from pathlib import Path

from .. import repos, userconfig
from ..command import Command, Context
from ..jsonout import EXIT_PRECONDITION, EngineError, Result


def _args(p: argparse.ArgumentParser) -> None:
    p.add_argument("name", help="repository name from project.yaml → repos")
    p.add_argument("--dest", metavar="PATH", help="local folder for the clone (default: the recorded path, "
                   "else ~/code/<slug>/<name>)")


def run(ctx: Context, args: argparse.Namespace) -> Result:
    project = ctx.require_project()
    config = project.config()
    repo = repos.find_declared(config, args.name)
    pid = project.project_id
    user = userconfig.load(pid)
    if args.dest:
        dest = Path(args.dest).expanduser()
    elif (user.get("repos") or {}).get(args.name):
        dest = Path(user["repos"][args.name])
    else:
        dest = repos.default_path(str(config.get("slug") or pid), args.name)
    dest = Path(dest).absolute()
    repos.refuse_inside_tree(dest, project.root)

    info = repos.inspect(dest, repo["url"])
    data = {"name": args.name, "url": repo["url"], "path": str(dest), "config": str(userconfig.user_config_path(pid))}
    if info["is_git"] and info["remote_matches"]:
        action = "adopt"
    elif info["exists"] and any(dest.iterdir()):
        why = "a git clone of a different remote" if info["is_git"] else "not empty and not a git clone"
        raise EngineError("clone_target_exists", f"{dest} exists and is {why}; choose another --dest "
                          "(nothing was changed).", EXIT_PRECONDITION)
    else:
        action = "clone"
    data["action"] = action
    if ctx.dry_run:
        return Result(data={**data, "dry_run": True}, text=f"would {action} {args.name} at {dest}")
    if action == "clone":
        repos.run_clone(repo["url"], dest, repo.get("default_branch"))
    unchanged = (user.get("repos") or {}).get(args.name) == str(dest) and user.get("metarepo_root") == str(project.root)
    if not unchanged:
        repos.record(pid, project.root, args.name, dest)
    result = Result(data=data, log_event=False,
                    text=f"{'cloned' if action == 'clone' else 'recorded existing clone'}: {dest}")
    return result


COMMAND = Command(name="repos clone", help="Clone a repository outside the synced tree", run=run,
                  add_arguments=_args, mutating=True, owner="006")
