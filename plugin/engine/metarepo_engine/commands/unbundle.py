"""``unbundle <container> [--staging <dir>] [--provisional]``: validate, then expand a container (007/R5, R5a).

The container is an archive file or a folder from ``00_inbox/``. Validation runs before any write; the
expansion goes to a local staging directory outside the tree and a ``manifest.json`` is saved there. With
``--dry-run`` the container is validated and nothing is written. Violations exit 3 with a rule code.
"""
from __future__ import annotations

import argparse
import json
import tempfile
from pathlib import Path

from .. import registry
from ..bundles import Limits, validate_and_expand
from ..command import Command, Context
from ..config import effective
from ..fsops import atomic_write_text
from ..inbox import require_outside_tree
from ..jsonout import EXIT_PRECONDITION, EngineError, Result
from ..provisional import provisional_extract


def _args(p: argparse.ArgumentParser) -> None:
    p.add_argument("container", help="archive file or folder (root-relative, e.g. 00_inbox/pack.zip)")
    p.add_argument("--staging", metavar="DIR", default=None,
                   help="local staging directory outside the metarepository (default: a new temp directory)")
    p.add_argument("--provisional", action="store_true",
                   help="also write a provisional extraction (and policy verdict) for every member")


def run(ctx: Context, args: argparse.Namespace) -> Result:
    project = ctx.require_project()
    root = project.root
    given = Path(args.container)
    source = given if given.is_absolute() else (root / given if (root / given).exists() else Path.cwd() / given)
    if not source.exists():
        raise EngineError("file_not_found", f"no such container: {args.container}", EXIT_PRECONDITION)
    if source.is_symlink():
        raise EngineError("is_symlink", "refusing to expand a symbolic link", EXIT_PRECONDITION)
    resolved = source.resolve()
    try:
        rel = resolved.relative_to(root).as_posix()
        if not rel.startswith("00_inbox/"):
            raise EngineError("not_in_inbox", "containers are expanded from 00_inbox/ only", EXIT_PRECONDITION, rel)
    except ValueError:
        pass  # outside the tree: allowed (e.g. a container staged by hand)
    if args.staging:
        staging = require_outside_tree(root, Path(args.staging))
        staging.mkdir(parents=True, exist_ok=True)
    elif ctx.dry_run:
        staging = Path(tempfile.gettempdir()) / "metarepo-staging-dry-run"
    else:
        staging = Path(tempfile.mkdtemp(prefix="metarepo-staging-"))
    cfg = effective(project.config())
    known = registry.build(root).known_ids()
    manifest = validate_and_expand(resolved, staging, Limits.from_config(cfg), known=known, dry_run=ctx.dry_run)
    by_hash = {r["sha256"]: r for r in registry.build(root).rows if r.get("sha256")}
    for row in manifest["members"]:
        if row.get("sha256") in by_hash:
            row["duplicate_of"] = by_hash[row["sha256"]]["id"]
        if args.provisional and row.get("staged") and not row.get("bundle") and not row.get("duplicate_of"):
            row["provisional"] = provisional_extract(project, Path(row["staged"]), staging / "provisional",
                                                     row["id"][:16])
    if len(manifest["excluded"]) > 200:
        manifest["excluded_total"] = len(manifest["excluded"])
        manifest["excluded"] = manifest["excluded"][:200]
    manifest["staging"] = str(staging)
    if not ctx.dry_run:
        atomic_write_text(staging / "manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")
    result = Result(data=manifest)
    result.text = f"{len(manifest['members'])} member(s), {len(manifest['junk'])} junk, staging {staging}"
    return result


COMMAND = Command(
    name="unbundle",
    help="Validate and expand a container (archive or folder) into local staging; writes a manifest",
    run=run,
    add_arguments=_args,
    mutating=True,  # adds --dry-run; writes only to staging, never to the project (no event)
    owner="007",
)
