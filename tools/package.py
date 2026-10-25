#!/usr/bin/env python3
"""Deterministic distribution archives (001/R11). Dev-only, stdlib only.

    python3 tools/package.py

Writes ``dist/metarepo-plugin-<version>.zip`` (the plugin folder) and
``dist/metarepo-toolkit-<version>.zip`` (the portable bundle). Entries are sorted, timestamps are fixed
and entries are stored uncompressed, so identical inputs give identical bytes on every platform.
"""
from __future__ import annotations

import sys
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import build  # noqa: E402

ROOT = build.ROOT
FIXED_TIME = (1980, 1, 1, 0, 0, 0)


def write_zip(target: Path, entries: dict[str, bytes]) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_name(target.name + ".part")
    with zipfile.ZipFile(tmp, "w", compression=zipfile.ZIP_STORED) as zf:
        for name in sorted(entries):
            info = zipfile.ZipInfo(name, date_time=FIXED_TIME)
            info.compress_type = zipfile.ZIP_STORED
            info.create_system = 3
            info.external_attr = 0o644 << 16
            zf.writestr(info, entries[name])
    tmp.replace(target)


def plugin_entries(root: Path) -> dict[str, bytes]:
    base = root / "plugin"
    return {f.relative_to(base).as_posix(): f.read_bytes() for f in build.iter_files(base)}


def toolkit_entries(root: Path, version: str) -> dict[str, bytes]:
    base = root / "tooling"
    prefix = f"metarepo-toolkit-{version}/"
    return {prefix + f.relative_to(base).as_posix(): f.read_bytes() for f in build.iter_files(base)}


def package(root: Path = ROOT, out_dir: Path | None = None) -> list[Path]:
    problems = build.diff_plugin(root)
    if problems:
        raise SystemExit("plugin/ is out of date; run tools/build.py first:\n  " + "\n  ".join(problems))
    version = build.read_version(root)
    out_dir = out_dir or (root / "dist")
    plugin_zip = out_dir / f"metarepo-plugin-{version}.zip"
    toolkit_zip = out_dir / f"metarepo-toolkit-{version}.zip"
    write_zip(plugin_zip, plugin_entries(root))
    write_zip(toolkit_zip, toolkit_entries(root, version))
    return [plugin_zip, toolkit_zip]


def main() -> int:
    for path in package():
        print(path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
