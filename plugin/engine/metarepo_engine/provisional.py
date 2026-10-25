"""Provisional extraction in local staging (007/R7, 007/ADR-002).

``extract`` only accepts files under ``10_sources/`` (008), so intake calls the same adapter selection and
quality scoring directly on a staged or inbox file and writes a plain Markdown file *outside the synced
tree*. Nothing here touches the metarepository. The policy gate (007/R9) runs on the result.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from . import policy as policy_mod
from .config import effective
from .extract.base import ExtractOptions, normalize_body, score_quality, thresholds_from
from .extract.registry import SelectionError, all_adapters, select
from .fsops import atomic_write_text, local_cache_dir
from .mediatype import detect
from .root import Project


def provisional_extract(project: Project, source: Path, out_dir: Path, name: str) -> dict[str, Any]:
    """Extract ``source`` to ``out_dir/<name>.md``; return quality, title, location and the policy verdict.

    Never raises for an extractor problem: a failed provisional extraction is reported (``quality: failed``)
    and the item is still triaged on its name and metadata.
    """
    cfg = effective(project.config())
    ecfg = cfg["extraction"]
    media = detect(source).media_type
    body, title, quality, notes, adapter_name = "", None, "failed", [], None
    try:
        sel = select(all_adapters(ecfg), media, ecfg, None)
    except SelectionError as exc:
        sel = None
        notes.append(exc.message)
    if sel is not None:
        work = None
        if project.project_id:
            work = local_cache_dir(project.project_id, create=True) / "work"
            work.mkdir(parents=True, exist_ok=True)
        options = ExtractOptions(media_type=media, large_table_rows=int(ecfg.get("large_table_rows", 200)),
                                 work_dir=work)
        for adapter in sel.candidates:
            try:
                result = adapter.extract(source, options)
            except Exception as exc:  # noqa: BLE001 - one bad adapter must not abort the scan
                notes.append(f"{adapter.name}: {type(exc).__name__}: {exc}")
                continue
            body = normalize_body(result.body)
            title = result.title
            quality, more = score_quality(body, result.structure, result.signals,
                                          thresholds_from(ecfg.get("quality_thresholds")))
            notes.extend([*result.notes, *more])
            adapter_name = adapter.name
            break
        else:
            if not notes:
                notes.append(f"no adapter is available for {media}")
    out_dir.mkdir(parents=True, exist_ok=True)
    target = out_dir / f"{name}.md"
    atomic_write_text(target, body)
    findings = policy_mod.scan_text(body, cfg.get("policy"))
    return {
        "path": str(target), "media_type": media, "quality": quality, "extractor": adapter_name, "title": title,
        "chars": len(body), "notes": notes,
        "policy": {"blocked": bool(findings), "findings": [f.as_dict() for f in findings]},
    }
