"""Spec 011: /consult-wiki skill structure, consumer contract and the read-only guarantee (R1-R11, AC1-AC5)."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from helpers import add_page, add_source, tree_snapshot
from metarepo_engine import okf
from metarepo_engine.wiki import gate

SKILL = Path(__file__).resolve().parents[2] / "tooling" / "skills" / "consult-wiki"
TEMPLATE = Path(__file__).resolve().parents[2] / "tooling" / "templates" / "wiki" / "answer-from-wiki.md"


def flat(path: Path) -> str:
    return " ".join(path.read_text(encoding="utf-8").split())


def test_skill_is_implemented_and_covers_every_requirement():
    body = flat(SKILL / "SKILL.md")
    low = body.lower()
    assert "placeholder" not in low and "CLAUDE_PLUGIN_ROOT" not in body
    for needle in ("status --json", "wiki_configured", "/init-wiki",            # R1
                   "index.md", "search", "--scope wiki",                        # R2
                   "--scope extracts", "section", "literal",                    # R3
                   "10_sources/", "explicitly ask",                             # R4
                   "locator", "relative link",                                  # R5
                   "draft", "stale_after", "verified", "wiki_status=stale",     # R6
                   "contradiction", "both sides",                               # R6/AC4
                   "not covered", "00_inbox/",                                  # R7
                   "/feed-wiki", "two declines",                                # R8
                   "repos path",                                                # R9
                   "read-only", "no lease",                                     # R1
                   "walk recoverable", "structured trace"):                     # R11
        assert needle.lower() in low, needle
    assert (SKILL / "references" / "query.md").is_file() and (SKILL / "references" / "engine.md").is_file()


def test_skill_description_is_a_single_line_with_trigger():
    head = (SKILL / "SKILL.md").read_text(encoding="utf-8").split("---")[1]
    assert head.count("name: consult-wiki") == 1 and "/consult-wiki" in head


def test_consumer_contract_is_a_valid_skill_page_restating_the_ladder():
    page = okf.OkfPage.load(TEMPLATE)
    assert page.type == "Skill"
    body = flat(TEMPLATE).lower()
    for needle in ("read-only", "honesty", "index.md", "footnotes", "20_extracts/", "10_sources/",
                   "explicit request", "relative markdown link", "locator", "not covered", "contradiction"):
        assert needle in body, needle


# ---- AC5: a consultation changes nothing in the metarepository

@pytest.fixture
def wiki_project(make_project, cli):
    root = make_project()
    add_page(root, "30_wiki/index.md", {"type": "Index", "title": "Index"}, "# Index\n\n- [Alpha](a/alpha.md)\n")
    add_page(root, "30_wiki/a/alpha.md", {"type": "Concept", "title": "Alpha", "status": "draft"},
             "# Alpha\n\nAlpha is documented.[^s1]\n")
    add_source(root, "docs/one.txt", b"one", title="One", body="# One\n\n## Page 2\n\nThe code is 4711.\n")
    state = root / "_system" / "state.json"
    data = json.loads(state.read_text(encoding="utf-8"))
    data["wiki"]["configured"] = True
    state.write_text(json.dumps(data), encoding="utf-8")
    return root


def test_consult_command_sequence_leaves_the_tree_byte_identical(cli, wiki_project):
    root = wiki_project
    before = tree_snapshot(root)
    digest = lambda snap: hashlib.sha256(  # noqa: E731
        b"".join(k.encode() + b"\0" + v for k, v in sorted(snap.items()))).hexdigest()
    for argv in (("status",), ("search", "alpha", "--scope", "wiki"), ("search", "4711", "--scope", "extracts"),
                 ("search", "nothing-here-xyz", "--scope", "all"), ("registry",)):
        code = cli(*argv, "--json", "--root", str(root))[0]
        assert code == 0, argv
    after = tree_snapshot(root)
    assert after == before and digest(after) == digest(before)


def test_literal_question_is_answered_with_extract_path_and_locator(cli, wiki_project):
    code, env, _ = cli("search", "4711", "--scope", "extracts", "--json", "--root", str(wiki_project))
    hit = env["data"]["results"][0]
    assert code == 0 and hit["path"] == "20_extracts/docs/one.txt.md"
    assert hit["section"] == "Page 2" and "4711" in hit["snippet"]  # AC2: locator without opening the original


def test_unanswerable_question_finds_nothing(cli, wiki_project):
    env = cli("search", "unobtainium", "--json", "--root", str(wiki_project))[1]
    assert env["data"]["count"] == 0  # AC3: the skill then answers "not covered"


def test_gate_closed_blocks_consult(make_project):
    from metarepo_engine.jsonout import EngineError
    from metarepo_engine.root import Project

    root = make_project()
    with pytest.raises(EngineError) as exc:
        gate.require_configured(Project(root))
    assert exc.value.code == "wiki_not_configured"
