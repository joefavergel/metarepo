"""``wiki source-page <id> [--write]``: pre-filled Source page skeleton for a source (010/R3, R4, R6, R8, R13).

Read-only by default: reports the source's ``wiki_status`` (pending / ingested / stale), the target path, the
skeleton frontmatter and the governance context (``can_write`` tells the skill whether to feed or to prepare a
proposal). The policy gate (007/R9) is re-run on the extraction: a match blocks (exit 1, rule names and
locations only). With ``--write`` a pending source gets its skeleton page created under the ``wiki`` lease;
an existing page is never overwritten, so re-feeding an unchanged source is a no-op (P12).
"""
from __future__ import annotations

import argparse
from pathlib import Path

from .. import leases, policy, registry as reg
from ..command import Command, Context
from ..config import effective
from ..events import configured_actor, now_iso, resolve_actor
from ..fsops import atomic_write_text
from ..jsonout import EXIT_BLOCKING, EXIT_USAGE, EngineError, Result, error
from ..wiki import gate, sourcepage


def _args(p: argparse.ArgumentParser) -> None:
    p.add_argument("source_id", help="source id (src-<12 hex>)")
    p.add_argument("--write", action="store_true",
                   help="create the skeleton page when the source is pending (never overwrites)")
    p.add_argument("--assisted-by", default=None, metavar="HARNESS/MODEL",
                   help="recorded as generated.assisted_by (the agent that helps the operator)")
    p.add_argument("--lease", default=None, help="nonce of a wiki lease already held (default: acquire one)")


def _row(root: Path, source_id: str) -> dict:
    view = reg.build(root)
    matches = [r for r in view.rows if r["id"] == source_id]
    if not matches:
        raise EngineError("unknown_source", f"no extraction records source {source_id!r}; run /intake first",
                          EXIT_USAGE)
    return matches[0]


def run(ctx: Context, args: argparse.Namespace) -> Result:
    project = ctx.require_project()
    root = project.root
    if not gate.feed_allowed(project.state()):
        gate.require_configured(project)
    row = _row(root, args.source_id)
    actor = resolve_actor(ctx.actor, project.project_id)
    config = project.config()
    gov = leases.governance(root)
    owner_mode = gov.get("wiki_write_mode", "lease") == "owner"
    can_write = not owner_mode or actor in [str(o) for o in (gov.get("wiki_owners") or [])]
    status = sourcepage.wiki_status(root, row)
    result = Result(log_event=False)

    # 010/R6: policy gate on the extraction, before anything is written.
    text = (root / row["extract"]).read_text(encoding="utf-8", errors="replace")
    findings = policy.scan_text(text, effective(config).get("policy"))
    if findings:
        result.data = {"source_id": args.source_id, "wiki_status": status, "blocked": True,
                       "policy": [f.as_dict() for f in findings]}
        rules = sorted({f.rule for f in findings})
        result.errors.append(error("policy_blocked", f"the policy gate matched rule(s) {', '.join(rules)} in "
                                   f"{row['extract']}; this source cannot be fed", row["extract"]))
        result.blocking, result.exit_code = True, EXIT_BLOCKING
        result.text = f"blocked by policy: {', '.join(rules)}"
        return result

    existing = sourcepage.source_pages(root).get(args.source_id)
    path = existing[0] if existing else sourcepage.page_rel(args.source_id)
    skeleton = sourcepage.skeleton(root, row, actor=actor, now=now_iso(), assisted_by=args.assisted_by)
    data = {
        "source_id": args.source_id, "wiki_status": status, "noop": status == sourcepage.INGESTED,
        "page": path, "page_exists": existing is not None, "extract": row["extract"], "original": row["path"],
        "triage": row["triage"], "quality": row["quality"], "language": row["language"],
        "governance": {"write_mode": "owner" if owner_mode else "lease", "actor": actor, "can_write": can_write},
        "frontmatter": skeleton, "written": False, "dry_run": ctx.dry_run,
    }
    if status == sourcepage.STALE:
        data["refresh"] = {"content_sha256": skeleton["content_sha256"], "extracted_at": skeleton["extracted_at"]}
    result.data = data
    if row["triage"] != "wiki" and existing is None:
        result.warn("not_triaged_wiki", f"{args.source_id} is triaged {row['triage']!r}, not 'wiki'", row["extract"])
    if row["quality"] in ("partial", "failed", "unreadable"):
        result.warn("low_quality_extraction", f"extraction quality is {row['quality']!r}; mark unclear claims",
                    row["extract"])
    if configured_actor(ctx.actor, project.project_id) is None:
        result.warn("actor_not_configured", f"no actor is configured; using the OS user {actor!r}")
    if status == sourcepage.INGESTED:
        result.text = f"{args.source_id}: already ingested, nothing to do ({path})"
        return result
    if not args.write:
        result.text = f"{args.source_id}: {status or 'not queued'}; skeleton for {path}"
        return result
    if existing is not None:
        result.warn("page_exists", f"{path} exists and is never overwritten; update it by hand ({status})", path)
        return result
    if ctx.dry_run:
        result.text = f"would create {path}"
        return result

    def work(handle):
        handle.ensure()
        target = root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_text(target, sourcepage.render(skeleton), overwrite=False)

    if args.lease:
        work(leases.attach(root, "wiki", args.lease))
    else:
        with leases.held(root, "wiki", actor, "wiki source-page") as handle:
            work(handle)
    data["written"] = True
    result.targets = [path]
    result.log_event = True
    result.text = f"created {path}"
    return result


COMMAND = Command(
    name="wiki source-page",
    help="Emit (or with --write create) a pre-filled Source page skeleton for a source",
    run=run,
    add_arguments=_args,
    mutating=True,
    writes_project=True,
    owner="010",
)
