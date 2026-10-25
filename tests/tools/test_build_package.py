"""001/R11 build and package (001/T2.1, T2.2)."""
import hashlib
import json
import shutil
import zipfile
from pathlib import Path

import pytest

import build
import package

IGNORE = shutil.ignore_patterns("__pycache__", "*.pyc", ".pytest_cache", ".DS_Store")


@pytest.fixture
def repo_copy(tmp_path, repo):
    dest = tmp_path / "repo"
    for name in ("tooling", "plugin", ".claude-plugin", "tools"):
        shutil.copytree(repo / name, dest / name, ignore=IGNORE)
    return dest


def test_committed_plugin_copies_equal_tooling(repo):
    assert build.diff_plugin(repo) == []  # 001/AC1: a fresh clone is already in sync


def test_build_is_idempotent_and_equal(repo_copy):
    assert build.build(repo_copy) == []
    assert build.diff_plugin(repo_copy) == []


def test_build_propagates_changes_and_removes_stale_files(repo_copy):
    (repo_copy / "tooling" / "engine" / "metarepo_engine" / "new_module.py").write_text("X = 1\n", encoding="utf-8")
    (repo_copy / "plugin" / "engine" / "stale.py").write_text("old\n", encoding="utf-8")
    (repo_copy / "plugin" / "skills" / "doctor" / "extra.md").write_text("old\n", encoding="utf-8")
    problems = build.diff_plugin(repo_copy)
    assert any("missing in plugin/" in p for p in problems) and any("stale in plugin/" in p for p in problems)
    changes = build.build(repo_copy)
    assert any("new_module.py" in c for c in changes) and any("removed engine/stale.py" == c for c in changes)
    assert build.diff_plugin(repo_copy) == []
    assert not (repo_copy / "plugin" / "skills" / "doctor" / "extra.md").exists()


def test_build_prunes_empty_directories(repo_copy):
    shutil.rmtree(repo_copy / "tooling" / "skills" / "unbundle")
    build.build(repo_copy)
    assert not (repo_copy / "plugin" / "skills" / "unbundle").exists()


def test_version_is_stamped_and_copied(repo_copy):
    (repo_copy / "tooling" / "VERSION").write_text("0.2.0\n", encoding="utf-8")
    assert any("not stamped" in p for p in build.diff_plugin(repo_copy))
    build.build(repo_copy)
    manifest = json.loads((repo_copy / "plugin" / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8"))
    assert manifest["version"] == "0.2.0"
    assert (repo_copy / "plugin" / "VERSION").read_text(encoding="utf-8") == "0.2.0\n"
    assert build.diff_plugin(repo_copy) == []


def test_engine_path_rewritten_in_plugin_copies_only(repo):
    for skill in (repo / "plugin" / "skills").iterdir():
        text = (skill / "references" / "engine.md").read_text(encoding="utf-8")
        assert "path: ../../engine/cli.py" in text
        # relative to the skill folder, that path is the bundled engine
        assert (skill / "../../engine/cli.py").resolve().is_file()
    for skill in (repo / "tooling" / "skills").iterdir():
        assert "path: unresolved" in (skill / "references" / "engine.md").read_text(encoding="utf-8")


def test_build_ignores_bytecode(repo_copy):
    pyc = repo_copy / "tooling" / "engine" / "metarepo_engine" / "__pycache__"
    pyc.mkdir(exist_ok=True)
    (pyc / "x.cpython-310.pyc").write_bytes(b"x")
    assert build.build(repo_copy) == []


# ---- package
def _sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def test_two_packagings_are_byte_identical(repo_copy, tmp_path):
    first = package.package(repo_copy, tmp_path / "out1")
    second = package.package(repo_copy, tmp_path / "out2")
    assert [p.name for p in first] == ["metarepo-plugin-0.1.0.zip", "metarepo-toolkit-0.1.0.zip"]
    for a, b in zip(first, second):
        assert _sha(a) == _sha(b)


def test_zip_layout_is_deterministic_and_complete(repo_copy, tmp_path):
    plugin_zip, toolkit_zip = package.package(repo_copy, tmp_path / "out")
    with zipfile.ZipFile(plugin_zip) as z:
        names = z.namelist()
        assert names == sorted(names)
        assert ".claude-plugin/plugin.json" in names and "hooks/hooks.json" in names and "skills/intake/SKILL.md" in names
        assert {i.date_time for i in z.infolist()} == {package.FIXED_TIME}
    with zipfile.ZipFile(toolkit_zip) as z:
        names = z.namelist()
        assert names == sorted(names)
        assert all(n.startswith("metarepo-toolkit-0.1.0/") for n in names)
        for needed in ("INSTALL.md", "VERSION", "CHANGELOG.md", "engine/cli.py", "skills/intake/SKILL.md"):
            assert f"metarepo-toolkit-0.1.0/{needed}" in names
        assert not any(".pyc" in n or "__pycache__" in n for n in names)


def test_package_refuses_a_stale_plugin(repo_copy, tmp_path):
    (repo_copy / "tooling" / "engine" / "cli.py").write_text("# changed\n", encoding="utf-8")
    with pytest.raises(SystemExit) as exc:
        package.package(repo_copy, tmp_path / "out")
    assert "build.py" in str(exc.value)
