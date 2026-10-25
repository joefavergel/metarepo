"""``extract-record <source-or-id> --body <md-file> --extractor agent:<model>``: wrap agent text (008/R5).

The agent writes its transcription or description to a temp file; the engine adds identity, hash,
provenance (``mode: agent``) and quality, applies the size guard and preserves triage and lineage.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from ..command import Command, Context
from ..extract.pipeline import run_record
from ..jsonout import EXIT_PRECONDITION, EXIT_USAGE, EngineError, Result


def _args(p: argparse.ArgumentParser) -> None:
    p.add_argument("source", help="source id (src-...) or path of a file under 10_sources/")
    p.add_argument("--body", required=True, metavar="FILE", help="Markdown body written by the agent ('-' = stdin)")
    p.add_argument("--extractor", required=True, metavar="agent:<model>", help="provenance, e.g. agent:<model id>")
    p.add_argument("--quality", choices=("high", "partial", "needs-review", "failed"), default=None,
                   help="override the computed quality (the agent states its own confidence)")
    p.add_argument("--quality-notes", default=None, metavar="TEXT")
    p.add_argument("--title", default=None)
    p.add_argument("--language", default=None, metavar="BCP47")
    p.add_argument("--structure", default=None, metavar="JSON", help='e.g. {"pages": 3}')
    p.add_argument("--force", action="store_true", help="replace an existing high/partial extraction")


def _read_body(spec: str) -> str:
    if spec == "-":
        return sys.stdin.read()
    path = Path(spec)
    if not path.is_file():
        raise EngineError("file_not_found", f"body file not found: {spec}", EXIT_PRECONDITION)
    try:
        return path.read_text(encoding="utf-8")
    except UnicodeDecodeError as exc:
        raise EngineError("bad_body", f"body file is not valid UTF-8: {exc}", EXIT_USAGE) from exc


def run(ctx: Context, args: argparse.Namespace) -> Result:
    structure = None
    if args.structure:
        try:
            structure = json.loads(args.structure)
        except ValueError as exc:
            raise EngineError("bad_structure", f"--structure is not valid JSON: {exc}", EXIT_USAGE) from exc
        if not isinstance(structure, dict):
            raise EngineError("bad_structure", "--structure must be a JSON object", EXIT_USAGE)
    out = run_record(ctx.require_project(), args.source, body=_read_body(args.body), extractor=args.extractor,
                     quality=args.quality, quality_notes=args.quality_notes, title=args.title,
                     language=args.language, structure=structure, force=args.force, dry_run=ctx.dry_run)
    result = Result(data={**out.as_data(), "dry_run": ctx.dry_run})
    result.targets = [*out.written, *out.removed]
    if out.removed:
        result.event_extra = {"removed": out.removed}
    result.text = f"{out.status}: {out.extract} ({out.quality}, mode agent)"
    return result


COMMAND = Command(
    name="extract-record",
    help="Wrap agent-produced text into an extraction",
    run=run,
    add_arguments=_args,
    mutating=True,
    writes_project=True,
    owner="008",
)
