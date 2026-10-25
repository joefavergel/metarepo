"""The extraction pipeline behind ``extract`` and ``extract-record`` (008).

Other specs call these functions directly (placement, intake staging, lint) so that there is one
implementation of selection, quality scoring and the file contract.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .. import registry as view_registry
from ..config import effective
from ..fsops import local_cache_dir, sha256_file
from ..hashing import SOURCE_ID_RE, source_id
from ..jsonout import EXIT_COMPAT, EXIT_PRECONDITION, EngineError
from ..mediatype import detect
from ..root import Project
from . import writer
from .base import (Adapter, AdapterError, ExtractOptions, ExtractResult, normalize_body, score_quality,
                   thresholds_from)
from .registry import SelectionError, all_adapters, select
from .staleness import min_versions, staleness

AGENT_READABLE = ("image/", "audio/", "application/pdf")


@dataclass
class Outcome:
    status: str  # written | unchanged | planned | kept
    source_id: str
    source_path: str
    extract: str
    media_type: str
    quality: str | None = None
    quality_notes: str = ""
    extractor: dict[str, Any] | None = None
    structure: dict[str, Any] = field(default_factory=dict)
    parts: int = 1
    written: list[str] = field(default_factory=list)
    removed: list[str] = field(default_factory=list)
    skipped: list[dict[str, str]] = field(default_factory=list)
    stale: list[dict[str, str]] = field(default_factory=list)
    warnings: list[tuple[str, str]] = field(default_factory=list)
    candidates: list[str] = field(default_factory=list)

    @property
    def agent_assist(self) -> dict[str, Any]:
        needs = self.quality in ("needs-review", "failed")
        readable = (self.media_type or "").startswith(AGENT_READABLE)
        return {"suggested": bool(needs and readable and (self.extractor or {}).get("mode") != "agent")}

    def as_data(self) -> dict[str, Any]:
        return {
            "status": self.status, "source_id": self.source_id, "source_path": self.source_path,
            "extract": self.extract, "media_type": self.media_type, "quality": self.quality,
            "quality_notes": self.quality_notes, "extractor": self.extractor, "structure": self.structure,
            "parts": self.parts, "written": self.written, "removed": self.removed, "skipped": self.skipped,
            "stale": self.stale, "agent_assist": self.agent_assist, "candidates": self.candidates,
        }


def resolve_source(root: Path, arg: str) -> tuple[Path, str]:
    """Accept a source id, a root-relative path or an absolute path; return (absolute, root-relative)."""
    if SOURCE_ID_RE.match(arg) and not (root / arg).exists():
        rows = [r for r in view_registry.build(root).rows if r["id"] == arg]
        if not rows:
            raise EngineError("unknown_source", f"no source with id {arg} in the registry", EXIT_PRECONDITION)
        arg = rows[0]["path"]
    given = Path(arg)
    candidate = given if given.is_absolute() else (root / given)
    if not candidate.exists() and not given.is_absolute() and Path.cwd().joinpath(given).exists():
        candidate = Path.cwd() / given
    if candidate.is_symlink():
        raise EngineError("is_symlink", "refusing to extract a symbolic link", EXIT_PRECONDITION)
    try:
        rel = candidate.resolve().relative_to(root).as_posix()
    except ValueError:
        raise EngineError("outside_root", "path is outside the metarepository", EXIT_PRECONDITION) from None
    if not rel.startswith(writer.SOURCES + "/"):
        raise EngineError("not_a_source", "only files under 10_sources/ have extractions", EXIT_PRECONDITION, rel)
    if not candidate.is_file():
        raise EngineError("file_not_found", f"no such source: {rel}", EXIT_PRECONDITION, rel)
    return candidate.resolve(), rel


def _work_dir(project: Project) -> Path | None:
    if not project.project_id:
        return None
    path = local_cache_dir(project.project_id, create=True) / "work"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _load_existing(root: Path, source_rel: str) -> dict[str, Any] | None:
    try:
        return writer.read_front(root, writer.extract_rel(source_rel))
    except Exception as exc:  # noqa: BLE001 - frontmatter errors, OSError
        raise EngineError("extraction_unreadable", f"existing extraction cannot be read: {exc}", EXIT_COMPAT,
                          writer.extract_rel(source_rel)) from exc


def _new_id(root: Path, sha: str) -> str:
    return source_id(sha, view_registry.build(root).known_ids())


def _max_bytes(cfg: dict[str, Any]) -> int:
    return int(float(cfg.get("max_body_mb", 2)) * 1024 * 1024)


def run_extract(project: Project, source_arg: str, *, force: bool = False, extractor: str | None = None,
                dry_run: bool = False, adapters: list[Adapter] | None = None) -> Outcome:
    root = project.root
    cfg = effective(project.config())["extraction"]
    abs_path, rel = resolve_source(root, source_arg)
    main_rel = writer.extract_rel(rel)
    sha = sha256_file(abs_path)
    existing = _load_existing(root, rel)
    sid = (existing or {}).get("source_id") or _new_id(root, sha)
    media = detect(abs_path).media_type
    out = Outcome("unchanged", sid, rel, main_rel, media)
    if writer.is_extracted(existing) and not force:
        out.quality = existing.get("quality")
        out.quality_notes = existing.get("quality_notes") or ""
        out.extractor = existing.get("extractor")
        out.structure = existing.get("structure") or {}
        out.stale = staleness(existing, sha, min_versions(cfg))
        for item in out.stale:
            out.warnings.append(("stale_extraction", item["message"] + "; run `extract --force` to regenerate"))
        return out
    pool = adapters if adapters is not None else all_adapters(cfg)
    try:
        sel = select(pool, media, cfg, extractor)
    except SelectionError as exc:
        raise EngineError(exc.code, exc.message, exc.exit_code) from None
    out.skipped = sel.skipped
    out.candidates = [a.name for a in sel.candidates]
    if dry_run:
        out.status = "planned"
        return out
    options = ExtractOptions(media_type=media, large_table_rows=int(cfg.get("large_table_rows", 200)),
                             work_dir=_work_dir(project))
    result: ExtractResult | None = None
    used: Adapter | None = None
    failures: list[str] = []
    for adapter in sel.candidates:
        try:
            result = adapter.extract(abs_path, options)
            used = adapter
            break
        except AdapterError as exc:
            failures.append(f"{adapter.name}: {exc}")
        except Exception as exc:  # noqa: BLE001 - a crashing adapter must not abort registration (R1)
            failures.append(f"{adapter.name}: {type(exc).__name__}: {exc}")
    if result is None or used is None:
        if writer.is_extracted(existing) and existing.get("quality") != "failed":
            out.status = "kept"
            out.warnings.append(("extraction_failed", "; ".join(failures) or f"no adapter for {media}"))
            out.quality = existing.get("quality")
            return out
        reason = "; ".join(failures) if failures else f"no adapter is available for {media}"
        if not failures and sel.skipped:
            reason += " (" + ", ".join(f"{s['name']}: {s['reason']}" for s in sel.skipped) + ")"
        result = ExtractResult(body="")
        quality, notes = "failed", [reason]
        extractor_meta = {"name": extractor or "none", "version": None, "mode": "local"}
        out.warnings.append(("extraction_failed", reason))
    else:
        body = normalize_body(result.body)
        result.body = body
        quality, notes = score_quality(body, result.structure, result.signals, thresholds_from(cfg.get("quality_thresholds")))
        notes = [*result.notes, *notes]
        extractor_meta = {"name": used.name, "version": used.version(), "mode": used.mode}
    _finish(project, out, abs_path, rel, existing, sid, sha, media, result, quality, notes, extractor_meta, cfg)
    return out


def _finish(project: Project, out: Outcome, abs_path: Path, rel: str, existing: dict[str, Any] | None, sid: str,
            sha: str, media: str, result: ExtractResult, quality: str, notes: list[str],
            extractor_meta: dict[str, Any], cfg: dict[str, Any]) -> None:
    pieces = writer.split_parts(result.body, _max_bytes(cfg))
    title = result.title or writer.jsonable((existing or {}).get("title")) or None
    if title is None:
        title = (existing or {}).get("original_name") or abs_path.stem
    front = writer.build_front(
        existing, source_id=sid, source_rel=rel, sha256=sha, size=abs_path.stat().st_size, media_type=media,
        original_name=abs_path.name, extractor=extractor_meta, quality=quality, notes="; ".join(notes),
        title=title, language=result.language, structure=result.structure, parts=len(pieces))
    if existing and existing.get("source_sha256") not in (None, sha):
        out.warnings.append(("source_modified", "the source changed since it was registered (sources are immutable); "
                             "source_sha256 was updated and source_id kept"))
    done = writer.write_extraction(project.root, rel, front, result.body, _max_bytes(cfg))
    out.status, out.quality, out.quality_notes = "written", quality, front["quality_notes"]
    out.extractor, out.structure, out.parts = extractor_meta, result.structure, done.parts
    out.written, out.removed = done.written, done.removed


_AGENT = re.compile(r"^agent:(.+)$")


def parse_agent_extractor(text: str) -> dict[str, Any]:
    m = _AGENT.match(text.strip())
    if not m or not m.group(1).strip():
        raise EngineError("bad_extractor", "--extractor must be agent:<model id>", 2)
    return {"name": "agent", "version": m.group(1).strip(), "mode": "agent"}


def run_record(project: Project, source_arg: str, *, body: str, extractor: str, quality: str | None = None,
               quality_notes: str | None = None, title: str | None = None, language: str | None = None,
               structure: dict[str, Any] | None = None, force: bool = False, dry_run: bool = False) -> Outcome:
    """Wrap agent-produced text into an extraction (008/R5). Replacing a ``high``/``partial`` extraction
    needs ``force``; the R7 fields of an existing record are always preserved."""
    meta = parse_agent_extractor(extractor)
    root = project.root
    cfg = effective(project.config())["extraction"]
    abs_path, rel = resolve_source(root, source_arg)
    existing = _load_existing(root, rel)
    if body.lstrip("﻿").startswith("---\n"):
        raise EngineError("body_has_frontmatter", "the body file must not carry its own frontmatter", 2)
    if writer.is_extracted(existing) and existing.get("quality") in ("high", "partial") and not force:
        raise EngineError("extraction_exists", f"{writer.extract_rel(rel)} already has quality "
                          f"{existing.get('quality')}; pass --force to replace it", EXIT_PRECONDITION)
    sha = sha256_file(abs_path)
    sid = (existing or {}).get("source_id") or _new_id(root, sha)
    media = detect(abs_path).media_type
    out = Outcome("planned" if dry_run else "written", sid, rel, writer.extract_rel(rel), media)
    text = normalize_body(body)
    structure = structure or {}
    scored, notes = score_quality(text, structure, {}, thresholds_from(cfg.get("quality_thresholds")))
    final = quality or scored
    note_list = [quality_notes] if quality_notes else ([] if quality else notes)
    if dry_run:
        out.quality, out.extractor = final, meta
        return out
    result = ExtractResult(body=text, title=title, language=language, structure=structure)
    _finish(project, out, abs_path, rel, existing, sid, sha, media, result, final, note_list, meta, cfg)
    return out
