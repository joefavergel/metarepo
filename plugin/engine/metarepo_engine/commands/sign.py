"""``sign <page> --role author|reviewer|auditor [--stable]``: record a governance sign-off (010/R17).

Appends a signed, timestamped event to the page (``generated`` / ``verified`` / ``audited``) and a line to
``30_wiki/log.md`` under the ``wiki`` lease. Never gating (010/ADR-002): overlapping roles produce a warning,
not an error. The identity is the claimed ``--actor`` (an attestation, not a signature).
"""
from __future__ import annotations

import argparse

from .. import frontmatter, leases
from ..command import Command, Context
from ..events import resolve_actor
from ..jsonout import Result
from ..wiki import gate, sign


def _args(p: argparse.ArgumentParser) -> None:
    p.add_argument("page", help="page path, relative to the metarepository root or to 30_wiki/")
    p.add_argument("--role", required=True, choices=tuple(sign.ROLE_KEYS), help="sign-off role")
    p.add_argument("--stable", action="store_true", help="also mark the page status: stable")
    p.add_argument("--assisted-by", default=None, metavar="HARNESS/MODEL", help="record the assisting agent")
    p.add_argument("--lease", default=None, help="nonce of a wiki lease already held (default: acquire one)")


def run(ctx: Context, args: argparse.Namespace) -> Result:
    project = ctx.require_project()
    root = project.root
    if not gate.feed_allowed(project.state()):
        gate.require_configured(project)
    actor = resolve_actor(ctx.actor, project.project_id)
    path, rel = sign.resolve_page(root, args.page)
    page = frontmatter.read(path)
    sp = sign.plan(page.front, args.role, actor, stable=args.stable, assisted_by=args.assisted_by)
    result = Result(data={"page": rel, "role": args.role, "key": sp.key, "entry": sp.entry,
                          "status": sp.updates.get("status", page.front.get("status")), "dry_run": ctx.dry_run})
    for code, message in sp.warnings:
        result.warn(code, message, rel)
    if ctx.dry_run:
        result.log_event = False
        result.text = f"would record {args.role} sign-off by {sp.entry['by']} on {rel}"
        return result
    if args.lease:
        sign.apply(root, leases.attach(root, "wiki", args.lease), path, rel, sp, args.role)
    else:
        with leases.held(root, "wiki", actor, "sign") as handle:
            sign.apply(root, handle, path, rel, sp, args.role)
    result.targets = [rel, f"{sign.bundle.WIKI_DIR}/log.md"]
    result.event_extra = {"role": args.role, "by": sp.entry["by"]}
    result.text = f"{args.role} sign-off recorded by {sp.entry['by']} on {rel}"
    return result


COMMAND = Command(
    name="sign",
    help="Record a governance sign-off (author, reviewer, auditor) on a wiki page; never gating",
    run=run,
    add_arguments=_args,
    mutating=True,
    writes_project=True,
    owner="010",
)
