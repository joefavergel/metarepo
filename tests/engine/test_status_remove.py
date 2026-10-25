"""status (003/R16, golden), remove (002/R6, P6) and dependency behaviour."""
import hashlib
from pathlib import Path

import pytest

from golden import assert_golden
from helpers import add_source, tree_snapshot
from metarepo_engine import dispatch
from metarepo_engine.events import read_events


# ---- status
def test_status_inside_a_project_golden(cli, make_project, monkeypatch):
    root = make_project()
    (root / "_system" / "locks" / "wiki.lease.json").write_text("{}", encoding="utf-8")
    code, env, _ = cli("status", "--json", "--root", str(root))
    assert code == 0 and env["ok"] is True
    data = env["data"]
    assert data["in_metarepo"] and data["root"] == root.resolve().as_posix()
    assert data["project"] == {"id": "7f3c1b2a-5d4e-4f6a-9b8c-0123456789ab", "name": "Example Project",
                               "slug": "example-project"}
    assert data["compatibility"]["status"] == "ok" and data["gates"] == {"metarepo_configured": True,
                                                                           "wiki_configured": False}
    assert data["leases"]["count"] == 1 and data["dependencies"]["pyyaml"]["available"] is True
    volatile = {
        root.resolve().as_posix(): "<root>",
        data["toolkit"]["engine"]: "<engine>",
        data["toolkit"]["python"]: "<python>",
        data["toolkit"]["platform"]: "<platform>",
        data["dependencies"]["pyyaml"]["version"]: "<pyyaml>",
        env["version"]: "<version>",
    }
    scrubbed = dict(env)
    scrubbed["data"] = {**data, "dependencies": {
        **data["dependencies"],
        "extractors": {k: {**v, "available": "<detected>"} for k, v in data["dependencies"]["extractors"].items()}}}
    assert_golden("status_in_project.json", scrubbed, volatile)


def test_status_outside_a_project_is_ok_with_a_warning(cli, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    code, env, _ = cli("status", "--json")
    assert code == 0 and env["data"]["in_metarepo"] is False and env["data"]["root"] is None
    assert env["data"]["project"] is None and [w["code"] for w in env["warnings"]] == ["not_a_metarepo"]


def test_status_with_explicit_bad_root_is_a_precondition_error(cli, tmp_path):
    code, env, _ = cli("status", "--json", "--root", str(tmp_path))
    assert code == 3 and env["errors"][0]["code"] == "not_a_metarepo"


def test_status_reports_incompatibility_without_failing(cli, make_project, monkeypatch):
    root = make_project(requires=">=0.3,<0.4")
    code, env, _ = cli("status", "--json", "--root", str(root))
    assert code == 0 and env["data"]["compatibility"]["status"] == "update-toolkit"  # installed 0.1.0 < >=0.3
    assert env["warnings"][0]["code"] == "toolkit_incompatible"
    monkeypatch.setattr(dispatch, "__version__", "0.4.0")
    env = cli("status", "--json", "--root", str(root))[1]
    assert env["data"]["compatibility"]["status"] == "migrate-project"


def test_status_degrades_without_pyyaml_r16(cli, make_project, monkeypatch):
    root = make_project()
    monkeypatch.setattr(dispatch, "has_yaml", lambda: False)
    import builtins

    real_import = builtins.__import__

    def no_yaml(name, *a, **k):
        if name == "yaml":
            raise ImportError("blocked")
        return real_import(name, *a, **k)

    monkeypatch.setattr(builtins, "__import__", no_yaml)
    code, env, _ = cli("status", "--json", "--root", str(root))
    assert code == 0 and env["data"]["dependencies"]["pyyaml"]["available"] is False
    assert env["data"]["project"] is None and any(w["code"] == "missing_dependency" for w in env["warnings"])
    assert "pip install pyyaml" in env["data"]["dependencies"]["pyyaml"]["install"]


def test_other_commands_exit_5_without_pyyaml(cli, make_project, monkeypatch):
    root = make_project()
    monkeypatch.setattr(dispatch, "has_yaml", lambda: False)
    for argv in (["config", "validate"], ["registry"], ["search", "x"], ["render-templates"], ["migrate"]):
        code, env, _ = cli(*argv, "--json", "--root", str(root))
        assert code == 5 and env["errors"][0]["code"] == "missing_dependency", argv
    code, env, _ = cli("hash", __file__, "--json")  # needs no YAML
    assert code == 0


def test_status_lists_optional_extractors_with_install_hints(cli, make_project):
    env = cli("status", "--json", "--root", str(make_project()))[1]
    extractors = env["data"]["dependencies"]["extractors"]
    assert set(extractors) == {"markitdown", "docling", "pymupdf", "pymupdf4llm"}
    assert all("pip install" in v["install"] and isinstance(v["available"], bool) for v in extractors.values())


# ---- remove
@pytest.fixture
def project(make_project):
    root = make_project()
    (root / "00_inbox" / "dup.pdf").write_bytes(b"duplicate bytes")
    (root / "shared" / "note.txt").write_text("keep?", encoding="utf-8")
    return root


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_remove_requires_the_matching_hash_and_logs_path_size_and_hash(cli, project):
    target = project / "00_inbox" / "dup.pdf"
    digest = sha(target)
    code, env, _ = cli("remove", "00_inbox/dup.pdf", "--confirm", digest, "--json", "--root", str(project))
    assert code == 0 and not target.exists()
    assert env["data"] == {"path": "00_inbox/dup.pdf", "size": 15, "sha256": digest, "dry_run": False}
    (event,) = read_events(project)
    assert event["command"] == "remove" and event["targets"] == ["00_inbox/dup.pdf"]
    assert event["size"] == 15 and event["sha256"] == digest and event["result"] == "ok"


def test_remove_wrong_hash_deletes_nothing_and_logs_nothing(cli, project):
    target = project / "00_inbox" / "dup.pdf"
    code, env, _ = cli("remove", "00_inbox/dup.pdf", "--confirm", "0" * 64, "--json", "--root", str(project))
    assert code == 4 and env["errors"][0]["code"] == "confirmation_mismatch" and target.exists()
    assert read_events(project) == []


@pytest.mark.parametrize("confirm", ["abc", "z" * 64, "0" * 63, ""])
def test_remove_rejects_malformed_confirmation(cli, project, confirm):
    code, env, _ = cli("remove", "00_inbox/dup.pdf", "--confirm", confirm, "--json", "--root", str(project))
    assert code == 2 and env["errors"][0]["code"] == "bad_confirmation"


def test_remove_dry_run(cli, project):
    target = project / "00_inbox" / "dup.pdf"
    code, env, _ = cli("remove", "00_inbox/dup.pdf", "--confirm", sha(target), "--dry-run", "--json",
                       "--root", str(project))
    assert code == 0 and target.exists() and env["data"]["dry_run"] is True and read_events(project) == []


def test_remove_never_touches_sources_even_with_the_right_hash(cli, project):
    info = add_source(project, "papers/a.pdf", b"original")
    before = tree_snapshot(project)
    for path in (info["path"], "10_SOURCES/papers/a.pdf", "shared/../10_sources/papers/a.pdf"):
        code, env, _ = cli("remove", path, "--confirm", info["sha256"], "--json", "--root", str(project))
        assert code == 3 and env["errors"][0]["code"] == "sources_immutable", path
    assert tree_snapshot(project) == before


def test_remove_refuses_core_state_and_non_files(cli, project):
    for rel in ("project.yaml", "_system/state.json", "_system/events.log"):
        (project / rel).parent.mkdir(exist_ok=True)
        if not (project / rel).exists():
            (project / rel).write_text("x", encoding="utf-8")
        code, env, _ = cli("remove", rel, "--confirm", sha(project / rel), "--json", "--root", str(project))
        assert code == 3 and env["errors"][0]["code"] == "protected_file", rel
        assert (project / rel).exists()
    code, env, _ = cli("remove", "shared", "--confirm", "0" * 64, "--json", "--root", str(project))
    assert code == 3 and env["errors"][0]["code"] == "not_a_file"
    code, env, _ = cli("remove", "shared/missing.txt", "--confirm", "0" * 64, "--json", "--root", str(project))
    assert code == 3 and env["errors"][0]["code"] == "file_not_found"


def test_remove_refuses_paths_outside_the_project_and_symlinks(cli, project, tmp_path):
    outside = tmp_path / "outside.txt"
    outside.write_text("x", encoding="utf-8")
    for arg in (str(outside), "../outside.txt"):
        code, env, _ = cli("remove", arg, "--confirm", sha(outside), "--json", "--root", str(project))
        assert code == 3 and env["errors"][0]["code"] == "outside_root", arg
    assert outside.exists()
    link = project / "shared" / "link.txt"
    try:
        link.symlink_to(project / "shared" / "note.txt")
    except (OSError, NotImplementedError):
        pytest.skip("symlinks unavailable")
    code, env, _ = cli("remove", "shared/link.txt", "--confirm", sha(project / "shared" / "note.txt"), "--json",
                       "--root", str(project))
    assert code == 3 and env["errors"][0]["code"] in ("is_symlink", "sources_immutable")
    assert (project / "shared" / "note.txt").exists()


def test_remove_accepts_absolute_paths_inside_the_project_and_uppercase_hash(cli, project):
    target = project / "shared" / "note.txt"
    code, env, _ = cli("remove", str(target), "--confirm", sha(target).upper(), "--json", "--root", str(project))
    assert code == 0 and env["data"]["path"] == "shared/note.txt" and not target.exists()


def test_remove_is_refused_outside_the_toolkit_range(cli, make_project, monkeypatch):
    root = make_project(requires=">=0.3,<0.4")
    (root / "shared" / "x.txt").write_text("x", encoding="utf-8")
    monkeypatch.setattr(dispatch, "__version__", "0.4.0")
    code, env, _ = cli("remove", "shared/x.txt", "--confirm", sha(root / "shared" / "x.txt"), "--json",
                       "--root", str(root))
    assert code == 4 and env["errors"][0]["code"] == "toolkit_incompatible" and (root / "shared" / "x.txt").exists()
