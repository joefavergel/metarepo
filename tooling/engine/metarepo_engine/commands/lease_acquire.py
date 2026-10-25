"""``lease acquire <area>``: take a write lease (004/R3, R4, R5, R7)."""
from __future__ import annotations

import argparse

from .. import leases
from ..command import Command, Context
from ..events import resolve_actor
from ..jsonout import Result


def _args(p: argparse.ArgumentParser) -> None:
    p.add_argument("area", choices=leases.AREAS)
    p.add_argument("--operation", default="manual", help="what the holder is about to do (shown to others)")
    p.add_argument("--ttl-minutes", type=float, default=None, help="override governance.lease_ttl_minutes")
    p.add_argument("--session", default=None, help="session id (default: METAREPO_SESSION or a fresh one)")


def run(ctx: Context, args: argparse.Namespace) -> Result:
    project = ctx.require_project()
    actor = resolve_actor(ctx.actor)
    gov = leases.governance(project.root)
    if ctx.dry_run:
        leases.check_owner(gov, args.area, actor)
        state = leases.status(project.root, args.area)
        if state["state"] == "held":
            h = state["lease"]["holder"]
            raise leases.LeaseHeld(args.area, None, f"'{args.area}' is held by {h['actor']}@{h['host']} until "
                                   f"{state['lease']['expires_at']}")
        return Result(data={"dry_run": True, "area": args.area, "would_acquire": True,
                            "path": leases.lease_rel(args.area)})
    got = leases.acquire(project.root, args.area, actor, args.operation, session=args.session,
                         ttl=args.ttl_minutes, gov=gov)
    data = {"area": args.area, "path": leases.lease_rel(args.area), "lease": got.lease.to_dict(),
            "took_over": got.took_over}
    result = Result(data=data, targets=[leases.lease_rel(args.area)], log_event=False)  # the library logs (R6)
    result.text = got.lease.nonce
    return result


COMMAND = Command(
    name="lease acquire",
    help="Acquire a write lease on an area (wiki, intake, config); prints the nonce to pass as --lease",
    run=run,
    add_arguments=_args,
    mutating=True,
    writes_project=True,
    owner="004",
)
