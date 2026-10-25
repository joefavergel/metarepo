"""``wiki scaffold``: create the OKF bundle and ``WIKI.md`` in ``30_wiki/`` (009/R5, R6, R1).

Never overwrites (P12). Runs under the ``wiki`` lease (owner mode applies). Does not open the gate.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from .. import leases
from ..command import Command, Context
from ..config import effective
from ..events import resolve_actor
from ..jsonout import EXIT_PRECONDITION, EXIT_USAGE, EngineError, Result
from ..wiki import bundle, gate
from ..wiki.config import AXES, PROFILES, WikiConfig, WikiConfigError
from ..wiki.scaffold import apply_scaffold, plan_scaffold


def _args(p: argparse.ArgumentParser) -> None:
    p.add_argument("--answers", metavar="FILE", help="JSON file with the interview answers (see the init-wiki skill)")
    p.add_argument("--axis", choices=AXES, help="organizing axis (overrides the answers file)")
    p.add_argument("--profile", choices=PROFILES, help="wiki profile (overrides the answers file)")
    p.add_argument("--area", action="append", default=None, metavar="NAME", help="an area (repeatable)")
    p.add_argument("--page-type", action="append", default=None, metavar="NAME[=FOLDER]",
                   help="a page type (repeatable); Source and Skill are implicit")
    p.add_argument("--reconfigure", action="store_true",
                   help="apply a new configuration to an already configured wiki (never deletes pages)")
    p.add_argument("--lease", default=None, help="nonce of a wiki lease already held (default: acquire one)")


def _load_answers(path: str | None) -> dict:
    if not path:
        return {}
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise EngineError("answers_unreadable", f"cannot read answers file {path}: {exc}", EXIT_USAGE) from exc
    if not isinstance(data, dict):
        raise EngineError("answers_invalid", "the answers file must contain a JSON object", EXIT_USAGE)
    return data


def build_config(answers: dict, args: argparse.Namespace, project_config: dict) -> WikiConfig:
    merged = dict(answers)
    for key in ("axis", "profile"):
        if getattr(args, key, None):
            merged[key] = getattr(args, key)
    if args.area:
        merged["areas"] = list(args.area)
    if args.page_type:
        parsed = [item.partition("=") for item in args.page_type]
        merged["page_types"] = [{"name": n.strip(), "folder": f.strip()} if f.strip() else n.strip()
                                for n, _, f in parsed]
    if "audit_expected" not in merged:
        merged["audit_expected"] = bool(effective(project_config)["governance"].get("audit_expected"))
    try:
        return WikiConfig.from_mapping(merged)
    except WikiConfigError as exc:
        raise EngineError("wiki_config_invalid", exc.message, EXIT_USAGE) from exc


def run(ctx: Context, args: argparse.Namespace) -> Result:
    project = ctx.require_project()
    gate.require_metarepo_configured(project)
    state = gate.status(project.state())
    if state["configured"] and not args.reconfigure:
        raise EngineError("wiki_already_configured",
                          "the wiki is already configured; use /feed-wiki to add material or /consult-wiki to ask "
                          "questions (re-configuration: wiki scaffold --reconfigure)", EXIT_PRECONDITION)
    config = project.config()
    answers = _load_answers(args.answers)
    cfg = build_config(answers, args, config)
    plan = plan_scaffold(cfg, config, answers)
    root = project.root
    result = Result()
    if ctx.dry_run:
        res = apply_scaffold(root, plan, cfg, reconfigure=args.reconfigure, dry_run=True)
    else:
        def work(handle):
            res_ = apply_scaffold(root, plan, cfg, reconfigure=args.reconfigure, ensure=handle.ensure)
            handle.ensure()
            gate.mark_scaffolded(root, handle, reconfigure=args.reconfigure)
            return res_

        if args.lease:
            res = work(leases.attach(root, "wiki", args.lease))
        else:
            with leases.held(root, "wiki", resolve_actor(ctx.actor), "wiki scaffold") as handle:
                res = work(handle)
    for code, message, path in res.warnings:
        result.warn(code, message, path)
    result.data = {
        "dry_run": ctx.dry_run, "axis": cfg.axis, "profile": cfg.profile,
        "areas": [a.folder for a in cfg.areas], "page_types": [t.name for t in cfg.all_types()],
        "folders": [f"{bundle.WIKI_DIR}/{f}/" for f in plan.folders],
        "created": res.created, "kept": res.kept, "updated": res.updated,
        "config": cfg.to_mapping(),
    }
    changed = [*res.created, *res.updated]
    result.targets = [] if ctx.dry_run else changed
    result.log_event = bool(changed) and not ctx.dry_run
    verb = "would create" if ctx.dry_run else "created"
    result.text = f"{verb}: {len(res.created)} item(s); kept: {len(res.kept)}; updated: {len(res.updated)}"
    return result


COMMAND = Command(
    name="wiki scaffold",
    help="Scaffold the OKF wiki bundle and WIKI.md in 30_wiki/ (never overwrites)",
    run=run,
    add_arguments=_args,
    mutating=True,
    writes_project=True,
    owner="009",
)
