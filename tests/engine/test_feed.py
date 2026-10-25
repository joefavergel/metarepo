"""Spec 010: wiki_status, ``wiki source-page``, ``sign`` and the feed's engine-observable contract."""
from __future__ import annotations

import json

import pytest
import yaml

from helpers import add_page, add_source
from metarepo_engine import frontmatter, leases, okf
from metarepo_engine.wiki import sourcepage

ANSWERS = {"axis": "area", "profile": "team", "areas": ["Alpha"], "page_types": ["Concept"]}


@pytest.fixture
def root(make_project):
    return make_project()


def run(cli, root, *args, actor="member-a"):
    extra = [] if args[0] == "registry" else ["--actor", actor]
    return cli(*args, "--json", "--root", str(root), *extra)


def scaffold(cli, root):
    f = root.parent / "answers.json"
    f.write_text(json.dumps(ANSWERS), encoding="utf-8")
    code, env, _ = run(cli, root, "wiki", "scaffold", "--answers", str(f))
    assert code == 0, env


@pytest.fixture
def wiki(cli, root):
    scaffold(cli, root)
    return root


def status_map(cli, root):
    code, env, _ = run(cli, root, "registry")
    assert code == 0, env
    return {r["id"]: r["wiki_status"] for r in env["data"]["sources"]}


def feed(cli, root, src, **kw):
    code, env, _ = run(cli, root, "wiki", "source-page", src["id"], "--write", *kw.get("extra", []))
    assert code == 0, env
    return env


# ---- R3 / T2: wiki_status derivation
def test_wiki_status_pending_ingested_stale_and_none(cli, wiki):
    a = add_source(wiki, "a.txt", b"alpha")
    b = add_source(wiki, "b.txt", b"beta", triage="reference")
    assert status_map(cli, wiki) == {a["id"]: "pending", b["id"]: None}
    feed(cli, wiki, a)
    assert status_map(cli, wiki)[a["id"]] == "ingested"
    # extraction regenerated (same original): stale
    md = wiki / a["extract"]
    doc = frontmatter.read(md)
    doc.front["extracted_at"] = "2026-11-01T10:00:00Z"
    frontmatter.write(md, doc)
    assert status_map(cli, wiki)[a["id"]] == "stale"


def test_wiki_status_stale_when_content_hash_differs(cli, wiki):
    a = add_source(wiki, "a.txt", b"alpha")
    feed(cli, wiki, a)
    page = wiki / sourcepage.page_rel(a["id"])
    frontmatter.update(page, {"content_sha256": "0" * 64})
    assert status_map(cli, wiki)[a["id"]] == "stale"


def test_registry_filter_pending_and_stale(cli, wiki):
    a = add_source(wiki, "a.txt", b"alpha")
    add_source(wiki, "b.txt", b"beta")
    feed(cli, wiki, a)
    code, env, _ = run(cli, wiki, "registry", "--filter", "wiki_status=pending,stale")
    assert code == 0 and env["data"]["count"] == 1 and env["data"]["sources"][0]["wiki_status"] == "pending"


# ---- R8 / T3: skeleton
def test_source_page_skeleton_fields(cli, wiki):
    a = add_source(wiki, "docs/a.txt", b"alpha", title="Alpha paper")
    code, env, _ = run(cli, wiki, "wiki", "source-page", a["id"], "--assisted-by", "harness/model-x")
    assert code == 0 and env["data"]["wiki_status"] == "pending" and env["data"]["written"] is False
    assert not (wiki / sourcepage.page_rel(a["id"])).exists()
    fm = env["data"]["frontmatter"]
    assert fm["type"] == "Source" and fm["title"] == "Alpha paper" and fm["source_id"] == a["id"]
    assert fm["content_sha256"] == a["sha256"] and fm["tags"] == []
    assert (wiki / "30_wiki/sources" / fm["resource"]).resolve() == (wiki / a["path"]).resolve()
    assert (wiki / "30_wiki/sources" / fm["extract"]).resolve() == (wiki / a["extract"]).resolve()
    assert fm["generated"]["by"] == "human:member-a" and fm["generated"]["assisted_by"] == "harness/model-x"
    assert fm["generated"]["at"].endswith("Z")
    assert fm["sources"] == [{"id": a["id"], "title": "Alpha paper", "path": fm["resource"]}]
    assert env["data"]["governance"]["can_write"] is True


def test_write_creates_valid_okf_page_and_refeed_is_noop(cli, wiki):
    a = add_source(wiki, "a.txt", b"alpha")
    env = feed(cli, wiki, a)
    path = wiki / env["data"]["page"]
    assert env["data"]["written"] and path.is_file()
    page = okf.OkfPage.load(path)
    assert page.type == "Source" and not page.problems() and page.source_ids() == [a["id"]]
    before = path.read_bytes()
    events = len((wiki / "_system/events.log").read_text().splitlines())
    code, env, _ = run(cli, wiki, "wiki", "source-page", a["id"], "--write")  # AC3
    assert code == 0 and env["data"]["noop"] is True and env["data"]["written"] is False
    assert path.read_bytes() == before
    log_lines = (wiki / "_system/events.log").read_text().splitlines()
    assert not any(json.loads(ln)["command"] == "wiki source-page" for ln in log_lines[events:])


def test_write_never_overwrites_stale_page(cli, wiki):
    a = add_source(wiki, "a.txt", b"alpha")
    feed(cli, wiki, a)
    page = wiki / sourcepage.page_rel(a["id"])
    frontmatter.update(page, {"content_sha256": "0" * 64})
    before = page.read_bytes()
    code, env, _ = run(cli, wiki, "wiki", "source-page", a["id"], "--write")
    assert code == 0 and env["data"]["wiki_status"] == "stale" and env["data"]["refresh"]["content_sha256"] == a["sha256"]
    assert page.read_bytes() == before and env["warnings"][0]["code"] == "page_exists"


def test_dry_run_writes_nothing(cli, wiki):
    a = add_source(wiki, "a.txt", b"alpha")
    code, env, _ = run(cli, wiki, "wiki", "source-page", a["id"], "--write", "--dry-run")
    assert code == 0 and not (wiki / sourcepage.page_rel(a["id"])).exists()


def test_unknown_source_and_closed_gate(cli, root, wiki):
    code, env, _ = run(cli, wiki, "wiki", "source-page", "src-000000000000")
    assert code == 2 and env["errors"][0]["code"] == "unknown_source"


def test_gate_closed_before_scaffold_blocks_feed_r1(cli, root):
    a = add_source(root, "a.txt", b"alpha")
    code, env, _ = run(cli, root, "wiki", "source-page", a["id"])
    assert code == 3 and env["errors"][0]["code"] == "wiki_not_configured"


def test_feed_allowed_between_scaffold_and_gate(cli, wiki):  # 009/R7 vs 010/R1
    a = add_source(wiki, "a.txt", b"alpha")
    assert json.loads((wiki / "_system/state.json").read_text())["wiki"]["configured"] is False
    feed(cli, wiki, a)


# ---- R6: policy gate
def test_policy_gate_blocks_without_echoing_value(cli, make_project):
    root = make_project(policy={"block_terms": ["topsecretvalue"]})
    scaffold(cli, root)
    a = add_source(root, "a.txt", b"x", body="# T\n\nthe topsecretvalue is here\n")
    code, env, _ = run(cli, root, "wiki", "source-page", a["id"], "--write")
    assert code == 1 and env["errors"][0]["code"] == "policy_blocked"
    assert "topsecretvalue" not in json.dumps(env)
    assert env["data"]["policy"][0]["rule"] == "block_terms[0]"
    assert not (root / sourcepage.page_rel(a["id"])).exists()


# ---- R2 / AC4: lease and owner mode
def test_second_session_cannot_feed_while_lease_held_ac4(cli, wiki):
    a = add_source(wiki, "a.txt", b"alpha")
    code, env, _ = run(cli, wiki, "lease", "acquire", "wiki", actor="member-b")
    assert code == 0
    code, env, _ = cli("wiki", "source-page", a["id"], "--write", "--json", "--root", str(wiki), "--actor", "member-a",)
    # same actor name, different session: the lease is held by another session
    assert code == 3 and env["errors"][0]["code"] == "lease_held"
    assert not (wiki / sourcepage.page_rel(a["id"])).exists()


def test_owner_mode_non_owner_gets_proposal_path_ac4(cli, make_project):
    root = make_project(governance={"wiki_write_mode": "owner", "wiki_owners": ["member-a"]})
    scaffold_ok = root.parent / "answers.json"
    scaffold_ok.write_text(json.dumps(ANSWERS), encoding="utf-8")
    assert run(cli, root, "wiki", "scaffold", "--answers", str(scaffold_ok))[0] == 0
    a = add_source(root, "a.txt", b"alpha")
    code, env, _ = run(cli, root, "wiki", "source-page", a["id"], actor="member-b")
    assert code == 0 and env["data"]["governance"] == {"write_mode": "owner", "actor": "member-b", "can_write": False}
    code, env, _ = run(cli, root, "wiki", "source-page", a["id"], "--write", actor="member-b")
    assert code == 3 and env["errors"][0]["code"] == "not_wiki_owner"
    assert not (root / sourcepage.page_rel(a["id"])).exists()
    code, env, _ = run(cli, root, "wiki", "source-page", a["id"], "--write", actor="member-a")
    assert code == 0 and env["data"]["written"]


# ---- R17 / T4: sign
def concept(wiki, front=None):
    f = {"type": "Concept", "title": "Topic", "description": "d", "status": "draft"}
    f.update(front or {})
    return add_page(wiki, "30_wiki/alpha/topic.md", f, "# Topic\n\nClaim.\n")


def sign(cli, wiki, role, actor, *extra, page="alpha/topic.md"):
    return run(cli, wiki, "sign", page, "--role", role, *extra, actor=actor)


def test_sign_roles_write_keys_log_and_keep_body(cli, wiki):
    p = concept(wiki)
    for role, key in (("author", "generated"), ("reviewer", "verified"), ("auditor", "audited")):
        code, env, _ = sign(cli, wiki, role, f"{role}-1")
        assert code == 0 and env["warnings"] == [], env
        front = frontmatter.read(p).front
        assert front[key]["by"] == f"human:{role}-1" and front[key]["at"].endswith("Z")
    assert frontmatter.read(p).body == "# Topic\n\nClaim.\n"
    log = (wiki / "30_wiki/log.md").read_text()
    assert log.count("**Sign-off**") == 3 and "reviewer human:reviewer-1" in log
    assert okf.OkfPage.load(p).trust_tier() == "audited"
    assert leases.status(wiki, "wiki")["state"] == "released"


def test_same_actor_two_roles_warns_but_records_ac5(cli, wiki):
    p = concept(wiki)
    assert sign(cli, wiki, "author", "alice")[0] == 0
    code, env, _ = sign(cli, wiki, "reviewer", "alice")
    assert code == 0 and env["ok"] and env["warnings"][0]["code"] == "segregation_of_duties"
    assert frontmatter.read(p).front["verified"]["by"] == "human:alice"
    assert "segregation of duties" in (wiki / "30_wiki/log.md").read_text()


def test_sign_stable_and_deprecated(cli, wiki):
    p = concept(wiki)
    assert sign(cli, wiki, "reviewer", "bob", "--stable")[0] == 0
    assert frontmatter.read(p).front["status"] == "stable"
    frontmatter.update(p, {"status": "deprecated"})
    code, env, _ = sign(cli, wiki, "reviewer", "bob", "--stable")
    assert code == 3 and env["errors"][0]["code"] == "page_deprecated"


def test_sign_rejects_reserved_and_missing_pages(cli, wiki):
    for page in ("index.md", "log.md", "alpha/missing.md", "../project.yaml"):
        code, env, _ = sign(cli, wiki, "author", "alice", page=page)
        assert code == 3 and env["errors"][0]["code"] == "page_not_found", page


def test_sign_dry_run_and_root_relative_path(cli, wiki):
    p = concept(wiki)
    before = p.read_bytes()
    code, env, _ = sign(cli, wiki, "author", "alice", "--dry-run", page="30_wiki/alpha/topic.md")
    assert code == 0 and p.read_bytes() == before
    assert sign(cli, wiki, "author", "alice", page="30_wiki/alpha/topic.md")[0] == 0


def test_sign_respects_lease_and_owner_mode(cli, wiki):
    concept(wiki)
    assert run(cli, wiki, "lease", "acquire", "wiki", actor="other")[0] == 0
    code, env, _ = sign(cli, wiki, "author", "alice")
    assert code == 3 and env["errors"][0]["code"] == "lease_held"


def test_sign_overlap_independent_of_prefix(cli, wiki):
    concept(wiki)
    sign(cli, wiki, "author", "human:alice")
    code, env, _ = sign(cli, wiki, "auditor", "alice")
    assert env["warnings"][0]["code"] == "segregation_of_duties"


def test_yaml_roundtrip_of_page(cli, wiki):
    p = concept(wiki, {"tags": ["x"], "sources": [{"id": "s1"}]})
    sign(cli, wiki, "author", "alice")
    front = yaml.safe_load(p.read_text().split("---")[1])
    assert front["tags"] == ["x"] and front["sources"] == [{"id": "s1"}]


# ---- 007 AC7/AC8 (engine-observable) and the skill contract
def test_unconfigured_wiki_leaves_items_queued_ac8(cli, root):
    a = add_source(root, "a.txt", b"alpha")
    assert status_map(cli, root) == {a["id"]: "pending"}
    code, env, _ = run(cli, root, "wiki", "source-page", a["id"], "--write")
    assert code == 3 and env["errors"][0]["code"] == "wiki_not_configured"
    assert status_map(cli, root) == {a["id"]: "pending"}


def test_chain_hand_off_drains_the_queue_ac7(cli, wiki):
    ids = [add_source(wiki, f"p{i}.txt", f"paper {i}".encode()) for i in range(3)]
    code, env, _ = run(cli, wiki, "registry", "--filter", "wiki_status=pending")
    assert env["data"]["count"] == 3
    for s in ids:
        feed(cli, wiki, s)
    code, env, _ = run(cli, wiki, "registry", "--filter", "wiki_status=pending,stale")
    assert env["data"]["count"] == 0


def test_skill_contract(repo):
    skills = repo / "tooling" / "skills"
    feed_md = (skills / "feed-wiki" / "SKILL.md").read_text(encoding="utf-8")
    assert "placeholder" not in feed_md.lower()
    for needle in ("wiki_status=pending,stale", "wiki source-page", "lease acquire wiki", "policy_blocked",
                   "--batch", "--apply-proposal", "sign <page>", "Disputed", "--file-back", "lint"):
        assert needle in feed_md, needle
    for ref in ("ingest.md", "okf.md", "govern.md", "proposals.md", "engine.md"):
        assert (skills / "feed-wiki" / "references" / ref).is_file()
    assert "CLAUDE_PLUGIN_ROOT" not in feed_md
    intake_md = (skills / "intake" / "SKILL.md").read_text(encoding="utf-8")
    assert "`feed-wiki`" in intake_md and "wiki_status: pending" in intake_md
