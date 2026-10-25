"""``extract <source-or-id> [--extractor NAME] [--force]``: run an adapter and write the extraction (008).

Without ``--force`` an existing extraction is left alone (idempotent, P12) and reported stale when the
source hash differs or the extractor is older than the configured minimum (R6). A failed extraction is
still written, with ``quality: failed`` and a reason (R1), and the command exits 0 with a warning.
"""
from __future__ import annotations

import argparse

from ..command import Command, Context
from ..extract.pipeline import run_extract
from ..jsonout import Result


def _args(p: argparse.ArgumentParser) -> None:
    p.add_argument("source", help="source id (src-...) or path of a file under 10_sources/")
    p.add_argument("--extractor", default=None, metavar="NAME", help="use this adapter only (see `extractors`)")
    p.add_argument("--force", action="store_true",
                   help="regenerate body and technical fields; triage, supersedes, bundle, aliases and "
                        "original_name are preserved")


def run(ctx: Context, args: argparse.Namespace) -> Result:
    out = run_extract(ctx.require_project(), args.source, force=args.force, extractor=args.extractor,
                      dry_run=ctx.dry_run)
    result = Result(data={**out.as_data(), "dry_run": ctx.dry_run})
    for code, message in out.warnings:
        result.warn(code, message, out.extract)
    result.targets = [*out.written, *out.removed]
    result.log_event = out.status == "written"
    if out.removed:
        result.event_extra = {"removed": out.removed}
    result.text = f"{out.status}: {out.extract} ({out.quality or 'n/a'})"
    return result


COMMAND = Command(
    name="extract",
    help="Run an adapter on a source and write its extraction",
    run=run,
    add_arguments=_args,
    mutating=True,
    writes_project=True,
    owner="008",
)
