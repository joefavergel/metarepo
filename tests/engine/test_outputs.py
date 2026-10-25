"""Spec 014: /make-output skill, output templates and ``output register`` (R1-R10, AC1-AC4)."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest
import yaml

from helpers import add_page, add_source, tree_snapshot
from metarepo_engine import frontmatter, outputs

REPO = Path(__file__).resolve().parents[2]
SKILL = REPO / "tooling" / "skills" / "make-output"
BUILTIN = REPO / "tooling" / "templates" / "outputs"
PAGES = ("a/alpha.md", "a/beta.md", "b/gamma.md")


def flat(path: Path) -> str:
    return " ".join(path.read_text(encoding="utf-8").split())


@pytest.fixture
def wiki(make_project, tmp_path):
    root = make_project()
    add_page(root, "30_wiki/index.md", {"type": "Index", "title": "Index"}, "# Index\n\n- [Alpha](a/alpha.md)\n")
    for name in PAGES:
        add_page(root, f"30_wiki/{name}", {"type": "Concept", "title": name, "status": "draft"}, f"# {name}\n\nText.\n")
    state = root / "_system" / "state.json"
    data = json.loads(state.read_text(encoding="utf-8"))
    data["wiki"]["configured"] = True
    state.write_text(json.dumps(data), encoding="utf-8")
    return root


@pytest.fixture
def draft(tmp_path):
    path = tmp_path / "draft.md"
    path.write_text("# Topic\n\nClaim.[^1]\n\n[^1]: [Alpha](../30_wiki/a/alpha.md)\n", encoding="utf-8")
    return path


def register(cli, root, draft, *extra, kind="one-pager", slug="topic", pages=PAGES):
    argv = ["output", "register", "--kind", kind, "--slug", slug, "--content-file", str(draft), "--date", "2026-10-25",
            "--assisted-by", "test/model", "--json", "--root", str(root)]
    for p in pages:
        argv += ["--based-on", p]
    return cli(*argv, *extra)


def sha(root, rel):
    return hashlib.sha256((root / rel).read_bytes()).hexdigest()


# ---- R1: kinds and templates

def test_five_builtin_kinds_each_have_template_and_instructions():
    for kind in outputs.BUILTIN_KINDS:
        for name in ("template.md", "instructions.md"):
            assert (BUILTIN / kind / name).read_text(encoding="utf-8").strip(), (kind, name)
    assert sorted(p.name for p in BUILTIN.iterdir()) == sorted(outputs.BUILTIN_KINDS)


def test_kinds_listing_reports_builtin_kinds(cli, wiki):
    code, env, _ = cli("output", "register", "--kinds", "--json", "--root", str(wiki))
    assert code == 0
    rows = {r["kind"]: r for r in env["data"]["kinds"]}
    assert set(rows) == set(outputs.BUILTIN_KINDS) and all(r["source"] == "builtin" for r in rows.values())
    assert rows["one-pager"]["files"] == ["template.md", "instructions.md"]


# ---- AC3: project template overrides the built-in one; project-defined kinds are added

def test_project_template_overrides_builtin_and_adds_kinds(cli, wiki):
    custom = wiki / "outputs" / "_templates" / "one-pager"
    custom.mkdir(parents=True)
    (custom / "template.md").write_text("# custom\n", encoding="utf-8")
    extra = wiki / "outputs" / "_templates" / "status-note"
    extra.mkdir(parents=True)
    (extra / "template.md").write_text("# note\n", encoding="utf-8")
    env = cli("output", "register", "--kinds", "--json", "--root", str(wiki))[1]
    rows = {r["kind"]: r for r in env["data"]["kinds"]}
    assert rows["one-pager"]["source"] == "project" and rows["one-pager"]["overrides_builtin"] is True
    assert rows["one-pager"]["path"] == "outputs/_templates/one-pager"
    assert rows["status-note"]["source"] == "project" and rows["status-note"]["overrides_builtin"] is False
    assert rows["faq"]["source"] == "builtin"


def test_kinds_listing_changes_nothing_and_unknown_kind_is_a_usage_error(cli, wiki, draft):
    before = tree_snapshot(wiki)
    assert cli("output", "register", "--kinds", "--json", "--root", str(wiki))[0] == 0
    assert tree_snapshot(wiki) == before
    code, env, _ = register(cli, wiki, draft, kind="no-such-kind")
    assert code == 2 and env["errors"][0]["code"] == "unknown_kind"


# ---- AC1 / R6 / R7: register with hashes, naming, revisions

def test_register_records_provenance_and_hashes_of_all_pages(cli, wiki, draft):
    hashes = {f"30_wiki/{p}": sha(wiki, f"30_wiki/{p}") for p in PAGES}
    code, env, _ = register(cli, wiki, draft)
    assert code == 0
    rel = "outputs/2026-10-25-one-pager-topic.md"
    assert env["data"]["output"] == rel and env["data"]["revision"] == 1 and env["data"]["written"] is True
    front = frontmatter.read(wiki / rel).front
    assert front["type"] == "Output" and front["kind"] == "one-pager"
    assert front["generated"]["by"] == "test-actor" and front["generated"]["assisted_by"] == "test/model"
    assert front["generated"]["at"]
    assert {e["path"]: e["sha256"] for e in front["based_on"]} == hashes
    assert front["wiki_index_sha256"] == sha(wiki, "30_wiki/index.md")
    assert front["language"] == "en" and front["template"] == "builtin"
    assert "Claim." in (wiki / rel).read_text(encoding="utf-8")        # body carried verbatim
    assert not (wiki / "outputs" / "2026-10-25-one-pager-topic.meta.yaml").exists()


def test_never_overwrites_revisions_get_suffix(cli, wiki, draft):
    first = register(cli, wiki, draft)[1]["data"]["output"]
    before = (wiki / first).read_bytes()
    second = register(cli, wiki, draft)
    third = register(cli, wiki, draft)
    assert second[1]["data"]["output"] == "outputs/2026-10-25-one-pager-topic-v2.md"
    assert third[1]["data"]["output"] == "outputs/2026-10-25-one-pager-topic-v3.md"
    assert second[1]["data"]["revision"] == 2 and any(w["code"] == "revision" for w in second[1]["warnings"])
    assert (wiki / first).read_bytes() == before


def test_draft_frontmatter_is_kept_and_marp_flag_added(cli, wiki, tmp_path):
    d = tmp_path / "deck.md"
    d.write_text("---\ntheme: plain\n---\n\n# Deck\n", encoding="utf-8")
    env = register(cli, wiki, d, "--format", "marp", kind="slides")[1]
    front = frontmatter.read(wiki / env["data"]["output"]).front
    assert front["marp"] is True and front["theme"] == "plain" and front["type"] == "Output"


def test_page_arguments_accept_wiki_relative_paths_and_dedupe(cli, wiki, draft):
    env = register(cli, wiki, draft, pages=("a/alpha.md", "30_wiki/a/alpha.md"))[1]
    assert [e["path"] for e in env["data"]["based_on"]] == ["30_wiki/a/alpha.md"]


def test_not_a_wiki_content_page_is_rejected(cli, wiki, draft):
    code, env, _ = register(cli, wiki, draft, pages=("index.md",))
    assert code == 3 and env["errors"][0]["code"] == "page_not_found"
    assert not list((wiki / "outputs").glob("*.md"))


# ---- R5: formats

def test_html_gets_sidecar_with_provenance(cli, wiki, tmp_path):
    page = tmp_path / "page.html"
    page.write_text("<!doctype html><html><body>Hi</body></html>", encoding="utf-8")
    code, env, _ = register(cli, wiki, page, "--format", "html", kind="report")
    assert code == 0
    assert env["data"]["output"] == "outputs/2026-10-25-report-topic.html"
    assert env["data"]["sidecar"] == "outputs/2026-10-25-report-topic.meta.yaml"
    assert (wiki / env["data"]["output"]).read_text(encoding="utf-8") == page.read_text(encoding="utf-8")
    meta = yaml.safe_load((wiki / env["data"]["sidecar"]).read_text(encoding="utf-8"))
    assert meta["type"] == "Output" and meta["based_on"][0]["sha256"]
    assert outputs.read_meta(wiki / env["data"]["output"])["kind"] == "report"
    # a second html registration takes the next revision for both files
    again = register(cli, wiki, page, "--format", "html", kind="report")[1]["data"]
    assert again["output"].endswith("-v2.html") and again["sidecar"].endswith("-v2.meta.yaml")


def test_artifact_stub_records_link_and_provenance(cli, wiki):
    argv = ("output", "register", "--kind", "slides", "--slug", "deck", "--format", "artifact", "--artifact-url",
            "https://example.test/a/1", "--based-on", "a/alpha.md", "--date", "2026-10-25", "--title", "Deck",
            "--json", "--root", str(wiki))
    code, env, _ = cli(*argv)
    assert code == 0
    path = wiki / env["data"]["output"]
    front = frontmatter.read(path).front
    assert front["artifact_url"] == "https://example.test/a/1" and front["type"] == "Output"
    assert "https://example.test/a/1" in path.read_text(encoding="utf-8")
    code, env, _ = cli("output", "register", "--kind", "slides", "--slug", "deck", "--format", "artifact",
                       "--based-on", "a/alpha.md", "--json", "--root", str(wiki))
    assert code == 2 and env["errors"][0]["code"] == "missing_artifact_url"


def test_default_format_comes_from_project_config(cli, make_project, tmp_path):
    root = make_project(outputs={"default_format": "html"})
    add_page(root, "30_wiki/a.md", {"type": "Concept", "title": "A"}, "# A\n")
    state = root / "_system" / "state.json"
    data = json.loads(state.read_text(encoding="utf-8"))
    data["wiki"]["configured"] = True
    state.write_text(json.dumps(data), encoding="utf-8")
    page = tmp_path / "p.html"
    page.write_text("<html></html>", encoding="utf-8")
    env = cli("output", "register", "--kind", "brief", "--slug", "x", "--content-file", str(page), "--based-on",
              "a.md", "--json", "--root", str(root))[1]
    assert env["data"]["format"] == "html" and env["data"]["output"].endswith(".html")


# ---- AC2 / R8: staleness

def test_editing_a_based_on_page_makes_the_output_stale(cli, wiki, draft):
    register(cli, wiki, draft)
    assert outputs.stale_outputs(wiki) == []
    page = wiki / "30_wiki" / "a" / "beta.md"
    page.write_text(page.read_text(encoding="utf-8") + "\nEdited.\n", encoding="utf-8")
    stale = outputs.stale_outputs(wiki)
    assert len(stale) == 1 and stale[0]["output"] == "outputs/2026-10-25-one-pager-topic.md"
    assert [c["path"] for c in stale[0]["changed"]] == ["30_wiki/a/beta.md"] and stale[0]["missing"] == []
    assert stale[0]["changed"][0]["current"] == sha(wiki, "30_wiki/a/beta.md")


def test_removed_page_is_reported_and_html_sidecar_outputs_are_checked(cli, wiki, tmp_path):
    page = tmp_path / "p.html"
    page.write_text("<html></html>", encoding="utf-8")
    register(cli, wiki, page, "--format", "html")
    (wiki / "30_wiki" / "b" / "gamma.md").unlink()
    stale = outputs.stale_outputs(wiki)
    assert stale[0]["missing"] == ["30_wiki/b/gamma.md"]


def test_templates_and_non_outputs_are_ignored_by_staleness(wiki):
    custom = wiki / "outputs" / "_templates" / "one-pager"
    custom.mkdir(parents=True)
    (custom / "template.md").write_text("---\ntype: Output\nbased_on: [{path: x, sha256: y}]\n---\n", encoding="utf-8")
    (wiki / "outputs" / "note.md").write_text("plain", encoding="utf-8")
    assert outputs.stale_outputs(wiki) == []


# ---- R9 / ADR-001: outputs are not sources

def test_outputs_are_not_ingested(cli, wiki, draft):
    add_source(wiki, "docs/one.txt", b"one", title="One")
    rows_before = cli("registry", "--json", "--root", str(wiki))[1]["data"]
    scan_before = cli("inbox", "scan", "--json", "--root", str(wiki))[1]["data"]
    register(cli, wiki, draft)
    assert cli("registry", "--json", "--root", str(wiki))[1]["data"] == rows_before
    assert cli("inbox", "scan", "--json", "--root", str(wiki))[1]["data"] == scan_before


def test_register_writes_only_the_output_file(cli, wiki, draft):
    before = tree_snapshot(wiki)
    code, env, _ = register(cli, wiki, draft)
    after = tree_snapshot(wiki)
    changed = {k for k in after if k not in before or after[k] != before[k]}
    # the output file and the event log; nothing in the wiki, sources, extracts, inbox or locks
    assert changed - {"_system/events.log"} == {env["data"]["output"]}
    assert not any(k.startswith(("30_wiki/", "10_sources/", "20_extracts/", "00_inbox/")) for k in changed)


def test_dry_run_changes_nothing(cli, wiki, draft):
    before = tree_snapshot(wiki)
    code, env, _ = register(cli, wiki, draft, "--dry-run")
    assert code == 0 and env["data"]["written"] is False and env["data"]["dry_run"] is True
    assert tree_snapshot(wiki) == before


# ---- AC4 / R3: nothing to base the output on

def test_no_wiki_basis_registers_nothing(cli, wiki, draft):
    code, env, _ = register(cli, wiki, draft, pages=())
    assert code == 2 and env["errors"][0]["code"] == "no_based_on"
    assert not list((wiki / "outputs").iterdir())


def test_gate_closed_blocks_register(cli, make_project, draft):
    root = make_project()
    add_page(root, "30_wiki/a.md", {"type": "Concept", "title": "A"}, "# A\n")
    code, env, _ = register(cli, root, draft, pages=("a.md",))
    assert code == 3 and env["errors"][0]["code"] == "wiki_not_configured"


def test_invalid_slug_and_missing_content(cli, wiki, draft):
    assert register(cli, wiki, draft, slug="Bad Slug")[1]["errors"][0]["code"] == "invalid_slug"
    code, env, _ = cli("output", "register", "--kind", "faq", "--slug", "x", "--based-on", "a/alpha.md", "--json",
                       "--root", str(wiki))
    assert code == 2 and env["errors"][0]["code"] == "missing_content"


# ---- skill structure (R2, R3, R4, R5, R9, R10; AC1, AC4 manual runs below)

def test_skill_is_implemented_and_covers_the_workflow():
    low = flat(SKILL / "SKILL.md").lower()
    assert "placeholder" not in low and "claude_plugin_root" not in low
    for needle in ("wiki_configured", "/init-wiki",                          # gate
                   "audience", "language.content", "outputs.default_format",  # R2
                   "output register --kinds", "outputs/_templates",          # R1
                   "consult-wiki", "ladder", "explicitly ask",               # R3
                   "not covered", "register nothing",                        # R3 / AC4
                   "relative link", "footnote",                              # R4
                   "marp", "html", "--format artifact", "--artifact-url",    # R5
                   "-v2", "never overwrites",                                # R6
                   "based_on", "wiki_index_sha256", ".meta.yaml",            # R7
                   "output-stale",                                           # R8
                   "never a source", "00_inbox/",                            # R9
                   "show the operator the draft", "iterate"):                # R10
        assert needle.lower() in low, needle
    assert (SKILL / "references" / "kinds.md").is_file() and (SKILL / "references" / "engine.md").is_file()


def test_skill_description_is_a_single_line_with_trigger():
    head = (SKILL / "SKILL.md").read_text(encoding="utf-8").split("---")[1]
    assert head.count("name: make-output") == 1 and "/make-output" in head


def test_templates_cite_wiki_pages_and_stay_neutral():
    for kind in outputs.BUILTIN_KINDS:
        text = (BUILTIN / kind / "template.md").read_text(encoding="utf-8").lower()
        assert "wiki page" in text and "[^1]" in text, kind
