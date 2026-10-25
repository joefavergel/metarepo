"""``doctor``: health of the environment, the sync layer and the toolkit (013).

Read-only unless ``--fix-safe`` is given together with ``--yes <action-id>`` for each action to perform.
Without ``--fix-safe`` the only write is the best-effort ``state.json -> last_doctor`` summary (013/R5),
skipped under ``--dry-run``. ``gate=False``: doctor must run (and say so) when the toolkit is outside
``toolkit.requires``.
"""
from __future__ import annotations

import argparse

from .. import doctor as core
from ..command import Command, Context
from ..events import resolve_actor
from ..jsonout import EXIT_USAGE, EngineError, Result


def _args(p: argparse.ArgumentParser) -> None:
    p.add_argument("--local", action="store_true",
                   help="only machine-specific checks: runtime, repos, not-local files, toolkit compatibility")
    p.add_argument("--fix-safe", action="store_true",
                   help="propose a safe action per finding; each runs only when confirmed with --yes <action-id>")
    p.add_argument("--yes", action="append", default=[], metavar="ACTION_ID",
                   help="confirm one proposed action (repeatable; requires --fix-safe)")
    p.add_argument("--dest", action="append", default=[], metavar="ACTION_ID=FOLDER",
                   help="destination folder (outside the synced tree) for a move-out action")


def _text(report: core.Report, applied: list[dict], proposals: list[core.Action], fix: bool, dry: bool) -> str:
    c = report.counts()
    lines = [f"doctor: {c['error']} error(s), {c['warn']} warning(s), {c['info']} note(s)"]
    for f in report.findings:
        where = f" {f.path}" if f.path else ""
        lines.append(f"[{f.severity}] {f.id}{where}: {f.message}")
        for a in f.actions:
            if fix:
                lines.append(f"    fix {a.id}: {a.description}")
    for name, info in report.checks.items():
        if info["status"] == "skipped":
            lines.append(f"[skipped] {name}: {info['reason']}")
    for row in applied:
        lines.append(f"{row['status']}: {row['message']}")
    if fix and proposals and not applied:
        lines.append("nothing was changed; confirm each action with --yes <action-id>"
                     + (" (dry run)" if dry else ""))
    return "\n".join(lines)


def run(ctx: Context, args: argparse.Namespace) -> Result:
    project = ctx.require_project()
    root = project.root
    actor = resolve_actor(ctx.actor)
    if args.yes and not args.fix_safe:
        raise EngineError("usage", "--yes needs --fix-safe", EXIT_USAGE)
    dests: dict[str, str] = {}
    for item in args.dest:
        key, sep, value = item.partition("=")
        if not sep or not key or not value:
            raise EngineError("usage", f"--dest expects ACTION_ID=FOLDER, got {item!r}", EXIT_USAGE)
        dests[key] = value

    result = Result()
    config_ok = True
    try:
        config = project.config()
    except EngineError as exc:
        config, config_ok = {}, False
        result.warn("config_invalid", f"project.yaml could not be read; using defaults: {exc.message}", "project.yaml")

    def do_scan() -> core.Report:
        return core.scan(root, config, local_only=args.local, version=ctx.version, config_ok=config_ok)

    report = do_scan()
    for code, message in report.warnings:
        result.warn(code, message)
    proposals = report.actions() if args.fix_safe else []
    applied: list[dict] = []
    if args.fix_safe and args.yes:
        by_id = {a.id: a for a in proposals}
        unknown = [i for i in args.yes if i not in by_id]
        if unknown:
            raise EngineError("unknown_action", "no such proposed action: " + ", ".join(unknown)
                              + "; run --fix-safe without --yes to list them", EXIT_USAGE)
        unused = [k for k in dests if k not in by_id]
        if unused:
            raise EngineError("unknown_action", "--dest names no proposed action: " + ", ".join(unused), EXIT_USAGE)
        for action_id in dict.fromkeys(args.yes):
            action = by_id[action_id]
            if ctx.dry_run:
                applied.append({"id": action.id, "kind": action.kind, "path": action.path, "status": "planned",
                                "message": action.description})
                continue
            factory = lambda: Context(project=project, json_mode=False, dry_run=False, actor=ctx.actor,  # noqa: E731
                                      version=ctx.version)
            applied.append(core.apply_action(factory, root, config, action, dest=dests.get(action_id), actor=actor))
        if any(r["status"] == "applied" for r in applied):
            report = do_scan()
            proposals = report.actions()

    recorded = False
    if not ctx.dry_run:
        try:
            core.record_last_doctor(root, report, version=ctx.version, actor=actor, local_only=args.local)
            recorded = True
        except (EngineError, OSError, ValueError) as exc:
            msg = exc.message if isinstance(exc, EngineError) else str(exc)
            result.warn("last_doctor_not_recorded", f"could not update state.json -> last_doctor: {msg}")

    counts = report.counts()
    result.data = {
        "scope": "local" if args.local else "full",
        "summary": counts,
        "findings": [f.as_dict() for f in report.findings],
        "checks": report.checks,
        "fix_safe": bool(args.fix_safe),
        "actions": [a.as_dict() for a in proposals],
        "applied": applied,
        "recorded": recorded,
        "dry_run": ctx.dry_run,
    }
    result.blocking = counts[core.ERROR] > 0
    result.text = _text(report, applied, proposals, bool(args.fix_safe), ctx.dry_run)
    return result


COMMAND = Command(
    name="doctor",
    help="Environment, sync and toolkit-compatibility health checks (read-only; --fix-safe proposes fixes)",
    run=run,
    add_arguments=_args,
    mutating=True,
    writes_project=False,
    needs_yaml=False,
    gate=False,
    owner="013",
)
