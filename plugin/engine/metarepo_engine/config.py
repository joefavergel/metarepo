"""project.yaml: loading, validation, defaults, forbidden-path defaults (002/R14, section 4 and 6)."""
from __future__ import annotations

import copy
import functools
import json
import re
from pathlib import Path, PurePosixPath
from typing import Any

from . import compat
from .jsonout import EXIT_COMPAT, EXIT_DEPENDENCY, EngineError
from .validate import Findings, validate

INSTALL_HINT = (
    "PyYAML is required. Install it with: python3 -m pip install pyyaml "
    "(Windows: py -3 -m pip install pyyaml). The toolkit never installs software on its own."
)

SCHEMAS_DIR = Path(__file__).resolve().parent / "schemas"

# 002 section 2: contract folders. Projects never rename them (R2).
CONTRACT_FOLDERS = ("00_inbox", "10_sources", "20_extracts", "30_wiki", "outputs", "shared", "personal", "code",
                    "_system")

# 002 section 6: engine defaults of policy.forbidden_paths (gitignore syntax).
FORBIDDEN_PATH_DEFAULTS = (
    ".git/", ".hg/", ".svn/", "node_modules/", ".venv/", "venv/", "__pycache__/", "*.pyc", ".env", ".env.*",
    "*.pem", "*.key", "id_rsa*", ".DS_Store", "Thumbs.db", "desktop.ini", "~$*", "*.tmp", "*.crdownload", "*.part",
)

# 002 section 4: documented defaults for optional keys.
DEFAULTS: dict[str, Any] = {
    "description": "",
    "sync": {"provider": "none", "notes": ""},
    "repos": [],
    "team": [],
    "governance": {"wiki_write_mode": "lease", "wiki_owners": [], "lease_ttl_minutes": 30,
                   "lease_settle_seconds": 10, "audit_expected": False},
    "intake": {"confirm": "always", "auto_feed": True, "triage_budget_tokens": 3000,
               "on_reject": "leave-in-inbox", "archive_bundles": True, "bundle_max_depth": 2,
               "max_source_depth": 4, "bundle_max_uncompressed_mb": 2048, "bundle_max_ratio": 100,
               "categories": []},
    "extraction": {"allow_cloud": False, "preferences": {}, "large_table_rows": 200, "max_body_mb": 2,
                   "quality_thresholds": {}, "commands": []},
    "policy": {"block_patterns": [], "block_terms": [], "path_limit": 200, "forbidden_paths": []},
    "lint": {"severity": {}, "allow_policy_downgrade": False},
    "doctor": {"inbox_max_age_days": 14, "large_file_mb": 200},
    "outputs": {"default_format": "markdown"},
    "extra_folders": [],
}


def yaml_module():
    try:
        import yaml  # type: ignore
    except ImportError as exc:  # pragma: no cover - exercised via the dispatcher test with has_yaml patched
        raise EngineError("missing_dependency", INSTALL_HINT, EXIT_DEPENDENCY) from exc
    return yaml


def load_project_yaml(path: Path) -> dict[str, Any]:
    yaml = yaml_module()
    try:
        text = Path(path).read_text(encoding="utf-8")
    except OSError as exc:
        raise EngineError("config_unreadable", f"cannot read {path.name}: {exc}", EXIT_COMPAT, path.name) from exc
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise EngineError("config_invalid", f"{path.name} is not valid YAML: {exc}", EXIT_COMPAT, path.name) from exc
    if not isinstance(data, dict):
        raise EngineError("config_invalid", f"{path.name} must contain a mapping", EXIT_COMPAT, path.name)
    return data


def dump_yaml(data: Any) -> str:
    yaml = yaml_module()
    return yaml.safe_dump(data, sort_keys=False, allow_unicode=True, default_flow_style=False)


@functools.lru_cache(maxsize=None)
def load_schema(name: str) -> dict[str, Any]:
    return json.loads((SCHEMAS_DIR / f"{name}.schema.json").read_text(encoding="utf-8"))


def _dups(items: list[Any], key: str) -> list[str]:
    seen: set[str] = set()
    dups: list[str] = []
    for item in items:
        if isinstance(item, dict) and isinstance(item.get(key), str):
            if item[key] in seen and item[key] not in dups:
                dups.append(item[key])
            seen.add(item[key])
    return dups


def validate_project(config: dict[str, Any]) -> Findings:
    """Schema check plus the cross-field rules of 002 section 4. Unknown keys are warnings (R14)."""
    findings = validate(config, load_schema("project"))
    # Cross-field rules assume well-typed values: skip a section that already has schema errors.
    bad_paths = {p for p, _ in findings.errors}

    def err(path: str, msg: str) -> None:
        findings.errors.append((path, msg))

    def ok(path: str) -> bool:
        return not any(p == path or p.startswith(path + ".") for p in bad_paths)

    requires = (config.get("toolkit") or {}).get("requires") if isinstance(config.get("toolkit"), dict) else None
    if isinstance(requires, str) and ok("toolkit.requires"):
        try:
            compat.parse_requires(requires)
        except compat.CompatError as exc:
            err("toolkit.requires", str(exc))

    gov = config.get("governance")
    if isinstance(gov, dict) and ok("governance"):
        if gov.get("wiki_write_mode") == "owner":
            owners = gov.get("wiki_owners")
            if not owners:
                err("governance.wiki_owners", "is required (at least one member id) when wiki_write_mode is 'owner'")
            else:
                team_ids = {t.get("id") for t in config.get("team") or [] if isinstance(t, dict)}
                for o in owners:
                    if team_ids and o not in team_ids:
                        findings.warnings.append(("governance.wiki_owners", f"{o!r} is not listed in team"))

    for key, label in (("repos", "name"), ("team", "id"), ("extra_folders", "path")):
        items = config.get(key)
        if isinstance(items, list) and ok(key):
            for d in _dups(items, label):
                err(key, f"duplicate {label}: {d!r}")
    cmds = (config.get("extraction") or {}).get("commands") if isinstance(config.get("extraction"), dict) else None
    if isinstance(cmds, list) and ok("extraction"):
        for d in _dups(cmds, "name"):
            err("extraction.commands", f"duplicate name: {d!r}")

    policy = config.get("policy")
    if isinstance(policy, dict) and isinstance(policy.get("block_patterns"), list) and ok("policy"):
        for i, pat in enumerate(policy["block_patterns"]):
            if isinstance(pat, dict) and isinstance(pat.get("regex"), str):
                try:
                    re.compile(pat["regex"])
                except re.error as exc:
                    err(f"policy.block_patterns.{i}.regex", f"is not a valid regular expression: {exc}")

    extra = config.get("extra_folders")
    if isinstance(extra, list) and ok("extra_folders"):
        for i, item in enumerate(extra):
            path = item.get("path") if isinstance(item, dict) else None
            if not isinstance(path, str):
                continue
            where = f"extra_folders.{i}.path"
            if "/" in path or "\\" in path or path in (".", "..") or path != path.strip():
                err(where, "must be a single top-level folder name")
            elif path in CONTRACT_FOLDERS:
                err(where, f"{path!r} is a contract folder and cannot be redeclared")
            elif path.startswith("_"):
                err(where, "names starting with '_' are machine areas reserved for the engine")
    return findings


def validate_state(state: Any) -> Findings:
    return validate(state, load_schema("state"))


def effective(config: dict[str, Any]) -> dict[str, Any]:
    """The configuration with documented defaults filled in. Never mutates ``config``."""
    merged = copy.deepcopy(config)

    def fill(target: dict[str, Any], defaults: dict[str, Any]) -> None:
        for key, default in defaults.items():
            if key not in target or target[key] is None:
                target[key] = copy.deepcopy(default)
            elif isinstance(default, dict) and isinstance(target[key], dict) and default:
                fill(target[key], default)

    fill(merged, DEFAULTS)
    return merged


def get_path(data: Any, dotted: str) -> Any:
    """Read ``a.b.0.c`` from nested mappings and lists. Raises KeyError when absent."""
    cur = data
    for part in dotted.split("."):
        if isinstance(cur, dict) and part in cur:
            cur = cur[part]
        elif isinstance(cur, list) and part.lstrip("-").isdigit() and -len(cur) <= int(part) < len(cur):
            cur = cur[int(part)]
        else:
            raise KeyError(dotted)
    return cur


def effective_forbidden_paths(config: dict[str, Any]) -> list[str]:
    extra = (config.get("policy") or {}).get("forbidden_paths") or []
    return list(FORBIDDEN_PATH_DEFAULTS) + [p for p in extra if isinstance(p, str) and p not in FORBIDDEN_PATH_DEFAULTS]


# ---- gitignore-syntax matching (checks only: this is not a sync filter)
class _Pattern:
    def __init__(self, raw: str):
        text = raw.strip()
        self.dir_only = text.endswith("/")
        text = text.rstrip("/")
        self.anchored = "/" in text
        text = text.lstrip("/")
        self.regex = re.compile(_glob_to_regex(text), re.IGNORECASE)


def _glob_to_regex(glob: str) -> str:
    out, i = [], 0
    while i < len(glob):
        c = glob[i]
        if glob.startswith("**/", i):
            out.append("(?:.*/)?")
            i += 3
            continue
        if glob.startswith("**", i):
            out.append(".*")
            i += 2
            continue
        if c == "*":
            out.append("[^/]*")
        elif c == "?":
            out.append("[^/]")
        else:
            out.append(re.escape(c))
        i += 1
    return "".join(out)


def compile_forbidden(patterns: list[str]) -> list[_Pattern]:
    return [_Pattern(p) for p in patterns if p.strip() and not p.strip().startswith("#")]


def is_forbidden(rel_path: str, patterns: list[_Pattern], *, is_dir: bool = False) -> bool:
    """True if ``rel_path`` (root-relative, POSIX) or any parent folder matches a forbidden pattern."""
    parts = PurePosixPath(rel_path.replace("\\", "/")).parts
    for i in range(len(parts)):
        sub = "/".join(parts[: i + 1])
        node_is_dir = i < len(parts) - 1 or is_dir
        for pat in patterns:
            if pat.dir_only and not node_is_dir:
                continue
            if pat.regex.fullmatch(sub if pat.anchored else parts[i]):
                return True
    return False
