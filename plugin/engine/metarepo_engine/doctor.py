"""Health checks for the environment, the sync layer and the toolkit (013).

``scan`` is read-only: it walks the tree once (pruning folders it does not descend into) and returns
findings. A finding may carry ``actions``: safe fixes that ``apply_action`` performs only when the caller
confirmed that action id (013/R3, R4). Nothing under ``10_sources/`` ever gets an action (P6), nothing is
deleted without its SHA-256 (``remove``), and conflicts are never merged.

Checks that need a later spec (per-user clone paths, 006) degrade to ``skipped`` with a reason instead of
failing.
"""
from __future__ import annotations

import argparse
import errno
import os
import platform
import shutil
import stat
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from . import compat, conflicts, leases, naming, readable
from .config import CONTRACT_FOLDERS, effective, effective_forbidden_paths
from .events import append_event
from .fsops import _TEMP_RE, fs_path, nfc, safe_move, sha256_file, write_if_absent
from .gitignore import IgnoreSet
from .jsonout import EngineError

ERROR, WARN, INFO = "error", "warn", "info"

# Every check id of 013 section 2, in report order.
CHECKS = (
    "contract-missing", "forbidden-content", "agents-md-shadowed", "nested-git", "conflict-copy",
    "cloud-native", "not-local", "path-too-long", "bad-name", "temp-stragglers", "stale-lease",
    "toolkit-incompatible", "toolkit-artifacts", "runtime", "code-folder-content", "repo-inside-tree",
    "repo-missing", "large-file", "inbox-age", "secrets-suspect",
)
# 013/R1: --local limits the run to machine-specific checks.
LOCAL_CHECKS = frozenset({"runtime", "repo-inside-tree", "repo-missing", "not-local", "toolkit-incompatible"})

INSTRUCTION_FILES = ("CLAUDE.md", ".claude/CLAUDE.md", "CLAUDE.local.md")
OFFLINE_AREAS = ("20_extracts", "30_wiki", "_system")  # 004/R13: expected available offline
SOURCES = "10_sources"
SECRET_GLOBS = ("*.pem", "*.key", "*.p12", "*.pfx", "id_rsa*", "id_ed25519*", "id_ecdsa*", ".env", ".env.*")
TOOLKIT_DIRS = ("metarepo_engine", ".claude-plugin")
TOOLKIT_CLAUDE_DIRS = ("skills", "commands", "agents")
MB = 1024 * 1024


@dataclass
class Action:
    id: str
    kind: str  # remove | move-out | release-lease | render-template
    path: str
    description: str
    needs_dest: bool = False
    extra: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        out = {"id": self.id, "kind": self.kind, "path": self.path, "description": self.description,
               "needs_dest": self.needs_dest}
        out.update(self.extra)
        return out


@dataclass
class Finding:
    id: str
    severity: str
    path: str | None
    message: str
    remedy: str | None = None
    actions: list[Action] = field(default_factory=list)
    details: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {"id": self.id, "severity": self.severity, "path": self.path,
                               "message": self.message, "remedy": self.remedy,
                               "actions": [a.as_dict() for a in self.actions]}
        if self.details:
            out["details"] = self.details
        return out


@dataclass
class Report:
    findings: list[Finding] = field(default_factory=list)
    checks: dict[str, dict[str, Any]] = field(default_factory=dict)
    warnings: list[tuple[str, str]] = field(default_factory=list)

    def counts(self) -> dict[str, int]:
        c = {ERROR: 0, WARN: 0, INFO: 0}
        for f in self.findings:
            c[f.severity] += 1
        c["total"] = len(self.findings)
        return c

    def actions(self) -> list[Action]:
        return [a for f in self.findings for a in f.actions]


def _action_id(check: str, kind: str, rel: str) -> str:
    return f"{check}-{kind}-{naming.hash6(rel)}"


class _Scan:
    def __init__(self, root: Path, config: dict[str, Any], *, local_only: bool, version: str,
                 config_ok: bool, now: float | None = None):
        self.root = Path(root)
        self.config = config
        self.cfg = effective(config)
        self.local_only = local_only
        self.version = version
        self.config_ok = config_ok
        self.now = now if now is not None else time.time()
        self.report = Report()
        self.enabled = [c for c in CHECKS if not local_only or c in LOCAL_CHECKS]
        for c in self.enabled:
            self.report.checks[c] = {"status": "ok"}
        self.ignore = IgnoreSet(effective_forbidden_paths(self.cfg))
        self.secret_ignore = IgnoreSet(SECRET_GLOBS)
        self.path_limit = int((self.cfg.get("policy") or {}).get("path_limit") or naming.DEFAULT_PATH_LIMIT)
        doctor_cfg = self.cfg.get("doctor") or {}
        self.large_bytes = float(doctor_cfg.get("large_file_mb", 200)) * MB
        self.inbox_days = float(doctor_cfg.get("inbox_max_age_days", 14))
        self.has_repos = bool(self.cfg.get("repos"))

    # ---------------------------------------------------------------- helpers
    def on(self, check: str) -> bool:
        return check in self.report.checks

    def add(self, check: str, severity: str, path: str | None, message: str, remedy: str | None = None,
            actions: list[Action] | None = None, **details: Any) -> None:
        if not self.on(check):
            return
        self.report.findings.append(Finding(check, severity, path, message, remedy, actions or [], details))
        self.report.checks[check]["status"] = "findings"

    def skip(self, check: str, reason: str) -> None:
        if self.on(check):
            self.report.checks[check] = {"status": "skipped", "reason": reason}

    def actionable(self, rel: str) -> bool:
        """P6/R4: nothing under 10_sources/ is ever offered a fix."""
        return rel.split("/")[0].casefold() != SOURCES

    def remove_action(self, check: str, rel: str, why: str) -> list[Action]:
        if not self.actionable(rel):
            return []
        return [Action(_action_id(check, "remove", rel), "remove", rel,
                       f"delete {rel} ({why}); recoverable from the sync provider's trash")]

    def move_action(self, check: str, rel: str, why: str) -> list[Action]:
        if not self.actionable(rel):
            return []
        return [Action(_action_id(check, "move-out", rel), "move-out", rel,
                       f"move {rel} out of the synced folder ({why}) to a folder you choose", needs_dest=True)]

    # ---------------------------------------------------------------- tree walk
    def walk(self) -> None:
        tree_checks = {c for c in self.enabled if c not in ("runtime", "toolkit-incompatible", "stale-lease",
                                                           "agents-md-shadowed", "contract-missing",
                                                           "repo-inside-tree", "repo-missing", "conflict-copy")}
        if not tree_checks:
            return
        root = self.root
        for dirpath, dirs, files in os.walk(root):
            here = Path(dirpath)
            rel_dir = "" if here == root else here.relative_to(root).as_posix()
            kept: list[str] = []
            for name in sorted(dirs):
                full = here / name
                rel = f"{rel_dir}/{name}" if rel_dir else name
                if full.is_symlink():
                    files.append(name)  # never follow links; treat as a file entry
                    continue
                if not self.entry(rel, name, True, full):
                    kept.append(name)
            dirs[:] = kept
            for name in sorted(files):
                rel = f"{rel_dir}/{name}" if rel_dir else name
                self.entry(rel, name, False, here / name)

    def entry(self, rel: str, name: str, is_dir: bool, full: Path) -> bool:
        """Run the per-entry checks. Returns True when the walk must not descend into a folder."""
        parts = rel.split("/")
        top = parts[0]
        top_cf = top.casefold()

        if name == ".git":
            kind = "folder" if is_dir else "file"
            self.add("nested-git", ERROR, rel,
                     f"a git {kind} ('.git') is inside the synced folder; git must never run inside it",
                     "move the .git entry out of the tree (the working files can stay) or ask the repository "
                     "owner where the clone should live",
                     self.move_action("nested-git", rel, "a repository belongs outside the synced tree"))
            return True

        if top_cf == "code" and len(parts) == 2 and rel != "code/README.md":
            if not self.ignore.is_ignored(rel, is_dir=is_dir):
                self.add("code-folder-content", ERROR, rel,
                         f"'{rel}' is inside code/, which may only hold README.md",
                         "clone repositories outside the synced folder (see code/README.md); move this item out",
                         self.move_action("code-folder-content", rel, "code/ holds only README.md"))
                return is_dir
        if not is_dir and self.secret_ignore.is_ignored(name):  # independent of forbidden-content
            self.add("secrets-suspect", WARN, rel, f"'{rel}' has a name typical of secrets or credentials",
                     "do not keep secrets in a shared folder; move it to a private location")

        forbidden = self.ignore.matching_rule(rel, is_dir=is_dir)
        if forbidden is not None:
            self.add("forbidden-content", ERROR, rel,
                     f"'{rel}' must not be in the tree (matches forbidden pattern '{forbidden.strip()}')",
                     "remove it, or move it out of the synced folder if you still need it",
                     self.move_action("forbidden-content", rel, "forbidden in the tree") if is_dir
                     else self.remove_action("forbidden-content", rel, "forbidden pattern"))
            return is_dir

        if is_dir and (name in TOOLKIT_DIRS or (name in TOOLKIT_CLAUDE_DIRS and parts[-2:-1] == [".claude"])):
            self.add("toolkit-artifacts", WARN, rel,
                     f"'{rel}' looks like toolkit code inside the project (projects hold no toolkit files)",
                     "install the toolkit per machine (plugin or bundle) and delete this copy")
            return True
        if not is_dir and name == "SKILL.md":
            self.add("toolkit-artifacts", WARN, rel, f"'{rel}' looks like a toolkit skill inside the project",
                     "install the toolkit per machine and delete this copy")

        self.name_checks(rel, name)

        if is_dir:
            return False

        if _TEMP_RE.match(name):
            self.add("temp-stragglers", INFO, rel, f"'{rel}' is a leftover of an interrupted write",
                     "safe to delete once nobody is writing",
                     self.remove_action("temp-stragglers", rel, "interrupted write leftover"))
        if top_cf in ("00_inbox", SOURCES) and full.suffix.lower() in readable.CLOUD_NATIVE_EXTENSIONS:
            self.add("cloud-native", WARN, rel, f"'{rel}' is a cloud-only document pointer with no content",
                     readable.GUIDANCE_CLOUD_NATIVE)
        try:
            st = full.lstat() if full.is_symlink() else os.stat(fs_path(full))
        except OSError:
            return False
        if st.st_size > self.large_bytes and top_cf != SOURCES:
            self.add("large-file", INFO, rel,
                     f"'{rel}' is {st.st_size // MB} MB (limit {self.large_bytes / MB:g} MB outside 10_sources/)",
                     "keep large files outside the synced folder or move them into 10_sources/ via intake")
        if top_cf == "00_inbox" and name != ".keep":
            age_days = (self.now - st.st_mtime) / 86400
            if age_days > self.inbox_days:
                self.add("inbox-age", INFO, rel, f"'{rel}' has waited {int(age_days)} days in the inbox "
                         f"(limit {self.inbox_days:g})", "run /intake, or remove it if it is no longer needed")
        if (top_cf in OFFLINE_AREAS or len(parts) == 1) and stat.S_ISREG(st.st_mode):
            try:
                verdict = readable.classify(full)
            except OSError:
                verdict = None
            if verdict is not None and verdict.status == readable.NOT_LOCAL:
                self.add("not-local", INFO, rel, f"'{rel}' is not downloaded on this machine ({verdict.reason})",
                         readable.GUIDANCE_NOT_LOCAL)
        return False

    def name_checks(self, rel: str, name: str) -> None:
        problems: list[str] = []
        if nfc(name) != name:
            problems.append("the name is not Unicode-normalised (NFC); it may look identical but not match")
        if name.endswith((".", " ")):
            problems.append("the name ends with a dot or a space (not valid on Windows)")
        if naming.is_reserved(name):
            problems.append("the name is reserved on Windows (CON, PRN, AUX, NUL, COM1-9, LPT1-9)")
        if any(c in name for c in '<>:"|?*') or any(ord(c) < 32 for c in name):
            problems.append('the name contains characters Windows does not allow (< > : " | ? *)')
        if problems:
            self.add("bad-name", WARN, rel, f"'{rel}': " + "; ".join(problems),
                     "rename it with plain letters, digits, dashes and underscores")
        if len(rel) > self.path_limit:
            self.add("path-too-long", WARN, rel,
                     f"'{rel}' is {len(rel)} characters (limit {self.path_limit} from policy.path_limit)",
                     "shorten the file or folder names; long paths break Windows and some sync clients")

    # ---------------------------------------------------------------- targeted checks
    def contract(self) -> None:
        from .scaffold import skeleton_folders
        from .templates import load_skeleton

        required = [f["path"] for f in skeleton_folders(self.config) if f["path"].count("/") == 0]
        for folder in CONTRACT_FOLDERS:
            if folder == "code" and not self.has_repos:
                continue
            if folder not in required:
                required.append(folder)
        files = ["project.yaml", "_system/state.json", "AGENTS.md", "README.md"]
        if self.has_repos:
            files.append("code/README.md")
        try:
            files += [f["path"] for f in load_skeleton().get("root_files", []) if f.get("always")]
        except (OSError, ValueError, KeyError):
            pass
        top_names = {p.name for p in self.root.iterdir()} if self.root.is_dir() else set()
        for rel in sorted(dict.fromkeys(required)):
            if (self.root / rel).is_dir() and rel in top_names:
                continue
            look = next((n for n in top_names if n.casefold() == rel.casefold() or nfc(n) == rel), None)
            hint = f" (found '{look}', which differs in spelling or case)" if look else ""
            self.add("contract-missing", ERROR, rel, f"contract folder '{rel}' is missing or renamed{hint}",
                     "restore the folder under its original name; folder names are fixed by the contract")
        for rel in sorted(dict.fromkeys(files)):
            if (self.root / rel).is_file():
                continue
            actions: list[Action] = []
            if rel in ("AGENTS.md", "README.md", "code/README.md"):
                actions = [Action(_action_id("contract-missing", "render-template", rel), "render-template", rel,
                                  f"re-render {rel} from the project's templates (never overwrites)")]
            self.add("contract-missing", ERROR, rel, f"contract file '{rel}' is missing or renamed",
                     "restore it from the sync provider's version history or re-render it", actions)
        try:
            wiki_on = bool((self.report_state().get("wiki") or {}).get("configured"))
        except EngineError:
            wiki_on = False
        if wiki_on and not (self.root / "30_wiki" / "WIKI.md").is_file():
            self.add("contract-missing", ERROR, "30_wiki/WIKI.md",
                     "the wiki is configured but 30_wiki/WIKI.md is missing",
                     "restore it from version history, or re-run /init-wiki")

    def report_state(self) -> dict[str, Any]:
        import json

        try:
            data = json.loads((self.root / "_system" / "state.json").read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise EngineError("state_invalid", str(exc), 4) from exc
        return data if isinstance(data, dict) else {}

    def instruction_files(self) -> None:
        for rel in INSTRUCTION_FILES:
            if (self.root / rel).is_file():
                self.add("agents-md-shadowed", WARN, rel,
                         f"'{rel}' in the project makes Claude ignore AGENTS.md (the project's only instruction file)",
                         "move anything useful into the project-notes block of AGENTS.md and delete this file")
        for parent in self.root.resolve().parents:
            for rel in INSTRUCTION_FILES:
                candidate = parent / rel
                try:
                    present = candidate.is_file()
                except OSError:
                    present = False
                if present:
                    self.add("agents-md-shadowed", WARN, None,
                             f"{candidate.as_posix()} sits above the project on this machine and may take "
                             "precedence over AGENTS.md",
                             "move that file elsewhere or keep the metarepository outside that folder",
                             location=candidate.as_posix())

    def conflict_copies(self) -> None:
        provider = (self.cfg.get("sync") or {}).get("provider") or "none"
        names = [] if provider == "none" else [provider]
        try:
            found = conflicts.detect(self.root, providers=names)
        except (OSError, EngineError) as exc:
            self.skip("conflict-copy", f"could not scan: {exc}")
            return
        for c in found:
            actions = []
            if c.confidence == "likely" and c.original:
                actions = self.remove_action("conflict-copy", c.path, "losing copy, only after reviewing both")
            self.add("conflict-copy", WARN, c.path,
                     f"'{c.path}' looks like a sync conflict copy of '{c.original or 'another file'}' "
                     f"({c.confidence}, rule {c.rule})",
                     "open both versions, keep what is right, then delete the losing copy; "
                     "never copy one over the other without reading both", actions,
                     original=c.original, confidence=c.confidence, rule=c.rule, provider=c.provider)

    def stale_leases(self) -> None:
        for area in leases.AREAS:
            try:
                st = leases.status(self.root, area)
            except EngineError as exc:
                self.add("stale-lease", INFO, leases.lease_rel(area), f"the '{area}' lease file is unreadable",
                         "delete the file once nobody is writing (a new lease is created on demand)", reason=exc.code)
                continue
            if st["state"] == "expired":
                rel = leases.lease_rel(area)
                holder = st["lease"]["holder"]
                self.add("stale-lease", INFO, rel,
                         f"the '{area}' lease of {holder.get('actor')}@{holder.get('host')} expired at "
                         f"{st['lease']['expires_at']} and was never released",
                         "nothing is wrong; releasing it just tidies up",
                         [Action(_action_id("stale-lease", "release-lease", area), "release-lease", rel,
                                 f"release the expired '{area}' lease", extra={"area": area})], area=area)

    def toolkit(self) -> None:
        if not self.config_ok:
            self.skip("toolkit-incompatible", "project.yaml could not be read")
            return
        requires = (self.config.get("toolkit") or {}).get("requires")
        if not requires:
            self.skip("toolkit-incompatible", "project.yaml has no toolkit.requires")
            return
        try:
            verdict = compat.check(str(requires), self.version)
        except compat.CompatError as exc:
            self.add("toolkit-incompatible", ERROR, "project.yaml", f"toolkit.requires is invalid: {exc}",
                     "fix toolkit.requires in project.yaml")
            return
        if not verdict.ok:
            remedy = ("update the toolkit (plugin or bundle) to a version inside the range"
                      if verdict.status == compat.UPDATE_TOOLKIT
                      else "run `migrate --dry-run` and, if it looks right, `migrate --yes`; or install a toolkit "
                      "inside the range")
            self.add("toolkit-incompatible", ERROR, "project.yaml", verdict.message, remedy,
                     status=verdict.status, requires=str(requires), installed=self.version)

    def runtime(self) -> None:
        if sys.version_info[:2] < (3, 10):
            self.add("runtime", ERROR, None, f"Python 3.10 or newer is required; this is {platform.python_version()}",
                     "install a newer Python 3 (the toolkit never installs software itself)")
        try:
            import yaml  # noqa: F401
        except ImportError:
            self.add("runtime", ERROR, None, "PyYAML is not installed",
                     "python3 -m pip install pyyaml  (Windows: py -3 -m pip install pyyaml)", dependency="pyyaml")
        try:
            from .extract.registry import all_adapters

            adapters = all_adapters(self.cfg.get("extraction"))
        except Exception as exc:  # noqa: BLE001 - the extractor registry is optional for the verdict
            self.report.warnings.append(("extractors_unchecked", f"extractor availability not checked: {exc}"))
            return
        for a in adapters:
            if getattr(a, "mode", "local") == "cloud" or a.available():
                continue
            self.add("runtime", INFO, None, f"optional extractor '{a.name}' is not installed",
                     a.install_hint or "optional; see `extractors`", extractor=a.name)

    def repos(self) -> None:
        if not self.has_repos:
            return
        provider = None
        try:
            from . import repos as repos_mod  # spec 006

            provider = getattr(repos_mod, "local_clone_paths", None)
        except ImportError:
            provider = None
        if provider is None:
            reason = "per-user clone paths are provided by spec 006 (not available in this toolkit version)"
            self.skip("repo-inside-tree", reason)
            self.skip("repo-missing", reason)
            return
        try:
            paths = provider(self.root, self.cfg)
        except Exception as exc:  # noqa: BLE001
            self.skip("repo-inside-tree", f"clone paths could not be read: {exc}")
            self.skip("repo-missing", f"clone paths could not be read: {exc}")
            return
        root = self.root.resolve()
        for repo in self.cfg.get("repos") or []:
            name = repo.get("name")
            path = paths.get(name)
            if not path or not Path(path).exists():
                self.add("repo-missing", INFO, None, f"repository '{name}' has no local clone on this machine",
                         f"clone it outside the synced folder: repos clone {name}", repo=name)
                continue
            try:
                inside = Path(path).resolve().relative_to(root).as_posix()
            except ValueError:
                continue
            self.add("repo-inside-tree", ERROR, inside,
                     f"the local clone of '{name}' is inside the synced tree ({inside})",
                     "move the clone out of the synced folder and point the repository there",
                     self.move_action("repo-inside-tree", inside, "clones belong outside the synced tree"),
                     repo=name)


def scan(root: Path, config: dict[str, Any], *, local_only: bool = False, version: str,
         config_ok: bool = True, now: float | None = None) -> Report:
    s = _Scan(root, config, local_only=local_only, version=version, config_ok=config_ok, now=now)
    s.runtime()
    s.toolkit()
    s.repos()
    if not local_only:
        s.contract()
        s.instruction_files()
        s.conflict_copies()
        s.stale_leases()
    s.walk()
    order = {c: i for i, c in enumerate(CHECKS)}
    sev = {ERROR: 0, WARN: 1, INFO: 2}
    s.report.findings.sort(key=lambda f: (order[f.id], sev[f.severity], f.path or ""))
    return s.report


# ------------------------------------------------------------------ fixes (013/R3, R4)
def _move_tree(src: Path, final: Path) -> None:
    try:
        os.rename(fs_path(src), fs_path(final))
        return
    except OSError as exc:
        if exc.errno != errno.EXDEV:
            raise
    if src.is_file():
        safe_move(src, final)
        return
    shutil.copytree(src, final, symlinks=True)
    for a in sorted(p for p in src.rglob("*") if p.is_file() and not p.is_symlink()):
        b = final / a.relative_to(src)
        if not b.is_file() or sha256_file(a) != sha256_file(b):
            raise OSError(errno.EIO, f"copy verification failed for {a}; the original was left in place")
    shutil.rmtree(src)


def move_out(root: Path, rel: str, dest: str) -> str:
    """Move ``rel`` into the folder ``dest`` (created if needed), which must be outside the synced tree."""
    root = Path(root).resolve()
    src = root / rel
    if src.is_symlink() or not src.exists():
        raise EngineError("move_failed", f"{rel} does not exist or is a link", 3, rel)
    target_dir = Path(dest).expanduser().resolve()
    if target_dir == root or root in target_dir.parents:
        raise EngineError("destination_inside_tree", f"{dest} is inside the synced folder; choose a folder "
                          "outside it", 3, rel)
    final = target_dir / src.name
    if final.exists():
        raise EngineError("destination_exists", f"{final} already exists; nothing was moved", 3, rel)
    target_dir.mkdir(parents=True, exist_ok=True)
    _move_tree(src, final)
    return final.as_posix()


def apply_action(ctx_factory: Any, root: Path, config: dict[str, Any], action: Action, *, dest: str | None,
                 actor: str) -> dict[str, Any]:
    """Perform one confirmed action. Returns ``{id, kind, path, status, message}``; never raises for an
    expected failure (the caller reports it and continues with the next confirmed action)."""
    root = Path(root)
    row = {"id": action.id, "kind": action.kind, "path": action.path}
    extra: dict[str, Any] = {}
    try:
        if action.kind == "remove":
            from .commands import remove as remove_cmd

            digest = sha256_file(root / action.path)
            res = remove_cmd.run(ctx_factory(), argparse.Namespace(path=action.path, confirm=digest))
            extra = dict(res.event_extra)
            message = f"deleted {action.path}"
        elif action.kind == "move-out":
            if not dest:
                return {**row, "status": "skipped", "message": "needs a destination folder (--dest "
                        f"{action.id}=<folder outside the synced tree>)"}
            final = move_out(root, action.path, dest)
            extra = {"moved_to": final}
            message = f"moved {action.path} to {final}"
        elif action.kind == "release-lease":
            with leases.held(root, action.extra["area"], actor, "doctor"):
                pass
            message = f"released the expired '{action.extra['area']}' lease"
        elif action.kind == "render-template":
            from .templates import render_project_files

            def read_existing(r: str) -> str | None:
                p = root / r
                return p.read_text(encoding="utf-8") if p.is_file() else None

            text = render_project_files(config, read_existing).get(action.path)
            if text is None:
                return {**row, "status": "failed", "message": f"{action.path} is not rendered for this project"}
            with leases.held(root, "config", actor, "doctor"):
                (root / action.path).parent.mkdir(parents=True, exist_ok=True)
                created = write_if_absent(root / action.path, text)
            message = f"re-rendered {action.path}" if created else f"{action.path} already exists; left as is"
        else:  # pragma: no cover - guarded by the action model
            return {**row, "status": "failed", "message": f"unknown action kind {action.kind}"}
    except EngineError as exc:
        return {**row, "status": "failed", "message": exc.message}
    except OSError as exc:
        return {**row, "status": "failed", "message": str(exc)}
    try:
        append_event(root, command="doctor", targets=[action.path], result="ok", actor=actor,
                     extra={"action": action.kind, "action_id": action.id, **extra})
    except OSError:
        pass
    return {**row, "status": "applied", "message": message}


def record_last_doctor(root: Path, report: Report, *, version: str, actor: str, local_only: bool) -> None:
    """013/R5: store a summary in ``state.json -> last_doctor`` under the config lease. Raises on failure;
    the caller treats it as best effort."""
    import json

    from .events import now_iso

    summary = {"at": now_iso(), "toolkit": version, "scope": "local" if local_only else "full",
               "counts": {k: v for k, v in report.counts().items() if k != "total"}}

    def transform(existing: bytes | None) -> bytes:
        state = json.loads(existing.decode("utf-8")) if existing else {}
        state["last_doctor"] = summary
        return (json.dumps(state, indent=2, ensure_ascii=False) + "\n").encode("utf-8")

    with leases.held(Path(root), "config", actor, "doctor") as handle:
        leases.update_hot_file(Path(root), handle, "_system/state.json", transform)
