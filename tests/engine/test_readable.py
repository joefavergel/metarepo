"""Readability check (004/R12, threat model rows 4 and 5)."""
from __future__ import annotations

import os

import pytest

from metarepo_engine import readable
from metarepo_engine.hashing import HashCache, sha256_file
from metarepo_engine.jsonout import EngineError


def test_regular_and_empty_files(tmp_path):
    f = tmp_path / "a.txt"
    f.write_text("hello", encoding="utf-8")
    assert readable.classify(f).status == readable.OK
    e = tmp_path / "e.txt"
    e.write_bytes(b"")
    assert readable.classify(e).status == readable.EMPTY and readable.classify(e).usable
    assert sha256_file(e)  # an empty file hashes fine


def test_sparse_placeholder_is_not_local(tmp_path):
    f = tmp_path / "placeholder.pdf"
    with open(f, "wb") as fh:
        fh.truncate(1_000_000)
    if os.stat(f).st_blocks != 0:
        pytest.skip("this file system does not make truncated files sparse")
    assert readable.classify(f).status == readable.NOT_LOCAL
    with pytest.raises(EngineError) as exc:
        sha256_file(f)
    assert exc.value.code == "not_local" and exc.value.exit_code == 3
    assert "available offline" in exc.value.message


def test_sparse_file_with_real_content_is_fine(tmp_path):
    f = tmp_path / "real.bin"
    with open(f, "wb") as fh:
        fh.write(b"content")
        fh.truncate(1_000_000)
    assert readable.classify(f).status == readable.OK


class _Stat:
    def __init__(self, **kw):
        self.st_mode = 0o100644
        self.st_size = 10
        self.st_blocks = 8
        for k, v in kw.items():
            setattr(self, k, v)


@pytest.mark.parametrize("attrs", [{"st_flags": 0x40000000}, {"st_file_attributes": 0x00400000},
                                   {"st_file_attributes": 0x00040000}, {"st_file_attributes": 0x1000}])
def test_platform_placeholder_flags(tmp_path, monkeypatch, attrs):
    f = tmp_path / "x.docx"
    f.write_text("stub")
    monkeypatch.setattr(readable.os, "stat", lambda p: _Stat(**attrs))
    assert readable.classify(f).status == readable.NOT_LOCAL


def test_read_errors_that_mean_not_downloaded(tmp_path, monkeypatch):
    f = tmp_path / "x.docx"
    f.write_text("stub")

    def boom(p):
        raise OSError(5, "Input/output error")

    monkeypatch.setattr(readable.os, "stat", boom)
    assert readable.classify(f).status == readable.NOT_LOCAL


def test_missing_file_still_raises_oserror(tmp_path):
    with pytest.raises(FileNotFoundError):
        readable.classify(tmp_path / "nope.txt")


def test_cloud_native_pointer(tmp_path):
    f = tmp_path / "Notes.GDOC"
    f.write_text('{"doc_id": "abc"}')
    assert readable.classify(f).status == readable.CLOUD_NATIVE
    with pytest.raises(EngineError) as exc:
        readable.ensure_local(f)
    assert exc.value.code == "cloud_native" and "export" in exc.value.message


def test_hash_cache_checks_before_trusting_the_cache(tmp_path):
    f = tmp_path / "a.txt"
    f.write_text("hello")
    cache = HashCache(None)
    cache.sha256(f)
    f.unlink()
    f.write_text(".")  # same path, different file
    with open(f, "wb") as fh:
        fh.truncate(100)
    if os.stat(f).st_blocks == 0:
        with pytest.raises(EngineError):
            cache.sha256(f)


def test_hash_command_refuses_a_placeholder(cli, tmp_path):
    f = tmp_path / "Plan.gdoc"
    f.write_text("{}")
    code, env, _ = cli("hash", str(f), "--json")
    assert code == 3 and env["errors"][0]["code"] == "cloud_native"
