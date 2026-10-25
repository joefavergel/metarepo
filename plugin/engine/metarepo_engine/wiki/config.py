"""The configuration block of ``30_wiki/WIKI.md`` and the folder layout it implies (009/R5, R6, 002/ADR-005).

``WIKI.md`` holds the editorial rulebook in prose plus one small YAML block limited to wiki concerns::

    <!-- wiki-config:start -->
    ```yaml
    profile: team            # team | personal
    axis: area               # area | type | flat
    areas: [...]             # axis = area
    page_types: [...]        # free list; Source and Skill are structural and always present
    audit_expected: false
    ```
    <!-- wiki-config:end -->

Unknown keys (for example ``lint.severity`` from 012) are preserved in ``WikiConfig.extra``.

Layout rules (009 plan, "Axis mechanics")::

    area axis   30_wiki/<area>/...        ``type`` in frontmatter; index grouped by area
    type axis   30_wiki/<type-folder>/... ``area`` as a tag; index grouped by type
    flat        30_wiki/pages/...         index grouped by type
    always      30_wiki/sources/ (type Source), 30_wiki/skills/ (type Skill)

Structural folder names stay English; domain folder names are kebab-case and keep the content language
(letters of any script are allowed; nothing is transliterated). The toolkit never pluralises: a page type's
folder defaults to its kebab-cased name and is set explicitly (``{name, folder}``) when the project wants
another form.
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..jsonout import EXIT_COMPAT, EngineError

WIKI_MD = "WIKI.md"
BLOCK_START = "<!-- wiki-config:start -->"
BLOCK_END = "<!-- wiki-config:end -->"
_BLOCK_RE = re.compile(re.escape(BLOCK_START) + r"\s*```ya?ml\n(.*?)\n```\s*" + re.escape(BLOCK_END), re.DOTALL)

PROFILES = ("team", "personal")
AXES = ("area", "type", "flat")
SOURCE_TYPE, SKILL_TYPE = "Source", "Skill"
STRUCTURAL_TYPES = {SOURCE_TYPE: "sources", SKILL_TYPE: "skills"}
FLAT_FOLDER = "pages"
RESERVED_FOLDERS = frozenset({"sources", "skills", FLAT_FOLDER, "_meta"})
KNOWN_KEYS = ("profile", "axis", "areas", "page_types", "audit_expected")

_KEBAB = re.compile(r"^[^\W_]+(?:-[^\W_]+)*$")
_SEPARATORS = re.compile(r"[\W_]+")


class WikiConfigError(ValueError):
    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


def kebab(text: str) -> str:
    """Lower-case NFC kebab-case that keeps letters of any script. May be empty (caller must handle)."""
    text = unicodedata.normalize("NFC", str(text)).strip().casefold()
    return _SEPARATORS.sub("-", text).strip("-")


def is_kebab(text: str) -> bool:
    return bool(_KEBAB.match(text)) and text == text.casefold() and text == unicodedata.normalize("NFC", text)


@dataclass(frozen=True)
class Named:
    """An area or a page type: a display ``name`` and the ``folder`` it maps to under ``30_wiki/``."""

    name: str
    folder: str
    description: str | None = None

    def to_yaml(self) -> Any:
        if self.folder == kebab(self.name) and not self.description:
            return self.name
        out: dict[str, Any] = {"name": self.name, "folder": self.folder}
        if self.description:
            out["description"] = self.description
        return out


def _named(raw: Any, what: str) -> Named:
    if isinstance(raw, str):
        name, folder, desc = raw.strip(), None, None
    elif isinstance(raw, dict) and isinstance(raw.get("name"), str):
        name, folder, desc = raw["name"].strip(), raw.get("folder"), raw.get("description")
    else:
        raise WikiConfigError(f"each {what} must be a string or a mapping with a 'name'")
    if not name:
        raise WikiConfigError(f"a {what} name is empty")
    folder = kebab(folder) if isinstance(folder, str) and folder.strip() else kebab(name)
    if not folder:
        raise WikiConfigError(f"{what} {name!r} has no letters or digits to build a folder name from; "
                              "give it an explicit 'folder'")
    if folder in RESERVED_FOLDERS and not (what == "page type" and name in STRUCTURAL_TYPES):
        raise WikiConfigError(f"{what} {name!r}: folder {folder!r} is reserved (structural folders: "
                              f"{', '.join(sorted(RESERVED_FOLDERS))})")
    return Named(name, folder, str(desc).strip() if desc else None)


@dataclass
class WikiConfig:
    profile: str = "team"
    axis: str = "type"
    areas: list[Named] = field(default_factory=list)
    page_types: list[Named] = field(default_factory=list)  # excludes Source and Skill
    audit_expected: bool = False
    extra: dict[str, Any] = field(default_factory=dict)

    # ---- construction and validation
    @classmethod
    def from_mapping(cls, data: dict[str, Any]) -> "WikiConfig":
        if not isinstance(data, dict):
            raise WikiConfigError("the wiki configuration must be a mapping")
        areas = [_named(a, "area") for a in (data.get("areas") or [])]
        types = [_named(t, "page type") for t in (data.get("page_types") or [])]
        types = [t for t in types if t.name not in STRUCTURAL_TYPES]
        cfg = cls(
            profile=str(data.get("profile", "team")),
            axis=str(data.get("axis", "area" if areas else "type")),
            areas=areas,
            page_types=types,
            audit_expected=bool(data.get("audit_expected", False)),
            extra={k: v for k, v in data.items() if k not in KNOWN_KEYS},
        )
        cfg.validate()
        return cfg

    def validate(self) -> None:
        if self.profile not in PROFILES:
            raise WikiConfigError(f"profile {self.profile!r} is not one of {', '.join(PROFILES)}")
        if self.axis not in AXES:
            raise WikiConfigError(f"axis {self.axis!r} is not one of {', '.join(AXES)}")
        if self.axis == "area" and not self.areas:
            raise WikiConfigError("axis 'area' needs at least one area")
        if not self.page_types:
            raise WikiConfigError("define at least one page type (Source and Skill are structural and implicit)")
        for what, items in (("area", self.areas), ("page type", self.page_types)):
            names = [i.name.casefold() for i in items]
            folders = [i.folder for i in items]
            for label, values in (("name", names), ("folder", folders)):
                dup = sorted({v for v in values if values.count(v) > 1})
                if dup:
                    raise WikiConfigError(f"duplicate {what} {label}: {', '.join(dup)}")
        if self.axis == "area":
            clash = {a.folder for a in self.areas} & RESERVED_FOLDERS
            if clash:
                raise WikiConfigError(f"area folder reserved: {', '.join(sorted(clash))}")

    def to_mapping(self) -> dict[str, Any]:
        out: dict[str, Any] = {"profile": self.profile, "axis": self.axis}
        if self.areas or self.axis == "area":
            out["areas"] = [a.to_yaml() for a in self.areas]
        out["page_types"] = [t.to_yaml() for t in self.page_types]
        out["audit_expected"] = self.audit_expected
        out.update(self.extra)
        return out

    # ---- layout
    def all_types(self) -> list[Named]:
        """Configured types plus the structural ``Source`` and ``Skill`` (always present)."""
        return [*self.page_types, *(Named(n, f) for n, f in STRUCTURAL_TYPES.items())]

    def type_names(self) -> list[str]:
        return [t.name for t in self.all_types()]

    def type_folder(self, type_name: str) -> str:
        for t in self.all_types():
            if t.name == type_name:
                return t.folder
        raise KeyError(f"unknown page type {type_name!r}")

    def area_folder(self, area: str) -> str:
        for a in self.areas:
            if a.name == area or a.folder == area:
                return a.folder
        raise KeyError(f"unknown area {area!r}")

    def folder_for(self, type_name: str, area: str | None = None) -> str:
        """Folder under ``30_wiki/`` (POSIX, no trailing slash) where a page of this type belongs."""
        if type_name in STRUCTURAL_TYPES:
            return STRUCTURAL_TYPES[type_name]
        if self.axis == "area":
            if area is None:
                raise KeyError("the area axis needs an area for this page")
            return self.area_folder(area)
        if self.axis == "type":
            return self.type_folder(type_name)
        self.type_folder(type_name)  # validates the type
        return FLAT_FOLDER

    def content_folders(self) -> list[str]:
        """Every folder the scaffold creates under ``30_wiki/`` (structural folders first)."""
        folders = list(STRUCTURAL_TYPES.values())
        if self.axis == "area":
            folders += [a.folder for a in self.areas]
        elif self.axis == "type":
            folders += [t.folder for t in self.page_types]
        else:
            folders.append(FLAT_FOLDER)
        return folders

    def index_groups(self) -> list[str]:
        """Index section headings in order: areas (area axis) or page types, then ``Sources``, ``Skills``."""
        groups = [a.name for a in self.areas] if self.axis == "area" else [t.name for t in self.page_types]
        return [*groups, "Sources", "Skills"]

    def group_of(self, type_name: str | None, rel_to_wiki: str) -> str | None:
        """Index group of a page: by area folder on the area axis, by type otherwise (Source/Skill apart)."""
        if type_name == SOURCE_TYPE:
            return "Sources"
        if type_name == SKILL_TYPE:
            return "Skills"
        if self.axis == "area":
            first = rel_to_wiki.split("/", 1)[0]
            return next((a.name for a in self.areas if a.folder == first), None)
        return type_name if type_name in {t.name for t in self.page_types} else None


# ---- WIKI.md text
def render_block(cfg: WikiConfig) -> str:
    from ..config import yaml_module

    body = yaml_module().safe_dump(cfg.to_mapping(), sort_keys=False, allow_unicode=True,
                                   default_flow_style=False, width=4096).rstrip("\n")
    return f"{BLOCK_START}\n```yaml\n{body}\n```\n{BLOCK_END}"


def parse_wiki_md(text: str) -> WikiConfig:
    """The configuration of a ``WIKI.md`` text. Raises ``WikiConfigError`` when the block is missing/invalid."""
    from ..config import yaml_module

    m = _BLOCK_RE.search(text.replace("\r\n", "\n"))
    if not m:
        raise WikiConfigError(f"{WIKI_MD} has no configuration block ({BLOCK_START} ... {BLOCK_END})")
    yaml = yaml_module()
    try:
        data = yaml.safe_load(m.group(1))
    except yaml.YAMLError as exc:
        raise WikiConfigError(f"the configuration block of {WIKI_MD} is not valid YAML: {exc}") from exc
    return WikiConfig.from_mapping(data if data is not None else {})


def replace_block(text: str, cfg: WikiConfig) -> str:
    """``text`` with only its configuration block replaced (prose untouched). Raises when there is none."""
    normalized = text.replace("\r\n", "\n")
    if not _BLOCK_RE.search(normalized):
        raise WikiConfigError(f"{WIKI_MD} has no configuration block to update")
    return _BLOCK_RE.sub(lambda _m: render_block(cfg), normalized, count=1)


def wiki_md_path(root: Path) -> Path:
    return Path(root) / "30_wiki" / WIKI_MD


def load(root: Path) -> WikiConfig:
    """Read the configuration from ``<root>/30_wiki/WIKI.md``; ``EngineError`` (exit 4) when unusable."""
    path = wiki_md_path(root)
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise EngineError("wiki_md_unreadable", f"cannot read 30_wiki/{WIKI_MD}: {exc}; run /init-wiki",
                          EXIT_COMPAT, f"30_wiki/{WIKI_MD}") from exc
    try:
        return parse_wiki_md(text)
    except WikiConfigError as exc:
        raise EngineError("wiki_config_invalid", exc.message, EXIT_COMPAT, f"30_wiki/{WIKI_MD}") from exc
