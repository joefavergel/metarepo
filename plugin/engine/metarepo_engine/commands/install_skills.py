"""``install-skills --target <dir>`` (001/R6)."""
from __future__ import annotations

import argparse

from ..command import Command, Context
from ..install import InstallError, install_skills
from ..jsonout import EXIT_PRECONDITION, Result, error


def _args(p: argparse.ArgumentParser) -> None:
    p.add_argument("--target", required=True, help="harness skills directory to install into")
    p.add_argument("--force", action="store_true", help="overwrite skills that exist with different content")


def run(ctx: Context, args: argparse.Namespace) -> Result:
    try:
        plans = install_skills(args.target, force=args.force, dry_run=ctx.dry_run)
    except InstallError as exc:
        return Result(errors=[error(exc.code, exc.message)], exit_code=EXIT_PRECONDITION)
    skills = [{"name": p.name, "action": p.action, "files": sorted(p.files)} for p in plans]
    lines = [f"{p.name}: {p.action}" for p in plans]
    prefix = "would install" if ctx.dry_run else "installed"
    return Result(
        data={"target": str(args.target), "dry_run": ctx.dry_run, "skills": skills},
        text=f"{prefix} {len(plans)} skills into {args.target}\n" + "\n".join(lines),
    )


COMMAND = Command(
    name="install-skills",
    help="Install the portable skills into a harness skills directory (never writes into a project)",
    run=run,
    add_arguments=_args,
    mutating=True,
    needs_project=False,
    needs_yaml=False,
    owner="001",
)
