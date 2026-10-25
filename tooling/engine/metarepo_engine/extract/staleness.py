"""Staleness and schema checks for extractions (008/R6); also called by lint (spec 012)."""
from __future__ import annotations

from typing import Any

from ..config import load_schema
from ..validate import Findings, validate
from .base import DEFAULT_MIN_VERSIONS, version_tuple
from .writer import jsonable


def min_versions(extraction_config: dict[str, Any] | None) -> dict[str, str]:
    """Engine table plus ``extraction.quality_thresholds.min_extractor_versions`` (the configured minimum)."""
    merged = dict(DEFAULT_MIN_VERSIONS)
    th = (extraction_config or {}).get("quality_thresholds")
    extra = th.get("min_extractor_versions") if isinstance(th, dict) else None
    if isinstance(extra, dict):
        merged.update({str(k): str(v) for k, v in extra.items()})
    return merged


def staleness(front: dict[str, Any], current_sha256: str | None,
              minimums: dict[str, str] | None = None) -> list[dict[str, str]]:
    """Reasons an extraction is stale: the source hash differs (tampering, since sources are immutable) or
    the extractor is older than the configured minimum. Empty list = fresh."""
    reasons: list[dict[str, str]] = []
    recorded = front.get("source_sha256")
    if current_sha256 and recorded and recorded != current_sha256:
        reasons.append({"code": "source_hash_mismatch",
                        "message": "source_sha256 differs from the current hash of source_path"})
    extractor = front.get("extractor") if isinstance(front.get("extractor"), dict) else {}
    wanted = (minimums or {}).get(str(extractor.get("name")))
    have = extractor.get("version")
    if wanted and have is not None and version_tuple(str(have)) < version_tuple(wanted):
        reasons.append({"code": "extractor_outdated",
                        "message": f"extractor {extractor.get('name')} {have} is older than the minimum {wanted}"})
    return reasons


def validate_front(front: dict[str, Any]) -> Findings:
    return validate(jsonable(front), load_schema("extract"))
