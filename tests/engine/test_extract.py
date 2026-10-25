"""Spec 008: extraction contract, adapters, selection, quality, staleness, parts, agent records."""
from __future__ import annotations

import io
import json
import sys
import textwrap
import types
import zipfile
from pathlib import Path

import pytest
import yaml

from metarepo_engine import frontmatter, registry
from metarepo_engine.config import load_schema
from metarepo_engine.extract import base, tables, writer
from metarepo_engine.extract.adapters.text import transcript_to_markdown
from metarepo_engine.extract.staleness import staleness, validate_front

PDF_BYTES = b"%PDF-1.4\n% fixture, not a real document\n"
XLSX_MT = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


# ----------------------------------------------------------------------------- helpers
def put(root: Path, rel: str, data: bytes | str) -> str:
    path = root / "10_sources" / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data.encode("utf-8") if isinstance(data, str) else data)
    return f"10_sources/{rel}"


def extract_md(root: Path, rel: str) -> Path:
    return root / "20_extracts" / f"{rel}.md"


def doc(root: Path, rel: str):
    return frontmatter.read(extract_md(root, rel))


def run(cli, root, *argv):
    return cli(*argv, "--json", "--root", str(root))


def make_xlsx(path: Path, rows: list[list], sheet: str = "Data") -> None:
    strings: list[str] = []

    def sst(text: str) -> int:
        if text not in strings:
            strings.append(text)
        return strings.index(text)

    cols = "ABCDEFGH"
    body = []
    for r, row in enumerate(rows, 1):
        cells = []
        for c, v in enumerate(row):
            ref = f"{cols[c]}{r}"
            if isinstance(v, str):
                cells.append(f'<c r="{ref}" t="s"><v>{sst(v)}</v></c>')
            else:
                cells.append(f'<c r="{ref}"><v>{v}</v></c>')
        body.append(f'<row r="{r}">{"".join(cells)}</row>')
    ns = 'xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"'
    files = {
        "xl/workbook.xml": f'<workbook {ns} xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
                           f'<sheets><sheet name="{sheet}" sheetId="1" r:id="rId1"/></sheets></workbook>',
        "xl/_rels/workbook.xml.rels": '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                                      '<Relationship Id="rId1" Target="worksheets/sheet1.xml"/></Relationships>',
        "xl/worksheets/sheet1.xml": f"<worksheet {ns}><sheetData>{''.join(body)}</sheetData></worksheet>",
        "xl/sharedStrings.xml": f"<sst {ns}>" + "".join(f"<si><t>{s}</t></si>" for s in strings) + "</sst>",
    }
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, data in files.items():
            zf.writestr(name, data)


def fake_pdf_lib(monkeypatch, pages: list[str]):
    class Page:
        def __init__(self, text):
            self.text = text

        def get_text(self, mode="text"):
            return self.text

    class Doc:
        page_count = len(pages)
        metadata = {"title": "Fixture Title"}

        def load_page(self, i):
            return Page(pages[i])

        def close(self):
            pass

    lib = types.ModuleType("pymupdf")
    lib.open = lambda path: Doc()
    monkeypatch.setitem(sys.modules, "pymupdf", lib)


def script(tmp_path: Path, name: str, code: str) -> str:
    p = tmp_path / name
    p.write_text(textwrap.dedent(code), encoding="utf-8")
    return str(p)


def seed(root: Path, rel: str, **front) -> None:
    """A placement stub: identity-less record carrying triage and lineage, no extractor yet."""
    md = extract_md(root, rel)
    md.parent.mkdir(parents=True, exist_ok=True)
    md.write_text("---\n" + yaml.safe_dump(front, sort_keys=False) + "---\n", encoding="utf-8")


# ----------------------------------------------------------------------------- schema (T2)
def test_schema_loads_and_accepts_a_written_record(cli, make_project):
    root = make_project()
    put(root, "notes/a.md", "# Title A\n\nBody.\n")
    code, env, _ = run(cli, root, "extract", "10_sources/notes/a.md")
    assert code == 0 and env["data"]["status"] == "written"
    assert load_schema("extract")["required"][0] == "type"
    front = doc(root, "notes/a.md").front
    assert validate_front(front).errors == []
    assert front["type"] == "Extract" and front["source_path"] == "10_sources/notes/a.md"
    assert front["source_id"] == "src-" + front["source_sha256"][:12]


def test_schema_rejects_bad_records():
    assert validate_front({"type": "Wiki"}).errors
    good = {"type": "Extract", "schema_version": 1, "source_id": "src-" + "a" * 12, "source_path": "10_sources/x",
            "source_sha256": "a" * 64, "quality": "great"}
    assert any("quality" in p for p, _ in validate_front(good).errors)


# ----------------------------------------------------------------------------- text adapter (T4)
def test_markdown_golden(cli, make_project):
    root = make_project()
    put(root, "a.md", "# Heading One\r\n\r\ntext  \r\n")
    run(cli, root, "extract", "10_sources/a.md")
    d = doc(root, "a.md")
    assert d.body == "# Heading One\n\ntext\n"
    assert d.front["title"] == "Heading One" and d.front["quality"] == "high"
    assert d.front["extractor"] == {"name": "text", "version": "1", "mode": "local"}


def test_csv_small_is_a_table(cli, make_project):
    root = make_project()
    put(root, "t.csv", "name,qty\nwidget,3\n\"a|b\",4\n")
    run(cli, root, "extract", "10_sources/t.csv")
    assert doc(root, "t.csv").body == "| name | qty |\n| --- | --- |\n| widget | 3 |\n| a\\|b | 4 |\n"


def test_html_golden(cli, make_project):
    root = make_project()
    put(root, "p.html", "<html><head><title>Page T</title><style>x{}</style></head><body><h1>Head</h1>"
                        "<p>Hello <a href='http://x.example/'>link</a> <b>bold</b></p><ul><li>one</li><li>two</li></ul>"
                        "<script>bad()</script></body></html>")
    run(cli, root, "extract", "10_sources/p.html")
    d = doc(root, "p.html")
    assert d.front["title"] == "Page T"
    assert d.body == "# Head\n\nHello [link](http://x.example/) **bold**\n\n- one\n- two\n"


def test_vtt_transcript_has_timestamps_and_coverage():
    md, duration, covered = transcript_to_markdown(
        "WEBVTT\n\n00:00:01.000 --> 00:00:03.000\nHello <b>there</b>\n\n00:01:05.500 --> 00:01:10.000\nSecond\n")
    assert md == "[00:00:01] Hello there\n[00:01:05] Second"
    assert duration == 70.0 and covered == pytest.approx(6.5)


def test_transcript_coverage_drives_quality(cli, make_project):
    root = make_project()
    put(root, "m.vtt", "WEBVTT\n\n00:00:00.000 --> 00:00:10.000\nshort start\n\n01:00:00.000 --> 01:00:01.000\nend\n")
    run(cli, root, "extract", "10_sources/m.vtt")
    d = doc(root, "m.vtt")
    assert d.front["quality"] == "needs-review" and d.front["structure"] == {"duration_s": 3601}


def test_undecodable_bytes_lower_quality(cli, make_project):
    root = make_project()
    put(root, "bad.txt", b"abc " * 10 + b"\xff\xfe\xfa\xfb" + b" def" * 10)
    run(cli, root, "extract", "10_sources/bad.txt")
    d = doc(root, "bad.txt")
    assert d.front["quality"] in ("partial", "needs-review") and "UTF-8" in d.front["quality_notes"]


# ----------------------------------------------------------------------------- spreadsheets (T8, AC2)
def test_ac2_xlsx_with_10000_rows_is_summarized(cli, make_project):
    root = make_project()
    rows = [["id", "label", "amount"]] + [[i, f"row-{i % 7}", i * 1.5] for i in range(1, 10001)]
    make_xlsx(root / "10_sources" / "big.xlsx", rows)
    (root / "10_sources" / "big.xlsx")  # created above
    code, env, _ = run(cli, root, "extract", "10_sources/big.xlsx")
    assert code == 0
    d = doc(root, "big.xlsx")
    assert d.front["media_type"] == XLSX_MT and d.front["structure"] == {"sheets": ["Data"]}
    assert "## Sheet: Data" in d.body and "10000 data rows" in d.body
    assert "full data is in the original" in d.body
    assert d.body.count("\n") < 60  # schema + stats + 10 sample rows, not 10,000 rows
    assert "| 5000 |" not in d.body
    assert "| id | integer | 10000 | 10000 | 1 | 10000 | 5000.5 |" in d.body


def test_xlsx_below_threshold_is_inline(cli, make_project):
    root = make_project()
    make_xlsx(root / "10_sources" / "s.xlsx", [["a", "b"], ["x", 1], ["y", 2.5]])
    run(cli, root, "extract", "10_sources/s.xlsx")
    assert doc(root, "s.xlsx").body == "## Sheet: Data\n\n| a | b |\n| --- | --- |\n| x | 1 |\n| y | 2.5 |\n"


def test_large_table_rows_is_configurable(cli, make_project):
    root = make_project(extraction={"large_table_rows": 2})
    put(root, "t.csv", "k,v\n1,a\n2,b\n3,c\n")
    run(cli, root, "extract", "10_sources/t.csv")
    assert "Large table: 3 data rows" in doc(root, "t.csv").body


def test_pipe_table_postprocessing_for_third_party_output():
    text = "## Sheet: S\n\n" + tables.md_table(["a"], [[str(i)] for i in range(30)]) + "\n"
    out = tables.summarize_pipe_tables(text, 10)
    assert "Large table: 30 data rows" in out and "| 15 |" not in out


# ----------------------------------------------------------------------------- determinism / force / idempotence
def test_ac5_two_runs_give_identical_bodies(cli, make_project):
    root = make_project()
    put(root, "t.csv", "a,b\n" + "\n".join(f"{i},{i * 2}" for i in range(300)) + "\n")
    run(cli, root, "extract", "10_sources/t.csv")
    first = doc(root, "t.csv").body
    code, env, _ = run(cli, root, "extract", "10_sources/t.csv", "--force")
    assert code == 0 and env["data"]["status"] == "written"
    assert doc(root, "t.csv").body == first


def test_extract_twice_without_force_is_a_noop(cli, make_project):
    root = make_project()
    put(root, "a.txt", "hello\n")
    run(cli, root, "extract", "10_sources/a.txt")
    log = (root / "_system" / "events.log").read_text(encoding="utf-8")
    before = extract_md(root, "a.txt").read_bytes()
    code, env, _ = run(cli, root, "extract", "10_sources/a.txt")
    assert code == 0 and env["data"]["status"] == "unchanged"
    assert extract_md(root, "a.txt").read_bytes() == before
    assert (root / "_system" / "events.log").read_text(encoding="utf-8") == log


def test_ac4_force_preserves_triage_and_lineage(cli, make_project):
    root = make_project()
    put(root, "papers/a.txt", "v1 text\n")
    seed(root, "papers/a.txt", type="Extract", original_name="Original Name.txt", aliases=["other.txt"],
         triage={"decision": "wiki", "reason": "relevant", "by": "member-a", "at": "2026-10-24T10:00:00Z",
                 "confidence": "high"},
         supersedes=["src-" + "b" * 12], bundle={"parent_id": "src-" + "c" * 12, "group": "g", "member_path": "x/y"},
         custom_key="kept")
    assert run(cli, root, "extract", "10_sources/papers/a.txt")[0] == 0  # fills the stub
    first = doc(root, "papers/a.txt")
    assert first.front["triage"]["decision"] == "wiki" and first.front["quality"] == "high"
    code, env, _ = run(cli, root, "extract", "10_sources/papers/a.txt", "--force")
    assert code == 0
    f = doc(root, "papers/a.txt").front
    assert f["original_name"] == "Original Name.txt" and f["aliases"] == ["other.txt"]
    assert f["triage"]["by"] == "member-a" and f["triage"]["at"] == "2026-10-24T10:00:00Z"
    assert f["supersedes"] == ["src-" + "b" * 12] and f["bundle"]["member_path"] == "x/y"
    assert f["custom_key"] == "kept"


def test_extract_by_source_id(cli, make_project):
    root = make_project()
    put(root, "a.txt", "text\n")
    run(cli, root, "extract", "10_sources/a.txt")
    sid = doc(root, "a.txt").front["source_id"]
    code, env, _ = run(cli, root, "extract", sid)
    assert code == 0 and env["data"]["status"] == "unchanged"


def test_rejects_paths_outside_sources(cli, make_project):
    root = make_project()
    (root / "00_inbox" / "x.txt").write_text("x", encoding="utf-8")
    code, env, _ = run(cli, root, "extract", "00_inbox/x.txt")
    assert code == 3 and env["errors"][0]["code"] == "not_a_source"
    assert run(cli, root, "extract", "src-" + "0" * 12)[1]["errors"][0]["code"] == "unknown_source"


def test_dry_run_changes_nothing(cli, make_project):
    root = make_project()
    put(root, "a.txt", "text\n")
    code, env, _ = cli("extract", "10_sources/a.txt", "--dry-run", "--json", "--root", str(root))
    assert code == 0 and env["data"]["status"] == "planned" and env["data"]["candidates"][0] == "text"
    assert not extract_md(root, "a.txt").exists()
    assert not (root / "_system" / "events.log").exists()


def test_event_is_logged_with_targets(cli, make_project):
    root = make_project()
    put(root, "a.txt", "text\n")
    run(cli, root, "extract", "10_sources/a.txt")
    events = [json.loads(l) for l in (root / "_system" / "events.log").read_text(encoding="utf-8").splitlines()]
    assert events[-1]["command"] == "extract" and "20_extracts/a.txt.md" in events[-1]["targets"]


# ----------------------------------------------------------------------------- failure, PDF, agent (R1, R5, AC1)
def test_r1_no_adapter_still_writes_a_failed_record(cli, make_project):
    root = make_project()
    put(root, "d/x.pdf", PDF_BYTES)
    code, env, _ = run(cli, root, "extract", "10_sources/d/x.pdf")
    assert code == 0 and env["data"]["quality"] == "failed"
    assert env["warnings"][0]["code"] == "extraction_failed"
    assert env["data"]["agent_assist"]["suggested"] is True
    f = doc(root, "d/x.pdf").front
    assert f["quality"] == "failed" and "no adapter" in f["quality_notes"]
    assert [r["quality"] for r in registry.build(root).rows] == ["failed"]


def test_crashing_adapter_is_recorded_not_raised(cli, make_project, monkeypatch):
    root = make_project()
    put(root, "a.txt", "x\n")
    from metarepo_engine.extract.adapters import text

    monkeypatch.setattr(text.TextAdapter, "extract", lambda self, p, o: (_ for _ in ()).throw(RuntimeError("boom")))
    code, env, _ = run(cli, root, "extract", "10_sources/a.txt")
    assert code == 0 and "boom" in doc(root, "a.txt").front["quality_notes"]


def test_force_failure_does_not_clobber_a_good_extraction(cli, make_project, monkeypatch):
    root = make_project()
    put(root, "a.txt", "good\n")
    run(cli, root, "extract", "10_sources/a.txt")
    before = extract_md(root, "a.txt").read_bytes()
    from metarepo_engine.extract.adapters import text

    monkeypatch.setattr(text.TextAdapter, "extract", lambda self, p, o: (_ for _ in ()).throw(RuntimeError("boom")))
    code, env, _ = run(cli, root, "extract", "10_sources/a.txt", "--force")
    assert code == 0 and env["data"]["status"] == "kept"
    assert extract_md(root, "a.txt").read_bytes() == before


def test_ac1_text_pdf_is_high_with_page_headings(cli, make_project, monkeypatch):
    fake_pdf_lib(monkeypatch, ["First page text. " * 10, "Second page text. " * 10])
    root = make_project()
    put(root, "p.pdf", PDF_BYTES)
    code, env, _ = run(cli, root, "extract", "10_sources/p.pdf")
    d = doc(root, "p.pdf")
    assert code == 0 and d.front["quality"] == "high" and d.front["structure"] == {"pages": 2}
    assert d.body.startswith("## Page 1\n\nFirst page") and "\n## Page 2\n" in d.body
    assert d.front["title"] == "Fixture Title" and d.front["extractor"]["name"] == "pymupdf"


def test_ac1_image_only_pdf_needs_review_then_agent_extraction(cli, make_project, monkeypatch, tmp_path):
    fake_pdf_lib(monkeypatch, ["", ""])
    root = make_project()
    put(root, "scan.pdf", PDF_BYTES)
    seed(root, "scan.pdf", type="Extract", triage={"decision": "reference", "reason": "r", "by": "m"})
    code, env, _ = run(cli, root, "extract", "10_sources/scan.pdf")
    assert env["data"]["quality"] == "needs-review" and env["data"]["agent_assist"]["suggested"] is True
    body = tmp_path / "body.md"
    body.write_text("## Page 1\n\nTranscribed by the agent.\n\n> [Figure: a bar chart]\n", encoding="utf-8")
    code, env, _ = run(cli, root, "extract-record", "10_sources/scan.pdf", "--body", str(body),
                       "--extractor", "agent:model-x", "--quality", "partial", "--quality-notes", "agent vision",
                       "--structure", '{"pages": 1}', "--json", "--root", str(root))
    assert code == 0
    f = doc(root, "scan.pdf").front
    assert f["extractor"] == {"name": "agent", "version": "model-x", "mode": "agent"}
    assert f["quality"] == "partial" and f["quality_notes"] == "agent vision" and f["structure"] == {"pages": 1}
    assert f["triage"]["decision"] == "reference"  # R7 fields preserved
    assert validate_front(f).errors == []
    # a partial agent extraction is not replaced silently
    code, env, _ = run(cli, root, "extract-record", "10_sources/scan.pdf", "--body", str(body),
                       "--extractor", "agent:model-x")
    assert code == 3 and env["errors"][0]["code"] == "extraction_exists"
    assert run(cli, root, "extract-record", "10_sources/scan.pdf", "--body", str(body), "--extractor",
               "agent:model-x", "--force")[0] == 0


def test_extract_record_input_validation(cli, make_project, tmp_path):
    root = make_project()
    put(root, "i.png", b"\x89PNG\r\n\x1a\n" + b"0" * 20)
    good = tmp_path / "b.md"
    good.write_text("Description of the image.\n", encoding="utf-8")
    assert run(cli, root, "extract-record", "10_sources/i.png", "--body", str(good), "--extractor", "gpt")[1][
        "errors"][0]["code"] == "bad_extractor"
    fm = tmp_path / "fm.md"
    fm.write_text("---\nx: 1\n---\nbody\n", encoding="utf-8")
    assert run(cli, root, "extract-record", "10_sources/i.png", "--body", str(fm), "--extractor", "agent:m")[1][
        "errors"][0]["code"] == "body_has_frontmatter"
    code, env, _ = run(cli, root, "extract-record", "10_sources/i.png", "--body", str(good), "--extractor", "agent:m")
    assert code == 0 and env["data"]["quality"] == "high"
    assert doc(root, "i.png").front["media_type"] == "image/png"


def test_extract_record_failed_stub_needs_no_force(cli, make_project, tmp_path):
    root = make_project()
    put(root, "i.png", b"\x89PNG\r\n\x1a\n" + b"0" * 20)
    assert run(cli, root, "extract", "10_sources/i.png")[1]["data"]["quality"] == "failed"
    good = tmp_path / "b.md"
    good.write_text("Text in the image.\n", encoding="utf-8")
    assert run(cli, root, "extract-record", "10_sources/i.png", "--body", str(good), "--extractor", "agent:m")[0] == 0


# ----------------------------------------------------------------------------- command adapter, cloud gating (T6, AC3)
def _cmd_config(tmp_path: Path, mode: str, marker: Path) -> dict:
    tool = script(tmp_path, f"tool_{mode}.py", f"""
        import sys, pathlib
        pathlib.Path({str(marker)!r}).write_text("ran", encoding="utf-8")
        data = sys.stdin.buffer.read() if len(sys.argv) < 2 else open(sys.argv[1], "rb").read()
        print("# Transcript\\n\\n[00:00:01] " + data.decode("utf-8", "replace").strip())
    """)
    return {"commands": [{"name": f"tool-{mode}", "media_types": ["audio/*"], "mode": mode,
                          "command": [sys.executable, tool], "timeout_s": 30}]}


def make_wav(path: Path) -> None:
    import wave

    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(8000)
        w.writeframes(b"\x00\x00" * 800)


def test_command_adapter_runs_via_stdin_and_input_placeholder(cli, make_project, tmp_path):
    marker = tmp_path / "ran.txt"
    cfg = _cmd_config(tmp_path, "local", marker)
    root = make_project(extraction=cfg)
    make_wav(root / "10_sources" / "a.wav")
    code, env, _ = run(cli, root, "extract", "10_sources/a.wav")
    assert code == 0 and marker.exists()
    d = doc(root, "a.wav")
    assert d.front["extractor"]["name"] == "tool-local" and d.front["extractor"]["version"].startswith("config-")
    assert d.body.startswith("# Transcript") and d.front["extractor"]["mode"] == "local"


def test_command_adapter_input_placeholder(cli, make_project, tmp_path):
    marker = tmp_path / "ran.txt"
    cfg = _cmd_config(tmp_path, "local", marker)
    cfg["commands"][0]["command"].append("{input}")
    root = make_project(extraction=cfg)
    put(root, "a.wav", b"RIFF\x00\x00\x00\x00WAVEdata is read from the file argument")
    code, env, _ = run(cli, root, "extract", "10_sources/a.wav")
    assert code == 0 and marker.exists()
    assert "[00:00:01]" in doc(root, "a.wav").body and "file argument" in doc(root, "a.wav").body


def test_ac3_cloud_adapter_is_never_invoked_when_disabled(cli, make_project, tmp_path):
    marker = tmp_path / "cloud-ran.txt"
    root = make_project(extraction=_cmd_config(tmp_path, "cloud", marker))
    make_wav(root / "10_sources" / "a.wav")
    code, env, _ = run(cli, root, "extract", "10_sources/a.wav")
    assert code == 0 and not marker.exists()
    assert env["data"]["quality"] == "failed" and env["data"]["skipped"][0]["name"] == "tool-cloud"
    code, env, _ = run(cli, root, "extract", "10_sources/a.wav", "--extractor", "tool-cloud", "--force")
    assert code == 3 and env["errors"][0]["code"] == "cloud_disabled" and not marker.exists()


def test_cloud_adapter_runs_only_with_allow_cloud(cli, make_project, tmp_path):
    marker = tmp_path / "cloud-ran.txt"
    cfg = _cmd_config(tmp_path, "cloud", marker)
    cfg["allow_cloud"] = True
    root = make_project(extraction=cfg)
    make_wav(root / "10_sources" / "a.wav")
    assert run(cli, root, "extract", "10_sources/a.wav")[0] == 0 and marker.exists()
    assert doc(root, "a.wav").front["extractor"]["mode"] == "cloud"


def test_command_failure_and_timeout_are_recorded(cli, make_project, tmp_path):
    bad = script(tmp_path, "bad.py", "import sys; sys.stderr.write('nope'); sys.exit(3)\n")
    slow = script(tmp_path, "slow.py", "import time; time.sleep(20)\n")
    root = make_project(extraction={"commands": [
        {"name": "bad", "media_types": ["audio/*"], "command": [sys.executable, bad]},
        {"name": "slow", "media_types": ["audio/mpeg"], "command": [sys.executable, slow], "timeout_s": 1}]})
    make_wav(root / "10_sources" / "a.wav")
    run(cli, root, "extract", "10_sources/a.wav")
    notes = doc(root, "a.wav").front["quality_notes"]
    assert "bad exited with status 3" in notes and "nope" in notes
    put(root, "b.mp3", b"ID3\x03\x00" + b"0" * 30)
    run(cli, root, "extract", "10_sources/b.mp3", "--extractor", "slow")
    assert "timed out" in doc(root, "b.mp3").front["quality_notes"]


def test_command_runs_outside_the_synced_tree(cli, make_project, tmp_path):
    tool = script(tmp_path, "cwd.py", "import os, pathlib; pathlib.Path('stray.txt').write_text('x'); print(os.getcwd())\n")
    root = make_project(extraction={"commands": [{"name": "c", "media_types": ["audio/*"],
                                                  "command": [sys.executable, tool]}]})
    make_wav(root / "10_sources" / "a.wav")
    run(cli, root, "extract", "10_sources/a.wav")
    assert not list(root.rglob("stray.txt"))


def test_preferences_reorder_candidates(cli, make_project):
    root = make_project(extraction={"preferences": {"text/*": ["xlsx", "text"]}})
    put(root, "a.txt", "x\n")
    code, env, _ = cli("extract", "10_sources/a.txt", "--dry-run", "--json", "--root", str(root))
    assert env["data"]["candidates"] == ["text"]  # xlsx does not handle text; the rest follow by priority


def test_explicit_unavailable_extractor_exits_5(cli, make_project):
    root = make_project()
    put(root, "a.txt", "x\n")
    for name, code_expected in (("docling", 5), ("nope", 2)):
        code, env, _ = run(cli, root, "extract", "10_sources/a.txt", "--extractor", name)
        assert code == code_expected


# ----------------------------------------------------------------------------- extractors command
def test_extractors_lists_availability_without_installing(cli, make_project, tmp_path):
    root = make_project(extraction={"commands": [{"name": "w", "media_types": ["audio/*"], "mode": "cloud",
                                                  "command": ["definitely-not-installed-xyz"]}]})
    code, env, _ = run(cli, root, "extractors")
    assert code == 0
    rows = {r["name"]: r for r in env["data"]["extractors"]}
    assert rows["text"]["available"] and rows["xlsx"]["available"] and rows["agent"]["mode"] == "agent"
    assert rows["w"]["available"] is False and rows["w"]["mode"] == "cloud"
    for opt in ("markitdown", "docling", "pymupdf"):
        assert opt in rows and (rows[opt]["available"] or "pip install" in rows[opt]["install"])


def test_extractors_works_outside_a_project(cli, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    code, env, _ = cli("extractors", "--json")
    assert code == 0 and env["data"]["in_metarepo"] is False


# ----------------------------------------------------------------------------- parts (R10)
def test_r10_oversized_body_is_split_and_stale_parts_removed(cli, make_project):
    root = make_project(extraction={"max_body_mb": 0.002})  # about 2 KB
    big = "".join(f"line {i:04d} " + "x" * 40 + "\n" for i in range(150))
    put(root, "big.txt", big)
    code, env, _ = run(cli, root, "extract", "10_sources/big.txt")
    n = env["data"]["parts"]
    assert code == 0 and n >= 3
    main = doc(root, "big.txt")
    assert main.front["parts"] == n
    parts = sorted((root / "20_extracts").glob("big.txt.part-*.md"))
    assert len(parts) == n - 1 and not parts[0].read_text(encoding="utf-8").startswith("---")
    joined = main.body + "".join(p.read_text(encoding="utf-8") for p in parts)
    assert joined == big
    assert all(len(p.read_bytes()) <= 2097 for p in parts)
    assert len(registry.build(root).rows) == 1  # continuation parts are not separate records
    (root / "10_sources" / "big.txt")
    # shrink the limit's need: re-extract with a bigger limit removes the leftovers
    cfg = yaml.safe_load((root / "project.yaml").read_text(encoding="utf-8"))
    cfg["extraction"]["max_body_mb"] = 2
    (root / "project.yaml").write_text(yaml.safe_dump(cfg, sort_keys=False), encoding="utf-8")
    code, env, _ = run(cli, root, "extract", "10_sources/big.txt", "--force")
    assert env["data"]["parts"] == 1 and len(env["data"]["removed"]) == n - 1
    assert not list((root / "20_extracts").glob("big.txt.part-*.md")) and "parts" not in doc(root, "big.txt").front


def test_split_parts_unit():
    assert writer.split_parts("a\nb\n", 100) == ["a\nb\n"]
    parts = writer.split_parts("aaaa\nbbbb\ncccc\n", 10)
    assert "".join(parts) == "aaaa\nbbbb\ncccc\n" and all(len(p) <= 10 for p in parts)
    assert "".join(writer.split_parts("x" * 50, 10)) == "x" * 50


# ----------------------------------------------------------------------------- quality (T7) and staleness (R6)
def test_quality_scoring_signals():
    q = base.score_quality
    assert q("", {}, {})[0] == "needs-review"
    assert q("word " * 100, {"pages": 2}, {"pages": 2})[0] == "high"
    assert q("w " * 5, {"pages": 4}, {"pages": 4})[0] == "needs-review"
    assert q("word " * 200, {"pages": 4}, {"pages": 4, "empty_pages": 1})[0] == "partial"
    assert q("word " * 200, {"pages": 4}, {"pages": 4, "empty_pages": 3})[0] == "needs-review"
    assert q("ok " * 100, {}, {"ocr_confidence": 0.7})[0] == "partial"
    assert q("ok " * 100, {}, {"ocr_confidence": 0.3})[0] == "needs-review"
    assert q("ok " * 100 + "�" * 10, {}, {})[0] in ("partial", "needs-review")
    relaxed = base.thresholds_from({"min_chars_per_page": 1, "unknown_key": 5, "ocr_review": "x"})
    assert q("w " * 5, {"pages": 4}, {"pages": 4}, relaxed)[0] == "high" and "unknown_key" not in relaxed


def test_staleness_hash_and_extractor_age():
    front = {"source_sha256": "a" * 64, "extractor": {"name": "docling", "version": "1.9", "mode": "local"}}
    assert staleness(front, "a" * 64) == []
    assert staleness(front, "b" * 64)[0]["code"] == "source_hash_mismatch"
    assert staleness(front, "a" * 64, {"docling": "2.0"})[0]["code"] == "extractor_outdated"
    assert staleness(front, "a" * 64, {"docling": "1.10"})[0]["code"] == "extractor_outdated"
    assert staleness(front, "a" * 64, {"docling": "1.9"}) == []


def test_extract_warns_on_stale_without_force_and_force_updates_hash(cli, make_project):
    root = make_project(extraction={"quality_thresholds": {"min_extractor_versions": {"text": "9"}}})
    put(root, "a.txt", "one\n")
    run(cli, root, "extract", "10_sources/a.txt")
    code, env, _ = run(cli, root, "extract", "10_sources/a.txt")
    assert env["warnings"][0]["code"] == "stale_extraction" and env["data"]["stale"][0]["code"] == "extractor_outdated"
    (root / "10_sources" / "a.txt").write_text("tampered\n", encoding="utf-8")
    sid = doc(root, "a.txt").front["source_id"]
    code, env, _ = run(cli, root, "extract", "10_sources/a.txt")
    assert {s["code"] for s in env["data"]["stale"]} == {"source_hash_mismatch", "extractor_outdated"}
    code, env, _ = run(cli, root, "extract", "10_sources/a.txt", "--force")
    assert any(w["code"] == "source_modified" for w in env["warnings"])
    f = doc(root, "a.txt").front
    assert f["source_id"] == sid and f["source_sha256"] != sid[4:].ljust(64, "0")


# ----------------------------------------------------------------------------- optional libraries (skipped when absent)
def _real(*mods):
    import importlib.util

    return all(importlib.util.find_spec(m) is not None for m in mods)


@pytest.mark.skipif(not _real("fitz") and not _real("pymupdf"), reason="PyMuPDF not installed (P13: never installed by tests)")
def test_real_pymupdf_on_a_generated_pdf(cli, make_project):  # pragma: no cover - environment dependent
    import importlib

    lib = importlib.import_module("pymupdf" if _real("pymupdf") else "fitz")
    pdf = lib.open()
    page = pdf.new_page()
    page.insert_text((72, 72), "Hello extraction. " * 5)
    root = make_project()
    (root / "10_sources").mkdir(exist_ok=True)
    (root / "10_sources" / "r.pdf").write_bytes(pdf.tobytes())
    code, env, _ = run(cli, root, "extract", "10_sources/r.pdf")
    assert code == 0 and env["data"]["quality"] == "high"


@pytest.mark.skipif(not _real("markitdown"), reason="MarkItDown not installed")
def test_real_markitdown_available_listing(cli, make_project):  # pragma: no cover - environment dependent
    root = make_project()
    rows = {r["name"]: r for r in run(cli, root, "extractors")[1]["data"]["extractors"]}
    assert rows["markitdown"]["available"]


@pytest.mark.skipif(not _real("docling"), reason="Docling not installed")
def test_real_docling_available_listing(cli, make_project):  # pragma: no cover - environment dependent
    root = make_project()
    rows = {r["name"]: r for r in run(cli, root, "extractors")[1]["data"]["extractors"]}
    assert rows["docling"]["available"]


def test_markitdown_and_docling_adapters_with_injected_fakes(monkeypatch, tmp_path):
    from metarepo_engine.extract.adapters.docling import PAGE_BREAK, DoclingAdapter
    from metarepo_engine.extract.adapters.markitdown import MarkItDownAdapter
    from metarepo_engine.extract.base import ExtractOptions

    mk = types.ModuleType("markitdown")

    class MarkItDown:
        def convert(self, path):
            rows = "\n".join(f"| {i} |" for i in range(30))
            return types.SimpleNamespace(text_content=f"## S1\n\n| n |\n| --- |\n{rows}\n")

    mk.MarkItDown = MarkItDown
    monkeypatch.setitem(sys.modules, "markitdown", mk)
    res = MarkItDownAdapter().extract(tmp_path / "x.xlsx", ExtractOptions(media_type=XLSX_MT, large_table_rows=10))
    assert "## Sheet: S1" in res.body and "Large table: 30 data rows" in res.body and res.structure == {"sheets": ["S1"]}

    dl = types.ModuleType("docling")
    dc = types.ModuleType("docling.document_converter")

    class Converter:
        def convert(self, path):
            md = lambda **kw: f"Page one text{kw['page_break_placeholder']}Page two text"  # noqa: E731
            return types.SimpleNamespace(document=types.SimpleNamespace(export_to_markdown=md))

    dc.DocumentConverter = Converter
    monkeypatch.setitem(sys.modules, "docling", dl)
    monkeypatch.setitem(sys.modules, "docling.document_converter", dc)
    res = DoclingAdapter().extract(tmp_path / "x.pdf", ExtractOptions(media_type="application/pdf"))
    assert res.body == "## Page 1\n\nPage one text\n\n## Page 2\n\nPage two text\n" and res.structure == {"pages": 2}
    assert PAGE_BREAK not in res.body


def test_no_extraction_writes_inside_sources(cli, make_project):
    root = make_project()
    rel = put(root, "a.txt", "x\n")
    before = (root / rel).read_bytes()
    run(cli, root, "extract", rel)
    run(cli, root, "extract", rel, "--force")
    assert (root / rel).read_bytes() == before
    assert sorted(p.name for p in (root / "10_sources").iterdir()) == ["a.txt"]
