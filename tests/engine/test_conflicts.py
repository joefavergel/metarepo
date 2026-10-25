"""Conflict-copy detection (004/R10, R11, AC4) and the provider table."""
from __future__ import annotations

import os
import re
import time

import pytest

from metarepo_engine import conflicts
from metarepo_engine.events import append_event

SPEC = conflicts.load_providers()


def touch(root, rel, text="x\n", mtime=None):
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")
    if mtime:
        os.utime(p, (mtime, mtime))
    return p


# One fixture copy per provider and pattern: (provider, original, copy-name, rule id)
FIXTURES = [
    ("dropbox", "30_wiki/page.md", "30_wiki/page (Alex Example's conflicted copy 2026-10-24).md", "common"),
    ("dropbox", "shared/notes.txt", "shared/notes (Case Conflict).txt", "dropbox-case-conflict"),
    ("onedrive", "shared/report.docx", "shared/report-LAPTOP01.docx", "onedrive-host-suffix"),
    ("onedrive", "shared/report.docx", "shared/report (1).docx", "onedrive-numbered-duplicate"),
    ("google-drive", "shared/plan.md", "shared/plan [Conflict].md", "gdrive-conflict-marker"),
    ("google-drive", "shared/plan.md", "shared/plan (2).md", "gdrive-numbered-duplicate"),
    (None, "shared/data.csv", "shared/data (conflicted copy).csv", "conflicted-copy"),
]


@pytest.mark.parametrize("provider,original,copy,rule", FIXTURES)
def test_ac4_fixture_conflict_copies_are_detected(tmp_path, provider, original, copy, rule):
    touch(tmp_path, original, "original\n")
    touch(tmp_path, copy, "copy, different\n")
    found = conflicts.detect(tmp_path, providers=[provider] if provider else None, spec=SPEC,
                             hosts={"laptop01"}, heuristic=False)
    assert [(c.path, c.original, c.kind) for c in found] == [(copy, original, "pattern")]
    assert found[0].rule == rule or rule == "common"


def test_every_configurable_provider_has_patterns_and_all_regexes_compile():
    schema_enum = {"google-drive", "onedrive", "dropbox"}
    assert schema_enum <= set(SPEC["providers"])
    for name, body in SPEC["providers"].items():
        assert body["verified"] in {"documented", "partial", "unverified"}
        assert body["patterns"], name
        for pat in body["patterns"]:
            rx = re.compile(pat["regex"])
            assert "stem" in rx.groupindex
            if "known_host" in pat["requires"]:
                assert "host" in rx.groupindex


def test_provider_narrowing(tmp_path):
    touch(tmp_path, "a.md", "1")
    touch(tmp_path, "a [Conflict].md", "2")
    assert conflicts.detect(tmp_path, providers=["dropbox"], spec=SPEC, heuristic=False) == []
    assert len(conflicts.detect(tmp_path, providers=["google-drive"], spec=SPEC, heuristic=False)) == 1


def test_numbered_duplicate_needs_an_original(tmp_path):
    touch(tmp_path, "shared/report (1).docx", "alone")
    assert conflicts.detect(tmp_path, spec=SPEC, heuristic=False) == []


def test_host_suffix_needs_a_known_host_and_an_original(tmp_path):
    touch(tmp_path, "shared/report.docx", "a")
    touch(tmp_path, "shared/report-final.docx", "b")  # a normal name, not a conflict
    assert conflicts.detect(tmp_path, spec=SPEC, hosts={"laptop01"}, heuristic=False) == []
    touch(tmp_path, "shared/report-Laptop01.docx", "c")
    found = conflicts.detect(tmp_path, spec=SPEC, hosts={"laptop01"}, heuristic=False)
    assert [c.path for c in found] == ["shared/report-Laptop01.docx"]


def test_known_hosts_come_from_the_event_log(tmp_path, make_project):
    root = make_project()
    append_event(root, command="x", targets=[], result="ok", actor="a")
    import platform

    assert platform.node().lower() in conflicts.known_hosts(root)


def test_heuristic_flags_same_stem_different_content_close_in_time(tmp_path):
    now = time.time()
    touch(tmp_path, "shared/memo.md", "version one", mtime=now)
    touch(tmp_path, "shared/memo - Copy.md", "version two", mtime=now + 30)
    found = conflicts.detect(tmp_path, spec=SPEC)
    assert [(c.path, c.kind, c.confidence) for c in found] == [("shared/memo - Copy.md", "heuristic", "possible")]


def test_heuristic_ignores_identical_content_and_distant_times_and_numbered_series(tmp_path):
    now = time.time()
    touch(tmp_path, "shared/a.md", "same", mtime=now)
    touch(tmp_path, "shared/a - Copy.md", "same", mtime=now)  # identical: a duplicate, not a conflict
    touch(tmp_path, "shared/b.md", "one", mtime=now)
    touch(tmp_path, "shared/b - Copy.md", "two", mtime=now + 86400)  # a day apart
    touch(tmp_path, "shared/chapter-1.md", "x", mtime=now)
    touch(tmp_path, "shared/chapter-2.md", "y", mtime=now)
    assert conflicts.detect(tmp_path, spec=SPEC) == []


def test_detect_does_not_change_the_tree(tmp_path):
    touch(tmp_path, "a.md", "1")
    touch(tmp_path, "a (conflicted copy).md", "2")
    before = sorted(p.name for p in tmp_path.iterdir())
    conflicts.detect(tmp_path, spec=SPEC)
    assert sorted(p.name for p in tmp_path.iterdir()) == before


def test_describe_gives_both_versions_and_a_diff_for_wiki_pages(tmp_path):
    touch(tmp_path, "30_wiki/page.md", "# Page\nline a\n", mtime=1_700_000_000)
    touch(tmp_path, "30_wiki/page (X conflicted copy 2026-10-24).md", "# Page\nline b\n", mtime=1_700_000_100)
    (found,) = conflicts.detect(tmp_path, spec=SPEC, heuristic=False)
    info = conflicts.describe(tmp_path, found)
    assert info["proposal"] == "merge" and info["identical"] is False
    assert info["copy"]["sha256"] != info["original_version"]["sha256"]
    assert info["copy"]["modified"] == "2023-11-14T22:15:00Z"
    assert "-line a" in info["diff"] and "+line b" in info["diff"]


def test_describe_non_wiki_binary_has_no_diff(tmp_path):
    (tmp_path / "shared").mkdir()
    (tmp_path / "shared/a.bin").write_bytes(b"\xff\xfe\x00")
    (tmp_path / "shared/a (1).bin").write_bytes(b"\xff\xfe\x01")
    (found,) = conflicts.detect(tmp_path, spec=SPEC, heuristic=False)
    info = conflicts.describe(tmp_path, found)
    assert info["proposal"] == "choose-one" and info["diff"] is None


def test_sync_providers_file_gives_r13_folder_guidance(repo):
    modes = {a["path"]: a["mode"] for a in SPEC["availability"]}
    for p in ("30_wiki/", "20_extracts/", "_system/", "(root files)"):
        assert modes[p] == "offline"
    for p in ("10_sources/", "shared/"):
        assert modes[p] == "on-demand"
    readme = (repo / "tooling/templates/root/README.md.tmpl").read_text(encoding="utf-8")
    assert "Available offline" in readme and "`10_sources/` and `shared/`" in readme
