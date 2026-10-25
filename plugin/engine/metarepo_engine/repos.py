"""Code repositories of a project (spec 006): where this member keeps them, their state, cloning.

Clones always live outside the synced tree (002/ADR-002, P9). This module and ``commands/repos_clone.py`` are the
only places allowed to reach the network, through the ``git`` subprocess on an explicit ``repos clone`` (003/R14).
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any

from . import userconfig
from .fsops import nfc
from .jsonout import EXIT_BLOCKING, EXIT_DEPENDENCY, EXIT_PRECONDITION, EngineError

GIT_HINT = ("git is required to clone repositories. Install it from https://git-scm.com/downloads "
            "(macOS: xcode-select --install; Windows: winget install Git.Git). The toolkit never installs software.")


def declared(config: dict[str, Any]) -> list[dict[str, Any]]:
    return [r for r in config.get("repos") or [] if isinstance(r, dict) and r.get("name")]


def find_declared(config: dict[str, Any], name: str) -> dict[str, Any]:
    for repo in declared(config):
        if repo["name"] == name:
            return repo
    known = ", ".join(r["name"] for r in declared(config)) or "none declared"
    raise EngineError("repo_unknown", f"project.yaml → repos has no repository named {name!r} ({known})",
                      EXIT_PRECONDITION)


def default_path(slug: str, name: str) -> Path:
    """Proposal for a clone location (006/R6): ``~/code/<slug>/<name>``."""
    return Path.home() / "code" / slug / name


def is_inside(path: Path, root: Path) -> bool:
    """True when ``path`` is the root, inside it, or contains it. Symlinks and junctions are resolved first."""
    p, r = Path(os.path.realpath(path)), Path(os.path.realpath(root))
    return p == r or p.is_relative_to(r) or r.is_relative_to(p)


def refuse_inside_tree(path: Path, root: Path) -> None:
    if is_inside(path, root):
        raise EngineError(
            "clone_inside_tree",
            f"{path} is inside (or contains) the synced metarepository at {root}. Clones must live outside it "
            "because git metadata and build folders must not be synchronised. Choose a local folder such as "
            "~/code/<project>/<name>.",
            EXIT_PRECONDITION,
        )


def _git(args: list[str], cwd: Path | None = None, timeout: int | None = 30) -> subprocess.CompletedProcess:
    env = dict(os.environ, GIT_TERMINAL_PROMPT="0", LC_ALL="C")
    return subprocess.run(["git", *args], cwd=str(cwd) if cwd else None, capture_output=True, text=True,
                          encoding="utf-8", errors="replace", env=env, timeout=timeout)


def git_available() -> bool:
    return shutil.which("git") is not None


def normalize_url(url: str) -> str:
    """Host + path of a git URL, ignoring scheme, user, port, a trailing ``.git`` and case of the host."""
    text = url.strip()
    m = re.match(r"^(?:[\w.+-]+@)?([^:/\s]+):(?!//)(.+)$", text)  # scp-like
    if m and "://" not in text:
        host, path = m.group(1), m.group(2)
    else:
        m = re.match(r"^[\w.+-]+://(?:[^@/]*@)?([^:/]+)(?::\d+)?/(.*)$", text)
        if m:
            host, path = m.group(1), m.group(2)
        else:
            return nfc(text.rstrip("/"))  # local path or unknown form: compare as written
    path = path.strip("/")
    if path.endswith(".git"):
        path = path[:-4]
    return f"{host.lower()}/{path}"


def inspect(path: Path, url: str) -> dict[str, Any]:
    """State of the folder at ``path`` against the declared ``url``."""
    info: dict[str, Any] = {"exists": path.is_dir(), "is_git": False, "remote": None, "remote_matches": None,
                            "branch": None}
    if not info["exists"] or not git_available():
        return info
    top = _git(["rev-parse", "--show-toplevel"], cwd=path)
    # the folder must be the top of its own working tree, not a subfolder of some other repository
    if top.returncode != 0 or Path(os.path.realpath(top.stdout.strip())) != Path(os.path.realpath(path)):
        return info
    info["is_git"] = True
    remote = _git(["remote", "get-url", "origin"], cwd=path)
    if remote.returncode == 0:
        info["remote"] = remote.stdout.strip()
        info["remote_matches"] = normalize_url(info["remote"]) == normalize_url(url)
    else:
        info["remote_matches"] = False
    branch = _git(["rev-parse", "--abbrev-ref", "HEAD"], cwd=path)
    if branch.returncode == 0:
        info["branch"] = branch.stdout.strip()
    return info


def classify(recorded: str | None, info: dict[str, Any] | None, inside_tree: bool) -> str:
    if not recorded:
        return "not-recorded"
    if inside_tree:
        return "inside-tree"
    assert info is not None
    if not info["exists"]:
        return "missing"
    if not info["is_git"]:
        return "not-git"
    if not info["remote_matches"]:
        return "remote-mismatch"
    return "ok"


def status(root: Path, config: dict[str, Any], user: dict[str, Any], names: list[str] | None = None) -> list[dict[str, Any]]:
    out = []
    recorded_all = user.get("repos") or {}
    for repo in declared(config):
        if names is not None and repo["name"] not in names:
            continue
        recorded = recorded_all.get(repo["name"])
        entry: dict[str, Any] = {"name": repo["name"], "url": repo.get("url"), "role": repo.get("role"),
                                 "default_branch": repo.get("default_branch"), "path": recorded}
        inside = bool(recorded) and is_inside(Path(recorded), root)
        info = inspect(Path(recorded), repo["url"]) if recorded and not inside else None
        entry.update({k: (info or {}).get(k) for k in ("exists", "is_git", "remote", "remote_matches", "branch")})
        entry["state"] = classify(recorded, info, inside)
        out.append(entry)
    return out


def run_clone(url: str, dest: Path, branch: str | None) -> None:
    """``git clone`` with the member's own credentials. Failures raise; nothing is retried (006/R6)."""
    if not git_available():
        raise EngineError("git_missing", GIT_HINT, EXIT_DEPENDENCY)
    dest.parent.mkdir(parents=True, exist_ok=True)
    args = ["clone"] + (["--branch", branch] if branch else []) + ["--", url, str(dest)]
    try:
        proc = _git(args, timeout=None)
    except OSError as exc:
        raise EngineError("clone_failed", f"git could not be started: {exc}", EXIT_BLOCKING) from exc
    if proc.returncode != 0:
        raise EngineError("clone_failed", f"git clone failed (exit {proc.returncode}): "
                          f"{(proc.stderr or proc.stdout).strip()[-800:]}", EXIT_BLOCKING)


def record(project_id: str, root: Path, name: str, path: Path) -> Path:
    data = userconfig.load(project_id)
    data["metarepo_root"] = str(root)
    data.setdefault("repos", {})[name] = str(path)
    return userconfig.save(project_id, data)


def local_clone_paths(root: Path, effective_config: dict[str, Any]) -> dict[str, Path | None]:
    """Repo name -> this member's recorded clone path (None when not recorded). Read-only; used by ``doctor`` (013)."""
    pid = effective_config.get("id")
    try:
        recorded = (userconfig.load(str(pid)).get("repos") or {}) if pid else {}
    except ValueError:
        recorded = {}
    return {r["name"]: (Path(recorded[r["name"]]) if recorded.get(r["name"]) else None)
            for r in declared(effective_config)}
