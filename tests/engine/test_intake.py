"""Spec 007: inbox scan, unbundle (bundles), policy gate, place and its modes."""
from __future__ import annotations

import io
import json
import os
import stat
import tarfile
import zipfile
from pathlib import Path

import pytest
import yaml

from helpers import add_source
from metarepo_engine import bundles, frontmatter, policy, registry
from metarepo_engine.bundles import BundleError, Limits

PDF = b"%PDF-1.4\n% fixture one\n"
PDF2 = b"%PDF-1.4\n% fixture two\n"
DOC = "# Title\n\nSome body text for the extraction.\n"


# ----------------------------------------------------------------------------- helpers
def inbox(root: Path, rel: str, data: bytes | str) -> Path:
    p = root / "00_inbox" / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(data.encode() if isinstance(data, str) else data)
    return p


def zipped(path: Path, entries: dict[str, bytes | str], *, extra=None) -> Path:
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, data in entries.items():
            zf.writestr(name, data)
        for info, data in extra or []:
            zf.writestr(info, data)
    return path


def run(cli, root, *argv, actor="member-a"):
    extra = () if argv[0] == "inbox" else ("--actor", actor)
    return cli(*argv, "--json", "--root", str(root), *extra)


def acquire(cli, root, actor="member-a"):
    code, env, _ = run(cli, root, "lease", "acquire", "intake", "--session", "s-" + actor, actor=actor)
    assert code == 0, env
    return env["data"]["lease"]["nonce"]


def place(cli, root, nonce, target, *extra, decision="wiki", **kw):
    triage = json.dumps({"decision": decision, "reason": "because", "confidence": "high"})
    return run(cli, root, "place", target, "--lease", nonce, "--triage", triage, *extra)


@pytest.fixture
def root(make_project):
    return make_project()


@pytest.fixture
def staging(tmp_path):
    d = tmp_path / "staging"
    d.mkdir()
    return d


# ----------------------------------------------------------------------------- T2 scan (R2, R3, R4)
def test_scan_lists_flags_junk_duplicates_cloud_native_and_bundles(cli, root, tmp_path):
    reg = add_source(root, "cat/known.pdf", PDF, media_type="application/pdf")
    inbox(root, "same.pdf", PDF)
    inbox(root, "new.pdf", PDF2)
    inbox(root, "copy-of-new.pdf", PDF2)
    inbox(root, "notes.txt", DOC)
    inbox(root, "x.gdoc", b'{"doc_id": "abc"}')
    inbox(root, ".DS_Store", b"x")
    inbox(root, "~$draft.docx", b"x")
    inbox(root, "__MACOSX/._a", b"x")
    zipped(root / "00_inbox" / "pack.zip", {"a.txt": "a"})
    inbox(root, "folder/sub/file.txt", "t")
    with zipfile.ZipFile(root / "00_inbox" / "doc.docx", "w") as zf:
        zf.writestr("word/document.xml", "<w/>")
    code, env, _ = run(cli, root, "inbox", "scan")
    assert code == 0, env
    data = env["data"]
    items = {i["name"]: i for i in data["items"]}
    assert set(data["junk"]) == {"00_inbox/.DS_Store", "00_inbox/~$draft.docx", "00_inbox/__MACOSX"}
    assert items["same.pdf"]["duplicate_of"] == reg["id"] and items["same.pdf"]["id"] == reg["id"]
    assert items["new.pdf"]["duplicate_of"] is None
    assert items["new.pdf"]["duplicate_in_inbox_of"] == "00_inbox/copy-of-new.pdf"
    assert items["x.gdoc"]["cloud_native"] is True and items["x.gdoc"]["sha256"] is None
    assert items["pack.zip"]["bundle"] is True and items["pack.zip"]["bundle_kind"] == "zip"
    assert items["doc.docx"]["bundle"] is False  # an Office file is not a container
    assert items["folder"]["bundle"] and items["folder"]["kind"] == "folder" and items["folder"]["file_count"] == 1
    for key in ("sha256", "id", "media_type", "size", "duplicate_of", "bundle", "cloud_native", "not_local"):
        assert key in items["notes.txt"]
    assert items["notes.txt"]["id"].startswith("src-") and len(items["notes.txt"]["id"]) == 16
    # read-only: nothing moved, nothing written to the tree
    assert (root / "00_inbox" / "same.pdf").exists() and not (root / "_system" / "events.log").exists()


def test_scan_reports_not_local(cli, root, monkeypatch):
    from metarepo_engine import inbox as inbox_mod
    from metarepo_engine.readable import NOT_LOCAL, Readability

    inbox(root, "stub.pdf", PDF)
    monkeypatch.setattr(inbox_mod, "classify", lambda p: Readability(NOT_LOCAL, "dataless"))
    code, env, _ = run(cli, root, "inbox", "scan")
    item = env["data"]["items"][0]
    assert item["not_local"] is True and item["sha256"] is None and "dataless" in item["note"]


def test_scan_provisional_writes_outside_tree_and_gates_policy(cli, make_project, tmp_path):
    root = make_project(policy={"block_patterns": [{"name": "token-like", "regex": r"SECRET-[0-9]{6}"}],
                                "block_terms": ["forbidden phrase"]})
    inbox(root, "ok.txt", DOC)
    inbox(root, "bad.txt", "line one\nvalue SECRET-123456 here\nthe Forbidden Phrase appears\n")
    stg = tmp_path / "stg"
    code, env, _ = run(cli, root, "inbox", "scan", "--provisional", str(stg))
    assert code == 0, env
    items = {i["name"]: i for i in env["data"]["items"]}
    assert items["ok.txt"]["provisional"]["policy"]["blocked"] is False
    bad = items["bad.txt"]["provisional"]["policy"]
    assert bad["blocked"] is True
    assert {f["rule"] for f in bad["findings"]} == {"token-like", "block_terms[0]"}
    dump = json.dumps(env)
    assert "SECRET-123456" not in dump and "orbidden phrase" not in dump  # never echoed (AC4)
    assert Path(items["ok.txt"]["provisional"]["path"]).read_text().strip().startswith("# Title")
    assert not list((root / "20_extracts").iterdir())  # nothing written into the tree
    code, env, _ = run(cli, root, "inbox", "scan", "--provisional", str(root / "inside"))
    assert code == 3 and env["errors"][0]["code"] == "staging_inside_tree"


# ----------------------------------------------------------------------------- T4 policy (R9, AC4)
def test_policy_masking_and_locations():
    f = policy.scan_text("a\nb token=ABCDEFGHIJ\n", {"block_patterns": [{"name": "r", "regex": "ABC[A-J]+"}]})
    assert [(x.rule, x.line, x.column) for x in f] == [("r", 2, 9)]
    assert "ABCDEFGHIJ" not in json.dumps(f[0].as_dict()) and policy.mask("abc") == "***"
    assert policy.scan_text("nothing", None) == []
    with pytest.raises(Exception, match="policy.block_patterns"):
        policy.scan_text("x", {"block_patterns": [{"name": "bad", "regex": "("}]})


# ----------------------------------------------------------------------------- T3 bundles (R5, R6, AC2, AC3)
def lim(**kw):
    return Limits(**{"forbidden": [], **kw})


def test_zip_slip_refused_before_any_write(tmp_path, staging):
    z = zipped(tmp_path / "evil.zip", {"ok.txt": "fine", "../../escape.txt": "x"})
    with pytest.raises(BundleError) as exc:
        bundles.validate_and_expand(z, staging, Limits.from_config({}))
    assert exc.value.code == "bundle_path_traversal"
    assert not (staging / "files").exists() or not any((staging / "files").rglob("*"))
    assert not (tmp_path.parent / "escape.txt").exists()


@pytest.mark.parametrize("name,code", [("/abs/file.txt", "bundle_path_traversal"),
                                       ("C:\\Windows\\x.txt", "bundle_path_traversal"),
                                       ("dir\\..\\..\\x.txt", "bundle_path_traversal"),
                                       ("docs/CON.txt", "bundle_invalid_name"),
                                       ("docs/a:b.txt", "bundle_invalid_name"),
                                       ("docs/trailing.", "bundle_invalid_name")])
def test_bad_member_names(tmp_path, staging, name, code):
    z = zipped(tmp_path / "bad.zip", {"fine.txt": "a", name: "b"})
    with pytest.raises(BundleError) as exc:
        bundles.validate_and_expand(z, staging, Limits.from_config({}))
    assert exc.value.code == code


def test_ratio_bomb_and_total_size_refused(tmp_path, staging):
    z = zipped(tmp_path / "bomb.zip", {"zeros.bin": b"\0" * (5 * 1024 * 1024)})
    with pytest.raises(BundleError) as exc:
        bundles.validate_and_expand(z, staging, Limits.from_config({"intake": {"bundle_max_ratio": 100}}))
    assert exc.value.code == "bundle_ratio"
    big = zipped(tmp_path / "big.zip", {"a.bin": os.urandom(2 * 1024 * 1024)})
    with pytest.raises(BundleError) as exc:
        bundles.validate_and_expand(big, staging, Limits.from_config({"intake": {"bundle_max_uncompressed_mb": 1}}))
    assert exc.value.code == "bundle_too_large"
    assert not any((staging / "files").rglob("*")) if (staging / "files").exists() else True


def test_lying_header_is_caught_while_streaming(tmp_path, staging, monkeypatch):
    z = zipped(tmp_path / "lie.zip", {"a.bin": os.urandom(3 * 1024 * 1024)})
    real = bundles._zip_entries

    def liar(zf):
        out = real(zf)
        for e in out:
            e.size = 10
        return out

    monkeypatch.setattr(bundles, "_zip_entries", liar)
    with pytest.raises(BundleError) as exc:
        bundles.validate_and_expand(z, staging, Limits.from_config({"intake": {"bundle_max_uncompressed_mb": 1, "bundle_max_ratio": 10000}}))
    assert exc.value.code == "bundle_too_large"
    assert not any(p.is_file() for p in (staging / "files").rglob("*"))


def test_symlink_and_encrypted_members_refused(tmp_path, staging):
    info = zipfile.ZipInfo("link")
    info.external_attr = (stat.S_IFLNK | 0o777) << 16
    z = zipped(tmp_path / "ln.zip", {"a.txt": "a"}, extra=[(info, "a.txt")])
    with pytest.raises(BundleError) as exc:
        bundles.validate_and_expand(z, staging, Limits.from_config({}))
    assert exc.value.code == "bundle_symlink"
    # encrypted flag bit set on a member
    z2 = zipped(tmp_path / "enc.zip", {"a.txt": "a"})
    raw = bytearray(z2.read_bytes())
    for sig, off in ((b"PK\x03\x04", 6), (b"PK\x01\x02", 8)):
        at = raw.find(sig)
        raw[at + off] |= 1
    z2.write_bytes(bytes(raw))
    with pytest.raises(BundleError) as exc:
        bundles.validate_and_expand(z2, staging, Limits.from_config({}))
    assert exc.value.code == "bundle_encrypted" and "password" in exc.value.message


def test_tar_symlink_and_tgz_expand(tmp_path, staging):
    tgz = tmp_path / "pack.tar.gz"
    with tarfile.open(tgz, "w:gz") as tf:
        data = b"hello"
        ti = tarfile.TarInfo("d/h.txt")
        ti.size = len(data)
        tf.addfile(ti, io.BytesIO(data))
    m = bundles.validate_and_expand(tgz, staging, Limits.from_config({}))
    assert [r["member_path"] for r in m["members"]] == ["d/h.txt"] and m["container"]["slug"] == "pack"
    bad = tmp_path / "ln.tar"
    with tarfile.open(bad, "w") as tf:
        ti = tarfile.TarInfo("l")
        ti.type, ti.linkname = tarfile.SYMTYPE, "/etc/passwd"
        tf.addfile(ti)
    with pytest.raises(BundleError) as exc:
        bundles.validate_and_expand(bad, tmp_path / "s2", Limits.from_config({}))
    assert exc.value.code == "bundle_symlink"


def test_unsupported_and_missing_external_tool(tmp_path, staging, monkeypatch):
    p = tmp_path / "x.7z"
    p.write_bytes(b"7z\xbc\xaf\x27\x1c" + b"\0" * 40)
    monkeypatch.setattr(bundles, "external_tool", lambda kind: None)
    with pytest.raises(BundleError) as exc:
        bundles.validate_and_expand(p, staging, Limits.from_config({}))
    assert exc.value.code == "bundle_unsupported" and "optional tool" in exc.value.message
    with pytest.raises(BundleError):
        bundles.validate_and_expand(tmp_path / "plain.txt", staging, Limits.from_config({}))


def test_external_tool_path_with_fake_seven_zip(tmp_path, staging, monkeypatch):
    fake = tmp_path / "bin" / "7z"
    fake.parent.mkdir()
    fake.write_text("#!/bin/sh\n"
                    'if [ "$1" = "l" ]; then printf "Path = a/b.txt\\nSize = 3\\nFolder = -\\nAttributes = A\\nEncrypted = -\\n\\n"; exit 0; fi\n'
                    'for a in "$@"; do case "$a" in -o*) d="${a#-o}";; esac; done\n'
                    'mkdir -p "$d/a"; printf abc > "$d/a/b.txt"\n')
    fake.chmod(0o755)
    arc = tmp_path / "x.7z"
    arc.write_bytes(b"7z\xbc\xaf\x27\x1c" + b"\0" * 40)
    monkeypatch.setattr(bundles, "external_tool", lambda kind: str(fake))
    m = bundles.validate_and_expand(arc, staging, Limits.from_config({}))
    assert [r["member_path"] for r in m["members"]] == ["a/b.txt"]
    assert Path(m["members"][0]["staged"]).read_bytes() == b"abc"


def test_ac2_zip_with_junk_duplicate_code_folder_and_nested_zip(cli, root, staging, tmp_path):
    dup = add_source(root, "cat/dup.pdf", PDF, media_type="application/pdf")
    inner = tmp_path / "inner.zip"
    zipped(inner, {"deep.txt": "deep text", "deeper.zip": zipped(tmp_path / "deeper.zip", {"x.txt": "x"}).read_bytes()})
    pack = zipped(root / "00_inbox" / "pack.zip", {
        ".DS_Store": "j", "__MACOSX/._a.pdf": "j", "docs/a.pdf": PDF2, "docs/dup-renamed.pdf": PDF,
        "app/package.json": "{}", "app/src/main.py": "print(1)", "app/.git/config": "[core]", "app/.env": "S=1",
        "docs/inner.zip": inner.read_bytes()})
    code, env, _ = run(cli, root, "unbundle", "00_inbox/pack.zip", "--staging", str(staging))
    assert code == 0, env
    m = env["data"]
    paths = {r["member_path"]: r for r in m["members"]}
    assert ".DS_Store" in m["junk"] and "__MACOSX/._a.pdf" in m["junk"]
    assert paths["docs/dup-renamed.pdf"]["duplicate_of"] == dup["id"]
    assert paths["app/src/main.py"]["hint"] == "code" and paths["app/src/main.py"]["suggested_decision"] == "reject"
    assert paths["app/src/main.py"]["suggested_on_reject"] == "move-to-shared"
    assert {e["path"] for e in m["excluded"]} >= {"app/.git/config", "app/.env"}
    assert not (Path(m["files_root"]) / "app" / ".git").exists() and not (Path(m["files_root"]) / "app" / ".env").exists()
    assert "docs/inner/deep.txt" in paths                       # nested ZIP expanded (level 2)
    assert "docs/inner/deeper.zip" in paths and paths["docs/inner/deeper.zip"]["bundle"] and not paths["docs/inner/deeper.zip"]["expanded"]
    assert "unexpanded" in paths["docs/inner/deeper.zip"]["reason"]
    assert m["code_folders"] == [{"path": "app", "markers": [".git", "package.json"]}]
    assert (Path(m["staging"]) / "manifest.json").is_file() and Path(m["staging"]).parent == staging.resolve()
    assert m["container"]["id"].startswith("src-") and m["group"] == "pack"
    assert (root / "00_inbox" / "pack.zip").exists()          # unbundle never touches the inbox


def test_unbundle_refusal_leaves_no_files_and_dry_run_writes_nothing(cli, root, staging, tmp_path):
    zipped(root / "00_inbox" / "evil.zip", {"../x.txt": "x"})
    code, env, _ = run(cli, root, "unbundle", "00_inbox/evil.zip", "--staging", str(staging))
    assert code == 3 and env["errors"][0]["code"] == "bundle_path_traversal"
    assert not list(staging.rglob("*.txt")) and not list(staging.rglob("manifest.json"))
    zipped(root / "00_inbox" / "ok.zip", {"a.txt": "a"})
    code, env, _ = run(cli, root, "unbundle", "00_inbox/ok.zip", "--staging", str(staging), "--dry-run")
    assert code == 0 and env["data"]["dry_run"] and not list(staging.rglob("*"))
    code, env, _ = run(cli, root, "unbundle", "00_inbox/ok.zip", "--staging", str(root / "inside"))
    assert code == 3 and env["errors"][0]["code"] == "staging_inside_tree"


def test_depth_limit_zero_refuses(tmp_path, staging):
    z = zipped(tmp_path / "a.zip", {"a.txt": "a"})
    with pytest.raises(BundleError) as exc:
        bundles.validate_and_expand(z, staging, Limits.from_config({"intake": {"bundle_max_depth": 0}}))
    assert exc.value.code == "bundle_depth"


def test_ac9_folder_bundle(cli, root, staging, tmp_path):
    base = root / "00_inbox" / "Quarterly Reports"
    (base / "2026" / "q1").mkdir(parents=True)
    (base / "2026" / "q1" / "a.txt").write_text("alpha")
    (base / "2026" / "b.txt").write_text("beta")
    (base / ".DS_Store").write_text("j")
    zipped(base / "2026" / "nested.zip", {"n.txt": "nested"})
    code, env, _ = run(cli, root, "unbundle", "00_inbox/Quarterly Reports", "--staging", str(staging))
    assert code == 0, env
    m = env["data"]
    assert m["container"]["kind"] == "folder" and m["group"] == "quarterly-reports" and m["category_hint"] == "quarterly-reports"
    assert {r["member_path"] for r in m["members"]} == {"2026/q1/a.txt", "2026/b.txt", "2026/nested/n.txt"}
    assert m["junk"] == [".DS_Store"] and m["container"]["id"] is None
    assert m["top_folders"] == ["2026"]
    # symlink inside a folder bundle is refused
    (base / "link").symlink_to("/etc")
    code, env, _ = run(cli, root, "unbundle", "00_inbox/Quarterly Reports", "--staging", str(tmp_path / "s2"))
    assert code == 3 and env["errors"][0]["code"] == "bundle_symlink"


# ----------------------------------------------------------------------------- T5 place (R12, AC1)
def test_ac1_single_pdf_scenario(cli, root):
    inbox(root, "Annual Report (final).pdf", PDF)
    nonce = acquire(cli, root)
    code, env, _ = place(cli, root, nonce, "00_inbox/Annual Report (final).pdf", "--category", "Reports")
    assert code == 0, env
    d = env["data"]
    assert d["source_path"] == "10_sources/reports/annual-report-final.pdf"
    assert (root / d["source_path"]).read_bytes() == PDF
    assert not (root / "00_inbox" / "Annual Report (final).pdf").exists()
    front = frontmatter.read(root / "20_extracts/reports/annual-report-final.pdf.md").front
    assert front["triage"]["decision"] == "wiki" and front["triage"]["by"] == "member-a" and front["triage"]["at"]
    assert front["triage"]["confidence"] == "high" and front["original_name"] == "Annual Report (final).pdf"
    assert front["source_id"] == d["id"] and front["extractor"] and front["extracted_at"]  # stub filled by 008
    assert front["bundle"] == {"parent_id": None, "group": None, "member_path": None}
    assert not any((root / "00_inbox").iterdir())
    ev = [json.loads(line) for line in (root / "_system/events.log").read_text().splitlines()]
    placed = [e for e in ev if e["command"] == "place"]
    assert placed and "10_sources/reports/annual-report-final.pdf" in placed[0]["targets"] and placed[0]["decision"] == "wiki"
    row = registry.build(root).rows[0]
    assert row["triage"] == "wiki" and row["id"] == d["id"]


def test_place_is_idempotent_and_reports_duplicates(cli, root):
    nonce = acquire(cli, root)
    inbox(root, "a.pdf", PDF)
    assert place(cli, root, nonce, "00_inbox/a.pdf", "--category", "c")[0] == 0
    inbox(root, "again.pdf", PDF)
    code, env, _ = place(cli, root, nonce, "00_inbox/again.pdf", "--category", "c")
    assert code == 0 and env["data"]["status"] == "duplicate" and env["data"]["placed"] is False
    assert (root / "00_inbox" / "again.pdf").exists()  # placed twice never; removal is a separate, confirmed step


def test_place_requires_the_intake_lease(cli, root):
    inbox(root, "a.pdf", PDF)
    code, env, _ = run(cli, root, "place", "00_inbox/a.pdf", "--category", "c", "--triage", '{"decision":"wiki"}')
    assert code == 3 and env["errors"][0]["code"] == "lease_required"
    code, env, _ = run(cli, root, "place", "00_inbox/a.pdf", "--category", "c", "--triage", '{"decision":"wiki"}',
                       "--lease", "bogus")
    assert code == 3 and env["errors"][0]["code"] == "lease_lost"
    assert (root / "00_inbox" / "a.pdf").exists()
    code, env, _ = run(cli, root, "place", "00_inbox/a.pdf", "--category", "c", "--triage", '{"decision":"wiki"}', "--dry-run")
    assert code == 0 and env["data"]["status"] == "planned" and (root / "00_inbox" / "a.pdf").exists()
    assert not list((root / "10_sources").rglob("*.pdf"))


def test_place_rejects_bad_triage_and_unstaged_paths(cli, root, tmp_path):
    nonce = acquire(cli, root)
    inbox(root, "a.txt", DOC)
    code, env, _ = place(cli, root, nonce, "00_inbox/a.txt", "--category", "c", decision="reject")
    assert code == 2 and env["errors"][0]["code"] == "bad_triage"
    add_source(root, "x/y.txt", b"zz")
    code, env, _ = place(cli, root, nonce, "10_sources/x/y.txt", "--category", "c")
    assert code == 3 and env["errors"][0]["code"] == "not_staged"
    code, env, _ = place(cli, root, nonce, "00_inbox/a.txt", "--category", "_bundles")
    assert code == 2 and env["errors"][0]["code"] == "bad_category"


def test_naming_collision_and_path_limit(cli, root, make_project, tmp_path):
    nonce = acquire(cli, root)
    inbox(root, "Résumé.txt", "first")
    inbox(root, "Resume.txt", "second")
    d1 = place(cli, root, nonce, "00_inbox/Résumé.txt", "--category", "c")[1]["data"]
    d2 = place(cli, root, nonce, "00_inbox/Resume.txt", "--category", "c")[1]["data"]
    assert d1["source_path"] == "10_sources/c/resume.txt"
    assert d2["renamed_for_collision"] and d2["source_path"].startswith("10_sources/c/resume-") and d2["source_path"] != d1["source_path"]
    inbox(root, "long.txt", "x")
    code, env, _ = place(cli, root, nonce, "00_inbox/long.txt", "--category", "c", "--name", "n" * 80 + ".txt",
                         "--dry-run")
    assert code == 0  # 80-char stem fits
    code, env, _ = place(cli, root, nonce, "00_inbox/long.txt", "--category", "/".join(["a" * 70] * 3))
    assert code == 3 and env["errors"][0]["code"] == "path_too_long"
    assert (root / "00_inbox" / "long.txt").exists()


def test_bundle_member_placement_and_depth_flattening(cli, root, staging):
    nonce = acquire(cli, root)
    deep = staging / "files" / "a" / "b" / "c" / "d" / "e" / "f.txt"
    deep.parent.mkdir(parents=True)
    deep.write_text("deep content")
    code, env, _ = place(cli, root, nonce, str(deep), "--category", "reports", "--bundle-slug", "pack",
                         "--member-path", "a/b/c/d/e/f.txt", "--group", "pack", "--parent", "src-aaaaaaaaaaaa")
    assert code == 0, env
    # max_source_depth 4: reports/pack/a/b = 4 levels, the rest flattened into the file name
    assert env["data"]["source_path"] == "10_sources/reports/pack/a/b/c--d--e--f.txt" and env["data"]["flattened"]
    front = frontmatter.read(root / env["data"]["extract"]).front
    assert front["bundle"] == {"parent_id": "src-aaaaaaaaaaaa", "group": "pack", "member_path": "a/b/c/d/e/f.txt"}
    assert front["original_name"] == "f.txt"
    assert not staging.joinpath("files/a/b/c/d/e/f.txt").exists()   # moved out of staging


def test_policy_gate_in_place(cli, make_project):
    root = make_project(policy={"block_patterns": [{"name": "token-like", "regex": r"SECRET-[0-9]{6}"}]})
    nonce = acquire(cli, root)
    inbox(root, "bad.txt", "x SECRET-123456 y")
    code, env, _ = place(cli, root, nonce, "00_inbox/bad.txt", "--category", "c")
    assert code == 3 and env["errors"][0]["code"] == "policy_blocked" and "token-like" in env["errors"][0]["message"]
    assert "SECRET-123456" not in json.dumps(env)
    code, env, _ = place(cli, root, nonce, "00_inbox/bad.txt", "--category", "c", decision="reference")
    assert code == 3 and env["errors"][0]["code"] == "policy_confirmation_required"
    code, env, _ = place(cli, root, nonce, "00_inbox/bad.txt", "--category", "c", "--confirm-policy", decision="reference")
    assert code == 0 and env["data"]["policy"]["blocked"] is True
    assert "SECRET-123456" not in json.dumps(env["data"]["policy"])
    assert (root / "10_sources/c/bad.txt").exists()


def test_supersedes_and_unknown_supersedes(cli, root):
    old = add_source(root, "c/old.txt", b"old content")
    nonce = acquire(cli, root)
    inbox(root, "new.txt", "new content")
    code, env, _ = place(cli, root, nonce, "00_inbox/new.txt", "--category", "c", "--supersedes", "src-ffffffffffff")
    assert code == 3 and env["errors"][0]["code"] == "unknown_source"
    code, env, _ = place(cli, root, nonce, "00_inbox/new.txt", "--category", "c", "--supersedes", old["id"])
    assert code == 0
    assert frontmatter.read(root / env["data"]["extract"]).front["supersedes"] == [old["id"]]


def test_container_placed_in_bundles_with_member_listing(cli, root, tmp_path):
    z = zipped(root / "00_inbox" / "pack.zip", {"a.txt": "a"})
    nonce = acquire(cli, root)
    members = tmp_path / "members.json"
    members.write_text(json.dumps([{"id": "src-123456789abc", "member_path": "a.txt", "decision": "wiki"}]))
    code, env, _ = place(cli, root, nonce, "00_inbox/pack.zip", "--container", "--members", str(members), decision="reference")
    assert code == 0, env
    assert env["data"]["source_path"] == "10_sources/_bundles/pack.zip"
    doc = frontmatter.read(root / "20_extracts/_bundles/pack.zip.md")
    assert "src-123456789abc" in doc.body and "a.txt" in doc.body and doc.front["triage"]["decision"] == "reference"
    assert doc.front["extractor"]["name"] == "bundle-manifest"


# ----------------------------------------------------------------------------- R3 / R14 / inbox cleanup (P6)
def test_duplicate_merge_adds_alias_and_removes(cli, root):
    src = add_source(root, "c/orig.pdf", PDF, original_name="orig.pdf", media_type="application/pdf")
    inbox(root, "Renamed Copy.pdf", PDF)
    inbox(root, "other.pdf", PDF2)
    nonce = acquire(cli, root)
    code, env, _ = run(cli, root, "place", "00_inbox/other.pdf", "--duplicate-of", src["id"], "--lease", nonce)
    assert code == 3 and env["errors"][0]["code"] == "not_a_duplicate" and (root / "00_inbox/other.pdf").exists()
    code, env, _ = run(cli, root, "place", "00_inbox/Renamed Copy.pdf", "--duplicate-of", src["id"], "--lease", nonce)
    assert code == 0 and env["data"]["alias_added"] == "Renamed Copy.pdf"
    assert not (root / "00_inbox/Renamed Copy.pdf").exists()
    assert frontmatter.read(root / src["extract"]).front["aliases"] == ["Renamed Copy.pdf"]
    inbox(root, "Renamed Copy.pdf", PDF)
    code, env, _ = run(cli, root, "place", "00_inbox/Renamed Copy.pdf", "--duplicate-of", src["id"], "--lease", nonce)
    assert env["data"]["alias_added"] is None  # no duplicate alias
    ev = [json.loads(line) for line in (root / "_system/events.log").read_text().splitlines()]
    removed = [e for e in ev if e["command"] == "place" and e.get("mode") == "duplicate"]
    assert removed and removed[0]["removed"][0]["sha256"]
    assert (root / src["path"]).read_bytes() == PDF  # source untouched (P6)


def test_discard_guards_content_and_cleans_folder(cli, root, staging):
    src = add_source(root, "c/a.txt", b"alpha", original_name="a.txt")
    base = root / "00_inbox" / "folder"
    (base / "sub").mkdir(parents=True)
    (base / "sub" / "a.txt").write_bytes(b"alpha")      # placed (registered hash)
    (base / ".DS_Store").write_bytes(b"j")              # junk
    (base / "keep.txt").write_bytes(b"unregistered")
    nonce = acquire(cli, root)
    code, env, _ = run(cli, root, "place", "00_inbox/folder", "--discard", "--lease", nonce)
    assert code == 3 and env["errors"][0]["code"] == "not_captured"
    assert (base / "keep.txt").exists() and (base / "sub" / "a.txt").exists()   # nothing removed on refusal
    (base / "keep.txt").unlink()
    code, env, _ = run(cli, root, "place", "00_inbox/folder", "--discard", "--lease", nonce)
    assert code == 0 and not base.exists()
    assert {r["why"] for r in env["data"]["removed"]} == {"junk", "captured"}
    ev = [json.loads(line) for line in (root / "_system/events.log").read_text().splitlines()]
    assert any(e.get("mode") == "discard" and e["removed"][0]["path"].startswith("00_inbox/folder") for e in ev)
    assert (root / src["path"]).exists()
    code, env, _ = run(cli, root, "place", "00_inbox", "--discard", "--lease", nonce)
    assert code == 3 and env["errors"][0]["code"] == "not_staged"
    code, env, _ = run(cli, root, "place", "10_sources/c/a.txt", "--discard", "--lease", nonce)
    assert code == 3 and env["errors"][0]["code"] == "not_staged"  # sources are never removed (P6)


@pytest.mark.parametrize("mode", ["leave-in-inbox", "move-to-shared", "delete"])
def test_reject_modes_on_inbox_file(cli, root, mode):
    p = inbox(root, "junk-tool.exe", b"MZ binary")
    import hashlib
    sha = hashlib.sha256(b"MZ binary").hexdigest()
    nonce = acquire(cli, root)
    args = ["place", "00_inbox/junk-tool.exe", "--reject", "--on-reject", mode, "--lease", nonce]
    if mode == "delete":
        code, env, _ = run(cli, root, *args)
        assert code == 4 and p.exists()                       # needs the content hash confirmation
        args += ["--confirm", sha]
    code, env, _ = run(cli, root, *args)
    assert code == 0, env
    if mode == "leave-in-inbox":
        assert p.exists() and env["data"]["status"] == "left"
    elif mode == "move-to-shared":
        assert not p.exists() and (root / "shared/_from-inbox/junk-tool.exe").read_bytes() == b"MZ binary"
    else:
        assert not p.exists() and env["data"]["removed"][0]["sha256"] == sha


def test_reject_default_mode_comes_from_config_and_staged_member_moves_to_shared(cli, make_project, staging):
    root = make_project(intake={"on_reject": "move-to-shared"})
    f = staging / "files" / "app" / "main.py"
    f.parent.mkdir(parents=True)
    f.write_text("print(1)")
    nonce = acquire(cli, root)
    code, env, _ = run(cli, root, "place", str(f), "--reject", "--bundle-slug", "Code Pack", "--member-path",
                       "app/main.py", "--lease", nonce)
    assert code == 0 and env["data"]["destination"] == "shared/_from-inbox/code-pack/app/main.py"
    assert (root / env["data"]["destination"]).read_text() == "print(1)" and not f.exists()
    g = staging / "files" / "x.txt"
    g.write_text("x")
    code, env, _ = run(cli, root, "place", str(g), "--reject", "--on-reject", "delete", "--lease", nonce)
    assert code == 0 and env["data"]["status"] == "dropped-with-staging"


# ----------------------------------------------------------------------------- R16 / R17 / R11
def test_retriage_updates_frontmatter_only(cli, root):
    src = add_source(root, "c/a.txt", b"alpha text", triage="defer")
    nonce = acquire(cli, root)
    before = (root / src["path"]).read_bytes()
    code, env, _ = run(cli, root, "place", src["id"], "--retriage", "--lease", nonce, "--triage",
                       '{"decision": "wiki", "reason": "now relevant", "confidence": "high"}')
    assert code == 0, env
    assert env["data"]["from"] == "defer" and env["data"]["to"] == "wiki"
    t = frontmatter.read(root / src["extract"]).front["triage"]
    assert t["decision"] == "wiki" and t["reason"] == "now relevant" and t["by"] == "member-a"
    assert (root / src["path"]).read_bytes() == before
    code, env, _ = run(cli, root, "place", src["id"], "--retriage", "--lease", nonce, "--triage",
                       '{"decision": "wiki", "reason": "now relevant", "confidence": "high"}')
    assert env["data"]["status"] == "unchanged"
    code, env, _ = run(cli, root, "place", src["id"], "--retriage", "--lease", nonce, "--triage", '{"decision": "reject"}')
    assert code == 2 and env["errors"][0]["code"] == "bad_triage"
    code, env, _ = run(cli, root, "place", "src-000000000000", "--retriage", "--lease", nonce, "--triage", '{"decision": "wiki"}')
    assert code == 3 and env["errors"][0]["code"] == "unknown_source"


def test_retriage_to_wiki_respects_policy(cli, make_project):
    root = make_project(policy={"block_terms": ["classified"]})
    src = add_source(root, "c/a.txt", b"alpha", triage="defer", body="This is classified material.\n")
    nonce = acquire(cli, root)
    code, env, _ = run(cli, root, "place", src["id"], "--retriage", "--lease", nonce, "--triage", '{"decision": "wiki"}')
    assert code == 3 and env["errors"][0]["code"] == "policy_blocked" and "classified" not in env["errors"][0]["message"]
    code, env, _ = run(cli, root, "place", src["id"], "--retriage", "--lease", nonce, "--triage", '{"decision": "reference"}')
    assert code == 0


def test_headless_proposal_and_completed_event(cli, root, tmp_path):
    prop = tmp_path / "p.json"
    prop.write_text(json.dumps({"rows": [{"name": "a.pdf", "decision": "wiki"}]}))
    inbox(root, "a.pdf", PDF)
    code, env, _ = run(cli, root, "place", "--proposal", str(prop))
    assert code == 0, env
    rel = env["data"]["proposal"]
    assert rel.startswith("personal/member-a/intake-proposals/") and json.loads((root / rel).read_text())["rows"]
    assert (root / "00_inbox/a.pdf").exists() and not list((root / "10_sources").rglob("*.pdf"))   # AC5: nothing placed
    code, env, _ = run(cli, root, "place", "--completed", '{"wiki": 2, "reference": 1, "defer": 0, "reject": 1}')
    assert code == 0
    ev = [json.loads(line) for line in (root / "_system/events.log").read_text().splitlines()]
    done = [e for e in ev if e["command"] == "intake.completed"]
    assert len(done) == 1 and done[0]["summary"]["wiki"] == 2


# ----------------------------------------------------------------------------- AC6 lease contention
def test_ac6_second_member_is_told_who_holds_the_intake_lease(cli, root):
    first = acquire(cli, root, "member-a")
    code, env, _ = run(cli, root, "lease", "acquire", "intake", "--session", "s-b", actor="member-b")
    err = env["errors"][0]
    held = json.loads((root / "_system/locks/intake.lease.json").read_text())
    assert code == 3 and err["code"] == "lease_held"
    assert "member-a" in err["message"]                      # who
    assert held["expires_at"] in err["message"]              # until when
    assert first not in json.dumps(env)                      # the nonce is never disclosed
    # the second member cannot write with a nonce that is not the holder's
    inbox(root, "a.txt", DOC)
    code, env, _ = run(cli, root, "place", "00_inbox/a.txt", "--category", "c", "--triage", '{"decision":"wiki"}',
                       "--lease", "not-mine", actor="member-b")
    assert code == 3 and env["errors"][0]["code"] == "lease_lost" and (root / "00_inbox/a.txt").exists()


def test_two_bundles_share_one_staging_directory(cli, root, staging):
    zipped(root / "00_inbox" / "first.zip", {"a.txt": "from first", "docs/b.txt": "first b"})
    zipped(root / "00_inbox" / "second.zip", {"a.txt": "from second", "../escape.txt": "x"})
    zipped(root / "00_inbox" / "third.zip", {"a.txt": "from third"})
    code, env, _ = run(cli, root, "unbundle", "00_inbox/first.zip", "--staging", str(staging))
    assert code == 0, env
    first = env["data"]
    code, env, _ = run(cli, root, "unbundle", "00_inbox/second.zip", "--staging", str(staging))
    assert code == 3 and env["errors"][0]["code"] == "bundle_path_traversal"
    code, env, _ = run(cli, root, "unbundle", "00_inbox/third.zip", "--staging", str(staging))
    assert code == 0, env
    third = env["data"]
    assert first["staging"] != third["staging"]
    assert Path(first["members"][0]["staged"]).read_text() == "from first"       # not clobbered
    assert {Path(r["staged"]).read_text() for r in first["members"]} == {"from first", "first b"}
    assert json.loads((Path(first["staging"]) / "manifest.json").read_text())["container"]["name"] == "first.zip"
    assert Path(third["members"][0]["staged"]).read_text() == "from third"
    assert len([p for p in staging.iterdir() if p.is_dir()]) == 2                  # refused bundle left nothing


def test_place_refuses_cloud_native_and_not_local_files(cli, root, monkeypatch):
    from metarepo_engine import readable
    from metarepo_engine.readable import NOT_LOCAL, Readability

    nonce = acquire(cli, root)
    inbox(root, "pointer.gdoc", b'{"doc_id": "abc"}')
    code, env, _ = place(cli, root, nonce, "00_inbox/pointer.gdoc", "--category", "c")
    assert code == 3 and env["errors"][0]["code"] == "cloud_native" and "export" in env["errors"][0]["message"]
    assert (root / "00_inbox/pointer.gdoc").exists() and not list((root / "10_sources").rglob("*.gdoc"))
    inbox(root, "stub.pdf", PDF)
    monkeypatch.setattr(readable, "classify", lambda p: Readability(NOT_LOCAL, "dataless"))
    code, env, _ = place(cli, root, nonce, "00_inbox/stub.pdf", "--category", "c")
    assert code == 3 and env["errors"][0]["code"] == "not_local" and (root / "00_inbox/stub.pdf").exists()
    zipped(root / "00_inbox" / "pack.zip", {"a.txt": "a"})
    code, env, _ = run(cli, root, "unbundle", "00_inbox/pack.zip", "--staging", str(root.parent / "stg"))
    assert code == 3 and env["errors"][0]["code"] == "not_local"


def test_r10_long_name_gets_a_proposed_shorter_name(cli, root):
    nonce = acquire(cli, root)
    inbox(root, "x.txt", DOC)
    category = "/".join(["c" * 50] * 3)               # 152 characters of folders: a shorter name still fits
    code, env, _ = place(cli, root, nonce, "00_inbox/x.txt", "--category", category, "--name", "n" * 80 + ".txt")
    err = env["errors"][0]
    assert code == 3 and err["code"] == "path_too_long" and "--name " in err["message"]
    proposed = err["message"].split("--name ")[1].rstrip(".")
    assert proposed.endswith(".txt") and len(proposed) < 80 + 4
    code, env, _ = place(cli, root, nonce, "00_inbox/x.txt", "--category", category, "--name", proposed)
    assert code == 0, env
    assert len(env["data"]["extract"]) <= 200
    inbox(root, "y.txt", "other")
    code, env, _ = place(cli, root, nonce, "00_inbox/y.txt", "--category", "/".join(["d" * 70] * 3))
    assert code == 3 and "shorter category" in env["errors"][0]["message"]


def test_end_to_end_headless_flow_leaves_inbox_empty(cli, root, staging):
    z = zipped(root / "00_inbox" / "pack.zip", {"one.txt": "one text", "two.txt": "two text", ".DS_Store": "j"})
    inbox(root, "solo.txt", DOC)
    nonce = acquire(cli, root)
    code, env, _ = run(cli, root, "unbundle", "00_inbox/pack.zip", "--staging", str(staging), "--provisional")
    m = env["data"]
    assert all(r["provisional"]["quality"] for r in m["members"])
    for r in m["members"]:
        c, e, _ = place(cli, root, nonce, r["staged"], "--category", "packs", "--bundle-slug", "pack",
                        "--member-path", r["member_path"], "--group", "pack", "--parent", m["container"]["id"])
        assert c == 0, e
    members = staging / "members.json"
    members.write_text(json.dumps([{"id": r["id"], "member_path": r["member_path"], "decision": "wiki"} for r in m["members"]]))
    assert place(cli, root, nonce, "00_inbox/pack.zip", "--container", "--members", str(members), decision="reference")[0] == 0
    assert place(cli, root, nonce, "00_inbox/solo.txt", "--category", "misc")[0] == 0
    left = [p for p in (root / "00_inbox").rglob("*") if p.is_file()]
    assert left == []
    rows = registry.build(root).rows
    assert len(rows) == 4 and {r["parent_id"] for r in rows if r["parent_id"]} == {m["container"]["id"]}
