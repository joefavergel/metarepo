#!/usr/bin/env python3
"""Structure checks for the repository (001/R12). Dev-only, standard library only.

    python3 tools/ci_check.py

Rules: manifests; SKILL.md frontmatter; command set; plugin == tooling; version sync and changelog;
templates parse; domain-agnosticism (denylist); no-artifact rule. Each rule is a function returning a
list of problem strings; the exit status is 1 when any rule reports a problem. Rules tolerate artifacts
that a later spec has not produced yet only where this file says so.
"""
from __future__ import annotations

import json
import os
import re
import string
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import build  # noqa: E402

ROOT = build.ROOT

# 001/ADR-003: user-facing commands. Internal skills are invoked by `intake` and are not commands.
COMMAND_SKILLS = (
    "create-metarepo", "join-metarepo", "intake", "init-wiki", "feed-wiki",
    "consult-wiki", "lint-wiki", "make-output", "doctor",
)
INTERNAL_SKILLS = ("extract", "unbundle")

_SEMVER = re.compile(r"^\d+\.\d+\.\d+$")
_KEBAB = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
_TEXT_SUFFIXES = {".md", ".py", ".json", ".yaml", ".yml", ".txt", ".tmpl", ".toml", ".cfg", ".ini", ".sh", ".ps1", ""}


def _load_json(path: Path, problems: list[str]):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        problems.append(f"missing file: {path.name} ({path})")
    except ValueError as exc:
        problems.append(f"{path}: invalid JSON: {exc}")
    return None


# ------------------------------------------------------------------ manifests
def check_manifests(root: Path) -> list[str]:
    problems: list[str] = []
    mp = root / ".claude-plugin" / "marketplace.json"
    market = _load_json(mp, problems)
    if isinstance(market, dict):
        if not isinstance(market.get("name"), str) or not _KEBAB.match(market.get("name", "")):
            problems.append("marketplace.json: 'name' must be a kebab-case string")
        owner = market.get("owner")
        if not isinstance(owner, dict) or not owner.get("name"):
            problems.append("marketplace.json: 'owner.name' is required")
        plugins = market.get("plugins")
        if not isinstance(plugins, list) or not plugins:
            problems.append("marketplace.json: 'plugins' must be a non-empty list")
        else:
            for i, entry in enumerate(plugins):
                if not isinstance(entry, dict) or not entry.get("name") or not entry.get("source"):
                    problems.append(f"marketplace.json: plugins[{i}] needs 'name' and 'source'")
                    continue
                src = entry["source"]
                if isinstance(src, str):
                    if not src.startswith("./") or ".." in src.split("/"):
                        problems.append(f"marketplace.json: plugins[{i}].source must be './...' without '..'")
                    elif not (root / src).is_dir():
                        problems.append(f"marketplace.json: plugins[{i}].source {src} does not exist")
                    else:
                        pj = _load_json(root / src / ".claude-plugin" / "plugin.json", problems)
                        if isinstance(pj, dict) and pj.get("name") != entry["name"]:
                            problems.append("marketplace.json entry name differs from plugin.json name")
    elif market is not None:
        problems.append("marketplace.json must be a JSON object")
    pj = _load_json(root / "plugin" / ".claude-plugin" / "plugin.json", problems)
    if isinstance(pj, dict):
        name = pj.get("name")
        if not isinstance(name, str) or not _KEBAB.match(name):
            problems.append("plugin.json: 'name' must be a kebab-case string")
        elif re.match(r"^(claude|anthropic|anthropics|cc-plugin)(-|$)", name):
            problems.append("plugin.json: name is reserved for Anthropic's own plugins")
        if not isinstance(pj.get("version"), str) or not _SEMVER.match(pj.get("version", "")):
            problems.append("plugin.json: 'version' must be semver")
        if not pj.get("description"):
            problems.append("plugin.json: 'description' is required")
    elif pj is not None:
        problems.append("plugin.json must be a JSON object")
    hooks = root / "plugin" / "hooks" / "hooks.json"
    hj = _load_json(hooks, problems)
    if isinstance(hj, dict):
        if not isinstance(hj.get("hooks"), dict):
            problems.append("hooks.json: top-level 'hooks' wrapper object is required")
        elif not (root / "plugin" / "hooks" / "guard_paths.py").is_file():
            problems.append("hooks.json present but plugin/hooks/guard_paths.py is missing")
    return problems


# ------------------------------------------------------------------ skills
def parse_frontmatter(text: str) -> dict[str, str] | None:
    """Minimal frontmatter reader: scalar keys, quoted strings, and folded/literal block scalars."""
    lines = text.replace("\r\n", "\n").split("\n")
    if not lines or lines[0].strip() != "---":
        return None
    try:
        end = lines.index("---", 1)
    except ValueError:
        return None
    out: dict[str, str] = {}
    i = 1
    while i < end:
        line = lines[i]
        m = re.match(r"^([A-Za-z0-9_-]+):\s*(.*)$", line)
        if not m:
            i += 1
            continue
        key, val = m.group(1), m.group(2).strip()
        if val in (">", ">-", "|", "|-"):
            block = []
            i += 1
            while i < end and (lines[i].startswith((" ", "\t")) or not lines[i].strip()):
                block.append(lines[i].strip())
                i += 1
            out[key] = " ".join(b for b in block if b)
            continue
        if len(val) >= 2 and val[0] == val[-1] and val[0] in "\"'":
            val = val[1:-1]
        out[key] = val
        i += 1
    return out


def check_skills(root: Path) -> list[str]:
    problems: list[str] = []
    skills_dir = root / "tooling" / "skills"
    present = sorted(p.name for p in skills_dir.iterdir() if p.is_dir()) if skills_dir.is_dir() else []
    for name in present:
        skill_md = skills_dir / name / "SKILL.md"
        if not skill_md.is_file():
            problems.append(f"skills/{name}: SKILL.md missing")
            continue
        fm = parse_frontmatter(skill_md.read_text(encoding="utf-8"))
        if fm is None:
            problems.append(f"skills/{name}/SKILL.md: frontmatter missing or unterminated")
            continue
        if fm.get("name") != name:
            problems.append(f"skills/{name}/SKILL.md: frontmatter name {fm.get('name')!r} != folder name")
        if not fm.get("description"):
            problems.append(f"skills/{name}/SKILL.md: description is required")
        elif len(fm["description"]) > 1024:
            problems.append(f"skills/{name}/SKILL.md: description longer than 1024 characters")
        if not (skills_dir / name / "references" / "engine.md").is_file():
            problems.append(f"skills/{name}: references/engine.md missing (engine resolution, 001/R5)")
        body = skill_md.read_text(encoding="utf-8")
        if "CLAUDE_PLUGIN_ROOT" in body:
            problems.append(f"skills/{name}/SKILL.md: harness-specific variable in skill body (001/R3)")
    missing = [c for c in COMMAND_SKILLS if c not in present]
    extra = [p for p in present if p not in COMMAND_SKILLS and p not in INTERNAL_SKILLS]
    if missing:
        problems.append("command set (001/ADR-003): missing skills: " + ", ".join(missing))
    if extra:
        problems.append("command set (001/ADR-003): unexpected skills: " + ", ".join(extra))
    return problems


# ------------------------------------------------------------------ plugin == tooling, versions
def check_plugin_sync(root: Path) -> list[str]:
    return build.diff_plugin(root)


def check_versions(root: Path) -> list[str]:
    problems: list[str] = []
    try:
        version = build.read_version(root)
    except FileNotFoundError:
        return ["tooling/VERSION missing"]
    if not _SEMVER.match(version):
        problems.append(f"tooling/VERSION {version!r} is not semver")
    pj_path = root / "plugin" / ".claude-plugin" / "plugin.json"
    if pj_path.is_file():
        try:
            pv = json.loads(pj_path.read_text(encoding="utf-8")).get("version")
        except ValueError:
            pv = None
        if pv != version:
            problems.append(f"plugin.json version {pv!r} != tooling/VERSION {version!r}")
    changelog = root / "tooling" / "CHANGELOG.md"
    if not changelog.is_file():
        problems.append("tooling/CHANGELOG.md missing (001/R13)")
    elif not re.search(rf"^##\s+\[?{re.escape(version)}\]?\b", changelog.read_text(encoding="utf-8"), re.M):
        problems.append(f"tooling/CHANGELOG.md has no entry for version {version}")
    return problems


# ------------------------------------------------------------------ templates
def check_templates(root: Path) -> list[str]:
    problems: list[str] = []
    tdir = root / "tooling" / "templates"
    if not tdir.is_dir():
        return ["tooling/templates missing"]
    for f in sorted(tdir.rglob("*")):
        if not f.is_file():
            continue
        rel = f.relative_to(root).as_posix()
        if f.suffix == ".json":
            try:
                json.loads(f.read_text(encoding="utf-8"))
            except ValueError as exc:
                problems.append(f"{rel}: invalid JSON: {exc}")
        elif f.suffix == ".tmpl" or f.name.endswith(".tmpl"):
            text = f.read_text(encoding="utf-8")
            for m in string.Template.pattern.finditer(text):
                if m.group("invalid") is not None:
                    line = text.count("\n", 0, m.start()) + 1
                    problems.append(f"{rel}:{line}: invalid placeholder (use $$ for a literal dollar sign)")
    schemas = root / "tooling" / "engine" / "metarepo_engine" / "schemas"
    if schemas.is_dir():
        for f in sorted(schemas.glob("*.json")):
            try:
                json.loads(f.read_text(encoding="utf-8"))
            except ValueError as exc:
                problems.append(f"{f.relative_to(root).as_posix()}: invalid JSON: {exc}")
    return problems


# ------------------------------------------------------------------ denylist
def load_denylist(path: Path) -> list[re.Pattern[str]]:
    patterns: list[re.Pattern[str]] = []
    if not path.is_file():
        return patterns
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("re:"):
            patterns.append(re.compile(line[3:], re.I))
        else:
            patterns.append(re.compile(r"(?<![A-Za-z0-9_])" + re.escape(line) + r"(?![A-Za-z0-9_])", re.I))
    return patterns


def _shipped_files(root: Path):
    for base in ("tooling", "plugin"):
        for f in build.iter_files(root / base):
            yield f


def check_denylist(root: Path) -> list[str]:
    problems: list[str] = []
    deny = root / "tools" / "denylist.txt"
    if not deny.is_file():
        return ["tools/denylist.txt missing"]
    patterns = load_denylist(deny)
    if not patterns:
        problems.append("tools/denylist.txt has no terms")
    for f in _shipped_files(root):
        try:
            text = f.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue  # binary or unreadable: not scanned
        for pat in patterns:
            m = pat.search(text)
            if m:
                line = text.count("\n", 0, m.start()) + 1
                problems.append(f"{f.relative_to(root).as_posix()}:{line}: denylisted term {m.group(0)!r} (P1)")
    return problems


# ------------------------------------------------------------------ no-artifact rule
_ARTIFACT_NAMES = {"SKILL.md", "cli.py", "plugin.json", "marketplace.json"}
_ARTIFACT_DIRS = {"engine", "plugin", ".claude-plugin", "_toolkit", "tooling", "skills", "metarepo_engine"}


def find_artifacts(project_root: Path) -> list[str]:
    """Files in a scaffolded project that belong to the toolkit (001/R12, 001/ADR-004)."""
    bad: list[str] = []
    for dirpath, dirs, files in os.walk(project_root):
        rel_dir = Path(dirpath).relative_to(project_root)
        for d in dirs:
            if d in _ARTIFACT_DIRS and not (rel_dir.parts[:1] == ("30_wiki",) and d == "skills"):
                bad.append((rel_dir / d).as_posix() + "/")
        for name in files:
            rel = (rel_dir / name).as_posix()
            if name in _ARTIFACT_NAMES or name.endswith((".py", ".pyc")):
                bad.append(rel)
                continue
            try:
                text = (Path(dirpath) / name).read_text(encoding="utf-8")
            except (UnicodeDecodeError, OSError):
                continue
            if "metarepo_engine" in text or "import argparse" in text:
                bad.append(rel + " (contains engine code)")
    return sorted(set(bad))


def check_no_artifacts(root: Path) -> list[str]:
    """Scaffold a fixture project with the engine and verify it holds no toolkit artifacts."""
    engine = root / "tooling" / "engine"
    if not (engine / "metarepo_engine" / "scaffold.py").is_file():
        return []  # scaffolding arrives with spec 002; the rule is enforced once it exists
    with tempfile.TemporaryDirectory() as tmp:
        target = Path(tmp) / "fixture-project"
        code = (
            "import sys; sys.path.insert(0, sys.argv[1]);"
            "from pathlib import Path;"
            "from metarepo_engine import scaffold;"
            "scaffold.scaffold(Path(sys.argv[2]), scaffold.fixture_config());"
        )
        proc = subprocess.run([sys.executable, "-c", code, str(engine), str(target)],
                              capture_output=True, text=True)
        if proc.returncode != 0 and "PyYAML is required" in proc.stderr:
            return ["no-artifact rule needs PyYAML to scaffold the fixture project: "
                    "run `python -m pip install -r requirements-dev.txt` (this rule is never skipped silently)"]
        if proc.returncode != 0:
            return ["no-artifact rule: could not scaffold a fixture project: " + (proc.stderr.strip() or proc.stdout.strip())]
        return [f"no-artifact rule: toolkit artifact in project: {p}" for p in find_artifacts(target)]


RULES = (
    ("manifests", check_manifests),
    ("skills", check_skills),
    ("plugin-sync", check_plugin_sync),
    ("versions", check_versions),
    ("templates", check_templates),
    ("denylist", check_denylist),
    ("no-artifacts", check_no_artifacts),
)


def run(root: Path = ROOT) -> dict[str, list[str]]:
    return {name: fn(root) for name, fn in RULES}


def main() -> int:
    results = run()
    failed = 0
    for name, problems in results.items():
        if problems:
            failed += 1
            print(f"FAIL {name}")
            for p in problems:
                print(f"  - {p}")
        else:
            print(f"ok   {name}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
