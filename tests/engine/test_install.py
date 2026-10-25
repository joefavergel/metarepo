"""001/R6 install-skills (001/T3.2)."""
from pathlib import Path

from helpers import tree_snapshot
from metarepo_engine import install, resolve


def test_installs_every_skill_and_records_the_engine_path(cli, tmp_path, repo):
    target = tmp_path / "harness-skills"
    code, env, _ = cli("install-skills", "--target", str(target), "--json")
    assert code == 0
    names = {s["name"] for s in env["data"]["skills"]}
    assert {"create-metarepo", "intake", "doctor", "extract", "unbundle"} <= names
    for name in names:
        md = (target / name / "references" / "engine.md").read_text(encoding="utf-8")
        assert resolve.recorded_engine_path(target / name) == str(repo / "tooling" / "engine" / "cli.py")
        assert "path: unresolved" not in md
        assert (target / name / "SKILL.md").is_file()


def test_rerun_is_a_no_op(cli, tmp_path):
    target = tmp_path / "skills"
    cli("install-skills", "--target", str(target), "--json")
    before = tree_snapshot(target)
    code, env, _ = cli("install-skills", "--target", str(target), "--json")
    assert code == 0 and {s["action"] for s in env["data"]["skills"]} == {"unchanged"}
    assert tree_snapshot(target) == before


def test_modified_skill_needs_force(cli, tmp_path):
    target = tmp_path / "skills"
    cli("install-skills", "--target", str(target), "--json")
    skill_md = target / "intake" / "SKILL.md"
    skill_md.write_text("local edit\n", encoding="utf-8")
    code, env, _ = cli("install-skills", "--target", str(target), "--json")
    assert code == 3 and env["errors"][0]["code"] == "skill_exists"
    assert skill_md.read_text(encoding="utf-8") == "local edit\n"
    code, env, _ = cli("install-skills", "--target", str(target), "--force", "--json")
    assert code == 0 and "local edit" not in skill_md.read_text(encoding="utf-8")


def test_dry_run_writes_nothing(cli, tmp_path):
    target = tmp_path / "skills"
    code, env, _ = cli("install-skills", "--target", str(target), "--dry-run", "--json")
    assert code == 0 and env["data"]["dry_run"] is True
    assert {s["action"] for s in env["data"]["skills"]} == {"install"}
    assert not target.exists()


def test_never_writes_into_a_project(cli, make_project):
    root = make_project()
    before = tree_snapshot(root)
    code, env, _ = cli("install-skills", "--target", str(root / "shared" / "skills"), "--json")
    assert code == 3 and env["errors"][0]["code"] == "target_inside_project"
    assert tree_snapshot(root) == before
    assert not (root / "shared" / "skills").exists()


def test_installing_does_not_touch_a_project_elsewhere(cli, make_project, tmp_path):
    root = make_project()
    before = tree_snapshot(root)
    cli("install-skills", "--target", str(tmp_path / "other" / "skills"), "--json")
    assert tree_snapshot(root) == before


def test_set_engine_path_requires_marker():
    import pytest

    with pytest.raises(install.InstallError):
        install.set_engine_path("no markers here", "/x")
    out = install.set_engine_path(f"a\n{resolve.MARKER_START}\npath: unresolved\n{resolve.MARKER_END}\nb\n", "/x/cli.py")
    assert "path: /x/cli.py" in out and "unresolved" not in out and out.endswith("b\n")


def test_ignores_bytecode_in_source(tmp_path):
    src = tmp_path / "src" / "demo"
    (src / "references").mkdir(parents=True)
    (src / "__pycache__").mkdir()
    (src / "__pycache__" / "x.pyc").write_bytes(b"x")
    (src / "SKILL.md").write_text("---\nname: demo\ndescription: d\n---\n", encoding="utf-8")
    plans = install.plan_install(tmp_path / "t", source=tmp_path / "src", engine_cli=Path("/e/cli.py"))
    assert list(plans[0].files) == ["SKILL.md"]
