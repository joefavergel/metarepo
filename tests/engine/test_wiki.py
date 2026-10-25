"""Spec 009: wiki configuration, bundle helpers, ``wiki scaffold`` and ``wiki gate``."""
from __future__ import annotations

import json

import pytest

from helpers import add_page, add_source, tree_snapshot
from metarepo_engine import okf
from metarepo_engine.wiki import bundle, config as wc, gate

AREA_ANSWERS = {
    "axis": "area", "profile": "team",
    "areas": ["Alpha", {"name": "Beta Topic", "folder": "beta"}],
    "page_types": ["Concept", {"name": "Process", "folder": "processes", "description": "How things are done"}],
}


def scaffold(cli, root, answers=None, *extra, actor="member-a"):
    args = []
    tmp = root.parent / "answers.json"
    if answers is not None:
        tmp.write_text(json.dumps(answers), encoding="utf-8")
        args += ["--answers", str(tmp)]
    return cli("wiki", "scaffold", *args, *extra, "--json", "--root", str(root), "--actor", actor)


def gate_cmd(cli, root, *extra):
    return cli("wiki", "gate", *extra, "--json", "--root", str(root), "--actor", "member-a")


@pytest.fixture
def root(make_project):
    return make_project()


def state(root):
    return json.loads((root / "_system" / "state.json").read_text(encoding="utf-8"))


# ---- configuration (R6, 002/ADR-005)
def test_config_roundtrip_and_layout():
    cfg = wc.WikiConfig.from_mapping(AREA_ANSWERS)
    assert cfg.axis == "area" and [a.folder for a in cfg.areas] == ["alpha", "beta"]
    assert cfg.content_folders() == ["sources", "skills", "alpha", "beta"]
    assert cfg.folder_for("Concept", "Alpha") == "alpha" and cfg.folder_for("Source") == "sources"
    assert cfg.index_groups() == ["Alpha", "Beta Topic", "Sources", "Skills"]
    assert cfg.group_of("Concept", "beta/x.md") == "Beta Topic" and cfg.group_of("Source", "sources/s.md") == "Sources"
    text = "# W\n" + wc.render_block(cfg) + "\nprose\n"
    again = wc.parse_wiki_md(text)
    assert again.to_mapping() == cfg.to_mapping()
    assert "prose" in wc.replace_block(text, again) and "prose" in wc.replace_block(text, again)


@pytest.mark.parametrize("axis,expect", [("type", ["sources", "skills", "concept", "processes"]),
                                         ("flat", ["sources", "skills", "pages"])])
def test_layout_other_axes(axis, expect):
    cfg = wc.WikiConfig.from_mapping({**AREA_ANSWERS, "axis": axis})
    assert cfg.content_folders() == expect
    assert cfg.folder_for("Process", None) == ("processes" if axis == "type" else "pages")
    assert cfg.index_groups() == ["Concept", "Process", "Sources", "Skills"]


@pytest.mark.parametrize("bad", [
    {"axis": "area", "page_types": ["A"]},  # no areas
    {"axis": "type"},  # no page types
    {"axis": "grid", "page_types": ["A"]},
    {"profile": "crowd", "page_types": ["A"]},
    {"page_types": ["A", "a"]},  # duplicate name
    {"page_types": [{"name": "A", "folder": "sources"}]},  # reserved folder
    {"page_types": ["!!!"]},  # no folder derivable
])
def test_config_rejects(bad):
    with pytest.raises(wc.WikiConfigError):
        wc.WikiConfig.from_mapping(bad)


def test_structural_types_implicit_and_unknown_keys_kept():
    cfg = wc.WikiConfig.from_mapping({"page_types": ["Source", "Concept", "Skill"], "lint": {"severity": {"x": "info"}}})
    assert [t.name for t in cfg.page_types] == ["Concept"] and cfg.extra == {"lint": {"severity": {"x": "info"}}}
    assert cfg.to_mapping()["lint"] == {"severity": {"x": "info"}}


def test_kebab_keeps_content_language():
    assert wc.kebab("Zeitplan & Übersicht") == "zeitplan-übersicht"
    assert wc.is_kebab("übersicht") and not wc.is_kebab("Upper")


# ---- bundle helpers
def test_index_entries_idempotent_and_grouped():
    cfg = wc.WikiConfig.from_mapping(AREA_ANSWERS)
    idx = bundle.render_index(cfg, "T")
    assert idx.startswith('---\nokf_version: "0.2"\n---\n# T')
    idx = bundle.add_index_entry(idx, "Alpha", "One", "alpha/one.md", "first")
    idx = bundle.add_index_entry(idx, "Alpha", "Two", "alpha/two.md", None)
    again = bundle.add_index_entry(idx, "Alpha", "One", "alpha/one.md", "first")
    assert again == idx
    refreshed = bundle.add_index_entry(idx, "Alpha", "One", "alpha/one.md", "changed")
    assert refreshed.count("alpha/one.md") == 1 and "- changed" in refreshed
    assert idx.index("[One]") < idx.index("[Two]") < idx.index("## Beta Topic")
    assert "## New\n\n* [N](n.md)" in bundle.add_index_entry(idx, "New", "N", "n.md")
    assert bundle.ensure_index_groups(idx, ["Alpha", "Gamma"]).count("## Gamma") == 1


def test_log_newest_first():
    log = bundle.render_log()
    log = bundle.add_log_entry(log, "2026-10-01", "Creation", "a")
    log = bundle.add_log_entry(log, "2026-10-03", "Update", "b")
    log = bundle.add_log_entry(log, "2026-10-02", "Update", "c")
    log = bundle.add_log_entry(log, "2026-10-03", "Update", "d")
    dates = [ln for ln in log.splitlines() if ln.startswith("## ")]
    assert dates == ["## 2026-10-03", "## 2026-10-02", "## 2026-10-01"]
    assert log.index("**Update** d") < log.index("**Update** b")


# ---- wiki scaffold (R5, R6, AC2, AC3, AC4)
@pytest.mark.parametrize("axis,folders", [("area", ["alpha", "beta"]), ("type", ["concept", "processes"]), ("flat", ["pages"])])
def test_scaffold_per_axis(cli, root, axis, folders):
    code, env, _ = scaffold(cli, root, {**AREA_ANSWERS, "axis": axis})
    assert code == 0, env
    wiki = root / "30_wiki"
    for name in ("WIKI.md", "index.md", "log.md", "skills/answer-from-wiki.md", "sources/.keep"):
        assert (wiki / name).is_file(), name
    for f in folders:
        assert (wiki / f / ".keep").is_file()
    text = (wiki / "WIKI.md").read_text(encoding="utf-8")
    for section in ("## Configuration", "## What this wiki is for", "## Layout", "## Page types", "## What earns a page",
                    "## Ingest emphasis", "## Governance", "## Conventions", "## Open questions"):
        assert section in text
    assert wc.load(root).axis == axis
    assert "$" not in text.replace("`", "")  # every placeholder substituted
    page = okf.OkfPage.load(wiki / "skills" / "answer-from-wiki.md")  # AC4
    assert page.type == "Skill" and page.problems() == []
    assert bundle.validate(root, wc.load(root)).ok
    s = state(root)["wiki"]
    assert s["configured"] is False and s["scaffolded_at"] and gate.feed_allowed(state(root))


def test_scaffold_default_objective_from_project_and_idempotent(cli, root):
    code, env, _ = scaffold(cli, root, AREA_ANSWERS)
    assert code == 0 and env["data"]["created"]
    assert "Example objective." in (root / "30_wiki" / "WIKI.md").read_text(encoding="utf-8")
    snap = tree_snapshot(root / "30_wiki")
    code, env, _ = scaffold(cli, root, AREA_ANSWERS)
    assert code == 0 and env["data"]["created"] == [] and env["data"]["updated"] == []
    assert tree_snapshot(root / "30_wiki") == snap


def test_scaffold_keeps_hand_edits_and_warns_on_different_config(cli, root):
    scaffold(cli, root, AREA_ANSWERS)
    wm = root / "30_wiki" / "WIKI.md"
    wm.write_text(wm.read_text(encoding="utf-8") + "\nHand-written note.\n", encoding="utf-8")
    code, env, _ = scaffold(cli, root, {**AREA_ANSWERS, "axis": "flat"})
    assert code == 0 and "Hand-written note." in wm.read_text(encoding="utf-8")
    assert [w["code"] for w in env["warnings"]] == ["config_differs"] and wc.load(root).axis == "area"


def test_scaffold_dry_run_writes_nothing(cli, root):
    before = tree_snapshot(root)
    code, env, _ = scaffold(cli, root, AREA_ANSWERS, "--dry-run")
    assert code == 0 and env["data"]["created"] and env["data"]["dry_run"]
    assert tree_snapshot(root) == before


def test_scaffold_invalid_answers_exit_2(cli, root):
    code, env, _ = scaffold(cli, root, {"axis": "area", "page_types": ["A"]})
    assert code == 2 and env["errors"][0]["code"] == "wiki_config_invalid"
    assert not (root / "30_wiki" / "WIKI.md").exists()


def test_scaffold_flags_without_answers_file(cli, root):
    code, env, _ = cli("wiki", "scaffold", "--axis", "type", "--page-type", "Idea", "--page-type", "Task=tasks",
                       "--json", "--root", str(root))
    assert code == 0, env
    assert wc.load(root).type_folder("Task") == "tasks" and (root / "30_wiki" / "idea").is_dir()


def test_owner_mode_blocks_non_owner(cli, make_project):
    root = make_project(governance={"wiki_write_mode": "owner", "wiki_owners": ["member-a"]})
    code, env, _ = scaffold(cli, root, AREA_ANSWERS, actor="member-b")
    assert code == 3 and env["errors"][0]["code"] == "not_wiki_owner"
    assert not (root / "30_wiki" / "WIKI.md").exists()
    code, env, _ = scaffold(cli, root, AREA_ANSWERS, actor="member-a")
    assert code == 0


def test_scaffold_requires_configured_metarepo(cli, root):
    st = state(root)
    st["metarepo"]["configured"] = False
    (root / "_system" / "state.json").write_text(json.dumps(st), encoding="utf-8")
    code, env, _ = scaffold(cli, root, AREA_ANSWERS)
    assert code == 3 and env["errors"][0]["code"] == "metarepo_not_configured"


def test_lease_released_after_scaffold(cli, root):
    scaffold(cli, root, AREA_ANSWERS)
    code, env, _ = cli("lease", "status", "--json", "--root", str(root))
    assert {r["area"]: r["state"] for r in env["data"]["leases"]}["wiki"] == "released"


# ---- wiki gate (R1, R8, AC1, AC2)
def feed_pages(root, n=3):
    for i in range(n):
        add_page(root, f"30_wiki/alpha/page-{i}.md", {"type": "Concept", "title": f"Page {i}", "description": "d",
                                                      "sources": [{"id": "s1"}]}, f"# Page {i}\n\nClaim.[^s1]\n\n[^s1]: S\n")


def test_gate_status_then_complete_flow_ac1(cli, root):
    for i in range(12):
        add_source(root, f"doc-{i}.txt", f"content {i}".encode())
    code, env, _ = gate_cmd(cli, root)
    assert code == 0 and env["data"]["gate"]["configured"] is False
    code, env, _ = gate_cmd(cli, root, "--complete")
    assert code == 3 and env["errors"][0]["code"] == "wiki_not_scaffolded"
    scaffold(cli, root, AREA_ANSWERS)
    feed_pages(root)
    code, env, _ = gate_cmd(cli, root, "--complete")
    assert code == 0, env
    st = state(root)["wiki"]
    assert st["configured"] is True and st["configured_at"] and st["scaffolded_at"]
    first = st["configured_at"]
    code, env, _ = gate_cmd(cli, root, "--complete")
    assert code == 0 and env["data"]["already_configured"] and state(root)["wiki"]["configured_at"] == first
    assert bundle.validate(root).pages == 4  # 3 pages + answer-from-wiki


def test_gate_blocks_on_invalid_page(cli, root):
    scaffold(cli, root, AREA_ANSWERS)
    add_page(root, "30_wiki/alpha/bad.md", {"title": "no type"}, "x\n")
    add_page(root, "30_wiki/alpha/worse.md", {"type": "Concept", "status": "weird"}, "x\n")
    code, env, _ = gate_cmd(cli, root, "--complete")
    assert code == 1 and len(env["errors"]) == 2, env
    assert state(root)["wiki"]["configured"] is False


def test_gate_warns_on_empty_bundle_and_undeclared_type(cli, root):
    scaffold(cli, root, AREA_ANSWERS)
    add_page(root, "30_wiki/alpha/odd.md", {"type": "Mystery"}, "x\n")
    code, env, _ = gate_cmd(cli, root, "--complete")
    assert code == 0 and "undeclared-type" in [w["code"] for w in env["warnings"]]


def test_gate_dry_run(cli, root):
    scaffold(cli, root, AREA_ANSWERS)
    code, env, _ = gate_cmd(cli, root, "--complete", "--dry-run")
    assert code == 0 and state(root)["wiki"]["configured"] is False


def test_rescaffold_refused_then_reconfigure_keeps_pages_ac2(cli, root):
    scaffold(cli, root, AREA_ANSWERS)
    feed_pages(root)
    gate_cmd(cli, root, "--complete")
    snap = {k: v for k, v in tree_snapshot(root / "30_wiki").items() if k.startswith("alpha/page")}
    code, env, _ = scaffold(cli, root, AREA_ANSWERS)
    assert code == 3 and env["errors"][0]["code"] == "wiki_already_configured"
    new = {**AREA_ANSWERS, "areas": ["Alpha", "Gamma"]}
    code, env, _ = scaffold(cli, root, new, "--reconfigure")
    assert code == 0, env
    assert [a.folder for a in wc.load(root).areas] == ["alpha", "gamma"]
    assert (root / "30_wiki" / "gamma" / ".keep").exists() and "## Gamma" in (root / "30_wiki" / "index.md").read_text(encoding="utf-8")
    assert {k: v for k, v in tree_snapshot(root / "30_wiki").items() if k.startswith("alpha/page")} == snap
    assert (root / "30_wiki" / "beta").is_dir()  # nothing deleted
    st = state(root)["wiki"]
    assert st["configured"] is True and st["reconfigured_at"]


def test_gate_api_requires_configured(make_project):
    from metarepo_engine.jsonout import EngineError
    from metarepo_engine.root import Project

    proj = Project(make_project())
    with pytest.raises(EngineError) as exc:
        gate.require_configured(proj)
    assert exc.value.code == "wiki_not_configured" and exc.value.exit_code == 3


def test_templates_are_domain_neutral(repo):
    text = "\n".join(p.read_text(encoding="utf-8") for p in (repo / "tooling" / "templates" / "wiki").iterdir())
    for word in ("client", "department", "invoice", "contract", "company", "customer"):
        assert word not in text.lower()
