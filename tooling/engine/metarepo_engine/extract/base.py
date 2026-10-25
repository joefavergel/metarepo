"""Adapter protocol, extraction result and quality scoring (008/R2, R4).

An adapter turns one file into a Markdown body. The engine never trusts an adapter's own opinion of
its output: ``score_quality`` derives ``quality`` from measurable signals (R4).
"""
from __future__ import annotations

import fnmatch
import importlib.metadata
import importlib.util
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# Default thresholds (R4). Projects override numeric keys through ``extraction.quality_thresholds``.
DEFAULT_THRESHOLDS: dict[str, float] = {
    "min_chars_per_page": 25.0,     # average non-whitespace characters per page below this: needs-review
    "empty_pages_partial": 0.01,    # share of empty pages at or above this: partial
    "empty_pages_review": 0.5,      # ... at or above this: needs-review
    "replacement_partial": 0.001,   # share of U+FFFD characters at or above this: partial
    "replacement_review": 0.02,     # ... at or above this: needs-review
    "ocr_partial": 0.85,            # OCR confidence (0..1) below this: partial
    "ocr_review": 0.6,              # ... below this: needs-review
    "coverage_partial": 0.8,        # transcript coverage of the media duration below this: partial
    "coverage_review": 0.5,         # ... below this: needs-review
}

# Engine-side minimum adapter versions (R6 staleness). ``extraction.quality_thresholds.min_extractor_versions``
# in project.yaml adds to / overrides this table. [TBD in the spec: where the configured minimum lives.]
DEFAULT_MIN_VERSIONS: dict[str, str] = {}

QUALITY_ORDER = ("high", "partial", "needs-review", "failed")


class AdapterError(Exception):
    """An adapter could not extract this file (corrupt input, timeout, tool error)."""


@dataclass
class ExtractOptions:
    media_type: str = ""  # detected media type of the file (adapters that serve several types branch on it)
    large_table_rows: int = 200
    timeout_s: int = 300
    work_dir: Path | None = None  # scratch directory for external tools (never inside the synced tree)


@dataclass
class ExtractResult:
    body: str
    title: str | None = None
    language: str | None = None
    structure: dict[str, Any] = field(default_factory=dict)
    # Measurable signals for R4: pages, empty_pages, ocr_confidence, duration_s, covered_s.
    signals: dict[str, Any] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)


class Adapter:
    """Protocol of 008/R2. Subclasses override ``handles`` and ``extract``."""

    name = "adapter"
    mode = "local"  # local | cloud
    kind = "builtin"  # builtin | optional | command
    install_hint: str | None = None
    media_summary: tuple[str, ...] = ()

    def version(self) -> str:
        return "1"

    def available(self) -> bool:
        return True

    def handles(self, media_type: str) -> int:
        """Priority (higher wins) for ``media_type``; 0 means not handled."""
        return 0

    def extract(self, path: Path, options: ExtractOptions) -> ExtractResult:  # pragma: no cover - abstract
        raise NotImplementedError


def media_matches(pattern: str, media_type: str) -> bool:
    pattern, media_type = pattern.strip().lower(), media_type.strip().lower()
    return pattern == media_type or fnmatch.fnmatchcase(media_type, pattern)


def module_available(name: str) -> bool:
    """Detect an optional module without importing it (P13). Honours modules injected into sys.modules."""
    if sys.modules.get(name) is not None:
        return True
    try:
        return importlib.util.find_spec(name) is not None
    except (ImportError, ValueError, AttributeError):
        return False


def dist_version(dist: str) -> str:
    try:
        return importlib.metadata.version(dist)
    except importlib.metadata.PackageNotFoundError:
        return "unknown"


def normalize_body(text: str) -> str:
    """LF line endings, no trailing whitespace on lines, exactly one final newline (empty stays empty)."""
    text = text.replace("\r\n", "\n").replace("\r", "\n").lstrip("﻿")
    lines = [ln.rstrip() for ln in text.split("\n")]
    out = "\n".join(lines).strip("\n")
    return out + "\n" if out else ""


def thresholds_from(config_thresholds: Any) -> dict[str, float]:
    merged = dict(DEFAULT_THRESHOLDS)
    if isinstance(config_thresholds, dict):
        for key, value in config_thresholds.items():
            if key in merged and isinstance(value, (int, float)) and not isinstance(value, bool):
                merged[key] = float(value)
    return merged


def worst(a: str, b: str) -> str:
    return a if QUALITY_ORDER.index(a) >= QUALITY_ORDER.index(b) else b


def score_quality(body: str, structure: dict[str, Any] | None, signals: dict[str, Any] | None,
                  thresholds: dict[str, float] | None = None) -> tuple[str, list[str]]:
    """R4: ``(quality, notes)`` from text-to-page ratio, replacement characters, empty pages, OCR confidence
    and transcript coverage. Never returns ``failed`` (that is reserved for adapter failure)."""
    th = thresholds or DEFAULT_THRESHOLDS
    structure, signals = structure or {}, signals or {}
    quality, notes = "high", []

    def bump(level: str, note: str) -> None:
        nonlocal quality
        quality = worst(quality, level)
        notes.append(note)

    text_chars = sum(1 for ch in body if not ch.isspace())
    if text_chars == 0:
        bump("needs-review", "no text was extracted (image-only or scanned content, or an empty source)")
        return quality, notes
    pages = signals.get("pages") or structure.get("pages")
    if isinstance(pages, int) and pages > 0:
        per_page = text_chars / pages
        if per_page < th["min_chars_per_page"]:
            bump("needs-review", f"only {per_page:.0f} characters per page on average (likely scanned)")
        empty = signals.get("empty_pages")
        if isinstance(empty, int) and empty > 0:
            share = empty / pages
            if share >= th["empty_pages_review"]:
                bump("needs-review", f"{empty} of {pages} pages have no text")
            elif share >= th["empty_pages_partial"]:
                bump("partial", f"{empty} of {pages} pages have no text")
    replacements = body.count("�")
    if replacements:
        share = replacements / max(len(body), 1)
        if share >= th["replacement_review"]:
            bump("needs-review", f"{replacements} undecodable characters ({share:.1%} of the text)")
        elif share >= th["replacement_partial"]:
            bump("partial", f"{replacements} undecodable characters")
    ocr = signals.get("ocr_confidence")
    if isinstance(ocr, (int, float)):
        if ocr < th["ocr_review"]:
            bump("needs-review", f"OCR confidence {ocr:.2f}")
        elif ocr < th["ocr_partial"]:
            bump("partial", f"OCR confidence {ocr:.2f}")
    duration, covered = signals.get("duration_s"), signals.get("covered_s")
    if isinstance(duration, (int, float)) and duration > 0 and isinstance(covered, (int, float)):
        coverage = covered / duration
        if coverage < th["coverage_review"]:
            bump("needs-review", f"transcript covers {coverage:.0%} of the duration")
        elif coverage < th["coverage_partial"]:
            bump("partial", f"transcript covers {coverage:.0%} of the duration")
    return quality, notes


def version_tuple(text: str) -> tuple[int, ...]:
    parts = []
    for piece in str(text).split("."):
        digits = "".join(ch for ch in piece if ch.isdigit())
        if not digits:
            break
        parts.append(int(digits))
    return tuple(parts)
