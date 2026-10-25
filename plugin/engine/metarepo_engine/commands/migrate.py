"""``migrate [--dry-run] [--yes]`` (001/R8, 003/T2.3)."""
from __future__ import annotations

import argparse

from ..command import Command, Context
from ..jsonout import EXIT_PRECONDITION, Result, error
from ..migrate import run_migrations


def _args(p: argparse.ArgumentParser) -> None:
    p.add_argument("--yes", action="store_true",
                   help="confirm and apply (without it, the dry run is shown and nothing changes)")


def run(ctx: Context, args: argparse.Namespace) -> Result:
    project = ctx.require_project()
    report = run_migrations(project, installed=ctx.version, dry_run=ctx.dry_run, confirmed=args.yes)
    data = {
        "status": report.status,
        "installed": report.installed,
        "requires": report.requires,
        "new_requires": report.new_requires,
        "steps": report.steps,
        "backup_dir": report.backup_dir,
        "changed": report.changed,
    }
    result = Result(data=data, targets=report.changed)
    if report.status == "up-to-date":
        result.log_event = False
        result.text = "project is already compatible with this toolkit; nothing to migrate"
    elif report.status == "planned":
        result.log_event = False
        if not ctx.dry_run:
            result.errors.append(error("confirmation_required",
                                       "dry run shown; re-run with --yes to back up and apply the migration"))
            result.exit_code = EXIT_PRECONDITION
        result.text = "migration plan:\n" + "\n".join(
            f"  {s['from']} -> {s['to']} ({s['file']}): {len(s['changes'])} file(s)" for s in report.steps)
    else:
        result.text = f"migrated; backups in {report.backup_dir}; requires is now {report.new_requires}"
    return result


COMMAND = Command(
    name="migrate",
    help="Run project migrations for a new compatibility line (dry run first, confirmation required)",
    run=run,
    add_arguments=_args,
    mutating=True,
    writes_project=True,
    gate=False,
    owner="001",
)
