#!/usr/bin/env python3
"""Copy the canonical toolkit into the Claude plugin (001/R11). Dev-only, stdlib only.

    python3 tools/build.py          # write plugin/skills, plugin/engine, plugin/templates, version stamp
    python3 tools/build.py --check  # exit 1 if plugin/ differs from what a build would produce

``tooling/`` is the only place to edit engine, skills and templates. The plugin copies are build output.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
COPY_DIRS = ("skills", "engine", "templates")
IGNORED_NAMES = {"__pycache__", ".DS_Store", ".pytest_cache"}
IGNORED_SUFFIXES = {".pyc", ".pyo"}
MARKER_START = "<!-- engine-path:start -->"
MARKER_END = "<!-- engine-path:end -->"
PLUGIN_ENGINE_PATH = "../../engine/cli.py"  # from a skill folder (plugin/skills/<name>/) to plugin/engine/cli.py


def is_ignored(rel: Path) -> bool:
    return bool(set(rel.parts) & IGNORED_NAMES) or rel.suffix in IGNORED_SUFFIXES


def iter_files(base: Path):
    if not base.is_dir():
        return
    for p in sorted(base.rglob("*")):
        if p.is_file() and not is_ignored(p.relative_to(base)):
            yield p


def set_engine_path(text: str, path: str) -> str:
    start, end = text.find(MARKER_START), text.find(MARKER_END)
    if start < 0 or end < start:
        raise ValueError("references/engine.md has no engine-path marker block")
    nl = "\r\n" if "\r\n" in text else "\n"
    return f"{text[: start + len(MARKER_START)]}{nl}path: {path}{nl}{text[end:]}"


def read_version(root: Path = ROOT) -> str:
    return (root / "tooling" / "VERSION").read_text(encoding="utf-8").strip()


def expected_plugin_tree(root: Path = ROOT) -> dict[str, bytes]:
    """Every file a build produces under plugin/ (excluding plugin.json, which is stamped in place)."""
    tooling = root / "tooling"
    tree: dict[str, bytes] = {}
    for name in COPY_DIRS:
        for f in iter_files(tooling / name):
            rel = f.relative_to(tooling).as_posix()
            data = f.read_bytes()
            parts = f.relative_to(tooling).parts
            if name == "skills" and len(parts) == 4 and parts[2:] == ("references", "engine.md"):
                data = set_engine_path(data.decode("utf-8"), PLUGIN_ENGINE_PATH).encode("utf-8")
            tree[rel] = data
    tree["VERSION"] = (read_version(root) + "\n").encode("utf-8")
    return tree


def stamped_plugin_json(root: Path = ROOT) -> str:
    path = root / "plugin" / ".claude-plugin" / "plugin.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    data["version"] = read_version(root)
    return json.dumps(data, indent=2, ensure_ascii=False) + "\n"


def actual_plugin_tree(root: Path = ROOT) -> dict[str, bytes]:
    plugin = root / "plugin"
    tree: dict[str, bytes] = {}
    for name in COPY_DIRS:
        for f in iter_files(plugin / name):
            tree[f.relative_to(plugin).as_posix()] = f.read_bytes()
    version_file = plugin / "VERSION"
    if version_file.is_file():
        tree["VERSION"] = version_file.read_bytes()
    return tree


def diff_plugin(root: Path = ROOT) -> list[str]:
    """Human-readable differences between plugin/ and a fresh build."""
    expected, actual = expected_plugin_tree(root), actual_plugin_tree(root)
    problems = [f"missing in plugin/: {p}" for p in sorted(expected.keys() - actual.keys())]
    problems += [f"stale in plugin/: {p}" for p in sorted(actual.keys() - expected.keys())]
    problems += [f"differs: plugin/{p}" for p in sorted(expected.keys() & actual.keys()) if expected[p] != actual[p]]
    manifest = root / "plugin" / ".claude-plugin" / "plugin.json"
    if not manifest.is_file():
        problems.append("missing plugin/.claude-plugin/plugin.json")
    elif manifest.read_text(encoding="utf-8") != stamped_plugin_json(root):
        problems.append("plugin.json version is not stamped from tooling/VERSION")
    return problems


def build(root: Path = ROOT) -> list[str]:
    """Mirror tooling/ into plugin/. Returns the list of changes made (empty when already current)."""
    plugin = root / "plugin"
    expected = expected_plugin_tree(root)
    actual = actual_plugin_tree(root)
    changes: list[str] = []
    for rel in sorted(actual.keys() - expected.keys()):
        (plugin / rel).unlink()
        changes.append(f"removed {rel}")
    for rel, data in sorted(expected.items()):
        if actual.get(rel) != data:
            target = plugin / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
            changes.append(f"wrote {rel}")
    for name in COPY_DIRS:  # prune empty directories left by removed files
        base = plugin / name
        if base.is_dir():
            for d in sorted((p for p in base.rglob("*") if p.is_dir()), reverse=True):
                if not any(d.iterdir()):
                    d.rmdir()
    manifest = plugin / ".claude-plugin" / "plugin.json"
    stamped = stamped_plugin_json(root)
    if manifest.read_text(encoding="utf-8") != stamped:
        manifest.write_text(stamped, encoding="utf-8", newline="\n")
        changes.append("stamped plugin.json version")
    return changes


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--check", action="store_true", help="verify only; exit 1 when plugin/ is out of date")
    args = parser.parse_args(argv)
    if args.check:
        problems = diff_plugin()
        for p in problems:
            print(p, file=sys.stderr)
        return 1 if problems else 0
    for line in build():
        print(line)
    return 0


if __name__ == "__main__":
    sys.exit(main())
