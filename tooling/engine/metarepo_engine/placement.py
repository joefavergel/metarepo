"""Placement of approved intake items into ``10_sources/`` with their extraction (007/R12, R13, R14, R17).

The functions here are the engine side of ``place``. They never decide anything: the skill supplies the
triage decision, category and name; the engine computes the id, enforces naming and depth (002/R9-R11b),
moves atomically, writes the placement stub (identity + triage + lineage, no ``extractor``/``extracted_at``),
and runs the full extraction (008), which fills the stub and keeps the triage fields.

Every function that writes to the intake area (moves out of ``00_inbox/``, writes under ``10_sources/`` or
``20_extracts/``) takes the ``intake`` lease nonce and verifies it first (004/R1, R4).
"""
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path, PurePosixPath
from typing import Any

from . import frontmatter, leases, policy as policy_mod, registry
from .bundles import is_junk_path
from .config import effective
from .events import append_event, now_iso
from .extract import writer
from .extract.base import normalize_body
from .extract.pipeline import run_extract
from .fsops import atomic_write_text, remove_file, safe_move, sha256_file
from .hashing import source_id
from .jsonout import EXIT_COMPAT, EXIT_PRECONDITION, EXIT_USAGE, EngineError
from .mediatype import detect
from .naming import check_relpath, hash6, nfc, slugify, slugify_filename, MAX_STEM
from .provisional import provisional_extract
from .root import Project

DECISIONS = ("wiki", "reference", "defer")
SOURCES = "10_sources"
INBOX = "00_inbox"
BUNDLES = "_bundles"
SHARED_REJECTS = "shared/_from-inbox"


# ----------------------------------------------------------------------------- shared helpers
def _intake_lease(project: Project, lease: str | None) -> None:
    leases.require_lease(project.root, "intake", lease)


def _locate(project: Project, arg: str) -> tuple[Path, str | None]:
    """(absolute path, root-relative path when inside the inbox else None). Staged files live outside the tree."""
    root = project.root
    given = Path(arg)
    if given.is_absolute():
        cand = given
    elif (root / given).exists():
        cand = root / given
    else:
        cand = Path.cwd() / given
    if not os.path.lexists(cand):
        raise EngineError("file_not_found", f"no such file or folder: {arg}", EXIT_PRECONDITION)
    if cand.is_symlink():
        raise EngineError("is_symlink", "refusing to handle a symbolic link", EXIT_PRECONDITION)
    resolved = cand.resolve()
    try:
        rel = resolved.relative_to(root).as_posix()
    except ValueError:
        return resolved, None
    if rel == INBOX or not rel.startswith(INBOX + "/"):
        raise EngineError("not_staged", "only files in 00_inbox/ or in a local staging directory outside the "
                          "metarepository can be handled here", EXIT_PRECONDITION, rel)
    return resolved, rel


def _triage(decision: str | None, base: dict[str, Any] | None, actor: str, allowed: tuple[str, ...]) -> dict[str, Any]:
    base = dict(base or {})
    decision = base.get("decision") or decision
    if decision not in allowed:
        raise EngineError("bad_triage", f"triage.decision must be one of {', '.join(allowed)} "
                          f"(got {decision!r}); rejected items are not placed (007/R14)", EXIT_USAGE)
    return {"decision": decision, "reason": base.get("reason"), "by": base.get("by") or actor,
            "at": base.get("at") or now_iso(), "confidence": base.get("confidence")}


def _policy_gate(project: Project, staged: Path, decision: str, confirm: bool) -> dict[str, Any]:
    cfg = effective(project.config())
    if not policy_mod.has_rules(cfg.get("policy")):
        return {"blocked": False, "findings": []}
    with tempfile.TemporaryDirectory(prefix="metarepo-policy-") as tmp:
        verdict = provisional_extract(project, staged, Path(tmp), "scan")["policy"]
    if verdict["blocked"]:
        where = "; ".join(f"{f['rule']} at {f['location']}" for f in verdict["findings"][:10])
        if decision == "wiki":
            raise EngineError("policy_blocked", "the policy gate matched this file, so it can never be placed as "
                              f"'wiki' (007/R9): {where}. Propose 'reference' or 'reject'.", EXIT_PRECONDITION)
        if not confirm:
            raise EngineError("policy_confirmation_required", f"the policy gate matched this file ({where}); "
                              "placing it at all needs explicit confirmation (--confirm-policy)", EXIT_PRECONDITION)
    return verdict


def _dest_name(original: str, name: str | None) -> str:
    base = slugify_filename(name) if name else slugify_filename(original)
    if name and "." not in name.strip(".") and "." in original.strip("."):
        base = slugify_filename(name + "." + original.rsplit(".", 1)[1])
    return base


def plan_path(category: str, bundle_slug: str | None, member_path: str | None, original: str, name: str | None,
              max_depth: int, container: bool) -> tuple[str, bool]:
    """``10_sources/...`` relative path and whether levels were flattened into the name (002/R11b)."""
    if container:
        dirs = [BUNDLES]
    else:
        segs = [s for s in category.replace("\\", "/").split("/") if s.strip()]
        if not segs:
            raise EngineError("bad_category", "a category is required (--category)", EXIT_USAGE)
        dirs = [slugify(s) for s in segs]
        if any(s.strip().startswith("_") for s in segs):
            raise EngineError("bad_category", "categories starting with '_' are reserved for the engine", EXIT_USAGE)
        if bundle_slug:
            dirs.append(slugify(bundle_slug))
    filename = _dest_name(PurePosixPath(member_path).name if member_path else original, name)
    if member_path and not container:
        dirs += [slugify(d) for d in PurePosixPath(member_path).parent.parts if d not in ("", ".")]
    flattened = False
    if len(dirs) > max_depth:
        flattened = True
        extra, dirs = dirs[max_depth:], dirs[:max_depth]
        stem, dot, ext = filename.rpartition(".")
        if not dot:
            stem, ext = filename, ""
        stem = "--".join([*extra, stem])[:MAX_STEM + 40].rstrip("-")
        filename = f"{stem}.{ext}" if ext else stem
    return "/".join([SOURCES, *dirs, filename]), flattened


def _check_length(rel_source: str, limit: int) -> None:
    extract_rel = writer.extract_rel(rel_source)
    check = check_relpath(extract_rel, limit)
    if not check.ok:
        hint = f" Proposed shorter extraction path: {check.proposal}" if check.proposal else ""
        raise EngineError("path_too_long" if check.length > limit else "path_invalid",
                          f"{rel_source}: {'; '.join(check.problems)} (002/R10).{hint} Pass a shorter --name "
                          "or category.", EXIT_PRECONDITION, rel_source)


# ----------------------------------------------------------------------------- place
def place(project: Project, staged_arg: str, *, category: str | None, name: str | None, parent_id: str | None,
          member_path: str | None, group: str | None, bundle_slug: str | None, decision: str | None,
          triage: dict[str, Any] | None, supersedes: list[str], actor: str, lease: str | None,
          confirm_policy: bool, container: bool, members: list[dict[str, Any]] | None,
          dry_run: bool) -> dict[str, Any]:
    root = project.root
    cfg = effective(project.config())
    staged, inbox_rel = _locate(project, staged_arg)
    if not staged.is_file():
        raise EngineError("not_a_file", "place handles single files; unbundle folders and archives first",
                          EXIT_PRECONDITION)
    tri = _triage(decision, triage, actor, ("reference",) if container else DECISIONS)
    view = registry.build(root)
    digest = sha256_file(staged)
    existing = next((r for r in view.rows if r.get("sha256") == digest), None)
    if existing:
        return {"status": "duplicate", "duplicate_of": existing["id"], "duplicate_path": existing["path"],
                "source_path": existing["path"], "id": existing["id"], "placed": False}
    for sid in supersedes:
        if sid not in view.known_ids():
            raise EngineError("unknown_source", f"supersedes: no source with id {sid} in the registry", EXIT_PRECONDITION)
    sid = source_id(digest, view.known_ids())
    original = PurePosixPath(member_path).name if member_path else nfc(staged.name)
    rel_source, flattened = plan_path(category or "", bundle_slug, member_path, original, name,
                                      int(cfg["intake"]["max_source_depth"]), container)
    renamed = False
    if (root / rel_source).exists():  # same name, different bytes: keep both, disambiguate by content
        stem, dot, ext = rel_source.rpartition(".")
        rel_source = f"{stem}-{digest[:6]}.{ext}" if dot and "/" not in ext else f"{rel_source}-{digest[:6]}"
        renamed = True
    _check_length(rel_source, int(cfg["policy"].get("path_limit", 200)))
    rel_extract = writer.extract_rel(rel_source)
    if (root / rel_extract).exists():
        raise EngineError("extraction_exists", f"{rel_extract} already exists for a different source", EXIT_COMPAT, rel_extract)
    verdict = _policy_gate(project, staged, tri["decision"], confirm_policy)
    plan = {"id": sid, "source_path": rel_source, "extract": rel_extract, "triage": tri, "flattened": flattened,
            "renamed_for_collision": renamed, "policy": verdict}
    if dry_run:
        return {"status": "planned", "placed": False, **plan}
    _intake_lease(project, lease)
    media = detect(staged).media_type
    size = staged.stat().st_size
    safe_move(staged, root / rel_source)
    front = {
        "type": "Extract", "schema_version": 1, "source_id": sid, "source_path": rel_source,
        "source_sha256": digest, "source_size": size, "media_type": media, "original_name": original,
        "aliases": [], "triage": tri, "supersedes": list(supersedes),
        "bundle": {"parent_id": parent_id, "group": group or (bundle_slug if member_path else None),
                   "member_path": member_path},
    }
    frontmatter.write(root / rel_extract, frontmatter.Document(front=front, body="", has_front=True))
    out: dict[str, Any] = {"status": "placed", "placed": True, **plan, "media_type": media, "warnings": []}
    targets = [rel_source, rel_extract]
    if container:
        out.update(_write_container_extraction(project, rel_source, members or []))
    else:
        try:
            ex = run_extract(project, rel_source)
            out.update(quality=ex.quality, extractor=ex.extractor, parts=ex.parts)
            out["warnings"] = [{"code": c, "message": m} for c, m in ex.warnings]
            targets = [rel_source, *ex.written]
        except EngineError as exc:  # placement stays valid; `extract` can be retried (008)
            out["warnings"].append({"code": exc.code, "message": exc.message})
            out["quality"] = None
    out["targets"] = targets
    return out


def _write_container_extraction(project: Project, rel_source: str, members: list[dict[str, Any]]) -> dict[str, Any]:
    root = project.root
    rel_extract = writer.extract_rel(rel_source)
    existing = writer.read_front(root, rel_extract) or {}
    lines = [f"# Bundle contents: {existing.get('original_name') or PurePosixPath(rel_source).name}", "",
             "| id | member path | decision |", "|---|---|---|"]
    for m in members:
        lines.append(f"| {m.get('id') or ''} | {str(m.get('member_path', '')).replace('|', '/')} | {m.get('decision') or ''} |")
    body = normalize_body("\n".join(lines) + "\n")
    front = writer.build_front(
        existing, source_id=existing["source_id"], source_rel=rel_source, sha256=existing["source_sha256"],
        size=existing["source_size"], media_type=existing["media_type"], original_name=existing["original_name"],
        extractor={"name": "bundle-manifest", "version": "1", "mode": "local"}, quality="high",
        notes=f"{len(members)} member(s) listed", title=existing.get("original_name"), language=None,
        structure={"members": len(members)}, parts=1)
    done = writer.write_extraction(root, rel_source, front, body, 2 * 1024 * 1024)
    return {"quality": "high", "extractor": front["extractor"], "parts": done.parts, "members_listed": len(members)}


# ----------------------------------------------------------------------------- inbox removals (P6, 002/R6)
def _inbox_files(target: Path) -> list[Path]:
    if target.is_file() or target.is_symlink():
        return [target]
    out: list[Path] = []
    for dirpath, dirs, files in os.walk(target, followlinks=False):
        out += [Path(dirpath) / d for d in dirs if (Path(dirpath) / d).is_symlink()]
        out += [Path(dirpath) / f for f in files]
    return sorted(out)


def _remove_tree(root: Path, target: Path, *, known_hashes: set[str], include_unplaced: bool,
                 confirm: str | None) -> list[dict[str, Any]]:
    files = _inbox_files(target)
    pending, removed = [], []
    for f in files:
        rel = f.relative_to(root).as_posix()
        if f.is_symlink():
            pending.append((f, rel, 0, None, "link"))
            continue
        junk = is_junk_path(f.relative_to(target.parent).parts)
        digest = sha256_file(f) if not junk else None
        captured = junk or digest in known_hashes
        if not captured:
            if not include_unplaced:
                raise EngineError("not_captured", f"{rel} is not registered as a source, so deleting it would lose "
                                  "content (P6); place it, move it to shared/, or pass --include-unplaced after the "
                                  "operator confirmed the deletion", EXIT_PRECONDITION, rel)
            if target.is_file() and (confirm or "").lower() != digest:
                raise EngineError("confirmation_mismatch", "--confirm must be this file's full SHA-256",
                                  EXIT_COMPAT, rel)
        pending.append((f, rel, f.stat().st_size, digest, "junk" if junk else "captured" if captured else "unplaced"))
    for f, rel, size, digest, why in pending:
        remove_file(f)
        removed.append({"path": rel, "size": size, "sha256": digest, "why": why})
    if target.is_dir():
        for dirpath, _dirs, _files in os.walk(target, topdown=False):
            try:
                Path(dirpath).rmdir()
            except OSError:
                pass
    return removed


def discard(project: Project, target_arg: str, *, lease: str | None, include_unplaced: bool, confirm: str | None,
            dry_run: bool) -> dict[str, Any]:
    """Remove junk, a placed copy, a duplicate, an unarchived container or an emptied folder from the inbox."""
    root = project.root
    target, rel = _locate(project, target_arg)
    if rel is None:
        raise EngineError("not_in_inbox", "only items inside 00_inbox/ can be discarded", EXIT_PRECONDITION)
    known = {r["sha256"] for r in registry.build(root).rows if r.get("sha256")}
    if dry_run:
        files = _inbox_files(target)
        return {"status": "planned", "would_remove": [f.relative_to(root).as_posix() for f in files]}
    _intake_lease(project, lease)
    removed = _remove_tree(root, target, known_hashes=known, include_unplaced=include_unplaced, confirm=confirm)
    return {"status": "discarded", "removed": removed, "targets": [r["path"] for r in removed]}


def merge_duplicate(project: Project, target_arg: str, source: str, *, lease: str | None, dry_run: bool) -> dict[str, Any]:
    """007/R3: the inbox file duplicates an existing source; record its name as an alias and remove it."""
    root = project.root
    target, rel = _locate(project, target_arg)
    if rel is None or not target.is_file():
        raise EngineError("not_in_inbox", "only a file inside 00_inbox/ can be merged as a duplicate", EXIT_PRECONDITION)
    row = next((r for r in registry.build(root).rows if r["id"] == source), None)
    if row is None:
        raise EngineError("unknown_source", f"no source with id {source} in the registry", EXIT_PRECONDITION)
    digest = sha256_file(target)
    if digest != row["sha256"]:
        raise EngineError("not_a_duplicate", f"{rel} has a different SHA-256 than {source}", EXIT_PRECONDITION, rel)
    name = nfc(target.name)
    extract_rel = row["extract"]
    aliases = list(writer.read_front(root, extract_rel).get("aliases") or [])
    add_alias = name != row.get("original_name") and name not in aliases
    plan = {"duplicate_of": source, "alias_added": name if add_alias else None, "extract": extract_rel}
    if dry_run:
        return {"status": "planned", **plan}
    _intake_lease(project, lease)
    targets = []
    if add_alias:
        frontmatter.update(root / extract_rel, {"aliases": [*aliases, name]})
        targets.append(extract_rel)
    size = target.stat().st_size
    remove_file(target)
    targets.append(rel)
    return {"status": "merged", **plan, "removed": [{"path": rel, "size": size, "sha256": digest}], "targets": targets}


def reject(project: Project, target_arg: str, *, mode: str | None, bundle_slug: str | None,
           member_path: str | None, lease: str | None, include_unplaced: bool, confirm: str | None,
           dry_run: bool) -> dict[str, Any]:
    """007/R14: apply ``intake.on_reject`` to a rejected item (an inbox file or a staged bundle member)."""
    root = project.root
    cfg = effective(project.config())
    mode = mode or cfg["intake"]["on_reject"]
    if mode not in ("leave-in-inbox", "move-to-shared", "delete"):
        raise EngineError("bad_mode", f"unknown on_reject mode {mode!r}", EXIT_USAGE)
    target, inbox_rel = _locate(project, target_arg)
    if mode == "leave-in-inbox":
        return {"status": "left", "mode": mode, "path": inbox_rel or str(target)}
    if mode == "delete":
        if inbox_rel is None:  # a staged copy disappears with staging; the inbox original is removed by `--discard`
            return {"status": "dropped-with-staging", "mode": mode}
        out = discard(project, target_arg, lease=lease, include_unplaced=True, confirm=confirm, dry_run=dry_run)
        return {**out, "mode": mode}
    if not target.is_file():
        raise EngineError("not_a_file", "move-to-shared handles single files", EXIT_PRECONDITION)
    tail = member_path or (inbox_rel[len(INBOX) + 1:] if inbox_rel else nfc(target.name))
    parts = [nfc(p) for p in PurePosixPath(tail).parts]
    if bundle_slug and member_path:
        parts = [slugify(bundle_slug), *parts]
    dest_rel = "/".join([SHARED_REJECTS, *parts])
    if (root / dest_rel).exists():
        stem, dot, ext = dest_rel.rpartition(".")
        dest_rel = f"{stem}-{hash6(dest_rel)}.{ext}" if dot and "/" not in ext else f"{dest_rel}-{hash6(dest_rel)}"
    check = check_relpath(dest_rel, int(cfg["policy"].get("path_limit", 200)))
    if not check.ok:
        raise EngineError("path_too_long", f"{dest_rel}: {'; '.join(check.problems)}", EXIT_PRECONDITION, dest_rel)
    if dry_run:
        return {"status": "planned", "mode": mode, "destination": dest_rel}
    if inbox_rel:
        _intake_lease(project, lease)
    safe_move(target, root / dest_rel)
    return {"status": "moved", "mode": mode, "destination": dest_rel, "targets": [dest_rel, *([inbox_rel] if inbox_rel else [])]}


# ----------------------------------------------------------------------------- re-triage, proposals, events
def retriage(project: Project, source: str, *, decision: str | None, triage: dict[str, Any] | None, actor: str,
             lease: str | None, confirm_policy: bool, dry_run: bool) -> dict[str, Any]:
    """007/R17: change a placed source's decision by updating its extraction frontmatter. Never moves the original."""
    root = project.root
    row = next((r for r in registry.build(root).rows if r["id"] == source), None)
    if row is None:
        raise EngineError("unknown_source", f"no source with id {source} in the registry", EXIT_PRECONDITION)
    tri = _triage(decision, triage, actor, DECISIONS)
    old = (writer.read_front(root, row["extract"]) or {}).get("triage") or {}
    plan = {"id": source, "extract": row["extract"], "from": old.get("decision"), "to": tri["decision"], "triage": tri}
    if tri["decision"] == "wiki":
        front = frontmatter.read(root / row["extract"])
        findings = policy_mod.scan_text(front.body, effective(project.config()).get("policy"))
        if findings:
            where = "; ".join(f"{f.rule} at {f.location}" for f in findings[:10])
            raise EngineError("policy_blocked", f"the policy gate matched this source, so it cannot become 'wiki' "
                              f"(007/R9): {where}", EXIT_PRECONDITION)
    if dry_run:
        return {"status": "planned", **plan}
    _intake_lease(project, lease)
    if old.get("decision") == tri["decision"] and old.get("reason") == tri["reason"]:
        return {"status": "unchanged", **plan, "targets": []}
    frontmatter.update(root / row["extract"], {"triage": tri})
    return {"status": "retriaged", **plan, "targets": [row["extract"]]}


def save_proposal(project: Project, proposal_file: str, actor: str, dry_run: bool) -> dict[str, Any]:
    """007/R11 (headless): persist the proposal for a later session; nothing is placed."""
    path = Path(proposal_file)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise EngineError("bad_proposal", f"cannot read proposal JSON: {exc}", EXIT_USAGE) from exc
    stamp = now_iso().replace("-", "").replace(":", "")
    rel = f"personal/{slugify(actor)}/intake-proposals/{stamp}.json"
    if dry_run:
        return {"status": "planned", "proposal": rel}
    atomic_write_text(project.root / rel, json.dumps(data, ensure_ascii=False, indent=2) + "\n", overwrite=False)
    return {"status": "saved", "proposal": rel, "targets": [rel]}


def log_completed(project: Project, summary: dict[str, Any], actor: str, dry_run: bool) -> dict[str, Any]:
    """007/R16: one ``intake.completed`` event with the per-decision counts."""
    if not dry_run:
        append_event(project.root, command="intake.completed", targets=[], result="ok", actor=actor,
                     extra={"summary": summary})
    return {"status": "logged" if not dry_run else "planned", "event": "intake.completed", "summary": summary}
