"""``lint [--only ids]``: knowledge-integrity checks over sources, extractions, wiki and outputs (spec 012).

Read-only (012/R3). Exit 1 only when a ``block`` finding exists (012/R1). The one write is the best-effort
``state.json -> last_lint`` summary under the ``config`` lease (012/R6), skipped under ``--dry-run`` and when
the lease is busy. ``environment, sync and tooling`` health belongs to ``doctor`` (013).
"""
from __future__ import annotations

import argparse

from .. import lint as core
from ..command import Command, Context
from ..events import resolve_actor
from ..jsonout import EXIT_USAGE, EngineError, Result


def _args(p: argparse.ArgumentParser) -> None:
    p.add_argument("--only", action="append", default=[], metavar="IDS",
                   help="run only these check ids (comma-separated, repeatable)")


def _text(report: core.Report) -> str:
    c = report.counts()
    lines = [f"lint: {c['block']} block, {c['error']} error(s), {c['warn']} warning(s), {c['info']} note(s) "
             f"({report.pages} page(s), {report.extractions} extraction(s))"]
    for f in report.findings:
        lines.append(f"[{f.severity}] {f.check} {f.where}: {f.detail}")
        if f.fix:
            lines.append(f"    fix: {f.fix}")
    return "\n".join(lines)


def run(ctx: Context, args: argparse.Namespace) -> Result:
    project = ctx.require_project()
    root = project.root
    only = [i.strip() for chunk in args.only for i in chunk.split(",") if i.strip()]
    unknown = [i for i in only if i not in core.CATALOG]
    if unknown:
        raise EngineError("unknown_check", f"unknown check id(s): {', '.join(unknown)}; available: "
                          f"{', '.join(core.CATALOG)}", EXIT_USAGE)
    result = Result()
    try:
        config = project.config()
    except EngineError as exc:
        config = {}
        result.warn("config_invalid", f"project.yaml could not be read; using defaults: {exc.message}", "project.yaml")
    report = core.Linter(root, config, only=only or None, project_id=project.project_id).run()
    for code, message in report.warnings:
        result.warn(code, message)
    recorded = False
    if not ctx.dry_run:
        try:
            core.record_last_lint(root, report, version=ctx.version, actor=resolve_actor(ctx.actor), only=only or None)
            recorded = True
        except (EngineError, OSError, ValueError) as exc:
            msg = exc.message if isinstance(exc, EngineError) else str(exc)
            result.warn("last_lint_not_recorded", f"could not update state.json -> last_lint: {msg}")
    counts = report.counts()
    result.data = {
        "summary": counts,
        "findings": [f.as_dict() for f in report.findings],
        "checks_run": report.checks_run,
        "scanned": {"pages": report.pages, "extractions": report.extractions},
        "last_lint_recorded": recorded,
    }
    result.blocking = report.blocking
    result.log_event = False
    result.text = _text(report)
    return result


COMMAND = Command(
    name="lint",
    help="Knowledge-integrity checks (sources, extractions, wiki, outputs); read-only, exit 1 only on a block finding",
    run=run,
    add_arguments=_args,
    mutating=True,
    writes_project=False,
    owner="012",
)
