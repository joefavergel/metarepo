"""The wiki configuration gate in ``_system/state.json`` (002 section 5, 009/R1, R8).

State keys under ``wiki``: ``configured`` (bool), ``configured_at``, ``bundle``; this spec adds the optional
``scaffolded_at`` (set by ``wiki scaffold``) and ``reconfigured_at``. Writers go through
``leases.update_hot_file`` so that the read-modify-write is one atomic replace under a lease (004/R8).

API for the other wiki commands::

    status(state)            -> {"configured", "configured_at", "scaffolded_at", "bundle", ...}
    require_configured(proj) -> raises EngineError(wiki_not_configured, exit 3) -- feed (010) and consult (011)
    feed_allowed(state)      -> configured, or scaffolded and not yet completed (the initial feed of /init-wiki)
"""
from __future__ import annotations

import json
from typing import Any

from .. import leases
from ..events import now_iso
from ..jsonout import EXIT_PRECONDITION, EngineError
from ..root import Project

STATE_REL = "_system/state.json"


def status(state: dict[str, Any]) -> dict[str, Any]:
    wiki = state.get("wiki") or {}
    return {
        "configured": bool(wiki.get("configured")),
        "configured_at": wiki.get("configured_at"),
        "scaffolded_at": wiki.get("scaffolded_at"),
        "reconfigured_at": wiki.get("reconfigured_at"),
        "bundle": wiki.get("bundle", "30_wiki"),
        "metarepo_configured": bool((state.get("metarepo") or {}).get("configured")),
    }


def require_metarepo_configured(project: Project) -> None:
    if not status(project.state())["metarepo_configured"]:
        raise EngineError("metarepo_not_configured",
                          "the metarepository is not configured yet; create it with /create-metarepo",
                          EXIT_PRECONDITION)


def require_configured(project: Project) -> None:
    """Gate for /feed-wiki (010/R1) and /consult-wiki (011/R1). Exit 3 with a pointer to /init-wiki."""
    if not status(project.state())["configured"]:
        raise EngineError("wiki_not_configured",
                          "the wiki is not configured yet; run /init-wiki first", EXIT_PRECONDITION)


def feed_allowed(state: dict[str, Any]) -> bool:
    """True once the gate is open, and also between ``wiki scaffold`` and ``wiki gate --complete``: /init-wiki
    chains the first feed before the gate opens (009/R7, R8)."""
    s = status(state)
    return s["configured"] or bool(s["scaffolded_at"])


def _update_state(root, handle, change) -> dict[str, Any]:
    out: dict[str, Any] = {}

    def transform(existing: bytes | None) -> bytes:
        state = json.loads(existing.decode("utf-8")) if existing else {}
        change(state.setdefault("wiki", {"configured": False, "configured_at": None, "bundle": "30_wiki"}))
        out.update(state["wiki"])
        return (json.dumps(state, indent=2, ensure_ascii=False) + "\n").encode("utf-8")

    leases.update_hot_file(root, handle, STATE_REL, transform)
    return out


def mark_scaffolded(root, handle, *, reconfigure: bool = False, now: str | None = None) -> dict[str, Any]:
    stamp = now or now_iso()

    def change(wiki: dict[str, Any]) -> None:
        if reconfigure and wiki.get("configured"):
            wiki["reconfigured_at"] = stamp
        else:
            wiki.setdefault("scaffolded_at", stamp)

    return _update_state(root, handle, change)


def mark_configured(root, handle, *, now: str | None = None) -> dict[str, Any]:
    """Open the gate: ``configured = true`` and ``configured_at`` (kept if already set, so it is idempotent)."""
    stamp = now or now_iso()

    def change(wiki: dict[str, Any]) -> None:
        wiki["configured"] = True
        wiki["configured_at"] = wiki.get("configured_at") or stamp

    return _update_state(root, handle, change)
