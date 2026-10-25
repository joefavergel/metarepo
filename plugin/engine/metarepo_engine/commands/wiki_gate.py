"""``wiki gate [--complete]``: inspect or open the wiki configuration gate (009/R1, R8).

``--complete`` validates the bundle (every page parseable with a ``type``) and then sets
``wiki.configured = true`` and ``configured_at`` in ``_system/state.json`` under the ``config`` lease.
Idempotent: a second run keeps the original ``configured_at``.
"""
from __future__ import annotations

import argparse

from .. import leases
from ..command import Command, Context
from ..events import resolve_actor
from ..jsonout import EXIT_BLOCKING, EXIT_PRECONDITION, EngineError, Result
from ..wiki import bundle, config as wiki_config, gate


def _args(p: argparse.ArgumentParser) -> None:
    p.add_argument("--complete", action="store_true", help="open the gate after the first feed and a lint pass")
    p.add_argument("--lease", default=None, help="nonce of a config lease already held (default: acquire one)")


def run(ctx: Context, args: argparse.Namespace) -> Result:
    project = ctx.require_project()
    root = project.root
    state = gate.status(project.state())
    result = Result(data={"gate": state, "dry_run": ctx.dry_run})
    if not args.complete:
        result.log_event = False
        result.text = ("wiki gate: open" + (f" since {state['configured_at']}" if state["configured_at"] else "")
                       if state["configured"] else "wiki gate: closed (run /init-wiki)")
        return result
    gate.require_metarepo_configured(project)
    if not (root / bundle.WIKI_DIR / wiki_config.WIKI_MD).is_file() or not (root / bundle.WIKI_DIR / "index.md").is_file():
        raise EngineError("wiki_not_scaffolded", "the wiki bundle is not scaffolded; run `wiki scaffold` first",
                          EXIT_PRECONDITION)
    cfg = wiki_config.load(root)
    report = bundle.validate(root, cfg)
    result.data["bundle"] = {"pages": report.pages, "errors": report.errors, "warnings": report.warnings}
    for w in report.warnings:
        result.warn(w["code"], w["message"], w["path"])
    if report.pages == 0:
        result.warn("no_pages", "the bundle has no content pages yet (nothing was fed)")
    if not report.ok:
        result.errors += [{"code": e["code"], "message": e["message"], "path": e["path"]} for e in report.errors]
        result.blocking = True
        result.exit_code = EXIT_BLOCKING
        result.log_event = False
        result.text = f"gate not opened: {len(report.errors)} invalid page(s)"
        return result
    if state["configured"]:
        result.data["already_configured"] = True
        result.log_event = False
        result.text = f"wiki gate already open since {state['configured_at']}"
        return result
    if ctx.dry_run:
        result.log_event = False
        result.text = "would open the wiki gate"
        return result
    if args.lease:
        new = gate.mark_configured(root, leases.attach(root, "config", args.lease))
    else:
        with leases.held(root, "config", resolve_actor(ctx.actor), "wiki gate") as handle:
            new = gate.mark_configured(root, handle)
    result.data["gate"] = {**state, "configured": True, "configured_at": new.get("configured_at")}
    result.targets = [gate.STATE_REL]
    result.text = "wiki gate opened"
    return result


COMMAND = Command(
    name="wiki gate",
    help="Show the wiki configuration gate; --complete opens it after the first feed",
    run=run,
    add_arguments=_args,
    mutating=True,
    writes_project=True,
    owner="009",
)
