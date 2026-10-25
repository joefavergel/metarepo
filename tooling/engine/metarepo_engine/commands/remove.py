"""``remove <path> --confirm <sha256>``: confirmed, logged deletion of a non-source file (002/R6, P6).

The path is relative to the metarepository root (absolute paths inside it are accepted). Nothing under
``10_sources/`` is ever removed. The caller must pass the file's full SHA-256, which proves the operator
confirmed *this* content; the event log records path, size and hash. Recovery relies on the sync
provider's trash: the toolkit keeps no trash folder of its own.
"""
from __future__ import annotations

import argparse
import re
from pathlib import Path

from ..command import Command, Context
from ..fsops import remove_file, sha256_file
from ..jsonout import EXIT_COMPAT, EXIT_PRECONDITION, EXIT_USAGE, EngineError, Result

PROTECTED_FILES = ("project.yaml", "_system/state.json", "_system/events.log")
_SHA = re.compile(r"^[0-9a-fA-F]{64}$")


def _args(p: argparse.ArgumentParser) -> None:
    p.add_argument("path", help="file to delete, relative to the metarepository root")
    p.add_argument("--confirm", required=True, metavar="SHA256",
                   help="the file's full SHA-256, confirming the operator approved deleting exactly this content")


def run(ctx: Context, args: argparse.Namespace) -> Result:
    project = ctx.require_project()
    root = project.root
    if not _SHA.match(args.confirm):
        raise EngineError("bad_confirmation", "--confirm must be the full 64-character SHA-256 of the file", EXIT_USAGE)
    given = Path(args.path)
    target = given if given.is_absolute() else root / given
    try:
        rel = Path(target).resolve().relative_to(root).as_posix()
    except ValueError:
        raise EngineError("outside_root", "path is outside the metarepository", EXIT_PRECONDITION) from None
    if target.is_symlink():
        raise EngineError("is_symlink", "refusing to delete a symbolic link", EXIT_PRECONDITION, rel)
    parts = rel.split("/")
    if parts[0].casefold() == "10_sources":
        raise EngineError("sources_immutable", "10_sources/ is never modified or deleted by the toolkit (P6); "
                          "a correction is a new source that supersedes the old one", EXIT_PRECONDITION, rel)
    if rel.casefold() in {p.casefold() for p in PROTECTED_FILES}:
        raise EngineError("protected_file", f"{rel} is part of the project's core state and cannot be removed",
                          EXIT_PRECONDITION, rel)
    resolved = root / rel
    if not resolved.exists():
        raise EngineError("file_not_found", f"no such file: {rel}", EXIT_PRECONDITION, rel)
    if not resolved.is_file():
        raise EngineError("not_a_file", "only single files can be removed", EXIT_PRECONDITION, rel)
    size = resolved.stat().st_size
    digest = sha256_file(resolved)
    if digest != args.confirm.lower():
        raise EngineError("confirmation_mismatch",
                          "the file's SHA-256 differs from --confirm (it changed, or the wrong hash was given); "
                          "nothing was deleted", EXIT_COMPAT, rel)
    result = Result(data={"path": rel, "size": size, "sha256": digest, "dry_run": ctx.dry_run})
    if ctx.dry_run:
        result.text = f"would delete {rel} ({size} bytes, sha256 {digest})"
        return result
    remove_file(resolved)
    result.targets = [rel]
    result.event_extra = {"size": size, "sha256": digest}
    result.text = f"deleted {rel} ({size} bytes, sha256 {digest})"
    return result


COMMAND = Command(
    name="remove",
    help="Delete a non-source file after explicit confirmation (logged with path, size and SHA-256)",
    run=run,
    add_arguments=_args,
    mutating=True,
    writes_project=True,
    owner="003",
)
