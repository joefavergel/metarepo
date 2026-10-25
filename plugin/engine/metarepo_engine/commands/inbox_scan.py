"""``inbox scan [--provisional <staging-dir>]``: inventory of ``00_inbox/`` (007/R2, R7, R9). Read-only.

With ``--provisional`` every candidate file also gets a provisional extraction written to the (local,
out-of-tree) staging directory, plus the policy-gate verdict: rule names and locations only, never values.
"""
from __future__ import annotations

import argparse
from pathlib import Path

from .. import inbox
from ..command import Command, Context
from ..jsonout import Result
from ..provisional import provisional_extract


def _args(p: argparse.ArgumentParser) -> None:
    p.add_argument("--provisional", metavar="DIR", default=None,
                   help="also write a provisional extraction of each candidate file into this local directory "
                        "(outside the metarepository) and run the policy gate on it")


def run(ctx: Context, args: argparse.Namespace) -> Result:
    project = ctx.require_project()
    data = inbox.scan(project)
    if args.provisional:
        out_dir = inbox.require_outside_tree(project.root, Path(args.provisional))
        for item in data["items"]:
            if item["kind"] != "file" or not item.get("sha256") or item.get("duplicate_of") or item["bundle"]:
                continue
            item["provisional"] = provisional_extract(project, project.root / item["path"], out_dir,
                                                      item["id"][:16])
    s = data["summary"]
    result = Result(data=data)
    result.text = "\n".join(
        [f"{s['items']} item(s), {s['junk']} junk, {s['duplicates']} duplicate(s), {s['bundles']} bundle(s)"]
        + [f"{i['path']}  {i.get('id') or i['kind']}" + ("  duplicate_of=" + i["duplicate_of"] if i.get("duplicate_of") else "")
           for i in data["items"]])
    return result


COMMAND = Command(
    name="inbox scan",
    help="Inventory 00_inbox/: hash, id, media type, size, duplicate-of, bundle, cloud-native, not-local",
    run=run,
    add_arguments=_args,
    owner="007",
)
