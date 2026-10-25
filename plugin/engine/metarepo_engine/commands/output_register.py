"""``output register``: write an output into ``outputs/`` with its provenance (014/R6, R7, R9; 014/ADR-001).

The skill drafts and iterates with the operator (014/R10); this command is the final step. It chooses the file
name (``<yyyy-mm-dd>-<kind>-<slug>.<ext>``, never overwriting: revisions get ``-v2``, ``-v3``), hashes every
``--based-on`` wiki page and the wiki index, and writes the file with ``type: Output`` provenance in its
frontmatter (a ``.meta.yaml`` sidecar for html). ``--kinds`` is read-only and lists the usable kinds and which
template folder wins (project ``outputs/_templates/<kind>/`` over the built-in one).

Outputs are derived, never sources: nothing here touches ``00_inbox/``, ``10_sources/``, ``20_extracts/`` or
``30_wiki/``, and no shared hot file is written, so no lease is needed. New files are created atomically and
exclusively.
"""
from __future__ import annotations

import argparse
import errno
from datetime import date as _date
from pathlib import Path

from .. import frontmatter, outputs
from ..command import Command, Context
from ..config import effective
from ..events import configured_actor, now_iso, resolve_actor
from ..fsops import create_new_bytes
from ..jsonout import EXIT_USAGE, EngineError, Result
from ..wiki import gate, sign


def _args(p: argparse.ArgumentParser) -> None:
    p.add_argument("--kinds", action="store_true",
                   help="list the usable kinds and their template folders (read-only), then exit")
    p.add_argument("--kind", help="output kind (one-pager, slides, report, brief, faq or a project kind)")
    p.add_argument("--slug", help="lowercase kebab-case topic slug used in the file name")
    p.add_argument("--based-on", action="append", default=[], metavar="PAGE",
                   help="wiki page used (relative to the root or to 30_wiki/); repeat for each page")
    p.add_argument("--content-file", metavar="PATH", help="the finished draft to store (markdown, marp or html)")
    p.add_argument("--format", choices=outputs.FORMATS, default=None,
                   help="markdown, marp, html, or artifact (stub for a harness-native artifact); "
                        "default outputs.default_format")
    p.add_argument("--artifact-url", metavar="URL", help="link of the native artifact (required for --format artifact)")
    p.add_argument("--title", default=None, help="recorded as title")
    p.add_argument("--audience", default=None, help="recorded as audience")
    p.add_argument("--language", default=None, help="recorded as language (default language.content)")
    p.add_argument("--assisted-by", default=None, metavar="HARNESS/MODEL", help="recorded as generated.assisted_by")
    p.add_argument("--date", default=None, metavar="YYYY-MM-DD", help="date used in the name (default today)")


def _list_kinds(root: Path) -> Result:
    rows = []
    for name, kind in outputs.kinds(root).items():
        rows.append({"kind": name, "source": kind.source, "overrides_builtin": kind.overrides,
                     "path": outputs.rel_to(root, kind.directory), "files": kind.files()})
    return Result(data={"kinds": rows, "builtin": list(outputs.BUILTIN_KINDS)}, log_event=False,
                  text="\n".join(f"{r['kind']}\t{r['source']}\t{r['path']}" for r in rows))


def _usage(code: str, message: str) -> EngineError:
    return EngineError(code, message, EXIT_USAGE)


def _stub_body(title: str | None, url: str) -> str:
    heading = title or "Output"
    return f"# {heading}\n\nThis deliverable lives in a harness-native artifact: {url}\n"


def run(ctx: Context, args: argparse.Namespace) -> Result:
    project = ctx.require_project()
    root = project.root
    if args.kinds:
        return _list_kinds(root)
    if not args.kind or not args.slug:
        raise _usage("missing_argument", "--kind and --slug are required (or use --kinds)")
    kind = outputs.resolve_kind(root, args.kind)
    if kind is None:
        known = ", ".join(outputs.kinds(root))
        raise _usage("unknown_kind", f"no template for kind {args.kind!r}; usable kinds: {known}")
    slug = outputs.clean_slug(args.slug)
    if not outputs.slug_ok(slug):
        raise _usage("invalid_slug", f"{args.slug!r} is not a lowercase kebab-case slug (letters, digits, hyphens)")
    gate.require_configured(project)

    config = project.config()
    fmt = args.format or (effective(config).get("outputs") or {}).get("default_format", "markdown")
    if fmt == "artifact" and not args.artifact_url:
        raise _usage("missing_artifact_url", "--format artifact needs --artifact-url (the stub records the link)")
    if fmt != "artifact" and not args.content_file:
        raise _usage("missing_content", "--content-file is required for markdown, marp and html outputs")
    if not args.based_on:
        raise _usage("no_based_on", "an output must be based on at least one wiki page (--based-on); if the wiki "
                     "does not cover the topic, tell the operator and register nothing (014/R3)")
    stamp = args.date or _date.today().isoformat()
    try:
        _date.fromisoformat(stamp)
    except ValueError:
        raise _usage("invalid_date", f"--date must be YYYY-MM-DD, got {stamp!r}") from None

    based_on: list[dict[str, str]] = []
    seen: set[str] = set()
    for page in args.based_on:
        _path, rel = sign.resolve_page(root, page)
        if rel not in seen:
            seen.add(rel)
            based_on.append({"path": rel, "sha256": outputs.page_hash(root, rel)})

    actor = resolve_actor(ctx.actor, project.project_id)
    extra: dict = {"format": fmt, "template": kind.source}
    for key, value in (("title", args.title), ("audience", args.audience),
                       ("language", args.language or (config.get("language") or {}).get("content"))):
        if value:
            extra[key] = value
    prov = outputs.provenance(kind=kind.name, actor=actor, at=now_iso(), assisted_by=args.assisted_by,
                              based_on=based_on, index_sha=outputs.wiki_index_hash(root), extra=extra)

    ext = outputs.EXTENSIONS[fmt]
    rel, revision = outputs.next_free(root, stamp, kind.name, slug, ext)
    targets = [rel]
    if fmt == "artifact":
        prov["artifact_url"] = args.artifact_url
        doc = frontmatter.Document(front=prov, body=_stub_body(args.title, args.artifact_url), has_front=True)
        payload, sidecar, side_payload = doc.to_bytes(), None, None
    else:
        source = Path(args.content_file)
        if not source.is_absolute():
            source = ctx.cwd / source
        try:
            text = source.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            raise _usage("content_unreadable", f"cannot read --content-file {args.content_file!r}: {exc}") from exc
        if fmt == "html":
            payload = text.encode("utf-8")
            sidecar, side_payload = outputs.sidecar_rel(rel), outputs.dump_sidecar(prov).encode("utf-8")
            targets.append(sidecar)
        else:
            try:
                draft = frontmatter.parse(text)
            except frontmatter.FrontmatterError as exc:
                raise _usage("content_invalid", f"--content-file frontmatter is not valid: {exc}") from exc
            front = outputs.merge_front(draft.front, prov)
            if fmt == "marp":
                front.setdefault("marp", True)
            payload = frontmatter.Document(front=front, body=draft.body, has_front=True).to_bytes()
            sidecar, side_payload = None, None

    result = Result(data={
        "output": rel, "kind": kind.name, "kind_source": kind.source, "format": fmt, "revision": revision,
        "sidecar": sidecar, "based_on": based_on, "wiki_index_sha256": prov["wiki_index_sha256"],
        "generated": prov["generated"], "written": False, "dry_run": ctx.dry_run})
    if configured_actor(ctx.actor, project.project_id) is None:
        result.warn("actor_not_configured", f"no actor is configured; using the OS user {actor!r}")
    if revision > 1:
        result.warn("revision", f"{outputs.output_stem(stamp, kind.name, slug)}{ext} already exists; "
                    f"registered as revision v{revision}", rel)
    if ctx.dry_run:
        result.log_event = False
        result.text = f"would write {rel}"
        return result
    try:
        create_new_bytes(root / rel, payload)  # exclusive: two writers racing for a name never overwrite
        if sidecar:
            create_new_bytes(root / sidecar, side_payload)
    except FileExistsError as exc:
        raise EngineError("output_exists", f"{rel} appeared while writing; run the command again", 3) from exc
    except OSError as exc:  # pragma: no cover - disk errors
        if exc.errno == errno.ENAMETOOLONG:
            raise _usage("name_too_long", f"{rel} is too long; use a shorter --slug") from exc
        raise
    result.data["written"] = True
    result.targets = targets
    result.event_extra = {"kind": kind.name, "based_on": len(based_on)}
    result.text = f"registered {rel} (revision v{revision}, {len(based_on)} wiki page(s))"
    return result


COMMAND = Command(
    name="output register",
    help="Write an output into outputs/ with provenance (based_on hashes); --kinds lists the usable kinds",
    run=run,
    add_arguments=_args,
    mutating=True,
    writes_project=True,
    owner="014",
)
