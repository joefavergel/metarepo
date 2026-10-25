"""OKF document model (003 T1.5; 009/ADR-001)."""
from datetime import datetime, timezone

import pytest

from helpers import add_page
from metarepo_engine import okf

PAGE = """\
---
type: Concept
title: Example Concept
description: One line.
status: stable
tags: [alpha, beta]
sources:
  - id: s1
  - id: s2
verified: {by: human:member-a, at: 2026-10-01T00:00:00Z}
custom_key: kept
---
# Example Concept

Claim one.[^s1] Claim two.[^s2] A dangling one.[^s9] Repeat.[^s1]

See [the other page](../area/other.md#section), [sibling](sibling.md "title"), [encoded](my%20page.md),
[web](https://example.com/x), [mail](mailto:a@example.com), [anchor](#top), ![img](images/pic.png).

```
code with [^ignored] and [link](ignored.md)
```

[^s1]: Source one, page 3.
[^s2]: Source two.
"""


@pytest.fixture
def page():
    return okf.OkfPage.from_text(PAGE)


def test_properties(page):
    assert page.type == "Concept" and page.title == "Example Concept" and page.description == "One line."
    assert page.status == "stable" and page.tags == ["alpha", "beta"]
    assert page.source_ids() == ["s1", "s2"] and page.front["custom_key"] == "kept"
    assert page.problems() == []


def test_footnotes_ignore_code_blocks_and_report_dangling(page):
    assert page.footnote_refs() == ["s1", "s2", "s9"]
    assert page.footnote_defs() == ["s1", "s2"]
    assert page.dangling_footnotes() == ["s9"]  # no sources[].id
    assert page.undefined_footnotes() == ["s9"]  # no definition


def test_links_are_relative_only_and_decoded(page):
    assert page.links() == ["../area/other.md", "sibling.md", "my page.md", "images/pic.png"]


def test_resolve_link_stays_inside_root(tmp_path):
    p = add_page(tmp_path, "30_wiki/a/page.md", {"type": "Concept"}, "[x](../b/o.md) [y](../../../../etc/passwd)\n")
    loaded = okf.OkfPage.load(p)
    assert loaded.resolve_link("../b/o.md", tmp_path) == tmp_path / "30_wiki" / "b" / "o.md"
    escaped = loaded.resolve_link("../../../../etc/passwd", tmp_path)
    assert tmp_path in escaped.parents or escaped.parent == tmp_path  # clamped at the root, never above it
    with pytest.raises(ValueError):
        okf.OkfPage.from_text(PAGE).resolve_link("x.md", tmp_path)


def test_trust_tier_and_expiry():
    assert okf.OkfPage.from_text(PAGE).trust_tier() == "verified"
    assert okf.OkfPage({"type": "T"}, "").trust_tier() == "unreviewed"
    assert okf.OkfPage({"type": "T", "verified": {"by": "a"}, "audited": {"by": "b"}}, "").trust_tier() == "audited"
    now = datetime(2026, 6, 1, tzinfo=timezone.utc)
    for value, expired in [("2026-05-31", True), ("2026-06-02", False), ("2026-05-31T00:00:00Z", True), (None, False),
                           ("garbage", False)]:
        front = {"type": "T"} if value is None else {"type": "T", "stale_after": value}
        assert okf.OkfPage(front, "").is_expired(now) is expired, value
    import datetime as dt

    assert okf.OkfPage({"type": "T", "stale_after": dt.date(2026, 5, 31)}, "").is_expired(now)


@pytest.mark.parametrize(
    "front,needle",
    [
        ({}, "type"),
        ({"type": "  "}, "type"),
        ({"type": "T", "status": "published"}, "status"),
        ({"type": "T", "sources": "s1"}, "must be a list"),
        ({"type": "T", "sources": [{"name": "x"}]}, "needs an 'id'"),
        ({"type": "T", "sources": [{"id": "a"}, {"id": "a"}]}, "duplicate"),
    ],
)
def test_problems(front, needle):
    assert any(needle in p for p in okf.OkfPage(front, "").problems())


def test_unreadable_page_reports_a_problem(tmp_path):
    p = tmp_path / "bad.md"
    p.write_text("---\nnever closed\n", encoding="utf-8")
    loaded = okf.OkfPage.load(p)
    assert loaded.parse_error and "unreadable" in loaded.problems()[0]


def test_reserved_files():
    assert okf.is_okf_content_page("30_wiki/topics/a.md")
    for rel in ("30_wiki/index.md", "30_wiki/log.md", "30_wiki/WIKI.md", "30_wiki/a/index.md", "30_wiki/data.json"):
        assert not okf.is_okf_content_page(rel)
