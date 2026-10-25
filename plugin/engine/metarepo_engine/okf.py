"""OKF document model (003 plan; 009/ADR-001).

An OKF page is Markdown with YAML frontmatter where ``type`` is required. The toolkit uses ``type``,
``title``, ``description``, ``sources[]`` (each with a stable ``id``), keyed footnotes ``[^id]``,
``generated`` / ``verified`` / ``audited``, ``status`` (``draft|stable|deprecated``), ``stale_after``,
``tags`` and relative Markdown links. Extra keys are preserved by the frontmatter layer.

This module is read-only: it exposes what lint (012), consult (011) and feed (010) need to query.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any
from urllib.parse import unquote

from . import frontmatter

RESERVED_FILES = frozenset({"index.md", "log.md", "WIKI.md"})
STATUSES = ("draft", "stable", "deprecated")

_FENCE = re.compile(r"^(```|~~~).*?^\1[^\n]*$", re.DOTALL | re.MULTILINE)
_FOOTNOTE_REF = re.compile(r"\[\^([^\]\s]+)\](?!:)")
_FOOTNOTE_DEF = re.compile(r"^\[\^([^\]\s]+)\]:", re.MULTILINE)
_LINK = re.compile(r"!?\[[^\]]*\]\(\s*<?([^)\s>]+)>?(?:\s+\"[^\"]*\")?\s*\)")
_EXTERNAL = re.compile(r"^(?:[A-Za-z][A-Za-z0-9+.-]*:|//)")


def _strip_code(body: str) -> str:
    return _FENCE.sub("", body)


@dataclass
class OkfPage:
    front: dict[str, Any]
    body: str
    path: Path | None = None
    parse_error: str | None = field(default=None)

    # ---- construction
    @classmethod
    def from_text(cls, text: str, path: Path | None = None) -> "OkfPage":
        doc = frontmatter.parse(text)
        return cls(doc.front, doc.body, path)

    @classmethod
    def load(cls, path: Path) -> "OkfPage":
        try:
            doc = frontmatter.read(path)
        except frontmatter.FrontmatterError as exc:
            return cls({}, "", path, parse_error=str(exc))
        return cls(doc.front, doc.body, path)

    # ---- properties
    @property
    def type(self) -> str | None:
        value = self.front.get("type")
        return value if isinstance(value, str) and value.strip() else None

    @property
    def title(self) -> str | None:
        value = self.front.get("title")
        return value if isinstance(value, str) else None

    @property
    def description(self) -> str | None:
        value = self.front.get("description")
        return value if isinstance(value, str) else None

    @property
    def status(self) -> str | None:
        value = self.front.get("status")
        return value if isinstance(value, str) else None

    @property
    def tags(self) -> list[str]:
        raw = self.front.get("tags")
        return [str(t) for t in raw] if isinstance(raw, list) else []

    @property
    def sources(self) -> list[dict[str, Any]]:
        raw = self.front.get("sources")
        return [s for s in raw if isinstance(s, dict)] if isinstance(raw, list) else []

    def source_ids(self) -> list[str]:
        return [str(s["id"]) for s in self.sources if s.get("id") is not None]

    # ---- body structure
    def footnote_refs(self) -> list[str]:
        """Footnote ids referenced in the body, in order of first appearance (code blocks ignored)."""
        seen: list[str] = []
        for m in _FOOTNOTE_REF.finditer(_strip_code(self.body)):
            if m.group(1) not in seen:
                seen.append(m.group(1))
        return seen

    def footnote_defs(self) -> list[str]:
        return list(dict.fromkeys(_FOOTNOTE_DEF.findall(_strip_code(self.body))))

    def dangling_footnotes(self) -> list[str]:
        """Referenced ``[^id]`` with no matching ``sources[].id`` (012 ``dangling-footnote``)."""
        ids = set(self.source_ids())
        return [f for f in self.footnote_refs() if f not in ids]

    def undefined_footnotes(self) -> list[str]:
        """Referenced ``[^id]`` with no ``[^id]:`` definition in the body."""
        defs = set(self.footnote_defs())
        return [f for f in self.footnote_refs() if f not in defs]

    def links(self) -> list[str]:
        """Relative link targets (fragment removed, percent-decoding applied); external links excluded."""
        out: list[str] = []
        for m in _LINK.finditer(_strip_code(self.body)):
            target = m.group(1)
            if _EXTERNAL.match(target) or target.startswith("#"):
                continue
            target = unquote(target.split("#", 1)[0].split("?", 1)[0])
            if target and target not in out:
                out.append(target)
        return out

    def resolve_link(self, target: str, root: Path) -> Path:
        """Filesystem path a relative link points to, from this page's folder (never escapes ``root``)."""
        if self.path is None:
            raise ValueError("page has no path")
        base = PurePosixPath(self.path.relative_to(root).as_posix()).parent
        parts: list[str] = []
        for part in (base / PurePosixPath(target)).parts:
            if part == "..":
                if parts:
                    parts.pop()
            elif part != ".":
                parts.append(part)
        return root.joinpath(*parts)

    # ---- governance view (011/R6)
    def trust_tier(self) -> str:
        """``audited`` > ``verified`` > ``unreviewed``, from the sign-off keys. Never forged, only read."""
        if self.front.get("audited"):
            return "audited"
        if self.front.get("verified"):
            return "verified"
        return "unreviewed"

    def is_expired(self, now: datetime | None = None) -> bool:
        raw = self.front.get("stale_after")
        if raw is None:
            return False
        now = now or datetime.now(timezone.utc)
        if isinstance(raw, datetime):
            when = raw if raw.tzinfo else raw.replace(tzinfo=timezone.utc)
        elif isinstance(raw, date):
            when = datetime(raw.year, raw.month, raw.day, tzinfo=timezone.utc)
        else:
            try:
                text = str(raw).strip().replace("Z", "+00:00")
                when = datetime.fromisoformat(text)
            except ValueError:
                return False
            if when.tzinfo is None:
                when = when.replace(tzinfo=timezone.utc)
        return now > when

    # ---- structural validation
    def problems(self) -> list[str]:
        out: list[str] = []
        if self.parse_error:
            return [f"unreadable: {self.parse_error}"]
        if self.type is None:
            out.append("missing required frontmatter key 'type'")
        if self.status is not None and self.status not in STATUSES:
            out.append(f"status {self.status!r} is not one of {', '.join(STATUSES)}")
        raw = self.front.get("sources")
        if raw is not None:
            if not isinstance(raw, list):
                out.append("'sources' must be a list")
            else:
                ids: list[str] = []
                for i, s in enumerate(raw):
                    if not isinstance(s, dict) or s.get("id") is None:
                        out.append(f"sources[{i}] needs an 'id'")
                    else:
                        if str(s["id"]) in ids:
                            out.append(f"duplicate sources id {s['id']!r}")
                        ids.append(str(s["id"]))
        return out


def is_okf_content_page(rel_posix: str) -> bool:
    """Every ``.md`` page of the bundle except ``index.md``, ``log.md`` and ``WIKI.md`` (009/ADR-001)."""
    p = PurePosixPath(rel_posix)
    return p.suffix == ".md" and p.name not in RESERVED_FILES
