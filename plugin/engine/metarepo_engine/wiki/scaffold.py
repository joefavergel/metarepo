"""Plan and create the wiki bundle (009/R5, R6; P12: never overwrites).

``plan_scaffold`` is pure; ``apply_scaffold`` performs the writes and must run under the ``wiki`` lease.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from string import Template
from typing import Any

from .. import frontmatter
from ..fsops import atomic_write_text, write_if_absent
from ..templates import templates_dir
from . import bundle
from .config import WIKI_MD, WikiConfig, parse_wiki_md, render_block, replace_block, WikiConfigError

KEEP = ".keep"
ANSWER_SKILL_REL = "skills/answer-from-wiki.md"
_DEFAULTS = {
    "threshold": ("A subject earns its own page when it recurs across several sources, or when readers will "
                  "need to look it up by name. A passing mention goes into the page it belongs to. Edit this "
                  "section to set a stricter or looser threshold."),
    "ingest_emphasis": ("Start with the sources that define the project's vocabulary, then add the rest. Check "
                        "page granularity after the first two or three sources and adjust this rulebook before "
                        "continuing."),
    "open_questions": "(none yet)",
}


@dataclass
class ScaffoldPlan:
    """Root-relative POSIX paths, grouped by what will happen to them."""

    files: dict[str, str] = field(default_factory=dict)  # 30_wiki-relative path -> content for new files
    folders: list[str] = field(default_factory=list)  # 30_wiki-relative folders (each gets a .keep)
    wiki_md: str = ""


def _bullets(items: Any) -> str:
    return "\n".join(f"- {i}" for i in items)


def render_wiki_md(cfg: WikiConfig, project: dict[str, Any], answers: dict[str, Any] | None = None) -> str:
    """The rulebook text. ``answers`` may carry ``objective``, ``threshold``, ``ingest_emphasis``,
    ``governance`` (text), ``roles`` (list), ``open_questions`` (list or text) and ``examples``
    ({type name: example title}); prose given there is written in the content language by the caller."""
    a = answers or {}
    objective = " ".join(str(a.get("objective") or project.get("objective") or "").split())
    if cfg.axis == "area":
        layout = ("Pages are organized **by area**: `30_wiki/<area>/<page>.md`. The page `type` lives in the "
                  "frontmatter. `index.md` is grouped by area.\n\n"
                  + "\n".join(f"- `{x.folder}/`: {x.name}" + (f" ({x.description})" if x.description else "")
                             for x in cfg.areas))
    elif cfg.axis == "type":
        layout = ("Pages are organized **by page type**: `30_wiki/<type-folder>/<page>.md`. The area, when "
                  "relevant, is recorded in `tags`. `index.md` is grouped by type.")
    else:
        layout = ("Pages are **flat**: `30_wiki/pages/<page>.md`, with `type` in the frontmatter. `index.md` is "
                  "grouped by type.")
    layout += ("\n\nAlways: `sources/` holds one `Source` page per source (the link from a page's footnotes to "
               "its extraction); `skills/` holds `Skill` pages such as `answer-from-wiki.md`. `WIKI.md`, "
               "`index.md` and `log.md` are not content pages.")
    examples = a.get("examples") or {}
    rows = ["| Type | Folder | Meaning | Example title |", "|---|---|---|---|"]
    for t in cfg.page_types:
        folder = f"`{t.folder}/`" if cfg.axis == "type" else "(see Layout)"
        rows.append(f"| {t.name} | {folder} | {t.description or ''} | {examples.get(t.name, '(after the first feed)')} |")
    rows.append("| Source | `sources/` | One page per source: where it came from and which extraction holds its text. | "
                f"{examples.get('Source', '(after the first feed)')} |")
    rows.append("| Skill | `skills/` | Procedures for agents that use the wiki. | Answer from the wiki |")
    roles = list(a.get("roles") or ["author", "reviewer"])
    gov_default = (
        f"Profile: **{cfg.profile}**. Roles expected: {', '.join(roles)}"
        + ("; an auditor sign-off is expected" if cfg.audit_expected else "")
        + ". Pages carry `generated` on creation; `verified` (and `audited`) are set only by the sign-off "
          "command, never by hand or by the writer."
    )
    oq = a.get("open_questions")
    open_questions = _bullets(oq) if isinstance(oq, list) and oq else (str(oq) if oq else _DEFAULTS["open_questions"])
    return Template((templates_dir() / "wiki" / "WIKI.md.tmpl").read_text(encoding="utf-8")).substitute(
        project_name=project.get("name", ""),
        config_block=render_block(cfg),
        purpose=objective,
        content_language=(project.get("language") or {}).get("content", ""),
        layout=layout,
        page_types="\n".join(rows),
        threshold=a.get("threshold") or _DEFAULTS["threshold"],
        ingest_emphasis=a.get("ingest_emphasis") or _DEFAULTS["ingest_emphasis"],
        governance=a.get("governance") or gov_default,
        open_questions=open_questions,
    ).replace("\r\n", "\n")


def plan_scaffold(cfg: WikiConfig, project: dict[str, Any], answers: dict[str, Any] | None = None,
                  today: str | None = None) -> ScaffoldPlan:
    from ..events import now_iso

    day = today or now_iso()[:10]
    plan = ScaffoldPlan(wiki_md=render_wiki_md(cfg, project, answers))
    title = f"{project.get('name', 'Project')} wiki"
    log = bundle.add_log_entry(bundle.render_log(), day, "Creation",
                               f"Wiki scaffolded (axis: {cfg.axis}, profile: {cfg.profile}).")
    skill_text = (templates_dir() / "wiki" / "answer-from-wiki.md").read_text(encoding="utf-8")
    skill = frontmatter.parse(skill_text).front
    # The shipped skill page is listed from the start so that a fresh scaffold lints clean (012 index-drift).
    index = bundle.add_index_entry(bundle.render_index(cfg, title), "Skills", str(skill.get("title") or "Skill"),
                                   ANSWER_SKILL_REL, skill.get("description"))
    plan.files = {
        WIKI_MD: plan.wiki_md,
        "index.md": index,
        "log.md": log,
        ANSWER_SKILL_REL: skill_text,
    }
    plan.folders = cfg.content_folders()
    return plan


@dataclass
class ScaffoldResult:
    created: list[str] = field(default_factory=list)
    kept: list[str] = field(default_factory=list)
    updated: list[str] = field(default_factory=list)
    warnings: list[tuple[str, str, str | None]] = field(default_factory=list)


def apply_scaffold(root: Path, plan: ScaffoldPlan, cfg: WikiConfig, *, reconfigure: bool = False,
                   dry_run: bool = False, ensure=lambda: None) -> ScaffoldResult:
    """Create what is missing under ``30_wiki/``. ``ensure`` re-verifies the lease before each write batch.

    Existing files are kept. With ``reconfigure`` only the configuration block of an existing ``WIKI.md`` is
    replaced (prose untouched), new groups are added to an existing ``index.md``, and no page is touched.
    """
    base = bundle.wiki_dir(root)
    res = ScaffoldResult()
    rel = lambda p: f"{bundle.WIKI_DIR}/{p}"  # noqa: E731
    for folder in plan.folders:
        keep = base / folder / KEEP
        if (base / folder).is_dir() and any((base / folder).iterdir()):
            res.kept.append(rel(folder + "/"))
            continue
        if not dry_run:
            ensure()
            keep.parent.mkdir(parents=True, exist_ok=True)
            write_if_absent(keep, "")
        res.created.append(rel(folder + "/"))
    for name, text in plan.files.items():
        target = base / name
        if not target.exists():
            if not dry_run:
                ensure()
                target.parent.mkdir(parents=True, exist_ok=True)
                write_if_absent(target, text)
            res.created.append(rel(name))
            continue
        res.kept.append(rel(name))
        if name == WIKI_MD:
            existing = target.read_text(encoding="utf-8")
            try:
                current = parse_wiki_md(existing)
            except WikiConfigError as exc:
                res.warnings.append(("wiki_md_unparseable", exc.message, rel(name)))
                continue
            if current.to_mapping() != cfg.to_mapping():
                if reconfigure:
                    if not dry_run:
                        ensure()
                        atomic_write_text(target, replace_block(existing, cfg))
                    res.updated.append(rel(name))
                else:
                    res.warnings.append(("config_differs", "30_wiki/WIKI.md already exists with a different "
                                         "configuration; it was kept (use --reconfigure to apply the new one)",
                                         rel(name)))
        elif name == "index.md" and reconfigure:
            text0 = target.read_text(encoding="utf-8")
            text1 = bundle.ensure_index_groups(text0, cfg.index_groups())
            if text1 != text0:
                if not dry_run:
                    ensure()
                    atomic_write_text(target, text1)
                res.updated.append(rel(name))
    return res
