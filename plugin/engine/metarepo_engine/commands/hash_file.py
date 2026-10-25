"""``hash <file>``: SHA-256 of a file (002/R12)."""
from __future__ import annotations

import argparse
from pathlib import Path

from ..command import Command, Context
from ..hashing import sha256_file
from ..jsonout import EXIT_PRECONDITION, EngineError, Result


def _args(p: argparse.ArgumentParser) -> None:
    p.add_argument("file")


def run(ctx: Context, args: argparse.Namespace) -> Result:
    path = Path(args.file)
    if not path.is_file():
        raise EngineError("file_not_found", f"not a file: {args.file}", EXIT_PRECONDITION)
    digest = sha256_file(path)
    return Result(data={"path": path.as_posix(), "sha256": digest, "size": path.stat().st_size}, text=digest)


COMMAND = Command(
    name="hash",
    help="Print the SHA-256 of a file",
    run=run,
    add_arguments=_args,
    needs_project=False,
    needs_yaml=False,
    owner="002",
)
