"""Sync-provider knowledge shared by ``init`` (005) and ``/join-metarepo`` (006): detection and availability.

The rules live in ``templates/sync-providers.yaml`` (P3); this module only reads them.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from .conflicts import load_providers


def detect_provider(path: Path, spec: dict[str, Any] | None = None) -> str | None:
    """Provider whose ``detect`` fragments occur in a segment of ``path`` (heuristic), else None."""
    spec = spec or load_providers()
    parts = [part.lower() for part in Path(path).resolve().parts]
    for name, entry in (spec.get("providers") or {}).items():
        hints = (entry or {}).get("detect") or []
        if any(h in part for part in parts for h in hints):
            return name
    return None


def availability(provider: str | None, spec: dict[str, Any] | None = None) -> dict[str, Any]:
    """Recommended per-folder offline availability (004/R13) and how-to text for ``provider``."""
    spec = spec or load_providers()
    howto = spec.get("availability_howto") or {}
    return {
        "provider": provider or "none",
        "folders": [{"path": a["path"], "mode": a["mode"]} for a in spec.get("availability") or []],
        "howto": howto.get(provider) if provider else None,
        "verified": howto.get("verified"),
    }
