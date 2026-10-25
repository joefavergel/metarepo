"""Reading and checking the OKF bundle in ``30_wiki/`` (009/ADR-001), plus text helpers for ``index.md``
and ``log.md`` (OKF v0.2 section "Index and Log Conventions").

Reads never write. The text helpers are pure (text in, text out): callers write the result under the
``wiki`` lease, e.g. with ``leases.update_hot_file(root, handle, "30_wiki/index.md", transform)``.

OKF conventions followed here:

* ``index.md`` has no frontmatter except the optional ``okf_version`` at the bundle root; the body has
  headed sections with ``* [Title](relative-url) - description`` entries.
* ``log.md`` is grouped by ``## YYYY-MM-DD`` headings, newest first, entries are bullets conventionally led
  by a bold word (**Creation**, **Update**, **Deprecation**).
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from string import Template
from typing import Iterator

from ..okf import OkfPage, is_okf_content_page
from ..templates import templates_dir
from .config import WikiConfig

WIKI_DIR = "30_wiki"
OKF_VERSION = "0.2"
_SKIP_DIR = re.compile(r"^[._]")  # hidden and machine folders (``_meta`` ...) are not part of the bundle
_DATE_HEADING = re.compile(r"^## (\d{4}-\d{2}-\d{2})\s*$", re.MULTILINE)


def wiki_dir(root: Path) -> Path:
    return Path(root) / WIKI_DIR


def iter_page_paths(root: Path) -> Iterator[Path]:
    """Every OKF content page of the bundle (sorted, POSIX order): all ``.md`` except ``index.md``,
    ``log.md`` and ``WIKI.md``; hidden and ``_`` folders are skipped."""
    base = wiki_dir(root)
    if not base.is_dir():
        return
    found: list[Path] = []
    for path in base.rglob("*.md"):
        rel = path.relative_to(base)
        if any(_SKIP_DIR.match(part) for part in rel.parts[:-1]):
            continue
        if is_okf_content_page(rel.as_posix()):
            found.append(path)
    yield from sorted(found, key=lambda p: p.relative_to(base).as_posix())


def iter_pages(root: Path) -> Iterator[OkfPage]:
    for path in iter_page_paths(root):
        yield OkfPage.load(path)


@dataclass
class BundleReport:
    pages: int = 0
    errors: list[dict[str, str]] = field(default_factory=list)  # {path, code, message}
    warnings: list[dict[str, str]] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors


def validate(root: Path, cfg: WikiConfig | None = None) -> BundleReport:
    """Structural check of the bundle: every page parseable with a ``type`` and well-formed ``status`` and
    ``sources`` (errors); pages whose ``type`` is not declared in ``WIKI.md`` (warning). Link and
    provenance checks belong to lint (012)."""
    report = BundleReport()
    known = set(cfg.type_names()) if cfg else None
    base = wiki_dir(root)
    for page in iter_pages(root):
        report.pages += 1
        rel = f"{WIKI_DIR}/{page.path.relative_to(base).as_posix()}"
        for problem in page.problems():
            report.errors.append({"path": rel, "code": "invalid-page", "message": problem})
        if known is not None and page.type and page.type not in known:
            report.warnings.append({"path": rel, "code": "undeclared-type",
                                    "message": f"type {page.type!r} is not declared in WIKI.md"})
    return report


def _template(name: str) -> str:
    return (templates_dir() / "wiki" / name).read_text(encoding="utf-8").replace("\r\n", "\n")


# ---- index.md
def render_index(cfg: WikiConfig, title: str) -> str:
    """The initial ``index.md``: bundle-root ``okf_version`` frontmatter and one empty section per group."""
    groups = "\n".join(f"## {g}\n" for g in cfg.index_groups())
    return Template(_template("index.md.tmpl")).substitute(title=title, groups=groups)


def index_entry(title: str, href: str, description: str | None) -> str:
    entry = f"* [{title}]({href})"
    return f"{entry} - {description.strip()}" if description and description.strip() else entry


def add_index_entry(text: str, group: str, title: str, href: str, description: str | None = None) -> str:
    """Add (or refresh, matched by link target) one entry under ``## <group>``; the section is created at the
    end when absent. Idempotent (P12)."""
    text = text.replace("\r\n", "\n")
    entry = index_entry(title, href, description)
    lines = text.split("\n")
    heading = f"## {group}"
    start = next((i for i, ln in enumerate(lines) if ln.strip() == heading), None)
    if start is None:
        body = text.rstrip("\n")
        return f"{body}\n\n{heading}\n\n{entry}\n"
    end = next((i for i in range(start + 1, len(lines)) if lines[i].startswith("## ")), len(lines))
    needle = f"]({href})"
    for i in range(start + 1, end):
        if lines[i].startswith("* [") and needle in lines[i]:
            lines[i] = entry
            return "\n".join(lines)
    section = lines[start + 1:end]
    while section and not section[-1].strip():
        section.pop()
    while section and not section[0].strip():
        section.pop(0)
    section.append(entry)
    rebuilt = lines[:start + 1] + [""] + section + [""] + lines[end:]
    return "\n".join(rebuilt).rstrip("\n") + "\n"


def ensure_index_groups(text: str, groups: list[str]) -> str:
    """Append a heading for every group that has no section yet (reconfiguration)."""
    out = text.replace("\r\n", "\n")
    for group in groups:
        if f"## {group}" not in [ln.strip() for ln in out.split("\n")]:
            out = out.rstrip("\n") + f"\n\n## {group}\n"
    return out


# ---- log.md
def render_log() -> str:
    return _template("log.md.tmpl")


def add_log_entry(text: str, date: str, kind: str, message: str) -> str:
    """Add ``* **<kind>** message`` under ``## <date>`` (newest date first, newest entry first within a day)."""
    text = text.replace("\r\n", "\n")
    bullet = f"* **{kind}** {message.strip()}"
    matches = list(_DATE_HEADING.finditer(text))
    for m in matches:
        if m.group(1) == date:
            rest = text[m.end():].lstrip("\n")
            return text[:m.end()] + "\n\n" + bullet + "\n" + rest
    block = f"## {date}\n\n{bullet}\n\n"
    for m in matches:
        if m.group(1) < date:
            return text[:m.start()] + block + text[m.start():]
    if matches:
        return text.rstrip("\n") + "\n\n" + block.rstrip("\n") + "\n"
    head = text.rstrip("\n")
    return (head + "\n\n" if head else "") + block.rstrip("\n") + "\n"

