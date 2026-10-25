"""BM25 search with a disposable cache (003 T2.2, R12)."""
import json
import os
from pathlib import Path

import pytest

from helpers import add_page, add_source
from metarepo_engine import search as s


@pytest.fixture
def project(make_project):
    root = make_project()
    add_page(root, "30_wiki/index.md", {"type": "Index", "title": "Index"}, "# Index\n\nOverview of everything.\n")
    add_page(root, "30_wiki/topics/cats.md",
             {"type": "Concept", "title": "Felines", "description": "Small domestic predators", "tags": ["cats"]},
             "# Felines\n\n## Behaviour\n\nCats sleep a lot. Cats groom themselves often.\n\n## Diet\n\nMeat.\n")
    add_page(root, "30_wiki/topics/dogs.md", {"type": "Concept", "title": "Canines"},
             "# Canines\n\nDogs are loyal. A dog may chase a cat.\n")
    add_page(root, "30_wiki/topics/cafe.md", {"type": "Place", "title": "Café guide"},
             "# Café guide\n\nThe café serves éclairs.\n")
    add_page(root, "30_wiki/topics/cjk.md", {"type": "Concept", "title": "中文文档"},
             "# 中文文档\n\n这是一个关于猫哪的文档。\n")
    add_source(root, "papers/feline-study.pdf", b"study bytes", title="Feline study",
               body="# Feline study\n\n## Page 1\n\nCats were observed for twelve weeks.\n\n## Page 2\n\nResults vary.\n")
    return root


def paths(env):
    return [r["path"] for r in env["data"]["results"]]


def run(cli, project, *argv):
    return cli("search", *argv, "--json", "--root", str(project))


def test_ranking_prefers_title_and_frequency(cli, project):
    code, env = run(cli, project, "cats")[:2]
    assert code == 0 and env["data"]["count"] >= 2
    assert paths(env)[0] == "30_wiki/topics/cats.md"  # tag + body frequency
    scores = [r["score"] for r in env["data"]["results"]]
    assert scores == sorted(scores, reverse=True)


def test_title_boost_beats_body_mention(cli, project):
    env = run(cli, project, "felines")[1]
    assert paths(env)[0] == "30_wiki/topics/cats.md"  # title
    env = run(cli, project, "feline")[1]  # exact-token only: 'feline' is the extraction's title, not 'felines'
    assert paths(env) == ["20_extracts/papers/feline-study.pdf.md"]


def test_multi_word_query_and_idf(cli, project):
    env = run(cli, project, "cats", "groom")[1]
    assert paths(env)[0] == "30_wiki/topics/cats.md"
    env_rare = run(cli, project, "twelve", "cats")[1]
    assert paths(env_rare)[0] == "20_extracts/papers/feline-study.pdf.md"  # rare term outweighs the common one


def test_scope_and_type_filters(cli, project):
    assert all(p.startswith("30_wiki/") for p in paths(run(cli, project, "cats", "--scope", "wiki")[1]))
    assert all(p.startswith("20_extracts/") for p in paths(run(cli, project, "cats", "--scope", "extracts")[1]))
    only_places = run(cli, project, "café", "--type", "place")[1]
    assert paths(only_places) == ["30_wiki/topics/cafe.md"]
    assert run(cli, project, "cats", "--type", "Place")[1]["data"]["count"] == 0
    extracts = run(cli, project, "cats", "--type", "Extract")[1]
    assert paths(extracts) == ["20_extracts/papers/feline-study.pdf.md"]


def test_limit(cli, project):
    assert run(cli, project, "cats", "dogs", "-n", "1")[1]["data"]["count"] == 1
    code, env = run(cli, project, "cats", "-n", "0")[:2]
    assert code == 2 and env["errors"][0]["code"] == "bad_limit"


def test_accent_and_case_insensitive(cli, project):
    for q in ("cafe", "CAFÉ", "Cafe", "café", "eclairs", "ÉCLAIRS"):
        assert paths(run(cli, project, q)[1]) == ["30_wiki/topics/cafe.md"], q


def test_cjk_bigrams(cli, project):
    assert paths(run(cli, project, "文档")[1])[0] == "30_wiki/topics/cjk.md"
    assert paths(run(cli, project, "猫哪")[1]) == ["30_wiki/topics/cjk.md"]
    assert s.tokenize("abc中文def") == ["abc", "中文", "def"]
    assert s.tokenize("猫") == ["猫"]
    assert s.tokenize("Hello, Wörld! x_y 42") == ["hello", "world", "x", "y", "42"]


def test_snippet_section_and_line(cli, project):
    env = run(cli, project, "groom")[1]
    hit = env["data"]["results"][0]
    assert hit["path"] == "30_wiki/topics/cats.md" and "groom" in hit["snippet"]
    assert hit["section"] == "Behaviour"
    lines = (project / hit["path"]).read_text(encoding="utf-8").splitlines()
    assert "groom" in lines[hit["line"] - 1]  # 1-based line number in the file, frontmatter included
    ext = run(cli, project, "twelve")[1]["data"]["results"][0]
    assert ext["section"] == "Page 1" and ext["type"] == "Extract"


def test_no_results_and_empty_queries(cli, project):
    code, env = run(cli, project, "zzzzqqqq")[:2]
    assert code == 0 and env["data"]["count"] == 0
    code, env = run(cli, project, "!!!", "...")[:2]
    assert code == 2 and env["errors"][0]["code"] == "empty_query"


def test_results_are_deterministic(cli, project):
    first = run(cli, project, "cats", "dogs", "feline")[1]["data"]["results"]
    second = run(cli, project, "cats", "dogs", "feline")[1]["data"]["results"]
    assert first == second


def test_cache_lives_outside_the_tree_and_is_reused(cli, project):
    env = run(cli, project, "cats")[1]
    index = env["data"]["index"]
    assert index["rebuilt"] == 6 and index["reused"] == 0
    cache = Path(s.local_cache_dir("7f3c1b2a-5d4e-4f6a-9b8c-0123456789ab") / s.CACHE_NAME)
    assert cache.is_file() and project not in cache.parents
    again = run(cli, project, "cats")[1]["data"]["index"]
    assert again["rebuilt"] == 0 and again["reused"] == 6


def test_cache_invalidates_on_change_and_deletion(cli, project):
    run(cli, project, "cats")  # warm the cache
    page = project / "30_wiki" / "topics" / "dogs.md"
    page.write_text(page.read_text(encoding="utf-8") + "\nA zebra appeared.\n", encoding="utf-8")
    env = run(cli, project, "zebra")[1]
    assert paths(env) == ["30_wiki/topics/dogs.md"] and env["data"]["index"]["rebuilt"] == 1
    page.unlink()
    env = run(cli, project, "zebra")[1]
    assert paths(env) == [] and env["data"]["index"]["removed"] == 1


def test_cache_invalidation_with_identical_size(cli, project):
    page = project / "30_wiki" / "topics" / "dogs.md"
    text = page.read_text(encoding="utf-8")
    run(cli, project, "dogs")
    swapped = text.replace("loyal", "tidal")  # same length
    assert len(swapped) == len(text)
    page.write_text(swapped, encoding="utf-8")
    st = page.stat()
    os.utime(page, ns=(st.st_atime_ns, st.st_mtime_ns + 7_000_000_000))
    assert paths(run(cli, project, "tidal")[1]) == ["30_wiki/topics/dogs.md"]
    assert paths(run(cli, project, "loyal")[1]) == []


def test_corrupt_cache_is_ignored(cli, project):
    run(cli, project, "cats")
    cache = s.local_cache_dir("7f3c1b2a-5d4e-4f6a-9b8c-0123456789ab") / s.CACHE_NAME
    cache.write_text("{broken", encoding="utf-8")
    env = run(cli, project, "cats")[1]
    assert env["data"]["index"]["rebuilt"] == 6 and paths(env)[0] == "30_wiki/topics/cats.md"
    cache.write_text(json.dumps({"version": 999, "docs": {}}), encoding="utf-8")
    assert run(cli, project, "cats")[1]["data"]["index"]["rebuilt"] == 6


def test_scopes_share_one_cache_without_clobbering(cli, project):
    run(cli, project, "cats", "--scope", "wiki")
    env = run(cli, project, "cats", "--scope", "extracts")[1]
    assert env["data"]["index"]["rebuilt"] == 1
    env = run(cli, project, "cats", "--scope", "all")[1]
    assert env["data"]["index"] == {"documents": 6, "reused": 6, "rebuilt": 0, "removed": 0}


def test_pages_without_frontmatter_or_with_broken_frontmatter_are_still_searchable(cli, project):
    add_page(project, "30_wiki/plain.md", None, "# Plain page\n\nwombat facts\n")
    (project / "30_wiki" / "broken.md").write_text("---\nunclosed\nplatypus facts\n", encoding="utf-8")
    assert paths(run(cli, project, "wombat")[1]) == ["30_wiki/plain.md"]
    assert paths(run(cli, project, "platypus")[1]) == ["30_wiki/broken.md"]


def test_hidden_and_non_markdown_files_are_not_indexed(cli, project):
    (project / "30_wiki" / ".hidden.md").write_text("quokka\n", encoding="utf-8")
    (project / "30_wiki" / "data.json").write_text('{"quokka": 1}', encoding="utf-8")
    (project / "30_wiki" / ".tmpdir").mkdir()
    (project / "30_wiki" / ".tmpdir" / "x.md").write_text("quokka\n", encoding="utf-8")
    assert run(cli, project, "quokka")[1]["data"]["count"] == 0


def test_empty_project_searches_cleanly(cli, make_project):
    root = make_project()
    code, env = cli("search", "anything", "--json", "--root", str(root))[:2]
    assert code == 0 and env["data"]["count"] == 0 and env["data"]["index"]["documents"] == 0


def test_bm25_scoring_formula_on_a_tiny_corpus():
    docs = [s.IndexedDoc("a.md", "wiki", 1, 1, {"x": 2.0}, 2.0, "a", None, None),
            s.IndexedDoc("b.md", "wiki", 1, 1, {"y": 1.0}, 1.0, "b", None, None)]
    (hit,) = s.rank(docs, "x")
    import math

    idf = math.log(1 + (2 - 1 + 0.5) / (1 + 0.5))
    avgdl = 1.5
    expected = idf * 2.0 * (s.K1 + 1) / (2.0 + s.K1 * (1 - s.B + s.B * 2.0 / avgdl))
    assert hit.score == pytest.approx(expected, rel=1e-5)
    assert s.rank(docs, "") == [] and s.rank([], "x") == []
