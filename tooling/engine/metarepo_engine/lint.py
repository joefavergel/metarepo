"""Knowledge-integrity checks over sources, extractions, wiki and outputs (spec 012).

Read-only: nothing here writes inside the project (R3); ``record_last_lint`` (R6) is the single, best-effort
exception and is called by the command, not by the checks. Every finding carries a check id, a severity, a
root-relative POSIX location, a detail (never a policy value, only its masked form) and the command or skill
that fixes it.

Severity resolution (R2): the default of the catalog, overridden per check by ``lint.severity`` in
``30_wiki/WIKI.md`` for findings located in the wiki bundle and in ``project.yaml`` for findings about
sources, extractions and outputs. ``policy-violation`` can be lowered only when ``project.yaml`` sets
``lint.allow_policy_downgrade: true``.
"""
from __future__ import annotations

import posixpath
import re
import unicodedata
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import unquote

from . import frontmatter, policy, registry
from .config import yaml_module
from .extract import staleness as extract_staleness
from .hashing import HashCache, sha256_file
from .okf import OkfPage
from .wiki import bundle, sourcepage
from .wiki import config as wiki_config
from .wiki.sign import ROLE_KEYS

SEVERITIES = ("block", "error", "warn", "info")
_RANK = {s: i for i, s in enumerate(SEVERITIES)}

# id -> default severity (012 section 2). ``log-drift`` is the one addition: 010 leaves ``index.md`` and
# ``log.md`` edits to the skill, so lint must be able to see when the log is malformed or links nowhere.
CATALOG: dict[str, str] = {
    "conformance": "error",
    "policy-violation": "block",
    "source-unregistered": "error",
    "extract-orphan": "warn",
    "extract-hash-mismatch": "error",
    "extract-failed": "warn",
    "extract-needs-review": "info",
    "triage-missing": "warn",
    "not-fed": "info",
    "source-page-stale": "warn",
    "source-page-dangling": "error",
    "drift": "warn",
    "no-sources": "warn",
    "dangling-footnote": "warn",
    "unused-source": "info",
    "orphan": "warn",
    "broken-link": "error",
    "index-drift": "warn",
    "log-drift": "info",
    "no-description": "warn",
    "expired": "info",
    "sign-coverage": "info",
    "duty-overlap": "info",
    "output-stale": "info",
}

SOURCES_DIR, EXTRACTS_DIR, OUTPUTS_DIR = "10_sources", "20_extracts", "outputs"
WIKI = bundle.WIKI_DIR
_JUNK_NAMES = {"Thumbs.db", "desktop.ini"}
_INDEX_ENTRY = re.compile(r"^\s*[*-]\s+\[([^\]]*)\]\(\s*<?([^)\s>]+)>?\s*\)(?:\s+[-–—]\s+(.*))?$")
_DATE_HEADING = re.compile(r"^##\s+(\S+)\s*$", re.MULTILINE)
_ISO_DATE = re.compile(r"\d{4}-\d{2}-\d{2}")
_EXTERNAL = re.compile(r"^(?:[A-Za-z][A-Za-z0-9+.-]*:|//)")
_PART_NAME = re.compile(r"\.part-\d{3}\.md$")

FIX_FEED = "/feed-wiki (the only wiki writer)"
FIX_RETRIAGE = "/intake --retriage"
SIDECAR = ".meta.yaml"  # 014/R7: provenance of an output without frontmatter
FIX_OUTPUT = "regenerate the output with /make-output"


@dataclass
class Finding:
    check: str
    severity: str
    where: str
    detail: str
    fix: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {"check": self.check, "severity": self.severity, "where": self.where, "detail": self.detail,
                "fix": self.fix}


@dataclass
class Report:
    findings: list[Finding] = field(default_factory=list)
    checks_run: list[str] = field(default_factory=list)
    warnings: list[tuple[str, str]] = field(default_factory=list)  # (code, message) about the lint setup
    pages: int = 0
    extractions: int = 0

    def counts(self) -> dict[str, int]:
        out = {s: 0 for s in SEVERITIES}
        for f in self.findings:
            out[f.severity] += 1
        out["total"] = len(self.findings)
        return out

    @property
    def blocking(self) -> bool:
        return any(f.severity == "block" for f in self.findings)


# --------------------------------------------------------------------------- helpers
def _resolve(page_rel: str, target: str) -> str | None:
    """Root-relative POSIX path a relative ``target`` points to from ``page_rel``; None when it leaves the root."""
    joined = posixpath.normpath(posixpath.join(posixpath.dirname(page_rel), target))
    return None if joined == ".." or joined.startswith("../") or joined.startswith("/") else joined


def _link_target(raw: str) -> str:
    return unquote(raw.split("#", 1)[0].split("?", 1)[0])


def _by(entry: Any) -> str | None:
    return str(entry["by"]) if isinstance(entry, dict) and entry.get("by") else None


@dataclass
class _Extract:
    rel: str
    front: dict[str, Any]
    row: dict[str, Any]


class Linter:
    def __init__(self, root: Path, project_config: dict[str, Any] | None = None, *, only: list[str] | None = None,
                 project_id: str | None = None, now: datetime | None = None):
        self.root = Path(root)
        self.config = project_config or {}
        self.only = set(only) if only else None
        self.project_id = project_id
        self.now = now or datetime.now(timezone.utc)
        self.report = Report()
        self.wiki_cfg: wiki_config.WikiConfig | None = None
        self._wiki_over: dict[str, str] = {}
        self._proj_over: dict[str, str] = {}
        self._allow_downgrade = False
        self._exists: dict[str, bool] = {}
        self._extracts: list[_Extract] = []
        self._pages: list[OkfPage] = []
        self._rel: dict[int, str] = {}
        self._by_rel: dict[str, OkfPage] = {}

    # ---- setup
    def _warn(self, code: str, message: str) -> None:
        if (code, message) not in self.report.warnings:
            self.report.warnings.append((code, message))

    def _setup(self) -> None:
        lint_cfg = self.config.get("lint") if isinstance(self.config.get("lint"), dict) else {}
        self._allow_downgrade = bool(lint_cfg.get("allow_policy_downgrade"))
        self._proj_over = self._overrides(lint_cfg.get("severity"), "project.yaml")
        md = wiki_config.wiki_md_path(self.root)
        if md.is_file():
            try:
                self.wiki_cfg = wiki_config.parse_wiki_md(md.read_text(encoding="utf-8"))
            except (wiki_config.WikiConfigError, OSError) as exc:
                self._warn("wiki_config_invalid", f"{WIKI}/WIKI.md could not be read ({exc}); wiki-side "
                           "severities, areas and governance checks use defaults")
            else:
                wiki_lint = self.wiki_cfg.extra.get("lint")
                if isinstance(wiki_lint, dict):
                    self._wiki_over = self._overrides(wiki_lint.get("severity"), f"{WIKI}/WIKI.md")

    def _overrides(self, raw: Any, origin: str) -> dict[str, str]:
        out: dict[str, str] = {}
        if not isinstance(raw, dict):
            return out
        for check, sev in raw.items():
            if check not in CATALOG:
                self._warn("lint_unknown_check", f"{origin}: lint.severity names unknown check {check!r}; ignored")
            elif sev not in SEVERITIES:
                self._warn("lint_bad_severity", f"{origin}: lint.severity.{check} = {sev!r} is not one of "
                           f"{', '.join(SEVERITIES)}; ignored")
            else:
                out[str(check)] = str(sev)
        return out

    def severity(self, check: str, where: str) -> str:
        """Wiki-located findings follow ``WIKI.md``, everything else follows ``project.yaml`` (012/R2)."""
        over = self._wiki_over if where == WIKI or where.startswith(WIKI + "/") else self._proj_over
        sev = over.get(check, CATALOG[check])
        if check == "policy-violation" and _RANK[sev] > _RANK["block"] and not self._allow_downgrade:
            self._warn("lint_policy_downgrade_refused",
                       "lint.severity.policy-violation lowers a blocking check; ignored unless project.yaml sets "
                       "lint.allow_policy_downgrade: true")
            sev = "block"
        return sev

    def add(self, check: str, where: str, detail: str, fix: str = "") -> None:
        self.report.findings.append(Finding(check, self.severity(check, where), where, detail, fix))

    def exists(self, rel: str) -> bool:
        hit = self._exists.get(rel)
        if hit is None:
            hit = self._exists[rel] = (self.root / rel).exists()
        return hit

    def rel(self, page: OkfPage) -> str:
        return self._rel[id(page)]

    # ---- run
    def run(self) -> Report:
        self._setup()
        rep = self.report
        rep.checks_run = [c for c in CATALOG if self.only is None or c in self.only]
        self._scan_extractions()
        self._load_pages()
        self.check_extractions()
        self.check_sources_dir()
        self.check_policy()
        self.check_source_pages()
        self.check_wiki_pages()
        self.check_index_and_log()
        self.check_outputs()
        rep.findings = [f for f in rep.findings if self.only is None or f.check in self.only]
        rep.findings.sort(key=lambda f: (_RANK[f.severity], f.check, f.where, f.detail))
        return rep

    # ---- loading
    def _scan_extractions(self) -> None:
        base = self.root / EXTRACTS_DIR
        if not base.is_dir():
            return
        for md in sorted(base.rglob("*.md")):
            if _PART_NAME.search(md.name):
                continue
            rel = md.relative_to(self.root).as_posix()
            try:
                front = frontmatter.read_head(md)
            except (frontmatter.FrontmatterError, OSError) as exc:
                self.add("conformance", rel, f"unreadable extraction: {exc}", f"extract --force {rel}")
                continue
            if front.get("type") != "Extract":
                self.add("conformance", rel, "not an extraction (frontmatter 'type' is not 'Extract')",
                         "move it out of 20_extracts/ or regenerate it with `extract --force`")
                continue
            missing = [k for k in ("source_id", "source_path", "source_sha256") if not front.get(k)]
            if missing:
                self.add("conformance", rel, "extraction lacks " + ", ".join(missing), f"extract --force {rel}")
                continue
            problems = extract_staleness.validate_front(front)
            if problems.errors:
                self.add("conformance", rel, "; ".join(f"{p}: {m}" for p, m in problems.errors[:5]),
                         f"extract --force {rel}")
            self._extracts.append(_Extract(rel, front, registry._row(self.root, md, front)))  # noqa: SLF001
        self.report.extractions = len(self._extracts)

    def _load_pages(self) -> None:
        for path in bundle.iter_page_paths(self.root):
            page = OkfPage.load(path)
            self._pages.append(page)
            self._rel[id(page)] = path.relative_to(self.root).as_posix()
            self._by_rel[self._rel[id(page)]] = page
        self.report.pages = len(self._pages)

    # ---- source and extraction checks
    def check_extractions(self) -> None:
        cache = HashCache(self.project_id)
        pages = sourcepage.source_pages(self.root)
        for ex in self._extracts:
            row, front = ex.row, ex.front
            src_rel = str(row["path"])
            src_norm = _resolve("x", src_rel) if not src_rel.startswith("/") else None
            src = self.root / src_norm if src_norm else None
            if src is None or not src.is_file():
                self.add("extract-orphan", ex.rel, f"source_path {src_rel} does not exist",
                         f"`remove {ex.rel} --confirm <sha256>` if the source is gone, or restore the source")
            else:
                try:
                    current = cache.sha256(src)
                except OSError as exc:
                    self.add("conformance", ex.rel, f"cannot hash {src_rel}: {exc}")
                else:
                    for reason in extract_staleness.staleness(front, current):
                        if reason["code"] == "source_hash_mismatch":
                            self.add("extract-hash-mismatch", ex.rel,
                                     f"source_sha256 differs from the current hash of {src_rel} (tampering: "
                                     "sources are immutable)",
                                     "restore the original from the sync provider history; `extract --force` "
                                     "only after a human decides the new file is the source")
            quality = front.get("quality")
            if quality == "failed":
                self.add("extract-failed", ex.rel, "quality: failed", f"extract --force {ex.rel}")
            elif quality == "needs-review":
                self.add("extract-needs-review", ex.rel, "quality: needs-review", "review the extraction by hand")
            triage = front.get("triage") if isinstance(front.get("triage"), dict) else {}
            if not triage.get("decision"):
                self.add("triage-missing", ex.rel, "no triage.decision", FIX_RETRIAGE)
            entry = pages.get(str(row["id"]))
            status = sourcepage.status_of(entry[1] if entry else None, row)
            if status == sourcepage.PENDING:
                self.add("not-fed", ex.rel, f"{row['id']} is triaged wiki and has no source page yet", FIX_FEED)
            elif status == sourcepage.STALE and entry:
                if str(entry[1].get("content_sha256") or "") != str(row.get("sha256") or ""):
                    why = "content_sha256 differs from the extraction's source_sha256"
                else:
                    why = "the extraction was regenerated after the page was written"
                self.add("source-page-stale", entry[0], f"{why} ({row['id']})", FIX_FEED)
        cache.save()

    def check_sources_dir(self) -> None:
        base = self.root / SOURCES_DIR
        if not base.is_dir():
            return
        known = {unicodedata.normalize("NFC", str(ex.row["path"])) for ex in self._extracts}
        for path in sorted(p for p in base.rglob("*") if p.is_file()):
            name = path.name
            if name.startswith(".") or name in _JUNK_NAMES or name.startswith("~$"):
                continue
            rel = path.relative_to(self.root).as_posix()
            if unicodedata.normalize("NFC", rel) not in known:
                self.add("source-unregistered", rel, "no extraction records this source",
                         "/intake (or `extract` on the file) to create its extraction")

    def check_policy(self) -> None:
        pol = self.config.get("policy") if isinstance(self.config.get("policy"), dict) else {}
        if not policy.has_rules(pol):
            return
        try:
            policy.compile_rules(pol)
        except Exception as exc:  # noqa: BLE001 - an invalid rule must not hide the other checks
            self._warn("policy_invalid", f"policy rules could not be compiled; policy-violation skipped: {exc}")
            return
        targets: list[tuple[str, Path]] = [(self.rel(p), p.path) for p in self._pages if p.path]
        for name in ("index.md", "log.md"):
            if (self.root / WIKI / name).is_file():
                targets.append((f"{WIKI}/{name}", self.root / WIKI / name))
        for ex in self._extracts:
            if ex.row.get("triage") != "wiki":
                continue
            main = self.root / ex.rel
            targets.append((ex.rel, main))
            for part in sorted(main.parent.glob(main.name[:-3] + ".part-*.md")):
                targets.append((part.relative_to(self.root).as_posix(), part))
        for rel, path in targets:
            try:
                text = path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            for f in policy.scan_text(text, pol):
                fix = (f"{FIX_FEED}: remove or rewrite the passage" if rel.startswith(WIKI)
                       else f"{FIX_RETRIAGE}: the source must not be fed while it matches the policy")
                self.add("policy-violation", rel, f"rule {f.rule} ({f.kind}) at {f.location}: {f.masked}", fix)

    def check_source_pages(self) -> None:
        known = {str(ex.row["id"]) for ex in self._extracts}
        for page in self._pages:
            if page.type != sourcepage.SOURCE_TYPE or page.parse_error:
                continue
            rel = self.rel(page)
            sid = page.front.get("source_id")
            if not sid:
                self.add("source-page-dangling", rel, "Source page has no source_id", FIX_FEED)
            elif str(sid) not in known:
                self.add("source-page-dangling", rel, f"source_id {sid} matches no extraction", FIX_FEED)
            for key in ("extract", "resource"):
                target = page.front.get(key)
                if not isinstance(target, str) or not target.strip():
                    self.add("source-page-dangling", rel, f"{key} is missing", FIX_FEED)
                elif not _EXTERNAL.match(target):
                    resolved = _resolve(rel, _link_target(target))
                    if resolved is None or not self.exists(resolved):
                        self.add("source-page-dangling", rel, f"{key} {target} does not resolve to a file", FIX_FEED)

    # ---- wiki pages
    def check_wiki_pages(self) -> None:
        source_ids = {str(sid) for sid in sourcepage.source_pages(self.root)}
        inbound: dict[str, set[str]] = {}
        team = self.wiki_cfg is not None and self.wiki_cfg.profile == "team"
        gov = self.config.get("governance") if isinstance(self.config.get("governance"), dict) else {}
        audit_expected = bool(gov.get("audit_expected")) or bool(self.wiki_cfg and self.wiki_cfg.audit_expected)
        for page in self._pages:
            rel = self.rel(page)
            for problem in page.problems():
                self.add("conformance", rel, problem, f"edit the page, or {FIX_FEED}")
            if page.parse_error:
                continue
            structural = page.type in wiki_config.STRUCTURAL_TYPES
            if page.type is None:  # nothing else about its frontmatter can be judged until conformance is fixed
                for target in page.links():
                    self._note_link(rel, target, inbound)
                continue
            if not (page.description or "").strip():
                self.add("no-description", rel, "no description", FIX_FEED)
            if page.is_expired(self.now):
                self.add("expired", rel, f"stale_after {page.front.get('stale_after')} has passed",
                         f"{FIX_FEED} to refresh, or set a new stale_after")
            if not structural:
                if not page.sources:
                    self.add("no-sources", rel, "empty sources[]", FIX_FEED)
                elif not self._bottoms_out(rel, source_ids):
                    self.add("drift", rel, "its sources[] never lead to a source page in 30_wiki/sources/", FIX_FEED)
            for fid in page.dangling_footnotes():
                self.add("dangling-footnote", rel, f"[^{fid}] has no matching sources[].id", FIX_FEED)
            if page.type != sourcepage.SOURCE_TYPE:  # a source page's own entry is never cited
                cited = set(page.footnote_refs())
                for sid in page.source_ids():
                    if sid not in cited:
                        self.add("unused-source", rel, f"sources[] entry {sid} is never cited", FIX_FEED)
            for target in page.links():
                self._note_link(rel, target, inbound)
            for s in page.sources:
                path = s.get("path")
                if isinstance(path, str) and path and not _EXTERNAL.match(path):
                    resolved = _resolve(rel, _link_target(path))
                    if resolved:
                        inbound.setdefault(resolved, set()).add(rel)
            if team and not structural and page.status != "deprecated":
                missing = []
                if not page.front.get("verified"):
                    missing.append("reviewer (verified)")
                if audit_expected and not page.front.get("audited"):
                    missing.append("auditor (audited)")
                if missing:
                    self.add("sign-coverage", rel, "no sign-off by " + " and ".join(missing),
                             "`sign <page> --role reviewer|auditor --actor <id>` once a person has reviewed it")
            actors: dict[str, list[str]] = {}
            for role, key in ROLE_KEYS.items():
                who = _by(page.front.get(key))
                if who:
                    actors.setdefault(who, []).append(role)
            for who, roles in actors.items():
                if len(roles) > 1:
                    self.add("duty-overlap", rel, f"{who} signed as {' and '.join(roles)}",
                             "have a different person sign one of the roles")
        for page in self._pages:
            rel = self.rel(page)
            if page.parse_error or page.type == wiki_config.SKILL_TYPE:
                continue
            if not (inbound.get(rel, set()) - {rel}):
                self.add("orphan", rel, "no page links here", f"{FIX_FEED}: link it from a related page")

    def _bottoms_out(self, rel: str, source_ids: set[str]) -> bool:
        """True when a ``sources[]`` entry of the page is a source page (by id), or points (by ``path``) to another
        wiki page that itself bottoms out: a file-back page cites the pages it synthesized (010/R15)."""
        by_rel = self._by_rel
        seen: set[str] = set()
        stack = [rel]
        while stack:
            current = stack.pop()
            if current in seen:
                continue
            seen.add(current)
            page = by_rel.get(current)
            if page is None or page.parse_error:
                continue
            if page.type == sourcepage.SOURCE_TYPE:
                return True
            if any(sid in source_ids for sid in page.source_ids()):
                return True
            for entry in page.sources:
                path = entry.get("path")
                if isinstance(path, str) and path and not _EXTERNAL.match(path):
                    nxt = _resolve(current, _link_target(path))
                    if nxt in by_rel:
                        stack.append(nxt)
        return False

    def _note_link(self, rel: str, target: str, inbound: dict[str, set[str]]) -> None:
        resolved = _resolve(rel, target)
        if resolved is None or not self.exists(resolved):
            self.add("broken-link", rel, f"link {target} points to a missing file", FIX_FEED)
        else:
            inbound.setdefault(resolved, set()).add(rel)

    # ---- index.md and log.md
    def check_index_and_log(self) -> None:
        if not self._pages:
            return
        rel_wiki = {self.rel(p)[len(WIKI) + 1:]: p for p in self._pages}
        index_path = self.root / WIKI / "index.md"
        if index_path.is_file():
            self._check_index(index_path, rel_wiki)
        else:
            self.add("index-drift", f"{WIKI}/index.md", "index.md is missing", FIX_FEED)
        log_path = self.root / WIKI / "log.md"
        if log_path.is_file():
            self._check_log(log_path)

    def _check_index(self, path: Path, rel_wiki: dict[str, OkfPage]) -> None:
        where = f"{WIKI}/index.md"
        text = path.read_text(encoding="utf-8", errors="replace").replace("\r\n", "\n")
        listed: set[str] = set()
        group = None
        for line in text.split("\n"):
            if line.startswith("## "):
                group = line[3:].strip()
                continue
            m = _INDEX_ENTRY.match(line)
            if not m or _EXTERNAL.match(m.group(2)):
                continue
            target = _link_target(m.group(2))
            if not target:
                continue
            resolved = _resolve(where, target)
            if resolved is None or not self.exists(resolved):
                self.add("index-drift", where, f"entry {target} points to a missing file", FIX_FEED)
                continue
            key = resolved[len(WIKI) + 1:] if resolved.startswith(WIKI + "/") else resolved
            listed.add(key)
            page = rel_wiki.get(key)
            if page is None:
                continue
            desc, want = (m.group(3) or "").strip(), (page.description or "").strip()
            if want and desc and desc != want:
                self.add("index-drift", where, f"entry for {key} has a description that differs from the page's",
                         f"{FIX_FEED} (index lines reuse the page description verbatim)")
            if self.wiki_cfg is not None and group is not None:
                expected = self.wiki_cfg.group_of(page.type, key)
                if expected is not None and expected != group:
                    self.add("index-drift", where, f"{key} is listed under '{group}', expected '{expected}'", FIX_FEED)
        for key in rel_wiki:
            if key not in listed:
                self.add("index-drift", f"{WIKI}/{key}", "page is not listed in index.md", FIX_FEED)

    def _check_log(self, path: Path) -> None:
        where = f"{WIKI}/log.md"
        text = path.read_text(encoding="utf-8", errors="replace").replace("\r\n", "\n")
        dates = [m.group(1) for m in _DATE_HEADING.finditer(text)]
        valid = [d for d in dates if _ISO_DATE.fullmatch(d)]
        for bad in (d for d in dates if d not in valid):
            self.add("log-drift", where, f"heading '## {bad}' is not a YYYY-MM-DD date", FIX_FEED)
        if len(set(valid)) != len(valid):
            self.add("log-drift", where, "a date appears under more than one heading", FIX_FEED)
        elif valid != sorted(valid, reverse=True):
            self.add("log-drift", where, "date headings are not ordered newest first", FIX_FEED)
        for m in re.finditer(r"\]\(\s*<?([^)\s>]+)", text):
            if _EXTERNAL.match(m.group(1)):
                continue
            resolved = _resolve(where, _link_target(m.group(1)))
            if resolved is not None and not self.exists(resolved):
                self.add("log-drift", where, f"entry links to missing file {m.group(1)}", FIX_FEED)

    # ---- outputs (contract of 014/R7: frontmatter or ``.meta.yaml`` sidecar with ``based_on: [{path, sha256}]``)
    def check_outputs(self) -> None:
        base = self.root / OUTPUTS_DIR
        if not base.is_dir():
            return
        for path in sorted(p for p in base.rglob("*") if p.is_file()):
            rel = path.relative_to(self.root).as_posix()
            if path.relative_to(base).parts[0].startswith("_"):
                continue  # ``_templates/`` and other reserved folders
            meta: Any = None
            where = rel
            try:
                if path.suffix.lower() in (".md", ".markdown"):
                    meta = frontmatter.read_head(path)
                elif path.name.endswith(SIDECAR):
                    meta = yaml_module().safe_load(path.read_text(encoding="utf-8"))
                    stem = path.name[: -len(SIDECAR)]
                    owners = [p for p in path.parent.glob(stem + ".*") if p.is_file() and not p.name.endswith(SIDECAR)]
                    if len(owners) == 1:
                        where = owners[0].relative_to(self.root).as_posix()
            except Exception:  # noqa: BLE001 - a malformed output record is not a lint failure
                continue
            if not isinstance(meta, dict) or meta.get("type") != "Output" or not isinstance(meta.get("based_on"), list):
                continue
            for item in meta["based_on"]:
                if not isinstance(item, dict) or not item.get("path"):
                    continue
                target = str(item["path"])
                resolved = _resolve("x", target)
                full = self.root / resolved if resolved else None
                if full is None or not full.is_file():
                    self.add("output-stale", where, f"based_on page {target} no longer exists", FIX_OUTPUT)
                elif item.get("sha256") and sha256_file(full) != str(item["sha256"]):
                    self.add("output-stale", where, f"based_on page {target} changed after the output was generated",
                             FIX_OUTPUT)


# --------------------------------------------------------------------------- state.json -> last_lint (R6)
def record_last_lint(root: Path, report: Report, *, version: str, actor: str, only: list[str] | None) -> None:
    """Store a summary under the ``config`` lease. Raises when busy or unwritable: the caller skips (non-critical)."""
    import json

    from . import leases
    from .events import now_iso

    summary = {"at": now_iso(), "toolkit": version, "scope": "partial" if only else "full",
               "counts": report.counts()}

    def transform(existing: bytes | None) -> bytes:
        state = json.loads(existing.decode("utf-8")) if existing else {}
        state["last_lint"] = summary
        return (json.dumps(state, indent=2, ensure_ascii=False) + "\n").encode("utf-8")

    with leases.held(Path(root), "config", actor, "lint") as handle:
        leases.update_hot_file(Path(root), handle, "_system/state.json", transform)
