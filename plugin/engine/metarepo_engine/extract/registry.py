"""Adapter discovery and selection (008/R3).

Order of candidates for a media type: the adapters named in ``extraction.preferences[media_type]``
(exact key, then ``type/*``), in the listed order, then every other adapter by descending priority
(ties broken by discovery order). Unavailable adapters and, unless ``extraction.allow_cloud`` is true,
cloud adapters are skipped with a recorded reason (P11).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .adapters import builtin_adapters
from .adapters.command import from_config
from .base import Adapter, media_matches


class SelectionError(Exception):
    def __init__(self, code: str, message: str, exit_code: int):
        super().__init__(message)
        self.code = code
        self.message = message
        self.exit_code = exit_code


@dataclass
class Selection:
    candidates: list[Adapter] = field(default_factory=list)
    skipped: list[dict[str, str]] = field(default_factory=list)


def all_adapters(extraction_config: dict[str, Any] | None) -> list[Adapter]:
    cfg = extraction_config or {}
    return from_config(cfg.get("commands")) + builtin_adapters()


def _preferred(prefs: dict[str, Any], media_type: str) -> list[str]:
    if media_type in prefs and isinstance(prefs[media_type], list):
        return [str(x) for x in prefs[media_type]]
    for key, value in prefs.items():
        if isinstance(value, list) and "*" in key and media_matches(key, media_type):
            return [str(x) for x in value]
    return []


def select(adapters: list[Adapter], media_type: str, extraction_config: dict[str, Any] | None,
           explicit: str | None = None) -> Selection:
    cfg = extraction_config or {}
    allow_cloud = bool(cfg.get("allow_cloud"))
    sel = Selection()
    by_name = {a.name: a for a in adapters}
    if explicit:
        adapter = by_name.get(explicit)
        if adapter is None:
            raise SelectionError("unknown_extractor", f"no extractor named {explicit!r}; see `extractors`", 2)
        if adapter.mode == "cloud" and not allow_cloud:
            raise SelectionError("cloud_disabled", f"extractor {explicit!r} is a cloud extractor and "
                                 "extraction.allow_cloud is false (P11)", 3)
        if not adapter.available():
            raise SelectionError("extractor_unavailable", f"extractor {explicit!r} is not available: "
                                 f"{adapter.install_hint or 'not installed'}", 5)
        if adapter.handles(media_type) <= 0:
            raise SelectionError("extractor_unsupported", f"extractor {explicit!r} does not handle {media_type}", 2)
        sel.candidates.append(adapter)
        return sel
    preferred = [by_name[n] for n in _preferred(cfg.get("preferences") or {}, media_type) if n in by_name]
    rest = sorted((a for a in adapters if a not in preferred), key=lambda a: -a.handles(media_type))
    seen: set[str] = set()
    for adapter in preferred + rest:
        if adapter.name in seen or adapter.handles(media_type) <= 0:
            continue
        seen.add(adapter.name)
        if adapter.mode == "cloud" and not allow_cloud:
            sel.skipped.append({"name": adapter.name, "reason": "cloud extractor and extraction.allow_cloud is false"})
        elif not adapter.available():
            sel.skipped.append({"name": adapter.name, "reason": "not available"})
        else:
            sel.candidates.append(adapter)
    return sel
