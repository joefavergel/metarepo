"""Registry (003 T2.1; 002/AC4) and the golden JSON test."""
import csv
import io
import json
from pathlib import Path

import pytest

from golden import assert_golden
from helpers import add_source, tree_snapshot
from metarepo_engine import registry as reg


@pytest.fixture
def project(make_project):
    root = make_project()
    add_source(root, "papers/alpha.pdf", b"alpha bytes", title="Alpha Paper", media_type="application/pdf")
    add_source(root, "papers/beta.md", b"beta bytes", title="Beta Notes", triage="reference", quality="partial",
               media_type="text/markdown")
    add_source(root, "data/gamma.csv", b"gamma bytes", title="Gamma", triage="defer", quality="failed",
               extra_front={"bundle": {"parent_id": "src-abcdefabcdef", "group": None, "member_path": "x/gamma.csv"}})
    return root


def test_rows_are_derived_from_extraction_frontmatter(project):
    view = reg.build(project)
    by_name = {Path(r["path"]).name: r for r in view.rows}
    alpha = by_name["alpha.pdf"]
    assert alpha["id"].startswith("src-") and len(alpha["id"]) == 16
    assert alpha["path"] == "10_sources/papers/alpha.pdf" and alpha["extract"] == "20_extracts/papers/alpha.pdf.md"
    assert alpha["triage"] == "wiki" and alpha["quality"] == "high" and alpha["extractor"] == "plain"
    assert by_name["gamma.csv"]["parent_id"] == "src-abcdefabcdef"
    assert view.warnings == [] and view.duplicates() == []
    assert sorted(view.known_ids()) == sorted(r["id"] for r in view.rows)


def test_rows_sorted_by_id_then_path(project):
    ids = [r["id"] for r in reg.build(project).rows]
    assert ids == sorted(ids)


def test_filters(project, cli):
    code, env, _ = cli("registry", "--filter", "triage=wiki,reference", "--json", "--root", str(project))
    assert code == 0 and env["data"]["count"] == 2
    code, env, _ = cli("registry", "--filter", "triage=wiki,reference", "--filter", "quality=partial", "--json",
                       "--root", str(project))
    assert [Path(r["path"]).name for r in env["data"]["sources"]] == ["beta.md"]
    code, env, _ = cli("registry", "--filter", "media_type=application/pdf", "--json", "--root", str(project))
    assert env["data"]["count"] == 1
    code, env, _ = cli("registry", "--filter", "quality=nonexistent", "--json", "--root", str(project))
    assert env["data"]["count"] == 0


@pytest.mark.parametrize("bad,needle", [("nokey", "expected key=value"), ("nope=1", "unknown filter key"),
                                        ("triage=", "expected key=value")])
def test_bad_filters(project, cli, bad, needle):
    code, env, _ = cli("registry", "--filter", bad, "--json", "--root", str(project))
    assert code == 2 and env["errors"][0]["code"] == "bad_filter" and needle in env["errors"][0]["message"]


def test_extra_columns_hook_for_later_specs(project, monkeypatch, cli):
    monkeypatch.setitem(reg.EXTRA_COLUMNS, "wiki_status", lambda root, row: "pending")
    code, env, _ = cli("registry", "--filter", "wiki_status=pending", "--json", "--root", str(project))
    assert code == 0 and env["data"]["count"] == 3 and env["data"]["sources"][0]["wiki_status"] == "pending"


def test_csv_format(project, cli):
    code, out, _ = cli("registry", "--format", "csv", "--root", str(project))
    rows = list(csv.DictReader(io.StringIO(out)))
    assert code == 0 and len(rows) == 3 and set(rows[0]) == set(reg.COLUMNS) | {"wiki_status"}
    code, env, _ = cli("registry", "--format", "csv", "--json", "--root", str(project))
    assert env["data"]["csv"].startswith("id,path,sha256")


def test_unreadable_and_foreign_files_become_warnings_not_crashes(project, cli):
    (project / "20_extracts" / "broken.md").write_text("---\nnever closed\n", encoding="utf-8")
    (project / "20_extracts" / "note.md").write_text("---\ntype: Concept\n---\nnot an extraction\n", encoding="utf-8")
    (project / "20_extracts" / "partial.md").write_text("---\ntype: Extract\nsource_id: src-1\n---\n", encoding="utf-8")
    (project / "20_extracts" / "papers" / "alpha.pdf.part-002.md").write_text("continuation text\n", encoding="utf-8")
    code, env, _ = cli("registry", "--json", "--root", str(project))
    assert code == 0 and env["data"]["count"] == 3
    assert len(env["warnings"]) == 3 and all(w["code"] == "extraction_unreadable" for w in env["warnings"])


def test_duplicates_by_hash_are_reported(project):
    add_source(project, "copies/alpha-copy.pdf", b"alpha bytes", title="Alpha again")
    view = reg.build(project)
    assert view.duplicates() == [["10_sources/copies/alpha-copy.pdf", "10_sources/papers/alpha.pdf"]]


def test_ac4_registry_never_edits_the_project_and_rebuilds_from_scratch(project, cli, tmp_path):
    before = tree_snapshot(project)
    code, env, _ = cli("registry", "--write", "--json", "--root", str(project))
    assert code == 0 and tree_snapshot(project) == before  # derived view: nothing written into the tree
    cache_files = [Path(p) for p in env["data"]["cache"]]
    assert all(p.is_file() and project not in p.parents for p in cache_files)
    first = json.loads(cache_files[0].read_text(encoding="utf-8"))
    for p in cache_files:  # delete the whole local cache: nothing is lost
        p.unlink()
    code, env, _ = cli("registry", "--write", "--json", "--root", str(project))
    assert json.loads(Path(env["data"]["cache"][0]).read_text(encoding="utf-8")) == first


def test_id_command_uses_the_registry(project, cli, tmp_path, monkeypatch):
    monkeypatch.chdir(project)
    same = tmp_path / "again.bin"
    same.write_bytes(b"alpha bytes")
    code, env, _ = cli("id", str(same), "--json")
    (item,) = env["data"]["files"]
    assert item["duplicate_of"] == "10_sources/papers/alpha.pdf"
    fresh = tmp_path / "fresh.bin"
    fresh.write_bytes(b"never seen")
    code, env, _ = cli("id", str(fresh), "--json")
    assert "duplicate_of" not in env["data"]["files"][0]


def test_golden_registry(project, cli):
    code, env, _ = cli("registry", "--json", "--root", str(project))
    assert code == 0
    assert_golden("registry.json", env, {env["version"]: "<version>"})
