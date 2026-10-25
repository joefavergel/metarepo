"""``lease status [area]``: show who holds which area (004/R2, R3). Never prints nonces."""
from __future__ import annotations

import argparse

from .. import leases
from ..command import Command, Context
from ..jsonout import Result


def _args(p: argparse.ArgumentParser) -> None:
    p.add_argument("area", nargs="?", choices=leases.AREAS, help="one area (default: all)")


def run(ctx: Context, args: argparse.Namespace) -> Result:
    project = ctx.require_project()
    areas = [args.area] if args.area else list(leases.AREAS)
    rows = [leases.status(project.root, a) for a in areas]
    lines = []
    for r in rows:
        if r["state"] == "free":
            lines.append(f"{r['area']}: free")
        else:
            h = r["lease"]["holder"]
            lines.append(f"{r['area']}: {r['state']} ({h['actor']}@{h['host']}, {r['lease']['operation']}, "
                         f"expires {r['lease']['expires_at']})")
    return Result(data={"leases": rows}, text="\n".join(lines))


COMMAND = Command(
    name="lease status",
    help="Show the state of the write leases (wiki, intake, config)",
    run=run,
    add_arguments=_args,
    owner="004",
)
