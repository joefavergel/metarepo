"""Governance sign-offs (010/R17, 010/ADR-002): recorded, timestamped, never gating, identities claimed.

Roles map to the OKF sign-off keys: ``author`` -> ``generated``, ``reviewer`` -> ``verified``, ``auditor`` ->
``audited``. Each key holds ``{by: human:<id>, at: <UTC timestamp>}`` (the latest sign-off of the role); the
history is the line added to ``30_wiki/log.md`` and the engine event log. The same actor in two roles on one
page is allowed and reported as a ``segregation_of_duties`` warning.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any

from .. import frontmatter, leases
from ..events import now_iso
from ..jsonout import EXIT_PRECONDITION, EXIT_USAGE, EngineError
from ..okf import is_okf_content_page
from . import bundle

ROLE_KEYS = {"author": "generated", "reviewer": "verified", "auditor": "audited"}
LOG_KIND = "Sign-off"


def claimed(actor: str) -> str:
    return actor if actor.startswith("human:") else f"human:{actor}"


def resolve_page(root: Path, page: str) -> tuple[Path, str]:
    """``(absolute path, root-relative POSIX path)``; ``page`` is relative to the root or to ``30_wiki/``."""
    text = page.replace("\\", "/").strip()
    rel = PurePosixPath(text)
    candidates = [rel] if str(rel).startswith(bundle.WIKI_DIR + "/") else [PurePosixPath(bundle.WIKI_DIR) / rel, rel]
    wiki = (Path(root) / bundle.WIKI_DIR).resolve()
    for cand in candidates:
        path = (Path(root) / cand).resolve()
        try:
            inside = path.relative_to(wiki).as_posix()
        except ValueError:
            continue
        if path.is_file() and is_okf_content_page(inside):
            return path, path.relative_to(Path(root).resolve()).as_posix()
    raise EngineError("page_not_found", f"{page!r} is not a content page of {bundle.WIKI_DIR}/ "
                      "(index.md, log.md and WIKI.md cannot be signed)", EXIT_PRECONDITION)


@dataclass
class SignPlan:
    key: str
    entry: dict[str, Any]
    updates: dict[str, Any]
    warnings: list[tuple[str, str]] = field(default_factory=list)


def plan(front: dict[str, Any], role: str, actor: str, *, stable: bool = False, now: str | None = None,
         assisted_by: str | None = None) -> SignPlan:
    if role not in ROLE_KEYS:
        raise EngineError("bad_role", f"role must be one of {', '.join(ROLE_KEYS)}", EXIT_USAGE)
    by = claimed(actor)
    key = ROLE_KEYS[role]
    entry: dict[str, Any] = {"by": by, "at": now or now_iso()}
    previous = front.get(key)
    if isinstance(previous, dict) and previous.get("by") == by and previous.get("assisted_by") and not assisted_by:
        entry["assisted_by"] = previous["assisted_by"]
    if assisted_by:
        entry["assisted_by"] = assisted_by
    warnings: list[tuple[str, str]] = []
    for other_role, other_key in ROLE_KEYS.items():
        existing = front.get(other_key)
        if other_key != key and isinstance(existing, dict) and existing.get("by") == by:
            warnings.append(("segregation_of_duties",
                             f"{by} already signed this page as {other_role}; the {role} sign-off is recorded "
                             "anyway (sign-offs never gate)"))
    updates: dict[str, Any] = {key: entry}
    if stable:
        if front.get("status") == "deprecated":
            raise EngineError("page_deprecated", "a deprecated page cannot be marked stable", EXIT_PRECONDITION)
        updates["status"] = "stable"
    return SignPlan(key, entry, updates, warnings)


def apply(root: Path, handle: leases.LeaseHandle, page_path: Path, page_rel: str, sp: SignPlan, role: str) -> None:
    """Write the sign-off into the page and a line into ``log.md``; the caller holds the ``wiki`` lease."""
    handle.ensure()
    doc = frontmatter.update(page_path, sp.updates)
    title = doc.front.get("title") or page_rel
    by = sp.entry["by"]
    wiki_rel = page_rel[len(bundle.WIKI_DIR) + 1:]
    message = f"{role} {by} on [{title}]({wiki_rel})"
    if sp.warnings:
        message += " (warning: segregation of duties)"
    date = sp.entry["at"][:10]

    def transform(existing: bytes | None) -> bytes:
        text = existing.decode("utf-8") if existing else bundle.render_log()
        return bundle.add_log_entry(text, date, LOG_KIND, message).encode("utf-8")

    leases.update_hot_file(root, handle, f"{bundle.WIKI_DIR}/log.md", transform)
