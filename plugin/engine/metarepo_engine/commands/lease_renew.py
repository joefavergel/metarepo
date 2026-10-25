"""``lease renew <area> --lease <nonce>``: extend a held lease (004/R5)."""
from __future__ import annotations

import argparse

from .. import leases
from ..command import Command, Context
from ..jsonout import Result


def _args(p: argparse.ArgumentParser) -> None:
    p.add_argument("area", choices=leases.AREAS)
    p.add_argument("--lease", required=True, help="the nonce printed by `lease acquire`")
    p.add_argument("--ttl-minutes", type=float, default=None, help="new TTL (default: the lease's own)")


def run(ctx: Context, args: argparse.Namespace) -> Result:
    project = ctx.require_project()
    handle = leases.attach(project.root, args.area, args.lease)
    if ctx.dry_run:
        return Result(data={"dry_run": True, "area": args.area, "lease_valid": True})
    renewed = leases.renew(project.root, handle.lease, ttl=args.ttl_minutes)
    return Result(data={"area": args.area, "lease": renewed.to_dict()}, log_event=False,
                  text=f"renewed until {renewed.expires_at}")


COMMAND = Command(
    name="lease renew",
    help="Renew a held lease (verifies the nonce first)",
    run=run,
    add_arguments=_args,
    mutating=True,
    writes_project=True,
    owner="004",
)
