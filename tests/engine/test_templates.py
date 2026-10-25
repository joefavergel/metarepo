"""002/R17, R18, R2a, R2b: rendering and scaffolding (002/AC2, AC5)."""
import copy
import re
from pathlib import Path

import pytest

from helpers import tree_snapshot
from metarepo_engine import scaffold, templates
from metarepo_engine.events import read_events

def render(config, existing=None):
    return templates.render_project_files(config, lambda rel: (existing or {}).get(rel))


def test_ac2_rendering_twice_yields_identical_files():
    cfg = scaffold.fixture_config()
    first = render(cfg)
    again = render(cfg, existing=first)
    assert first == again
    assert set(first) == {"AGENTS.md", "README.md", "code/README.md"}


def test_ac2_project_notes_survive_regeneration():
    cfg = scaffold.fixture_config()
    first = render(cfg)
    notes = "Line one with **markdown**.\n\n- item 1\n- item 2\n\nTrailing line."
    edited = {k: v.replace(templates.DEFAULT_NOTES, notes) for k, v in first.items()}
    assert all(notes in v for v in edited.values())
    cfg2 = copy.deepcopy(cfg)
    cfg2["name"] = "Renamed Project"
    cfg2["objective"] = "A different objective."
    second = render(cfg2, existing=edited)
    for rel, text in second.items():
        assert notes in text
        assert "Renamed Project" in text or rel == "code/README.md"
        assert templates.extract_notes(text) == notes
    assert "A different objective." in second["AGENTS.md"]
    assert render(cfg2, existing=second) == second  # stable


def test_notes_block_edge_cases():
    cfg = scaffold.fixture_config()
    base = render(cfg)["AGENTS.md"]
    empty = base.replace(templates.DEFAULT_NOTES, "")
    assert templates.extract_notes(empty) == ""
    assert render(cfg, {"AGENTS.md": empty})["AGENTS.md"] == empty
    crlf = base.replace("\n", "\r\n")
    assert render(cfg, {"AGENTS.md": crlf})["AGENTS.md"] == base
    assert templates.extract_notes("no block") is None
    # no block in the existing file: default text, no crash
    assert templates.DEFAULT_NOTES in render(cfg, {"AGENTS.md": "hand written\n"})["AGENTS.md"]
    # text that looks like template syntax inside the notes is kept verbatim
    tricky = "uses $HOME and ${not_a_placeholder}"
    out = render(cfg, {"AGENTS.md": base.replace(templates.DEFAULT_NOTES, tricky)})["AGENTS.md"]
    assert tricky in out


R18_HEADINGS = [
    r"what this is",
    r"folder map and access tiers",
    r"escalation ladder",
    r"write rules",
    r"workflows",
    r"running the engine",
    r"code repositories",
    r"language rules",
    r"sync etiquette",
    r"project scope",
    r"project notes",
]


def test_ac5_agents_md_has_every_r18_item():
    text = render(scaffold.fixture_config())["AGENTS.md"]
    headings = [h.lower() for h in re.findall(r"^##\s+(.*)$", text, re.M)]
    for needle in R18_HEADINGS:
        assert any(needle in h for h in headings), needle
    # workflows: every ADR-003 command is listed, with where the SKILL.md lives
    for command in ("create-metarepo", "join-metarepo", "intake", "init-wiki", "feed-wiki", "consult-wiki",
                    "lint-wiki", "make-output", "doctor"):
        assert f"`/{command}`" in text
    assert "skills directory" in text
    # how to run the engine on each OS
    assert "python3" in text and "py -3" in text and "METAREPO_ENGINE" in text and "PATH" in text
    # tiers (R7) and the ladder
    for needle in ("free", "on demand", "explicit request only", "search before reading", "index.md"):
        assert needle in text
    assert "outputs/" in text and "30_wiki/" in text and "10_sources/" in text
    # write rules (P6) and the single-instruction-file rule (R2b)
    assert "remove <path> --confirm <sha256>" in text and "CLAUDE.md" in text
    # language rules (P14) with the content language injected
    assert "`en`" in text
    # scope summary from project.yaml, repos rules, sync etiquette
    assert "topic A; topic B" in text and "topic C" in text
    assert "repos path <name>" in text and "outside" in text
    assert "git repository" in text and "conflict" in text.lower()
    assert templates.NOTES_START in text and templates.NOTES_END in text
    assert "${" not in text


def test_agents_md_without_repos_or_scope():
    cfg = scaffold.fixture_config()
    del cfg["repos"]
    cfg["scope"] = {}
    files = render(cfg)
    assert set(files) == {"AGENTS.md", "README.md"}
    assert "no code repositories registered" in files["AGENTS.md"]
    assert "`code/`" not in files["AGENTS.md"]
    assert "(not specified)" in files["AGENTS.md"]


def test_extra_folders_are_regenerated_into_the_map_with_their_tier():  # 002/R8
    cfg = scaffold.fixture_config()
    cfg["extra_folders"] = [{"path": "25_datasets", "tier": "on-demand", "purpose": "Curated datasets"},
                            {"path": "archive", "tier": "explicit"}]
    text = render(cfg)["AGENTS.md"]
    assert re.search(r"\| `25_datasets/` \| Curated datasets \| on demand \|", text)
    assert re.search(r"\| `archive/` \| .* \| explicit request only \|", text)


def test_values_with_special_characters_do_not_break_rendering():
    cfg = scaffold.fixture_config()
    cfg["name"] = "Price $5 | table & <tag>"
    cfg["repos"][0]["url"] = "https://example.com/a|b.git"
    files = render(cfg)
    assert "Price $5 | table & <tag>" in files["README.md"]
    assert "a\\|b.git" in files["code/README.md"]


def test_readme_covers_how_to_drop_files_and_what_not_to_touch():  # 002/T6
    text = render(scaffold.fixture_config())["README.md"]
    for needle in ("00_inbox/", "/intake", "Do **not** edit", "10_sources/", "20_extracts/", "_system/",
                   "git repository", "/join-metarepo", ">=0.1,<0.2"):
        assert needle in text


def test_rendered_files_have_no_unreplaced_placeholders():
    for text in render(scaffold.fixture_config()).values():
        assert "$" not in text


# ---- scaffolding
def test_scaffold_creates_only_project_files(tmp_path):
    target = tmp_path / "p"
    created = scaffold.scaffold(target, scaffold.fixture_config(), now="2026-01-01T00:00:00Z")
    files = sorted(p.relative_to(target).as_posix() for p in target.rglob("*") if p.is_file())
    assert files == ["AGENTS.md", "README.md", "_system/state.json", "code/README.md", "outputs/.keep",
                     "outputs/_templates/.keep", "personal/.keep", "project.yaml", "shared/.keep"]
    for folder in ("00_inbox", "10_sources", "20_extracts", "30_wiki", "outputs", "shared", "personal", "_system",
                   "_system/locks", "code"):
        assert (target / folder).is_dir()
    assert "project.yaml" in created and "00_inbox" in created
    assert not (target / "CLAUDE.md").exists()
    import json

    state = json.loads((target / "_system" / "state.json").read_text(encoding="utf-8"))
    assert state["metarepo"]["created_at"] == "2026-01-01T00:00:00Z" and state["wiki"]["configured"] is False


def test_scaffold_is_idempotent_and_never_overwrites(tmp_path):
    target = tmp_path / "p"
    cfg = scaffold.fixture_config()
    scaffold.scaffold(target, cfg)
    (target / "AGENTS.md").write_text("edited by a human\n", encoding="utf-8")
    (target / "project.yaml").write_text("# hand edited\n" + (target / "project.yaml").read_text(encoding="utf-8"),
                                         encoding="utf-8")
    before = tree_snapshot(target)
    assert scaffold.scaffold(target, cfg) == []
    assert tree_snapshot(target) == before


def test_scaffold_without_repos_has_no_code_folder(tmp_path):
    cfg = scaffold.fixture_config()
    del cfg["repos"]
    scaffold.scaffold(tmp_path / "p", cfg)
    assert not (tmp_path / "p" / "code").exists()


def test_scaffold_rejects_invalid_config(tmp_path):
    cfg = scaffold.fixture_config()
    cfg["slug"] = "Bad Slug"
    with pytest.raises(ValueError, match="slug"):
        scaffold.scaffold(tmp_path / "p", cfg)
    assert not (tmp_path / "p").exists()


def test_scaffolded_project_validates_and_is_a_root(tmp_path, cli):
    scaffold.scaffold(tmp_path / "p", scaffold.fixture_config())
    code, env, _ = cli("config", "validate", "--json", "--root", str(tmp_path / "p"))
    assert code == 0 and env["data"]["findings"] == []


def test_no_toolkit_artifacts_in_a_scaffolded_project(tmp_path):  # 001/AC3
    import ci_check

    scaffold.scaffold(tmp_path / "p", scaffold.fixture_config())
    assert ci_check.find_artifacts(tmp_path / "p") == []


def test_ci_no_artifact_rule_runs_the_engine_scaffold(repo):
    import ci_check

    assert ci_check.check_no_artifacts(repo) == []


def test_ci_no_artifact_rule_is_never_skipped_silently_without_pyyaml(repo, monkeypatch):
    import subprocess

    import ci_check

    fake = subprocess.CompletedProcess([], 1, stdout="", stderr="...EngineError: PyYAML is required. Install it...")
    monkeypatch.setattr(ci_check.subprocess, "run", lambda *a, **k: fake)
    (problem,) = ci_check.check_no_artifacts(repo)
    assert "needs PyYAML" in problem and "requirements-dev.txt" in problem


def test_ci_no_artifact_rule_fires_on_leaked_artifacts(repo, tmp_path, monkeypatch):
    import ci_check

    real = ci_check.find_artifacts
    monkeypatch.setattr(ci_check, "find_artifacts", lambda root: real(root) + ["engine/"])
    assert any("toolkit artifact in project: engine/" in p for p in ci_check.check_no_artifacts(repo))


# ---- render-templates command
def _project(tmp_path):
    root = tmp_path / "p"
    scaffold.scaffold(root, scaffold.fixture_config())
    return root


def test_render_templates_noop_when_current(cli, tmp_path):
    root = _project(tmp_path)
    code, env, _ = cli("render-templates", "--json", "--root", str(root))
    assert code == 0 and env["data"]["changed"] == [] and read_events(root) == []


def test_render_templates_updates_after_config_change_and_keeps_notes(cli, tmp_path):
    root = _project(tmp_path)
    agents = root / "AGENTS.md"
    agents.write_text(agents.read_text(encoding="utf-8").replace(templates.DEFAULT_NOTES, "my notes"), encoding="utf-8")
    yml = root / "project.yaml"
    yml.write_text(yml.read_text(encoding="utf-8").replace("What the example project must achieve.", "New objective."),
                   encoding="utf-8")
    code, env, _ = cli("render-templates", "--dry-run", "--json", "--root", str(root))
    assert code == 0 and "AGENTS.md" in env["data"]["changed"]
    assert "New objective." not in agents.read_text(encoding="utf-8")  # dry run wrote nothing
    code, env, _ = cli("render-templates", "--json", "--root", str(root))
    text = agents.read_text(encoding="utf-8")
    assert "New objective." in text and "my notes" in text
    (event,) = read_events(root)
    assert event["command"] == "render-templates" and "AGENTS.md" in event["targets"]
    code, env, _ = cli("render-templates", "--json", "--root", str(root))
    assert env["data"]["changed"] == [] and len(read_events(root)) == 1


def test_render_templates_warns_about_claude_md_and_refuses_invalid_config(cli, tmp_path):
    root = _project(tmp_path)
    (root / "CLAUDE.md").write_text("x", encoding="utf-8")
    code, env, _ = cli("render-templates", "--json", "--root", str(root))
    assert code == 0 and [w["code"] for w in env["warnings"]] == ["forbidden_instruction_file"]
    yml = root / "project.yaml"
    yml.write_text(yml.read_text(encoding="utf-8").replace("slug: example-project", "slug: Bad"), encoding="utf-8")
    code, env, _ = cli("render-templates", "--json", "--root", str(root))
    assert code == 4 and env["errors"][0]["code"] == "schema_invalid"


def test_render_templates_refused_outside_toolkit_range(cli, tmp_path, monkeypatch):  # 001/AC4 through the real CLI
    from metarepo_engine import dispatch

    root = _project(tmp_path)
    yml = root / "project.yaml"
    yml.write_text(yml.read_text(encoding="utf-8").replace(">=0.1,<0.2", ">=0.3,<0.4"), encoding="utf-8")
    monkeypatch.setattr(dispatch, "__version__", "0.4.0")
    code, env, _ = cli("render-templates", "--json", "--root", str(root))
    assert code == 4 and env["errors"][0]["code"] == "toolkit_incompatible"
    code, env, _ = cli("config", "get", "name", "--json", "--root", str(root))  # read: works with a warning
    assert code == 0 and env["warnings"][0]["code"] == "toolkit_incompatible"
