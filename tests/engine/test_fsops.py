"""003/R7, R8, R9, R15 and 002/R6a: atomic writes (003/AC2 fault injection), moves, local cache, paths."""
import errno
import hashlib
import os
import subprocess
import sys
import textwrap
import time
from pathlib import Path

import pytest

from metarepo_engine import fsops


def names(directory: Path):
    return sorted(p.name for p in directory.iterdir())


# ---- atomic_write (R7)
def test_atomic_write_creates_and_replaces(tmp_path):
    target = tmp_path / "sub" / "a.txt"
    fsops.atomic_write_text(target, "one")
    assert target.read_text(encoding="utf-8") == "one"
    fsops.atomic_write_text(target, "two \u00e9\u4e2d")
    assert target.read_bytes() == "two \u00e9\u4e2d".encode("utf-8")
    assert names(target.parent) == ["a.txt"]  # no temp left behind


def test_temp_name_follows_the_spec(tmp_path):
    assert fsops.temp_name_for(tmp_path / "x.md").name == f".x.md.tmp-{os.getpid()}"


def test_failure_in_replace_keeps_the_old_content_and_cleans_up(tmp_path, monkeypatch):  # AC2 (fault injection)
    target = tmp_path / "a.txt"
    fsops.atomic_write_text(target, "old content")

    def boom(*args, **kwargs):
        raise OSError(errno.EIO, "injected failure")

    monkeypatch.setattr(os, "replace", boom)
    with pytest.raises(OSError, match="injected"):
        fsops.atomic_write_text(target, "new content")
    monkeypatch.undo()
    assert target.read_text(encoding="utf-8") == "old content"
    assert names(tmp_path) == ["a.txt"]


def test_failure_during_write_or_fsync_never_touches_the_target(tmp_path, monkeypatch):  # AC2
    target = tmp_path / "a.txt"
    fsops.atomic_write_text(target, "old content")
    monkeypatch.setattr(os, "fsync", lambda fd: (_ for _ in ()).throw(OSError(errno.EIO, "disk gone")))
    with pytest.raises(OSError):
        fsops.atomic_write_bytes(target, b"x" * 100_000)
    monkeypatch.undo()
    assert target.read_text(encoding="utf-8") == "old content"
    assert names(tmp_path) == ["a.txt"]


def test_failure_on_first_write_leaves_no_target_and_no_temp(tmp_path, monkeypatch):
    target = tmp_path / "new.txt"
    monkeypatch.setattr(os, "replace", lambda *a, **k: (_ for _ in ()).throw(OSError(errno.ENOSPC, "full")))
    with pytest.raises(OSError):
        fsops.atomic_write_text(target, "x")
    monkeypatch.undo()
    assert names(tmp_path) == []


KILL_CHILD = textwrap.dedent(
    """
    import sys
    from pathlib import Path
    sys.path.insert(0, sys.argv[1])
    from metarepo_engine import fsops
    target = Path(sys.argv[2])
    size = int(sys.argv[3])
    payloads = [b"A" * size, b"B" * size]
    fsops.atomic_write_bytes(target, payloads[0])
    print("ready", flush=True)
    i = 0
    while True:
        i += 1
        fsops.atomic_write_bytes(target, payloads[i % 2])
    """
)


def test_killing_the_process_mid_write_never_leaves_a_truncated_target(tmp_path, repo):  # AC2 (kill test)
    target = tmp_path / "hot.bin"
    size = 4 * 1024 * 1024
    proc = subprocess.Popen([sys.executable, "-c", KILL_CHILD, str(repo / "tooling" / "engine"), str(target),
                             str(size)], stdout=subprocess.PIPE, text=True)
    try:
        assert proc.stdout.readline().strip() == "ready"
        time.sleep(0.4)  # let it rewrite the file many times
    finally:
        proc.kill()
        proc.wait()
    data = target.read_bytes()
    assert data in (b"A" * size, b"B" * size), "target must hold one complete payload, never a partial one"
    # a straggler temp file may remain after a hard kill: doctor can find it, and it is never the target
    for stray in fsops.find_stale_temps(tmp_path):
        assert stray != target


def test_find_stale_temps(tmp_path):
    (tmp_path / "d").mkdir()
    (tmp_path / ".a.md.tmp-123").write_text("x", encoding="utf-8")
    (tmp_path / "d" / ".b.tmp-9").write_text("x", encoding="utf-8")
    (tmp_path / "normal.md").write_text("x", encoding="utf-8")
    (tmp_path / ".hidden").write_text("x", encoding="utf-8")
    assert [p.name for p in fsops.find_stale_temps(tmp_path)] == [".a.md.tmp-123", ".b.tmp-9"]


# ---- never overwrite (R8)
def test_create_new_refuses_to_overwrite(tmp_path):
    target = tmp_path / "a.txt"
    fsops.create_new_bytes(target, b"first")
    with pytest.raises(FileExistsError):
        fsops.create_new_bytes(target, b"second")
    assert target.read_bytes() == b"first" and names(tmp_path) == ["a.txt"]


def test_write_if_absent_is_idempotent(tmp_path):
    target = tmp_path / "x" / "a.txt"
    assert fsops.write_if_absent(target, "one") is True
    assert fsops.write_if_absent(target, "two") is False
    assert target.read_text(encoding="utf-8") == "one"


def test_create_new_falls_back_when_hard_links_are_unsupported(tmp_path, monkeypatch):
    monkeypatch.setattr(os, "link", lambda *a, **k: (_ for _ in ()).throw(OSError(errno.EPERM, "no links")))
    target = tmp_path / "a.txt"
    fsops.create_new_bytes(target, b"first")
    assert target.read_bytes() == b"first"
    with pytest.raises(FileExistsError):
        fsops.create_new_bytes(target, b"second")
    assert names(tmp_path) == ["a.txt"]


# ---- moves (R9)
def test_safe_move_same_volume(tmp_path):
    src, dst = tmp_path / "a" / "x.bin", tmp_path / "b" / "c" / "y.bin"
    src.parent.mkdir()
    src.write_bytes(b"payload")
    moved = fsops.safe_move(src, dst)
    assert moved.method == "replace" and dst.read_bytes() == b"payload" and not src.exists()


def test_safe_move_never_overwrites(tmp_path):
    src, dst = tmp_path / "x", tmp_path / "y"
    src.write_bytes(b"1")
    dst.write_bytes(b"2")
    with pytest.raises(FileExistsError):
        fsops.safe_move(src, dst)
    assert src.read_bytes() == b"1" and dst.read_bytes() == b"2"
    with pytest.raises(FileNotFoundError):
        fsops.safe_move(tmp_path / "missing", tmp_path / "z")


def _force_cross_volume(monkeypatch, src: Path):
    real = os.replace

    def replace(a, b, *args, **kwargs):
        if str(a) == fsops.fs_path(src):
            raise OSError(errno.EXDEV, "cross-device link")
        return real(a, b, *args, **kwargs)

    monkeypatch.setattr(os, "replace", replace)


def test_cross_volume_move_copies_verifies_then_deletes(tmp_path, monkeypatch):
    src, dst = tmp_path / "x.bin", tmp_path / "out" / "y.bin"
    data = os.urandom(300_000)
    src.write_bytes(data)
    _force_cross_volume(monkeypatch, src)
    moved = fsops.safe_move(src, dst)
    assert moved.method == "copy-verify-delete" and moved.sha256 == hashlib.sha256(data).hexdigest()
    assert dst.read_bytes() == data and not src.exists()
    assert names(dst.parent) == ["y.bin"]


def test_cross_volume_move_with_corrupt_copy_keeps_the_original(tmp_path, monkeypatch):
    src, dst = tmp_path / "x.bin", tmp_path / "out" / "y.bin"
    src.write_bytes(b"precious")
    _force_cross_volume(monkeypatch, src)
    calls = {"n": 0}
    real = fsops.sha256_file

    def lying(path):
        calls["n"] += 1
        return real(path) if calls["n"] == 1 else "0" * 64

    monkeypatch.setattr(fsops, "sha256_file", lying)
    with pytest.raises(OSError, match="hash mismatch"):
        fsops.safe_move(src, dst)
    assert src.read_bytes() == b"precious" and not dst.exists()
    assert names(dst.parent) == []


def test_other_replace_errors_propagate(tmp_path, monkeypatch):
    src = tmp_path / "x"
    src.write_bytes(b"1")
    monkeypatch.setattr(os, "replace", lambda *a, **k: (_ for _ in ()).throw(PermissionError(errno.EACCES, "locked")))
    with pytest.raises(PermissionError):
        fsops.safe_move(src, tmp_path / "y")
    monkeypatch.undo()
    assert src.read_bytes() == b"1"


# ---- local cache (002/R6a)
def test_local_cache_dir_is_per_project_and_outside_the_tree(monkeypatch, tmp_path):
    monkeypatch.setenv("METAREPO_CACHE_DIR", str(tmp_path / "c"))
    p = fsops.local_cache_dir("proj-1", create=True)
    assert p == tmp_path / "c" / "proj-1" and p.is_dir()
    assert fsops.local_cache_dir("proj-2") != p
    for bad in ("", "../x", "a/b", "a\\b", "x" * 200):
        with pytest.raises(ValueError):
            fsops.local_cache_dir(bad)


def test_default_cache_locations(monkeypatch, tmp_path):
    monkeypatch.delenv("METAREPO_CACHE_DIR", raising=False)
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    assert fsops.local_cache_root() == tmp_path / ".cache" / "metarepo"
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "LocalAppData"))
    assert fsops.local_cache_root() == tmp_path / "LocalAppData" / "metarepo"
    monkeypatch.delenv("LOCALAPPDATA")
    assert fsops.local_cache_root() == tmp_path / "AppData" / "Local" / "metarepo"


# ---- paths (R15, P10)
def test_long_path_prefix_on_windows(monkeypatch):
    monkeypatch.setattr(sys, "platform", "win32")
    long = "C:\\" + "\\".join(["d" * 50] * 6) + "\\file.md"
    assert len(long) > 248
    monkeypatch.setattr(os.path, "abspath", lambda p: p)
    assert fsops.fs_path(long).startswith("\\\\?\\C:\\")
    assert fsops.fs_path("C:\\short\\file.md") == "C:\\short\\file.md"
    unc = "\\\\server\\share\\" + "\\".join(["d" * 50] * 6)
    assert fsops.fs_path(unc).startswith("\\\\?\\UNC\\server\\share")
    already = "\\\\?\\C:\\" + "x" * 300
    assert fsops.fs_path(already) == already


def test_fs_path_is_identity_off_windows(monkeypatch, tmp_path):
    monkeypatch.setattr(sys, "platform", "linux")
    assert fsops.fs_path(tmp_path / "a") == str(tmp_path / "a")


def test_deep_but_legal_tree_round_trips(tmp_path):
    deep = tmp_path.joinpath(*(["segment-name"] * 8)) / "file.md"
    fsops.atomic_write_text(deep, "deep \u00e9")
    assert deep.read_text(encoding="utf-8") == "deep \u00e9"
    assert fsops.sha256_file(deep) == hashlib.sha256("deep \u00e9".encode("utf-8")).hexdigest()


def test_nfc_helper():
    assert fsops.nfc("e\u0301") == "\u00e9"
