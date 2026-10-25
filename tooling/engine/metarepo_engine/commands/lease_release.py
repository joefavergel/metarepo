"""``lease release <area> --lease <nonce>``: release a held lease (004/R6)."""
from __future__ import annotations

import argparse

from .. import leases
from ..command import Command, Context
from ..jsonout import Result


def _args(p: argparse.ArgumentParser) -> None:
    p.add_argument("area", choices=leases.AREAS)
    p.add_argument("--lease", required=True, help="the nonce printed by `lease acquire`")


def run(ctx: Context, args: argparse.Namespace) -> Result:
    project = ctx.require_project()
    current = leases._read(leases.LeaseEnv(), project.root, args.area)
    if current is not None and current.nonce == args.lease and current.released:
        return Result(data={"area": args.area, "released": True, "already": True}, log_event=False)
    handle = leases.attach(project.root, args.area, args.lease)
    if ctx.dry_run:
        return Result(data={"dry_run": True, "area": args.area, "would_release": True})
    released = leases.release(project.root, handle.lease)
    return Result(data={"area": args.area, "released": True, "released_at": released.released_at},
                  targets=[leases.lease_rel(args.area)], log_event=False)


COMMAND = Command(
    name="lease release",
    help="Release a held lease (the lease file is kept and reused)",
    run=run,
    add_arguments=_args,
    mutating=True,
    writes_project=True,
    owner="004",
)
