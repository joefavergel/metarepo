"""Spec 012: the lint catalog, severity overrides, read-only discipline, last_lint and performance."""
from __future__ import annotations

import json
import shutil
import time
from datetime import datetime, timezone

import pytest
import yaml

from helpers import add_page, add_source, tree_snapshot
from metarepo_engine import frontmatter, leases, lint
from metarepo_engine.wiki import bundle, sourcepage
from metarepo_engine.wiki import config as wiki_config

ANSWERS = {"axis": "area", "profile": "team", "areas": ["Alpha"], "page_types": ["Concept"]}
TOPIC = "30_wiki/alpha/topic.md"
SECRET = "hunter2-token"


def run(cli, root, *args, actor="member-a"):
    return cli(*args, "--json", "--root", str(root), *([] if args[0] == "registry" else ["--actor", actor]))


def lint_run(cli, root, *extra):
    code, env, _ = run(cli, root, "lint", *extra)
    return code, env


def found(env, check, where=None):
    return [f for f in env["data"]["findings"] if f["check"] == check and (where is None or f["where"] == where)]


def edit_project(root, **kv):
    path = root / "project.yaml"
    cfg = yaml.safe_load(path.read_text(encoding="utf-8"))
    for key, value in kv.items():
        cfg[key] = value
    path.write_text(yaml.safe_dump(cfg, sort_keys=False), encoding="utf-8")


def edit_wiki_md(root, **extra):
    path = wiki_config.wiki_md_path(root)
    cfg = wiki_config.parse_wiki_md(path.read_text(encoding="utf-8"))
    cfg.extra.update(extra)
    path.write_text(wiki_config.replace_block(path.read_text(encoding="utf-8"), cfg), encoding="utf-8")


def set_front(root, rel, **updates):
    frontmatter.update(root / rel, updates)


def append(root, rel, text):
    p = root / rel
    p.write_text(p.read_text(encoding="utf-8") + text, encoding="utf-8")


@pytest.fixture
def root(make_project):
    return make_project()


@pytest.fixture
def clean(cli, root):
    """A fed, completed, indexed and logged wiki: the 'clean fixture' of 012/AC1."""
    answers = root.parent / "answers.json"
    answers.write_text(json.dumps(ANSWERS), encoding="utf-8")
    code, env, _ = run(cli, root, "wiki", "scaffold", "--answers", str(answers))
    assert code == 0, env
    src = add_source(root, "a.txt", b"alpha text")
    code, env, _ = run(cli, root, "wiki", "source-page", src["id"], "--write")
    assert code == 0, env
    page_rel = sourcepage.page_rel(src["id"])
    sid = src["id"]
    set_front(root, page_rel, description="Notes on the alpha source.")
    append(root, page_rel, "\nClaim from the source.\n\n## Pages touched\n\n- [Topic](../alpha/topic.md)\n")
    add_page(root, TOPIC, {
        "type": "Concept", "title": "Topic", "description": "What alpha says.", "status": "stable",
        "tags": ["alpha"], "sources": [{"id": sid, "title": "a", "path": f"../sources/{sid}.md"}],
        "generated": {"by": "human:member-a", "at": "2026-10-24T15:00:00Z"},
        "verified": {"by": "human:member-b", "at": "2026-10-24T16:00:00Z"},
    }, f"# Topic\n\nAlpha is a letter.[^{sid}]\n\nSee [the source](../sources/{sid}.md).\n\n[^{sid}]: a.txt\n")
    index = (root / "30_wiki/index.md").read_text(encoding="utf-8")
    index = bundle.add_index_entry(index, "Alpha", "Topic", "alpha/topic.md", "What alpha says.")
    index = bundle.add_index_entry(index, "Sources", "a", f"sources/{sid}.md", "Notes on the alpha source.")
    (root / "30_wiki/index.md").write_text(index, encoding="utf-8")
    log = (root / "30_wiki/log.md").read_text(encoding="utf-8")
    log = bundle.add_log_entry(log, "2026-10-24", "Creation",
                               f"Fed [a](sources/{sid}.md) and created [Topic](alpha/topic.md).")
    (root / "30_wiki/log.md").write_text(log, encoding="utf-8")
    return {"root": root, "src": src, "sid": sid, "source_page": page_rel}


# ---- AC1: silent on the clean fixture (also 010/AC1: a normal feed lints clean)
def test_clean_fixture_has_no_findings(cli, clean):
    code, env = lint_run(cli, clean["root"])
    assert code == 0 and env["data"]["findings"] == [], env["data"]["findings"]
    assert env["data"]["summary"]["total"] == 0 and env["data"]["last_lint_recorded"] is True


def test_fresh_scaffold_lints_clean(cli, root):
    answers = root.parent / "answers.json"
    answers.write_text(json.dumps(ANSWERS), encoding="utf-8")
    assert run(cli, root, "wiki", "scaffold", "--answers", str(answers))[0] == 0
    code, env = lint_run(cli, root)
    assert code == 0 and env["data"]["findings"] == []


def test_unfed_skeleton_page_only_asks_for_completion(cli, root):
    """010/AC1 boundary: a source page the skill has not yet completed is advisory, never an error or a block."""
    answers = root.parent / "answers.json"
    answers.write_text(json.dumps(ANSWERS), encoding="utf-8")
    run(cli, root, "wiki", "scaffold", "--answers", str(answers))
    src = add_source(root, "a.txt", b"alpha")
    run(cli, root, "wiki", "source-page", src["id"], "--write")
    code, env = lint_run(cli, root)
    assert code == 0 and not [f for f in env["data"]["findings"] if f["severity"] in ("block", "error")]
    assert found(env, "no-description")


# ---- AC1: every check fires on its own fixture
def m_conformance(c):
    add_page(c["root"], "30_wiki/alpha/bad.md", None, "# no frontmatter\n")


def m_policy(c):
    edit_project(c["root"], policy={"block_terms": [SECRET], "block_patterns": []})
    append(c["root"], TOPIC, f"\nThe password is {SECRET}.\n")


def m_source_unregistered(c):
    (c["root"] / "10_sources/stray.bin").write_bytes(b"stray")


def m_extract_orphan(c):
    other = add_source(c["root"], "b.txt", b"beta", triage="reference")
    (c["root"] / other["path"]).unlink()


def m_hash_mismatch(c):
    (c["root"] / c["src"]["path"]).write_bytes(b"tampered")


def m_failed(c):
    add_source(c["root"], "b.txt", b"beta", triage="reference", quality="failed")


def m_needs_review(c):
    add_source(c["root"], "b.txt", b"beta", triage="reference", quality="needs-review")


def m_triage_missing(c):
    other = add_source(c["root"], "b.txt", b"beta")
    set_front(c["root"], other["extract"], triage={})


def m_not_fed(c):
    add_source(c["root"], "b.txt", b"beta", triage="wiki")


def m_page_stale(c):
    set_front(c["root"], c["source_page"], content_sha256="0" * 64)


def m_page_dangling(c):
    set_front(c["root"], c["source_page"], extract="../../20_extracts/missing.md")


def m_drift(c):
    set_front(c["root"], TOPIC, sources=[{"id": "src-ffffffffffff", "title": "x"}])
    p = c["root"] / TOPIC
    p.write_text(p.read_text(encoding="utf-8").replace(f"[^{c['sid']}]", "[^src-ffffffffffff]"), encoding="utf-8")


def m_no_sources(c):
    set_front(c["root"], TOPIC, sources=[])
    p = c["root"] / TOPIC
    p.write_text(p.read_text(encoding="utf-8").replace(f"[^{c['sid']}]", ""), encoding="utf-8")


def m_dangling_footnote(c):
    append(c["root"], TOPIC, "\nAnother claim.[^src-000000000000]\n")


def m_unused_source(c):
    set_front(c["root"], TOPIC, sources=[{"id": c["sid"], "title": "a", "path": f"../sources/{c['sid']}.md"},
                                         {"id": "src-111111111111", "title": "unused"}])


def m_orphan(c):
    add_page(c["root"], "30_wiki/alpha/lonely.md", {"type": "Concept", "title": "Lonely", "description": "d",
             "sources": [{"id": c["sid"], "title": "a", "path": f"../sources/{c['sid']}.md"}],
             "verified": {"by": "human:b", "at": "2026-10-24T16:00:00Z"}}, f"# Lonely\n\nText.[^{c['sid']}]\n\n"
             f"[^{c['sid']}]: a\n")
    # indexed and logged so only the orphan check fires
    index = bundle.add_index_entry((c["root"] / "30_wiki/index.md").read_text(encoding="utf-8"), "Alpha", "Lonely",
                                   "alpha/lonely.md", "d")
    (c["root"] / "30_wiki/index.md").write_text(index, encoding="utf-8")
    append(c["root"], "30_wiki/log.md", "\n* **Creation** [Lonely](alpha/lonely.md)\n")


def m_broken_link(c):
    append(c["root"], TOPIC, "\nSee [gone](missing-page.md).\n")


def m_index_missing(c):
    p = c["root"] / "30_wiki/index.md"
    p.write_text("\n".join(ln for ln in p.read_text(encoding="utf-8").split("\n") if "alpha/topic.md" not in ln),
                 encoding="utf-8")


def m_log_drift(c):
    log = bundle.add_log_entry((c["root"] / "30_wiki/log.md").read_text(encoding="utf-8"), "2026-10-20", "Update",
                               "older")
    log = bundle.add_log_entry(log, "2026-10-25", "Update", "newer, inserted below the older one")
    # force a wrong order: put the oldest heading first
    head, _, rest = log.partition("## 2026-10-25")
    log = head + "## 2026-10-20" + log.split("## 2026-10-20", 1)[1].split("## ", 1)[0] + "## 2026-10-25" + rest
    (c["root"] / "30_wiki/log.md").write_text(log, encoding="utf-8")


def m_no_description(c):
    set_front(c["root"], TOPIC, description="")


def m_expired(c):
    set_front(c["root"], TOPIC, stale_after="2020-01-01")


def m_sign_coverage(c):
    frontmatter.update(c["root"] / TOPIC, remove=["verified"])


def m_duty_overlap(c):
    set_front(c["root"], TOPIC, verified={"by": "human:member-a", "at": "2026-10-24T16:00:00Z"})


def m_output_stale(c):
    outputs = c["root"] / "outputs"
    outputs.mkdir(exist_ok=True)
    add_page(c["root"], "outputs/2026-10-24-brief-topic.md", {
        "type": "Output", "kind": "brief", "generated": {"by": "human:member-a", "at": "2026-10-24T17:00:00Z"},
        "based_on": [{"path": TOPIC, "sha256": "0" * 64}], "wiki_index_sha256": "0" * 64}, "# Brief\n")


CASES = {
    "conformance": (m_conformance, "error"),
    "policy-violation": (m_policy, "block"),
    "source-unregistered": (m_source_unregistered, "error"),
    "extract-orphan": (m_extract_orphan, "warn"),
    "extract-hash-mismatch": (m_hash_mismatch, "error"),
    "extract-failed": (m_failed, "warn"),
    "extract-needs-review": (m_needs_review, "info"),
    "triage-missing": (m_triage_missing, "warn"),
    "not-fed": (m_not_fed, "info"),
    "source-page-stale": (m_page_stale, "warn"),
    "source-page-dangling": (m_page_dangling, "error"),
    "drift": (m_drift, "warn"),
    "no-sources": (m_no_sources, "warn"),
    "dangling-footnote": (m_dangling_footnote, "warn"),
    "unused-source": (m_unused_source, "info"),
    "orphan": (m_orphan, "warn"),
    "broken-link": (m_broken_link, "error"),
    "index-drift": (m_index_missing, "warn"),
    "log-drift": (m_log_drift, "info"),
    "no-description": (m_no_description, "warn"),
    "expired": (m_expired, "info"),
    "sign-coverage": (m_sign_coverage, "info"),
    "duty-overlap": (m_duty_overlap, "info"),
    "output-stale": (m_output_stale, "info"),
}


# a page the engine cannot read as OKF is also unindexed, unlogged and unlinked: that is the fixture, not noise
COLLATERAL: dict[str, set[str]] = {"conformance": {"index-drift", "orphan"}}


def test_every_catalog_check_has_a_fixture():
    assert set(CASES) == set(lint.CATALOG)
    assert {c: sev for c, (_, sev) in CASES.items()} == lint.CATALOG  # defaults of 012 section 2


@pytest.mark.parametrize("check", sorted(CASES))
def test_check_fires_on_its_fixture_and_only_there(cli, clean, check):
    mutate, severity = CASES[check]
    mutate(clean)
    code, env = lint_run(cli, clean["root"])
    hits = found(env, check)
    assert hits, f"{check} did not fire: {env['data']['findings']}"
    assert {h["severity"] for h in hits} == {severity}
    assert all(h["where"] and h["detail"] for h in hits)
    assert code == (1 if severity == "block" else 0)  # AC2
    stray = {f["check"] for f in env["data"]["findings"]} - {check}
    assert stray == COLLATERAL.get(check, set()), stray


def test_index_drift_variants(cli, clean):
    root = clean["root"]
    index = root / "30_wiki/index.md"
    text = index.read_text(encoding="utf-8").replace("What alpha says.", "Something else entirely.")
    index.write_text(text + "\n* [Ghost](alpha/ghost.md) - nowhere\n", encoding="utf-8")
    _, env = lint_run(cli, root)
    details = " | ".join(f["detail"] for f in found(env, "index-drift"))
    assert "differs" in details and "alpha/ghost.md" in details


def test_index_wrong_group_and_missing_index(cli, clean):
    root = clean["root"]
    index = root / "30_wiki/index.md"
    lines = index.read_text(encoding="utf-8").split("\n")
    moved = [ln for ln in lines if "alpha/topic.md" in ln]
    rest = [ln for ln in lines if "alpha/topic.md" not in ln] + moved  # now under the last group (Skills)
    index.write_text("\n".join(rest), encoding="utf-8")
    _, env = lint_run(cli, root)
    assert any("expected 'Alpha'" in f["detail"] for f in found(env, "index-drift"))
    index.unlink()
    _, env = lint_run(cli, root)
    assert any("missing" in f["detail"] for f in found(env, "index-drift", "30_wiki/index.md"))


def test_pages_are_not_required_to_be_listed_in_the_log(cli, clean):
    (clean["root"] / "30_wiki/log.md").write_text(bundle.render_log(), encoding="utf-8")
    _, env = lint_run(cli, clean["root"])
    assert found(env, "log-drift") == []


def test_log_entry_pointing_nowhere(cli, clean):
    append(clean["root"], "30_wiki/log.md", "\n* **Update** [Gone](alpha/gone.md)\n")
    _, env = lint_run(cli, clean["root"])
    assert any("gone.md" in f["detail"] for f in found(env, "log-drift", "30_wiki/log.md"))


def test_source_page_not_tracking_the_extraction_regeneration(cli, clean):
    set_front(clean["root"], clean["src"]["extract"], extracted_at="2026-11-01T10:00:00Z")
    _, env = lint_run(cli, clean["root"])
    hit = found(env, "source-page-stale")[0]
    assert "regenerated" in hit["detail"] and hit["where"] == clean["source_page"]


def test_extraction_part_of_wiki_source_is_policy_scanned(cli, clean):
    root = clean["root"]
    edit_project(root, policy={"block_terms": [SECRET], "block_patterns": []})
    ex = root / clean["src"]["extract"]
    (ex.parent / (ex.name[:-3] + ".part-002.md")).write_text(f"continued {SECRET}\n", encoding="utf-8")
    code, env = lint_run(cli, root)
    assert code == 1
    assert found(env, "policy-violation", "20_extracts/a.txt.part-002.md")


def test_policy_violation_masks_the_value_and_ignores_non_wiki_extractions(cli, clean):
    root = clean["root"]
    edit_project(root, policy={"block_terms": [], "block_patterns": [{"name": "token", "regex": r"hunter\d-token"}]})
    add_source(root, "ref.txt", b"reference", triage="reference", body=f"has {SECRET}\n")  # not wiki-decided
    wiki_src = add_source(root, "w.txt", b"wiki doc", triage="wiki", body=f"has {SECRET}\n")
    code, env = lint_run(cli, root)
    hits = found(env, "policy-violation")
    assert code == 1 and [h["where"] for h in hits] == [wiki_src["extract"]]
    assert SECRET not in json.dumps(env) and "token" in hits[0]["detail"]


# ---- R1 / AC2
def test_only_filter_and_unknown_id(cli, clean):
    m_broken_link(clean)
    m_no_description(clean)
    _, env = lint_run(cli, clean["root"], "--only", "broken-link")
    assert {f["check"] for f in env["data"]["findings"]} == {"broken-link"}
    assert env["data"]["checks_run"] == ["broken-link"]
    _, env = lint_run(cli, clean["root"], "--only", "broken-link,no-description", "--only", "expired")
    assert {f["check"] for f in env["data"]["findings"]} == {"broken-link", "no-description"}
    code, env = lint_run(cli, clean["root"], "--only", "nope")
    assert code == 2 and env["errors"][0]["code"] == "unknown_check"


def test_errors_alone_do_not_fail_the_exit_code(cli, clean):
    m_broken_link(clean)
    code, env = lint_run(cli, clean["root"])
    assert code == 0 and env["data"]["summary"]["error"] >= 1 and env["data"]["summary"]["block"] == 0


def test_human_output_lists_findings_with_fixes(cli, clean):
    m_broken_link(clean)
    code, out, _ = cli("lint", "--root", str(clean["root"]))
    assert code == 0 and "[error] broken-link" in out and "fix: /feed-wiki" in out


# ---- R2: severity overrides
def test_wiki_severity_override_from_wiki_md(cli, clean):
    m_no_description(clean)
    edit_wiki_md(clean["root"], lint={"severity": {"no-description": "error", "expired": "warn"}})
    m_expired(clean)
    set_front(clean["root"], TOPIC, description="")
    _, env = lint_run(cli, clean["root"])
    assert {f["severity"] for f in found(env, "no-description")} == {"error"}
    assert {f["severity"] for f in found(env, "expired")} == {"warn"}


def test_project_severity_override_for_extraction_checks_only(cli, clean):
    m_failed(clean)
    m_no_description(clean)
    edit_project(clean["root"], lint={"severity": {"extract-failed": "error", "no-description": "info"}})
    _, env = lint_run(cli, clean["root"])
    assert {f["severity"] for f in found(env, "extract-failed")} == {"error"}
    # no-description is a wiki check: project.yaml does not move it (WIKI.md does)
    assert {f["severity"] for f in found(env, "no-description")} == {"warn"}


def test_override_to_block_fails_the_run(cli, clean):
    m_failed(clean)
    edit_project(clean["root"], lint={"severity": {"extract-failed": "block"}})
    code, env = lint_run(cli, clean["root"])
    assert code == 1


def test_policy_violation_downgrade_needs_explicit_allowance(cli, clean):
    """Extraction-located matches follow project.yaml; lowering one needs allow_policy_downgrade."""
    root = clean["root"]
    bad = add_source(root, "w.txt", b"wiki doc", triage="wiki", body=f"has {SECRET}\n")
    edit_project(root, lint={"severity": {"policy-violation": "warn"}}, policy={"block_terms": [SECRET]})
    code, env = lint_run(cli, root)
    assert code == 1 and {f["severity"] for f in found(env, "policy-violation", bad["extract"])} == {"block"}
    assert "lint_policy_downgrade_refused" in {w["code"] for w in env["warnings"]}
    edit_project(root, lint={"severity": {"policy-violation": "warn"}, "allow_policy_downgrade": True})
    code, env = lint_run(cli, root)
    assert code == 0 and {f["severity"] for f in found(env, "policy-violation", bad["extract"])} == {"warn"}


def test_policy_violation_downgrade_in_wiki_md_needs_allowance_too(cli, clean):
    root = clean["root"]
    m_policy(clean)
    edit_wiki_md(root, lint={"severity": {"policy-violation": "info"}})
    code, env = lint_run(cli, root)
    assert code == 1 and {f["severity"] for f in found(env, "policy-violation", TOPIC)} == {"block"}
    assert "lint_policy_downgrade_refused" in {w["code"] for w in env["warnings"]}
    edit_project(root, lint={"allow_policy_downgrade": True})
    code, env = lint_run(cli, root)
    assert code == 0 and {f["severity"] for f in found(env, "policy-violation", TOPIC)} == {"info"}


def test_bad_overrides_are_reported_and_ignored(cli, clean):
    m_failed(clean)
    edit_project(clean["root"], lint={"severity": {"extract-failed": "loud", "made-up": "warn"}})
    code, env = lint_run(cli, clean["root"])
    assert code == 0 and {f["severity"] for f in found(env, "extract-failed")} == {"warn"}
    assert {"lint_bad_severity", "lint_unknown_check"} <= {w["code"] for w in env["warnings"]}


# ---- R3: read-only
def test_lint_never_modifies_content(cli, clean):
    for m in (m_broken_link, m_failed, m_orphan, m_policy):
        m(clean)
    before = tree_snapshot(clean["root"])
    code, env = lint_run(cli, clean["root"])
    after = tree_snapshot(clean["root"])
    changed = {p for p in set(before) | set(after) if before.get(p) != after.get(p)}
    assert changed <= {"_system/state.json", "_system/events.log", "_system/locks/config.lease.json"} and not any(
        p.startswith(("10_sources", "20_extracts", "30_wiki", "outputs")) for p in changed)
    assert env["data"]["summary"]["total"] > 0


def test_dry_run_writes_nothing_at_all(cli, clean):
    before = tree_snapshot(clean["root"])
    code, env, _ = run(cli, clean["root"], "lint", "--dry-run")
    assert code == 0 and tree_snapshot(clean["root"]) == before and env["data"]["last_lint_recorded"] is False


# ---- R6: last_lint
def test_last_lint_records_timestamp_and_counts(cli, clean):
    m_broken_link(clean)
    lint_run(cli, clean["root"])
    state = json.loads((clean["root"] / "_system/state.json").read_text(encoding="utf-8"))
    ll = state["last_lint"]
    assert ll["at"].endswith("Z") and ll["scope"] == "full" and ll["counts"]["error"] == 1
    assert ll["counts"]["total"] == len(found(lint_run(cli, clean["root"])[1], "broken-link"))
    lint_run(cli, clean["root"], "--only", "expired")
    state = json.loads((clean["root"] / "_system/state.json").read_text(encoding="utf-8"))
    assert state["last_lint"]["scope"] == "partial"
    assert state["wiki"]["bundle"] == "30_wiki"  # the rest of the state is untouched


def test_last_lint_skipped_when_config_lease_is_busy(cli, clean):
    leases.acquire(clean["root"], "config", "someone-else", "test", session="s2", host="h2")
    code, env = lint_run(cli, clean["root"])
    assert code == 0 and env["data"]["last_lint_recorded"] is False
    assert "last_lint_not_recorded" in {w["code"] for w in env["warnings"]}
    state = json.loads((clean["root"] / "_system/state.json").read_text(encoding="utf-8"))
    assert state["last_lint"] is None


# ---- governance checks
def test_sign_coverage_only_for_team_profile_and_audit_when_expected(cli, clean):
    root = clean["root"]
    edit_project(root, governance={"audit_expected": True})
    _, env = lint_run(cli, root)
    assert any("auditor" in f["detail"] for f in found(env, "sign-coverage", TOPIC))
    assert not any(f["where"] == clean["source_page"] for f in found(env, "sign-coverage"))
    # personal profile: no sign-off expectations
    cfg = wiki_config.parse_wiki_md(wiki_config.wiki_md_path(root).read_text(encoding="utf-8"))
    cfg.profile = "personal"
    path = wiki_config.wiki_md_path(root)
    path.write_text(wiki_config.replace_block(path.read_text(encoding="utf-8"), cfg), encoding="utf-8")
    _, env = lint_run(cli, root)
    assert found(env, "sign-coverage") == []


def test_duty_overlap_names_actor_and_roles(cli, clean):
    m_duty_overlap(clean)
    _, env = lint_run(cli, clean["root"])
    hit = found(env, "duty-overlap", TOPIC)[0]
    assert "human:member-a" in hit["detail"] and "author" in hit["detail"] and "reviewer" in hit["detail"]


def test_sign_via_engine_then_overlap_is_detected(cli, clean):
    root = clean["root"]
    code, env, _ = run(cli, root, "sign", TOPIC, "--role", "auditor", actor="member-b")
    assert code == 0
    _, env = lint_run(cli, root)
    assert any("member-b" in f["detail"] for f in found(env, "duty-overlap", TOPIC))  # verified + audited


# ---- output-stale
def test_output_stale_detects_changed_page_and_accepts_fresh_hash(cli, clean):
    import hashlib

    root = clean["root"]
    digest = hashlib.sha256((root / TOPIC).read_bytes()).hexdigest()
    add_page(root, "outputs/2026-10-24-brief-topic.md", {"type": "Output", "kind": "brief",
             "based_on": [{"path": TOPIC, "sha256": digest}]}, "# Brief\n")
    (root / "outputs/2026-10-24-faq-topic.html").write_text("<p>faq</p>", encoding="utf-8")
    (root / "outputs/2026-10-24-faq-topic.meta.yaml").write_text(
        yaml.safe_dump({"type": "Output", "based_on": [{"path": TOPIC, "sha256": digest}]}), encoding="utf-8")
    _, env = lint_run(cli, root)
    assert found(env, "output-stale") == []
    append(root, TOPIC, "\nEdited later.\n")
    _, env = lint_run(cli, root)
    assert {f["where"] for f in found(env, "output-stale")} == {"outputs/2026-10-24-brief-topic.md",
                                                                 "outputs/2026-10-24-faq-topic.html"}
    (root / TOPIC).unlink()
    _, env = lint_run(cli, root)
    assert all("no longer exists" in f["detail"] for f in found(env, "output-stale"))


# ---- not a metarepo / unscaffolded wiki
def test_source_checks_run_without_a_wiki(cli, root):
    add_source(root, "a.txt", b"alpha")
    code, env = lint_run(cli, root)
    assert code == 0 and [f["check"] for f in env["data"]["findings"]] == ["not-fed"]


def test_outside_a_metarepo_is_exit_3(cli, tmp_path):
    code, env, _ = cli("lint", "--json", "--root", str(tmp_path))
    assert code == 3


# ---- R5 / AC3: performance
def test_performance_on_a_large_fixture(cli, make_project, tmp_path):
    root = make_project()
    answers = tmp_path / "answers.json"
    answers.write_text(json.dumps(ANSWERS), encoding="utf-8")
    assert run(cli, root, "wiki", "scaffold", "--answers", str(answers))[0] == 0
    sid = None
    for i in range(2000):
        s = add_source(root, f"bulk/doc-{i:04d}.txt", f"document number {i}".encode(), triage="reference" if i else "wiki",
                       body=f"# Doc {i}\n\nText of document {i}.\n")
        sid = sid or s["id"]
    for i in range(1000):
        add_page(root, f"30_wiki/alpha/page-{i:04d}.md", {
            "type": "Concept", "title": f"Page {i}", "description": f"Page {i}.", "status": "stable",
            "sources": [{"id": sid, "title": "d"}], "generated": {"by": "human:a", "at": "2026-10-24T00:00:00Z"}},
            f"# Page {i}\n\nClaim.[^{sid}]\n\nNext: [next](page-{(i + 1) % 1000:04d}.md)\n\n[^{sid}]: d\n")
    started = time.perf_counter()
    code, env = lint_run(cli, root)
    elapsed = time.perf_counter() - started
    assert code == 0 and env["data"]["scanned"] == {"pages": 1001, "extractions": 2000}
    assert elapsed < 10, f"lint took {elapsed:.1f}s"
    # second run uses the hash cache and stays within budget too
    started = time.perf_counter()
    lint_run(cli, root)
    assert time.perf_counter() - started < 10


# ---- drift follows the provenance chain (010/R15 file-back pages cite wiki pages)
def _filed_back(c, name, cites):
    add_page(c["root"], f"30_wiki/alpha/{name}.md", {
        "type": "Concept", "title": name, "description": name, "sources": [
            {"id": f"page-{i}", "title": t, "path": f"{t}.md"} for i, t in enumerate(cites)],
        "verified": {"by": "human:member-b", "at": "2026-10-24T16:00:00Z"}}, f"# {name}\n\nSee [topic](topic.md).\n")


def test_file_back_page_citing_a_grounded_page_is_not_drift(cli, clean):
    _filed_back(clean, "answer", ["topic"])
    _filed_back(clean, "second-hop", ["answer"])
    _, env = lint_run(cli, clean["root"])
    assert found(env, "drift") == []


def test_chain_that_ends_without_a_source_is_drift_and_cycles_terminate(cli, clean):
    add_page(clean["root"], "30_wiki/alpha/ungrounded.md", {
        "type": "Concept", "title": "u", "description": "u", "sources": [{"id": "x", "title": "x", "path": "cycle.md"}],
        "verified": {"by": "human:member-b", "at": "2026-10-24T16:00:00Z"}}, "# u\n\nSee [c](cycle.md).\n")
    add_page(clean["root"], "30_wiki/alpha/cycle.md", {
        "type": "Concept", "title": "c", "description": "c", "sources": [{"id": "y", "title": "y", "path": "ungrounded.md"}],
        "verified": {"by": "human:member-b", "at": "2026-10-24T16:00:00Z"}}, "# c\n\nSee [u](ungrounded.md).\n")
    _, env = lint_run(cli, clean["root"])
    assert {f["where"] for f in found(env, "drift")} == {"30_wiki/alpha/ungrounded.md", "30_wiki/alpha/cycle.md"}


# ---- source-unregistered vs. real intake output (archived containers, 002/R4) and Unicode names (P10)
def test_archived_bundle_flow_leaves_no_unregistered_sources(cli, root, tmp_path):
    import test_intake as ti

    z = ti.zipped(root / "00_inbox" / "pack.zip", {"one.txt": "one text", "two.txt": "two text"})
    ti.inbox(root, "solo.txt", ti.DOC)
    staging = tmp_path / "staging"
    staging.mkdir()
    nonce = ti.acquire(cli, root)
    code, env, _ = ti.run(cli, root, "unbundle", "00_inbox/pack.zip", "--staging", str(staging), "--provisional")
    m = env["data"]
    for r in m["members"]:
        assert ti.place(cli, root, nonce, r["staged"], "--category", "packs", "--bundle-slug", "pack",
                        "--member-path", r["member_path"], "--group", "pack", "--parent", m["container"]["id"])[0] == 0
    members = staging / "members.json"
    members.write_text(json.dumps([{"id": r["id"], "member_path": r["member_path"], "decision": "wiki"}
                                   for r in m["members"]]))
    assert ti.place(cli, root, nonce, "00_inbox/pack.zip", "--container", "--members", str(members),
                    decision="reference")[0] == 0
    assert ti.place(cli, root, nonce, "00_inbox/solo.txt", "--category", "misc")[0] == 0
    assert (root / "10_sources/_bundles/pack.zip").is_file()
    code, env = lint_run(cli, root)
    assert code == 0
    assert not [f for f in env["data"]["findings"] if f["severity"] in ("block", "error", "warn")], env["data"]["findings"]


def test_decomposed_unicode_source_name_is_not_unregistered(cli, root):
    import unicodedata

    name = unicodedata.normalize("NFD", "caf\u00e9.txt")
    src = add_source(root, name, b"coffee", triage="reference")
    registered = root / src["extract"]
    doc = frontmatter.read(registered)
    doc.front["source_path"] = unicodedata.normalize("NFC", doc.front["source_path"])
    frontmatter.write(registered, doc)
    _, env = lint_run(cli, root)
    assert found(env, "source-unregistered") == []
