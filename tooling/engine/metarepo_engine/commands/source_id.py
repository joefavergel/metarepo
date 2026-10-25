"""``id <file>...``: content-addressed source ids, with duplicates reported (002/R12, AC3).

Inside a metarepository the registry supplies the ids already taken, so a 12-character prefix collision
with a different hash yields the longer id for the newcomer; a file whose bytes are already registered is
reported with ``duplicate_of``.
"""
from __future__ import annotations

import argparse
from pathlib import Path

from .. import registry
from ..command import Command, Context
from ..hashing import group_duplicates, sha256_file, source_id
from ..jsonout import EXIT_PRECONDITION, EngineError, Result
from ..root import find_root, is_metarepo_root


def _args(p: argparse.ArgumentParser) -> None:
    p.add_argument("files", nargs="+", metavar="file")


def _registered(root_arg: str | None) -> tuple[dict[str, str], dict[str, str]]:
    """(known ids -> hash, hash -> registered source path) from the enclosing metarepository, if any."""
    try:
        root = Path(root_arg).expanduser().resolve() if root_arg else find_root(Path.cwd())
        if root is None or not is_metarepo_root(root):
            return {}, {}
        view = registry.build(root)
    except (EngineError, OSError):
        return {}, {}
    return view.known_ids(), {r["sha256"]: r["path"] for r in view.rows if r.get("sha256")}


def run(ctx: Context, args: argparse.Namespace) -> Result:
    hashes: dict[str, str] = {}
    for name in args.files:
        path = Path(name)
        if not path.is_file():
            raise EngineError("file_not_found", f"not a file: {name}", EXIT_PRECONDITION)
        hashes[path.as_posix()] = sha256_file(path)
    known, by_hash = _registered(getattr(args, "root", None))
    items = []
    for p, h in hashes.items():
        item = {"path": p, "sha256": h, "id": source_id(h, known)}
        if h in by_hash:
            item["duplicate_of"] = by_hash[h]
        items.append(item)
    dups = [{"id": source_id(hashes[group[0]], known), "paths": group} for group in group_duplicates(hashes)]
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
