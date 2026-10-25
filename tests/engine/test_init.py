"""``init`` (spec 005): R1-R9 and AC1-AC7 at engine level. Skill-level checks are in test_create_metarepo_skill.py."""
import io
import json
from pathlib import Path

import pytest

from metarepo_engine import dispatch
from metarepo_engine.events import read_events
from metarepo_engine.commands import init as init_cmd

BASIC = {"name": "Example Project", "description": "A short description.", "language": "en"}


def write_answers(tmp_path, answers, name="answers.json"):
    p = tmp_path / name
    p.write_text(json.dumps(answers), encoding="utf-8")
    return str(p)


def files_of(root: Path) -> dict[str, bytes | None]:
    out = {}
    for p in sorted(root.rglob("*")):
        out[p.relative_to(root).as_posix()] = None if p.is_dir() else p.read_bytes()
    return out


@pytest.fixture
def target(tmp_path):
    t = tmp_path / "Shared drives" / "proj"  # looks like a sync root, so no sync warning noise
    t.mkdir(parents=True)
    return t


def test_init_scaffolds_valid_tree_ac1(cli, target, tmp_path):
    code, env, _ = cli("init", str(target), "--answers", write_answers(tmp_path, BASIC), "--json")
    assert code == 0 and env["ok"], env
    assert "project.yaml" in env["data"]["created"] and env["data"]["kept"] == []
    code, env, _ = cli("config", "validate", "--root", str(target), "--json")
    assert code == 0 and env["data"]["valid"] is True and env["warnings"] == []
    for folder in ("00_inbox", "10_sources", "20_extracts", "30_wiki", "outputs/_templates", "shared/tools",
                   "personal", "_system/locks"):
        assert (target / folder).is_dir(), folder
    assert not (target / "code").exists()  # AC7: no repositories, no code/
    code, env, _ = cli("status", "--root", str(target), "--json")
    assert env["data"]["compatibility"]["status"] == "ok" and env["data"]["gates"] == {
        "metarepo_configured": True, "wiki_configured": False}


def test_project_yaml_values_r3_r5(cli, target, tmp_path):
    import yaml
    cli("init", str(target), "--answers", write_answers(tmp_path, BASIC), "--json")
    cfg = yaml.safe_load((target / "project.yaml").read_text(encoding="utf-8"))
    assert cfg["slug"] == "example-project" and cfg["objective"] == BASIC["description"]
    assert cfg["description"] == BASIC["description"]
    assert cfg["scope"] == {"in": [], "out": []} and cfg["team"] == []
    assert cfg["governance"]["wiki_write_mode"] == "lease" and cfg["intake"]["confirm"] == "always"
    assert cfg["toolkit"]["requires"] == ">=0.1,<0.2" and len(cfg["id"]) == 36
    assert cfg["sync"]["provider"] == "google-drive"  # detected from the path
    state = json.loads((target / "_system" / "state.json").read_text(encoding="utf-8"))
    assert state["metarepo"]["configured"] is True and state["wiki"]["configured"] is False


def test_root_files_and_notes_r5(cli, target, tmp_path):
    answers = {**BASIC, "notes": "Always answer briefly.",
               "repos": [{"url": "https://example.com/org/My-App.git", "default_branch": "main"},
                         {"url": "git@example.com:org/second_repo.git"}]}
    cli("init", str(target), "--answers", write_answers(tmp_path, answers), "--json")
    readme = (target / "README.md").read_text(encoding="utf-8")
    assert readme.startswith("# Example Project") and "A short description." in readme
    agents = (target / "AGENTS.md").read_text(encoding="utf-8")
    assert "<!-- project-notes:start -->\nAlways answer briefly.\n<!-- project-notes:end -->" in agents
    for section in ("Access tier", "Code repositories"):
        assert section in agents
    code_readme = (target / "code" / "README.md").read_text(encoding="utf-8")
    assert "my-app" in code_readme and "second-repo" in code_readme and "outside" in code_readme
    events = read_events(target)
    assert [e["command"] for e in events] == ["metarepo.created"] and "project.yaml" in events[0]["targets"]


def test_idempotent_second_run_ac2_r6(cli, target, tmp_path):
    a = write_answers(tmp_path, {**BASIC, "notes": "x", "repos": [{"url": "https://example.com/o/r.git"}]})
    cli("init", str(target), "--answers", a, "--json")
    before = files_of(target)
    code, env, _ = cli("init", str(target), "--answers", a, "--json")
    assert code == 0 and env["data"]["created"] == [] and env["data"]["kept"]
    assert files_of(target) == before  # includes events.log: no second event
    assert any(w["code"] == "already_initialized" for w in env["warnings"])


def test_partial_scaffold_creates_only_missing_r6(cli, target, tmp_path):
    a = write_answers(tmp_path, BASIC)
    cli("init", str(target), "--answers", a, "--json")
    (target / "shared" / "tools" / ".keep").unlink()
    (target / "README.md").write_text("# Edited by a human\n", encoding="utf-8")
    code, env, _ = cli("init", str(target), "--answers", a, "--json")
    assert env["data"]["created"] == ["shared/tools/.keep"]
    assert (target / "README.md").read_text(encoding="utf-8") == "# Edited by a human\n"


def test_refuses_inside_existing_metarepo_ac3(cli, target, tmp_path):
    a = write_answers(tmp_path, BASIC)
    cli("init", str(target), "--answers", a, "--json")
    inner = target / "30_wiki" / "inner"
    inner.mkdir()
    code, env, _ = cli("init", str(inner), "--answers", a, "--json")
    assert code == 3 and env["errors"][0]["code"] == "already_in_metarepo"
    assert "/join-metarepo" in env["errors"][0]["message"]
    assert list(inner.iterdir()) == []


def test_target_preconditions_r1(cli, tmp_path):
    a = write_answers(tmp_path, BASIC)
    code, env, _ = cli("init", str(tmp_path / "missing"), "--answers", a, "--json")
    assert code == 3 and env["errors"][0]["code"] == "target_missing"
    full = tmp_path / "full"
    full.mkdir()
    (full / "notes.txt").write_text("x", encoding="utf-8")
    code, env, _ = cli("init", str(full), "--answers", a, "--json")
    assert code == 3 and env["errors"][0]["code"] == "target_not_empty"
    assert (full / "project.yaml").exists() is False
    meta = tmp_path / "meta"
    meta.mkdir()
    for name in (".DS_Store", "desktop.ini", ".tmp.driveupload"):
        (meta / name).write_text("", encoding="utf-8") if name != ".tmp.driveupload" else (meta / name).mkdir()
    code, env, _ = cli("init", str(meta), "--answers", a, "--json")
    assert code == 0, env


def test_sync_detection_and_git_warning_r2(cli, tmp_path):
    plain = tmp_path / "plain"
    plain.mkdir()
    code, env, _ = cli("init", str(plain), "--answers", write_answers(tmp_path, BASIC), "--json")
    assert env["data"]["sync"] == {"provider": "none", "detected": None}
    assert any(w["code"] == "sync_undetected" for w in env["warnings"])
    chosen = tmp_path / "chosen"
    chosen.mkdir()
    a = write_answers(tmp_path, {**BASIC, "sync_provider": "dropbox"}, "b.json")
    code, env, _ = cli("init", str(chosen), "--answers", a, "--json")
    assert env["data"]["sync"]["provider"] == "dropbox" and not any(w["code"] == "sync_undetected" for w in env["warnings"])
    assert init_cmd.detect_sync_provider(Path("/Users/x/Library/CloudStorage/OneDrive-Corp/p")) == "onedrive"
    assert init_cmd.detect_sync_provider(Path("/Users/x/Dropbox/p")) == "dropbox"
    assert init_cmd.detect_sync_provider(Path("/Users/x/Google Drive/My Drive/p")) == "google-drive"
    git = tmp_path / "gitrepo" / "sub"
    git.mkdir(parents=True)
    (tmp_path / "gitrepo" / ".git").mkdir()
    code, env, _ = cli("init", str(git), "--answers", a, "--json")
    assert code == 0 and any(w["code"] == "inside_git_worktree" for w in env["warnings"])


def test_dry_run_changes_nothing_and_is_stable_r4(cli, target, tmp_path):
    a = write_answers(tmp_path, {**BASIC, "repos": [{"url": "https://example.com/o/r.git"}]})
    c1, e1, _ = cli("init", str(target), "--answers", a, "--dry-run", "--json")
    assert c1 == 0 and list(target.iterdir()) == []
    c2, e2, _ = cli("init", str(target), "--answers", a, "--dry-run", "--json")
    assert e1["data"]["created"] == e2["data"]["created"] and e1["data"]["dry_run"] is True
    code, real, _ = cli("init", str(target), "--answers", a, "--json")
    assert sorted(real["data"]["created"]) == sorted(e1["data"]["created"])
    assert e1["data"]["project"]["repos"] == ["r"] and "code/README.md" in e1["data"]["created"]


def test_answers_validation(cli, target, tmp_path):
    for bad in ({"name": "x"}, {**BASIC, "bogus": 1}, {**BASIC, "sync_provider": "box"},
                {**BASIC, "repos": [{"name": "a"}]}, {**BASIC, "notes": "<!-- project-notes:end -->"},
                {**BASIC, "repos": [{"url": "u1", "name": "dup"}, {"url": "u2", "name": "dup"}]}):
        code, env, _ = cli("init", str(target), "--answers", write_answers(tmp_path, bad), "--json")
        assert code in (2, 4) and env["errors"][0]["code"] == "answers_invalid", bad
        assert list(target.iterdir()) == []
    p = tmp_path / "broken.json"
    p.write_text("{nope", encoding="utf-8")
    code, env, _ = cli("init", str(target), "--answers", str(p), "--json")
    assert code == 2 and env["errors"][0]["code"] == "answers_invalid"


def test_headless_without_answers_creates_nothing_r9(cli, target):
    code, env, _ = cli("init", str(target), "--json")
    assert code == 2 and env["errors"][0]["code"] == "answers_required" and list(target.iterdir()) == []


def test_repo_name_proposal_r3d():
    f = init_cmd.repo_name_from_url
    assert f("https://example.com/org/My-App.git") == "my-app"
    assert f("git@example.com:org/second_repo.git") == "second-repo"
    assert f("https://example.com/org/plain/") == "plain"


def test_interactive_equivalent_to_answers_ac4(cli, tmp_path, monkeypatch):
    answers = {"name": "Example Project", "description": "A short description.", "notes": "Be brief.",
               "repos": [{"url": "https://example.com/o/app.git", "name": "app", "default_branch": "main"}],
               "language": "en"}
    a_dir, b_dir = tmp_path / "a" / "Shared drives" / "p", tmp_path / "b" / "Shared drives" / "p"
    a_dir.mkdir(parents=True)
    b_dir.mkdir(parents=True)
    cli("init", str(a_dir), "--answers", write_answers(tmp_path, answers), "--json")
    script = "\n".join([
        "Example Project", "A short description.", "Be brief.",
        "y", "https://example.com/o/app.git", "app", "main", "",  # one repo, then finish
        "y", "", "", "", "en", "lease", "always",                  # advanced: accept the defaults
    ]) + "\n"
    monkeypatch.setattr("sys.stdin", io.StringIO(script))
    code, env, _ = cli("init", str(b_dir), "--interactive", "--json")
    assert code == 0, env
    import yaml
    ca = yaml.safe_load((a_dir / "project.yaml").read_text(encoding="utf-8"))
    cb = yaml.safe_load((b_dir / "project.yaml").read_text(encoding="utf-8"))
    for c in (ca, cb):
        c.pop("id")
    assert ca == cb
    fa, fb = files_of(a_dir), files_of(b_dir)
    assert fa.keys() == fb.keys()
    for k in ("AGENTS.md", "README.md", "code/README.md"):
        assert fa[k] == fb[k], k


def test_interactive_eof_creates_nothing(cli, target, monkeypatch):
    monkeypatch.setattr("sys.stdin", io.StringIO(""))
    code, env, _ = cli("init", str(target), "--interactive", "--json")
    assert code == 2 and env["errors"][0]["code"] == "no_input" and list(target.iterdir()) == []


def test_no_toolkit_artifacts_ac6(cli, target, tmp_path, repo):
    import ci_check
    cli("init", str(target), "--answers", write_answers(tmp_path, {**BASIC, "notes": "n",
                                                                    "repos": [{"url": "https://e.com/o/r.git"}]}))
    assert ci_check.find_artifacts(target) == []


def test_neutral_templates_ac5(repo):
    """No domain, company or natural-language assumption in shipped init text (checked by denylist CI too)."""
    import re
    text = (Path(init_cmd.__file__)).read_text(encoding="utf-8") + (
        repo / "tooling" / "engine" / "metarepo_engine" / "schemas" / "answers.schema.json").read_text(encoding="utf-8")
    for term in ("hargol", "legal", "tax", "invoice", "client", "patient"):
        assert re.search(rf"\b{term}\b", text, re.I) is None, term


def test_in_process_help_lists_init():
    assert dispatch.main(["init", "--help"]) == 0
