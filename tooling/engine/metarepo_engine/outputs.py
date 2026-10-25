"""Outputs: deliverables derived from the wiki, with recorded provenance (014/R1-R9, 014/ADR-001).

* Kinds: a kind is a folder holding ``template.md`` + ``instructions.md``. Built-in kinds ship with the toolkit
  (``templates/outputs/<kind>/``); a project adds or overrides kinds in ``outputs/_templates/<kind>/`` (R1; the
  project folder wins).
* Names: ``outputs/<yyyy-mm-dd>-<kind>-<slug>.<ext>``; an existing name is never overwritten, the next free
  revision suffix ``-v2``, ``-v3`` ... is used instead (R6).
* Provenance (R7): ``type: Output``, ``kind``, ``generated``, ``based_on`` (path + sha256 of every wiki page
  used) and ``wiki_index_sha256``, in the frontmatter, or in a sidecar ``<name>.meta.yaml`` for html.
* Staleness (R8): :func:`stale_outputs` compares the recorded hashes with the current pages; lint (012) reports
  each result as ``output-stale``.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator

from . import frontmatter
from .config import yaml_module
from .fsops import nfc, sha256_file
from .templates import templates_dir

OUTPUTS_DIR = "outputs"
TEMPLATES_DIR = "outputs/_templates"
WIKI_DIR = "30_wiki"
BUILTIN_KINDS = ("one-pager", "slides", "report", "brief", "faq")
KIND_FILES = ("template.md", "instructions.md")
FORMATS = ("markdown", "marp", "html", "artifact")
EXTENSIONS = {"markdown": ".md", "marp": ".md", "html": ".html", "artifact": ".md"}
SIDECAR = ".meta.yaml"
OUTPUT_TYPE = "Output"
_KIND_RE = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")


def builtin_dir() -> Path:
    return templates_dir() / "outputs"


def valid_kind_name(kind: str) -> bool:
    return bool(_KIND_RE.match(kind))


@dataclass
class Kind:
    name: str
    source: str  # "project" | "builtin"
    directory: Path
    overrides: bool = False  # a project kind that shadows a built-in one

    def files(self) -> list[str]:
        return [f for f in KIND_FILES if (self.directory / f).is_file()]


def _scan(base: Path, source: str) -> dict[str, Kind]:
    found: dict[str, Kind] = {}
    if not base.is_dir():
        return found
    for entry in sorted(base.iterdir()):
        if entry.is_dir() and valid_kind_name(entry.name) and (entry / "template.md").is_file():
            found[entry.name] = Kind(entry.name, source, entry)
    return found


def kinds(root: Path) -> dict[str, Kind]:
    """All usable kinds; a project kind overrides the built-in one of the same name (R1, AC3)."""
    merged = _scan(builtin_dir(), "builtin")
    for name, kind in _scan(Path(root) / TEMPLATES_DIR, "project").items():
        kind.overrides = name in merged
        merged[name] = kind
    return dict(sorted(merged.items()))


def resolve_kind(root: Path, kind: str) -> Kind | None:
    return kinds(root).get(kind)


def rel_to(root: Path, path: Path) -> str:
    """Root-relative POSIX path when inside the project, else the absolute POSIX path (built-in templates)."""
    try:
        return path.resolve().relative_to(Path(root).resolve()).as_posix()
    except ValueError:
        return path.as_posix()


# ---- hashing

def page_hash(root: Path, rel: str) -> str:
    return sha256_file(Path(root) / rel)


def wiki_index_hash(root: Path) -> str | None:
    index = Path(root) / WIKI_DIR / "index.md"
    return sha256_file(index) if index.is_file() else None


# ---- naming (R6)

def output_stem(date: str, kind: str, slug: str, revision: int = 1) -> str:
    stem = f"{date}-{kind}-{slug}"
    return stem if revision <= 1 else f"{stem}-v{revision}"


def next_free(root: Path, date: str, kind: str, slug: str, ext: str) -> tuple[str, int]:
    """First ``(root-relative path, revision)`` whose file and sidecar do not exist yet."""
    out = Path(root) / OUTPUTS_DIR
    revision = 1
    while True:
        stem = output_stem(date, kind, slug, revision)
        if not (out / f"{stem}{ext}").exists() and not (out / f"{stem}{SIDECAR}").exists():
            return f"{OUTPUTS_DIR}/{stem}{ext}", revision
        revision += 1


def sidecar_rel(rel: str) -> str:
    return f"{rel.rsplit('.', 1)[0]}{SIDECAR}"


# ---- provenance (R7)

def provenance(*, kind: str, actor: str, at: str, assisted_by: str | None, based_on: list[dict[str, str]],
               index_sha: str | None, extra: dict[str, Any] | None = None) -> dict[str, Any]:
    front: dict[str, Any] = {"type": OUTPUT_TYPE, "kind": kind}
    front.update(extra or {})
    front["generated"] = {"by": actor, "at": at, "assisted_by": assisted_by}
    front["based_on"] = based_on
    front["wiki_index_sha256"] = index_sha
    return front


def merge_front(user: dict[str, Any], prov: dict[str, Any]) -> dict[str, Any]:
    """Provenance keys first, then any frontmatter the draft already had (for example ``marp: true``)."""
    merged = dict(prov)
    for key, value in user.items():
        if key not in merged:
            merged[key] = value
    return merged


def dump_sidecar(front: dict[str, Any]) -> str:
    return yaml_module().safe_dump(front, sort_keys=False, allow_unicode=True, default_flow_style=False, width=4096)


# ---- reading outputs and staleness (R8)

def read_meta(path: Path) -> dict[str, Any]:
    """The provenance mapping of an output: its frontmatter, or the sidecar for a file without frontmatter."""
    path = Path(path)
    if path.suffix.lower() in (".md", ".markdown"):
        try:
            return frontmatter.read_head(path)
        except (frontmatter.FrontmatterError, OSError):
            return {}
    side = path.with_name(path.stem + SIDECAR)
    if side.is_file():
        try:
            loaded = yaml_module().safe_load(side.read_text(encoding="utf-8"))
        except (OSError, yaml_module().YAMLError):
            return {}
        return loaded if isinstance(loaded, dict) else {}
    return {}


def iter_outputs(root: Path) -> Iterator[tuple[str, dict[str, Any]]]:
    """``(root-relative path, provenance)`` of every registered output (``type: Output``); ``_templates/`` and
    sidecar files are skipped."""
    base = Path(root) / OUTPUTS_DIR
    if not base.is_dir():
        return
    for path in sorted(base.rglob("*")):
        rel = path.relative_to(base)
        if not path.is_file() or rel.parts[0].startswith("_") or path.name.endswith(SIDECAR):
            continue
        meta = read_meta(path)
        if meta.get("type") == OUTPUT_TYPE:
            yield f"{OUTPUTS_DIR}/{rel.as_posix()}", meta


def stale_outputs(root: Path) -> list[dict[str, Any]]:
    """One entry per output with at least one ``based_on`` page that changed or disappeared:
    ``{output, changed: [{path, recorded, current}], missing: [path]}``. Lint (012) maps each to ``output-stale``."""
    found: list[dict[str, Any]] = []
    for rel, meta in iter_outputs(root):
        changed: list[dict[str, str]] = []
        missing: list[str] = []
        for entry in meta.get("based_on") or []:
            if not isinstance(entry, dict) or not entry.get("path"):
                continue
            target = Path(root) / str(entry["path"])
            if not target.is_file():
                missing.append(str(entry["path"]))
                continue
            current = sha256_file(target)
            if current != entry.get("sha256"):
                changed.append({"path": str(entry["path"]), "recorded": entry.get("sha256"), "current": current})
        if changed or missing:
            found.append({"output": rel, "changed": changed, "missing": missing})
    return found


def clean_slug(slug: str) -> str:
    return nfc(slug).strip().lower()


def slug_ok(slug: str) -> bool:
    return bool(_KIND_RE.match(slug)) and len(slug) <= 80

