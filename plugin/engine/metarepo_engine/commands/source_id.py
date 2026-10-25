"""``id <file>...``: content-addressed source ids, with duplicates reported (002/R12, AC3)."""
from __future__ import annotations

import argparse
from pathlib import Path

from ..command import Command, Context
from ..hashing import group_duplicates, sha256_file, source_id
from ..jsonout import EXIT_PRECONDITION, EngineError, Result


def _args(p: argparse.ArgumentParser) -> None:
    p.add_argument("files", nargs="+", metavar="file")


def run(ctx: Context, args: argparse.Namespace) -> Result:
    hashes: dict[str, str] = {}
    for name in args.files:
        path = Path(name)
        if not path.is_file():
            raise EngineError("file_not_found", f"not a file: {name}", EXIT_PRECONDITION)
        hashes[path.as_posix()] = sha256_file(path)
    items = [{"path": p, "sha256": h, "id": source_id(h)} for p, h in hashes.items()]
    dups = [{"id": source_id(hashes[group[0]]), "paths": group} for group in group_duplicates(hashes)]
    text = "\n".join(f"{i['id']}  {i['path']}" for i in items)
    return Result(data={"files": items, "duplicates": dups}, text=text)


COMMAND = Command(
    name="id",
    help="Print the source id (src-<12 hex of sha256>) of one or more files and report duplicates",
    run=run,
    add_arguments=_args,
    needs_project=False,
    needs_yaml=False,
    owner="002",
)
