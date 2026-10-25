"""003/R6 frontmatter I/O; 003/AC3 round trip with unknown keys and a byte-identical body."""
import pytest

from metarepo_engine import frontmatter as fm

BODY_VARIANTS = [
    "# Title\n\nText with unicode: café 中文 \U0001f600\n",
    "no trailing newline",
    "",
    "\n\n\n",
    "---\nnot frontmatter, a horizontal rule follows\n---\nafter rule\n",
    "line one\r\nline two\r\nline three\r\n",
    "mixed\nendings\r\nhere\rand there\n",
    "trailing spaces   \n\ttabs\t\n",
    "```yaml\n---\nkey: value\n---\n```\n",
]

FRONT = """\
type: Concept
title: Example Page
zzz_unknown: {nested: [1, 2, 3], flag: true}
tags: [b, a]
aaa_second_unknown: "quoted: value"
sources:
  - id: src-123456789abc
    note: kept
"""


def doc_text(front=FRONT, body="body\n", nl="\n"):
    return nl.join(["---", *front.rstrip("\n").split("\n"), "---", ""]) + body


@pytest.mark.parametrize("body", BODY_VARIANTS)
def test_ac3_unchanged_roundtrip_is_byte_identical(body):
    data = doc_text(body=body).encode("utf-8")
    assert fm.parse(data).to_bytes() == data


@pytest.mark.parametrize("body", BODY_VARIANTS)
def test_ac3_changed_frontmatter_preserves_unknown_keys_order_and_body(body):
    data = doc_text(body=body).encode("utf-8")
    doc = fm.parse(data)
    doc.front["title"] = "Changed"
    doc.front["new_key"] = "added"
    out = fm.parse(doc.to_bytes())
    assert out.body == body  # byte-for-byte
    assert out.front["zzz_unknown"] == {"nested": [1, 2, 3], "flag": True}
    assert out.front["aaa_second_unknown"] == "quoted: value"
    assert list(out.front) == ["type", "title", "zzz_unknown", "tags", "aaa_second_unknown", "sources", "new_key"]
    assert out.front["sources"] == [{"id": "src-123456789abc", "note": "kept"}]
    assert doc.to_bytes().endswith(body.encode("utf-8"))


def test_crlf_file_keeps_crlf_delimiters_when_rewritten():
    data = doc_text(body="a\r\nb\r\n", nl="\r\n").encode("utf-8")
    doc = fm.parse(data)
    assert doc.newline == "\r\n"
    doc.front["title"] = "T2"
    out = doc.to_bytes()
    assert b"\n" not in out.replace(b"\r\n", b"")  # every newline is CRLF
    assert fm.parse(out).front["title"] == "T2" and fm.parse(out).body == "a\r\nb\r\n"


def test_bom_is_preserved():
    data = ("﻿" + doc_text()).encode("utf-8")
    doc = fm.parse(data)
    assert doc.bom and doc.front["type"] == "Concept"
    assert doc.to_bytes() == data
    doc.front["x"] = 1
    assert doc.to_bytes().startswith(b"\xef\xbb\xbf---")


def test_comments_survive_only_while_frontmatter_is_unchanged():
    data = doc_text(front="type: T  # inline comment\n# own line comment\nkey: v\n").encode("utf-8")
    doc = fm.parse(data)
    assert doc.to_bytes() == data  # unchanged: verbatim, comments kept
    doc.front["key"] = "w"
    assert b"comment" not in doc.to_bytes()  # documented limitation once re-serialized


def test_no_frontmatter_documents():
    doc = fm.parse("# just markdown\n")
    assert doc.front == {} and not doc.has_front and doc.body == "# just markdown\n"
    assert doc.to_bytes() == b"# just markdown\n"
    doc.front["type"] = "Note"
    assert doc.to_bytes() == b"---\ntype: Note\n---\n# just markdown\n"


def test_empty_frontmatter_block():
    doc = fm.parse("---\n---\nbody\n")
    assert doc.front == {} and doc.has_front and doc.body == "body\n"
    assert doc.to_bytes() == b"---\n---\nbody\n"
    doc.front["a"] = 1
    assert fm.parse(doc.to_bytes()).front == {"a": 1}


def test_closing_delimiter_at_end_of_file_without_newline():
    doc = fm.parse("---\ntype: T\n---")
    assert doc.front == {"type": "T"} and doc.body == ""


@pytest.mark.parametrize("bad", ["---\ntype: T\nbody never closed\n", "---\n- a\n- b\n---\nx\n",
                                 "---\nkey: [unclosed\n---\nx\n", "---\n: : :\n---\n"])
def test_malformed_frontmatter_raises(bad):
    with pytest.raises(fm.FrontmatterError):
        fm.parse(bad)


def test_invalid_utf8_raises():
    with pytest.raises(fm.FrontmatterError, match="UTF-8"):
        fm.parse(b"---\ntype: T\n---\n\xff\xfe broken")


def test_dates_and_non_string_scalars_round_trip():
    data = "---\ndate: 2026-10-24\nwhen: 2026-10-24T15:00:00Z\nn: 3\nratio: 0.5\nflag: no\nnothing: null\n---\nb\n"
    doc = fm.parse(data)
    doc.front["extra"] = "x"
    again = fm.parse(doc.to_bytes())
    assert again.front["date"] == doc.front["date"] and again.front["when"] == doc.front["when"]
    assert again.front["flag"] is False and again.front["nothing"] is None and again.front["n"] == 3


def test_strings_that_look_like_other_types_stay_strings():
    doc = fm.parse("---\ncode: '007'\nversion: '1.0'\nanswer: 'yes'\n---\nb\n")
    doc.front["touch"] = 1
    again = fm.parse(doc.to_bytes())
    assert again.front["code"] == "007" and again.front["version"] == "1.0" and again.front["answer"] == "yes"


def test_unicode_in_keys_values_is_not_escaped():
    doc = fm.parse("---\ntitle: café 中文\n---\nb\n")
    doc.front["other"] = "ü"
    assert "café 中文" in doc.dump() and "\\u" not in doc.dump()


def test_long_strings_are_not_wrapped():
    long = "word " * 80
    doc = fm.parse("---\na: 1\n---\nb\n")
    doc.front["desc"] = long.strip()
    assert fm.parse(doc.to_bytes()).front["desc"] == long.strip()
    assert "desc: " + long.strip() in doc.dump()


# ---- files
def test_update_changes_only_frontmatter_and_is_atomic(tmp_path):
    path = tmp_path / "page.md"
    body = "# Body\r\n\r\nkeep me exactly é\r\n"
    path.write_bytes(doc_text(body=body).encode("utf-8"))
    before = path.read_bytes()
    doc = fm.update(path, {"title": "New", "added": [1, 2]}, remove=["tags"])
    after = path.read_bytes()
    assert after != before and after.endswith(body.encode("utf-8"))
    assert "tags" not in doc.front and fm.read(path).front["added"] == [1, 2]
    assert [p.name for p in tmp_path.iterdir()] == ["page.md"]


def test_update_without_effective_change_does_not_rewrite(tmp_path):
    path = tmp_path / "page.md"
    path.write_bytes(doc_text().encode("utf-8"))
    mtime = path.stat().st_mtime_ns
    fm.update(path, {"title": "Example Page"})
    assert path.stat().st_mtime_ns == mtime


def test_update_adds_frontmatter_to_a_plain_file(tmp_path):
    path = tmp_path / "plain.md"
    path.write_text("# hello\n", encoding="utf-8")
    fm.update(path, {"type": "Note"})
    assert path.read_text(encoding="utf-8") == "---\ntype: Note\n---\n# hello\n"


def test_read_head_reads_only_the_frontmatter(tmp_path):
    path = tmp_path / "big.md"
    path.write_bytes(b"---\ntype: Extract\nsource_id: src-1\n---\n" + b"x" * 5_000_000)
    assert fm.read_head(path) == {"type": "Extract", "source_id": "src-1"}
    (tmp_path / "plain.md").write_text("no front\n", encoding="utf-8")
    assert fm.read_head(tmp_path / "plain.md") == {}
    (tmp_path / "bad.md").write_text("---\nunclosed\n", encoding="utf-8")
    with pytest.raises(fm.FrontmatterError):
        fm.read_head(tmp_path / "bad.md")
