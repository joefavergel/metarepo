"""002/R12 source ids (002/AC3), 003/R10 hashing and its cache, forbidden-path defaults (002 section 6)."""
import hashlib

import pytest

from metarepo_engine import config as cfgmod
from metarepo_engine import hashing

H1 = "9f2c41ab03de" + "0" * 52
H2_SAME_PREFIX = "9f2c41ab03de" + "1" * 52


def test_source_id_format():
    assert hashing.source_id(H1) == "src-9f2c41ab03de"
    assert hashing.SOURCE_ID_RE.match(hashing.source_id(H1))
    with pytest.raises(ValueError):
        hashing.source_id("abc")
    assert hashing.source_id(H1.upper()) == "src-9f2c41ab03de"


def test_collision_extends_to_16_for_the_newcomer_and_keeps_both():
    known = {"src-9f2c41ab03de": H1}
    assert hashing.source_id(H1, known) == "src-9f2c41ab03de"  # same bytes: same id (duplicate)
    newcomer = hashing.source_id(H2_SAME_PREFIX, known)
    assert newcomer == "src-" + H2_SAME_PREFIX[:16] and len(newcomer) == 4 + 16
    known[newcomer] = H2_SAME_PREFIX
    assert hashing.source_id(H1, known) == "src-9f2c41ab03de" and hashing.source_id(H2_SAME_PREFIX, known) == newcomer


def test_collision_chain_extends_further():
    third = "9f2c41ab03de" + "1" * 4 + "2" * 48
    known = {"src-9f2c41ab03de": H1, "src-" + H2_SAME_PREFIX[:16]: H2_SAME_PREFIX}
    assert hashing.source_id(third, known) == "src-" + third[:20]


def test_ac3_identical_files_under_different_names_share_an_id_and_are_duplicates(tmp_path, cli):
    a, b, c = tmp_path / "Report.PDF", tmp_path / "copy-of-report (1).pdf", tmp_path / "other.pdf"
    a.write_bytes(b"same bytes")
    b.write_bytes(b"same bytes")
    c.write_bytes(b"different bytes")
    code, env, _ = cli("id", str(a), str(b), str(c), "--json")
    assert code == 0
    ids = {Path_.split("/")[-1]: i["id"] for i in env["data"]["files"] for Path_ in [i["path"]]}
    assert ids["Report.PDF"] == ids["copy-of-report (1).pdf"] != ids["other.pdf"]
    digest = hashlib.sha256(b"same bytes").hexdigest()
    assert ids["Report.PDF"] == f"src-{digest[:12]}"
    (dup,) = env["data"]["duplicates"]
    assert dup["id"] == ids["Report.PDF"] and len(dup["paths"]) == 2


def test_hash_command(tmp_path, cli):
    f = tmp_path / "x.bin"
    f.write_bytes(b"abc")
    code, env, _ = cli("hash", str(f), "--json")
    assert code == 0 and env["data"]["sha256"] == hashlib.sha256(b"abc").hexdigest() and env["data"]["size"] == 3
    code, env, _ = cli("hash", str(tmp_path / "missing"), "--json")
    assert code == 3 and env["errors"][0]["code"] == "file_not_found"
    code, out, _ = cli("id", str(f))
    assert out.startswith("src-" + hashlib.sha256(b"abc").hexdigest()[:12])


def test_streaming_hash_handles_multi_chunk_files(tmp_path):
    f = tmp_path / "big.bin"
    data = b"0123456789abcdef" * (1024 * 200)  # 3.2 MiB: several 1 MiB chunks
    f.write_bytes(data)
    assert hashing.sha256_file(f) == hashlib.sha256(data).hexdigest()


def test_empty_file_hash(tmp_path):
    f = tmp_path / "empty"
    f.write_bytes(b"")
    assert hashing.sha256_file(f) == hashlib.sha256(b"").hexdigest()


def test_hash_cache_hits_and_invalidation(tmp_path, monkeypatch):
    f = tmp_path / "a.bin"
    f.write_bytes(b"one")
    cache = hashing.HashCache("proj-id")
    calls = []
    real = hashing.sha256_file
    monkeypatch.setattr(hashing, "sha256_file", lambda p: calls.append(p) or real(p))
    first = cache.sha256(f)
    assert cache.sha256(f) == first and len(calls) == 1  # second call served from cache
    cache.save()
    again = hashing.HashCache("proj-id")
    assert again.sha256(f) == first and len(calls) == 1  # persisted in the local cache
    f.write_bytes(b"two-different-size")  # size and mtime change
    assert again.sha256(f) == hashlib.sha256(b"two-different-size").hexdigest() and len(calls) == 2


def test_hash_cache_is_disposable_and_outside_the_tree(tmp_path):
    f = tmp_path / "proj" / "a.bin"
    f.parent.mkdir()
    f.write_bytes(b"x")
    cache = hashing.HashCache("pid-1")
    cache.sha256(f)
    cache.save()
    path = cache._path
    assert path.is_file() and tmp_path / "proj" not in path.parents
    path.write_text("{corrupt", encoding="utf-8")  # a damaged cache is rebuilt silently
    assert hashing.HashCache("pid-1").sha256(f) == hashlib.sha256(b"x").hexdigest()
    path.unlink()
    assert hashing.HashCache("pid-1").sha256(f) == hashlib.sha256(b"x").hexdigest()  # losing it loses nothing


def test_cache_without_project_id_is_memory_only(tmp_path):
    f = tmp_path / "a"
    f.write_bytes(b"x")
    cache = hashing.HashCache(None)
    cache.sha256(f)
    cache.save()  # no-op


def test_group_duplicates():
    groups = hashing.group_duplicates({"a": "1", "b": "2", "c": "1", "d": "3", "e": "1"})
    assert groups == [["a", "c", "e"]]


# ---- forbidden paths (002 section 6)
def _forbidden(rel, extra=(), is_dir=False):
    cfg = {"policy": {"forbidden_paths": list(extra)}}
    return cfgmod.is_forbidden(rel, cfgmod.compile_forbidden(cfgmod.effective_forbidden_paths(cfg)), is_dir=is_dir)


@pytest.mark.parametrize(
    "rel",
    [".git/config", "shared/tools/app/.git/HEAD", "node_modules/x/y.js", "a/.venv/bin/python", "a/venv/x",
     "a/__pycache__/m.pyc", "m.pyc", "deep/dir/mod.pyc", ".env", "shared/.env.local", "keys/server.pem",
     "a/b/id_rsa", "a/id_rsa.pub", "x/.DS_Store", "Thumbs.db", "a/desktop.ini", "~$report.docx", "x/draft.tmp",
     "x/file.crdownload", "x/big.part", ".hg/x", ".svn/x", "k.key", "a/THUMBS.DB"],
)
def test_default_forbidden_paths_match(rel):
    assert _forbidden(rel), rel


@pytest.mark.parametrize(
    "rel",
    ["30_wiki/index.md", "shared/tools/readme.md", "environment.md", "monkey.keys", "gitignore.txt", "a/envelope.md",
     "10_sources/papers/a.pdf", "my.parts.md", "vendor/x"],
)
def test_ordinary_paths_are_not_forbidden(rel):
    assert not _forbidden(rel), rel


def test_project_patterns_are_appended_to_defaults():
    assert _forbidden("shared/build/out.bin", extra=["build/"])
    assert _forbidden("secret-notes.txt", extra=["/secret-notes.txt"])
    assert not _forbidden("sub/secret-notes.txt", extra=["/secret-notes.txt"])  # anchored
    assert _forbidden("a/b/c/data.sqlite", extra=["**/data.sqlite"])
    assert _forbidden("x/y/z.tmp", extra=["# comment", ""])  # defaults still apply
    assert _forbidden(".git/x")  # defaults always apply
    assert _forbidden("dirpattern", extra=["dirpattern/"], is_dir=True)
    assert not _forbidden("dirpattern", extra=["dirpattern/"], is_dir=False)
    assert len(cfgmod.FORBIDDEN_PATH_DEFAULTS) == 20
