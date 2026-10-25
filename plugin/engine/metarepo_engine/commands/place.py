"""``place``: the engine side of intake's writes (007/R3, R11-R14, R16, R17).

One command, several modes (the 003 command table is fixed, so intake's inbox-side writes live here):

* ``place <staged> --category C [--triage JSON] ...``  move a staged or inbox file into ``10_sources/``,
  write its placement stub and run the full extraction (the default mode)
* ``--discard``          remove junk / placed copies / duplicates / emptied folders from the inbox
* ``--duplicate-of ID``  record the file's name as an alias of ID, then remove the duplicate (R3)
* ``--reject``           apply ``intake.on_reject`` to a rejected item (R14)
* ``--retriage``         change a placed source's decision (R17); ``target`` is the source id
* ``--proposal FILE``    save a headless proposal under ``personal/<actor>/intake-proposals/`` (R11)
* ``--completed JSON``   log the ``intake.completed`` event (R16)

Writes to the intake area need the lease: ``lease acquire intake`` first, then ``--lease <nonce>``.
"""
from __future__ import annotations

import argparse
import json
from typing import Any

from .. import placement
from ..command import Command, Context
from ..events import resolve_actor
from ..jsonout import EXIT_USAGE, EngineError, Result

MODES = ("discard", "duplicate_of", "reject", "retriage", "proposal", "completed")


def _args(p: argparse.ArgumentParser) -> None:
    p.add_argument("target", nargs="?", help="staged file, inbox path (00_inbox/...), or source id with --retriage")
    p.add_argument("--category", help="folder under 10_sources/ (kebab-case; approved by the operator)")
    p.add_argument("--name", help="placed file name (default: slug of the original name)")
    p.add_argument("--parent", metavar="ID", help="bundle container id (bundle.parent_id)")
    p.add_argument("--member-path", metavar="PATH", help="path of the file inside its bundle (bundle.member_path)")
    p.add_argument("--group", metavar="SLUG", help="bundle.group label for members of a folder bundle")
    p.add_argument("--bundle-slug", metavar="SLUG", help="folder below the category that keeps the bundle together")
    p.add_argument("--triage", metavar="JSON", help='{"decision": "wiki|reference|defer", "reason": "...", "confidence": "high"}')
    p.add_argument("--supersedes", action="append", default=[], metavar="ID", help="source id this one supersedes")
    p.add_argument("--lease", metavar="NONCE", help="nonce printed by `lease acquire intake`")
    p.add_argument("--confirm-policy", action="store_true",
                   help="the operator confirmed placing a file the policy gate matched (never allowed as 'wiki')")
    p.add_argument("--container", action="store_true",
                   help="place an archive in 10_sources/_bundles/ (intake.archive_bundles)")
    p.add_argument("--members", metavar="FILE", help="JSON list of {id, member_path, decision} for a container")
    p.add_argument("--on-reject", choices=("leave-in-inbox", "move-to-shared", "delete"), default=None)
    p.add_argument("--confirm", metavar="SHA256", help="full SHA-256 confirming the deletion of one file")
    p.add_argument("--include-unplaced", action="store_true",
                   help="allow --discard / delete to remove files that are not registered sources (operator confirmed)")
    mode = p.add_mutually_exclusive_group()
    mode.add_argument("--discard", action="store_true", help="remove an inbox item the engine captured elsewhere")
    mode.add_argument("--duplicate-of", metavar="ID", help="merge an inbox duplicate into source ID")
    mode.add_argument("--reject", action="store_true", help="apply intake.on_reject to the item")
    mode.add_argument("--retriage", action="store_true", help="change the triage decision of a placed source")
    mode.add_argument("--proposal", metavar="FILE", help="save a headless intake proposal (JSON file)")
    mode.add_argument("--completed", metavar="JSON", help="log the intake.completed event with this summary")


def _json(text: str | None, what: str) -> Any:
    if text is None:
        return None
    try:
        return json.loads(text)
    except ValueError as exc:
        raise EngineError("bad_json", f"{what} is not valid JSON: {exc}", EXIT_USAGE) from exc


def _need(args: argparse.Namespace, what: str) -> str:
    if not args.target:
        raise EngineError("usage", f"{what} needs a target argument", EXIT_USAGE)
    return args.target


def run(ctx: Context, args: argparse.Namespace) -> Result:
    project = ctx.require_project()
    actor = resolve_actor(ctx.actor)
    dry = ctx.dry_run
    triage = _json(args.triage, "--triage")
    if triage is not None and not isinstance(triage, dict):
        raise EngineError("bad_json", "--triage must be a JSON object", EXIT_USAGE)
    event_extra: dict[str, Any] = {}
    log = True
    if args.completed is not None:
        out = placement.log_completed(project, _json(args.completed, "--completed") or {}, actor, dry)
        log = False
    elif args.proposal:
        out = placement.save_proposal(project, args.proposal, actor, dry)
    elif args.retriage:
        out = placement.retriage(project, _need(args, "--retriage"), decision=None, triage=triage, actor=actor,
                                 lease=args.lease, confirm_policy=args.confirm_policy, dry_run=dry)
        event_extra = {"mode": "retriage", "from": out["from"], "to": out["to"], "source_id": out["id"]}
    elif args.duplicate_of:
        out = placement.merge_duplicate(project, _need(args, "--duplicate-of"), args.duplicate_of,
                                        lease=args.lease, dry_run=dry)
        event_extra = {"mode": "duplicate", "duplicate_of": args.duplicate_of, "removed": out.get("removed")}
    elif args.reject:
        out = placement.reject(project, _need(args, "--reject"), mode=args.on_reject, bundle_slug=args.bundle_slug,
                               member_path=args.member_path, lease=args.lease,
                               include_unplaced=args.include_unplaced, confirm=args.confirm, dry_run=dry)
        event_extra = {"mode": f"reject:{out.get('mode')}"}
        if out.get("removed"):
            event_extra["removed"] = out["removed"]
    elif args.discard:
        out = placement.discard(project, _need(args, "--discard"), lease=args.lease,
                                include_unplaced=args.include_unplaced, confirm=args.confirm, dry_run=dry)
        event_extra = {"mode": "discard", "removed": out.get("removed")}
    else:
        members = None
        if args.members:
            with open(args.members, encoding="utf-8") as fh:
                members = json.load(fh)
        out = placement.place(
            project, _need(args, "place"), category=args.category, name=args.name, parent_id=args.parent,
            member_path=args.member_path, group=args.group, bundle_slug=args.bundle_slug, decision=None,
            triage=triage, supersedes=args.supersedes, actor=actor, lease=args.lease,
            confirm_policy=args.confirm_policy, container=args.container, members=members, dry_run=dry)
        event_extra = {"mode": "place", "source_id": out.get("id"), "decision": (out.get("triage") or {}).get("decision")}
    result = Result(data={**out, "dry_run": dry})
    for w in out.pop("warnings", None) or []:
        result.warn(w["code"], w["message"])
    targets = out.pop("targets", None) or []
    result.data = {**out, "dry_run": dry}
    result.targets = targets
    result.log_event = log and bool(targets) and not dry
    result.event_extra = {k: v for k, v in event_extra.items() if v is not None}
    result.text = f"{out.get('status')}: {out.get('source_path') or out.get('proposal') or out.get('destination') or ''}".rstrip(": ")
    return result


COMMAND = Command(
    name="place",
    help="Place a staged/inbox file into 10_sources/ with its extraction (also: discard, duplicate, reject, retriage)",
    run=run,
    add_arguments=_args,
    mutating=True,
    writes_project=True,
    owner="007",
)
