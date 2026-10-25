"""Safe file operations for a synced tree (003/R7, R8, R9; 002/R6a; P9, P10, P12).

* Writes are atomic: temp file in the same directory -> flush -> fsync -> os.replace.
* Creation helpers never overwrite unless the caller asks.
* Moves never overwrite; cross-volume moves copy, verify the hash, then delete the original.
* The local cache lives outside the synced tree.
"""
from __future__ import annotations

import errno
import hashlib
import os
import re
import shutil
import sys
import unicodedata
from dataclasses import dataclass
from pathlib import Path

TEMP_MARKER = ".tmp-"
_TEMP_RE = re.compile(r"^\..+\.tmp-\d+$")
_SAFE_SEGMENT_RE = re.compile(r"^[A-Za-z0-9._-]{1,128}$")


def fs_path(path: Path | str) -> str:
    """Path string safe for the OS: adds the long-path prefix on Windows when needed (003/R15)."""
    text = str(path)
    if sys.platform == "win32":
        p = os.path.abspath(text)
        if len(p) >= 248 and not p.startswith("\\\\?\\"):
            if p.startswith("\\\\"):
                return "\\\\?\\UNC\\" + p[2:]
            return "\\\\?\\" + p
        return p
    return text


def nfc(text: str) -> str:
    return unicodedata.normalize("NFC", text)


def temp_name_for(target: Path) -> Path:
    return target.with_name(f".{target.name}{TEMP_MARKER}{os.getpid()}")


def _fsync_dir(directory: Path) -> None:
    if sys.platform == "win32":
        return
    try:
        fd = os.open(str(directory), os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(fd)
    except OSError:
        pass
    finally:
        os.close(fd)


def atomic_write_bytes(target: Path, data: bytes, *, overwrite: bool = True) -> None:
    """Atomically write ``data`` to ``target``.

    With ``overwrite=False`` the call raises ``FileExistsError`` if the target exists (R8).
    The temp file is always cleaned up on failure (R7).
    """
    target = Path(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    if not overwrite and target.exists():
        raise FileExistsError(errno.EEXIST, "target exists", str(target))
    tmp = temp_name_for(target)
    try:
        with open(fs_path(tmp), "wb") as fh:
            fh.write(data)
            fh.flush()
            os.fsync(fh.fileno())
        if overwrite:
            os.replace(fs_path(tmp), fs_path(target))
        else:
            try:
                os.link(fs_path(tmp), fs_path(target))  # fails if the target appeared meanwhile
            except FileExistsError:
                raise
            except OSError:
                # Filesystems without hard links: fall back to check-then-replace.
                if target.exists():
                    raise FileExistsError(errno.EEXIST, "target exists", str(target)) from None
                os.replace(fs_path(tmp), fs_path(target))
        _fsync_dir(target.parent)
    finally:
        try:
            os.unlink(fs_path(tmp))
        except FileNotFoundError:
            pass
        except OSError:
            pass


def atomic_write_text(target: Path, text: str, *, overwrite: bool = True) -> None:
    atomic_write_bytes(target, text.encode("utf-8"), overwrite=overwrite)


def create_new_bytes(target: Path, data: bytes) -> None:
    """Create a file that must not exist yet (R8, P12)."""
    atomic_write_bytes(target, data, overwrite=False)


def write_if_absent(target: Path, data: bytes | str) -> bool:
    """Idempotent scaffolding helper: create ``target`` unless it exists. Returns True if created."""
    payload = data.encode("utf-8") if isinstance(data, str) else data
    try:
        create_new_bytes(target, payload)
    except FileExistsError:
        return False
    return True


def find_stale_temps(directory: Path) -> list[Path]:
    """Temp files left behind by a crashed writer (reported by /doctor)."""
    found: list[Path] = []
    for dirpath, _dirs, files in os.walk(directory):
        for name in files:
            if _TEMP_RE.match(name):
                found.append(Path(dirpath) / name)
    return sorted(found)


def sha256_file(path: Path, chunk: int = 1024 * 1024) -> str:
    h = hashlib.sha256()
    with open(fs_path(path), "rb") as fh:
        while True:
            block = fh.read(chunk)
            if not block:
                break
            h.update(block)
    return h.hexdigest()


@dataclass(frozen=True)
class MoveResult:
    method: str  # "replace" | "copy-verify-delete"
    sha256: str | None


def safe_move(src: Path, dst: Path) -> MoveResult:
    """Move ``src`` to ``dst`` without ever overwriting (R8, R9).

    Same volume: os.replace. Different volume: copy to a temp file next to the destination,
    verify the SHA-256, replace into place, then delete the original.
    """
    src, dst = Path(src), Path(dst)
    if not src.is_file():
        raise FileNotFoundError(errno.ENOENT, "source is not a file", str(src))
    if dst.exists():
        raise FileExistsError(errno.EEXIST, "destination exists", str(dst))
    dst.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.replace(fs_path(src), fs_path(dst))
        return MoveResult("replace", None)
    except OSError as exc:
        if exc.errno != errno.EXDEV:
            raise
    digest = sha256_file(src)
    tmp = temp_name_for(dst)
    try:
        with open(fs_path(src), "rb") as r, open(fs_path(tmp), "wb") as w:
            shutil.copyfileobj(r, w, 1024 * 1024)
            w.flush()
            os.fsync(w.fileno())
        if sha256_file(tmp) != digest:
            raise OSError(errno.EIO, "hash mismatch after copy; original left in place", str(src))
        if dst.exists():
            raise FileExistsError(errno.EEXIST, "destination exists", str(dst))
        os.replace(fs_path(tmp), fs_path(dst))
    finally:
        try:
            os.unlink(fs_path(tmp))
        except OSError:
            pass
    os.unlink(fs_path(src))
    return MoveResult("copy-verify-delete", digest)


def remove_file(path: Path) -> None:
    """Delete one regular file. Callers enforce confirmation and logging (P6)."""
    os.unlink(fs_path(path))


def local_cache_root() -> Path:
    """Base of the per-member local cache (002/R6a). ``METAREPO_CACHE_DIR`` overrides it."""
    override = os.environ.get("METAREPO_CACHE_DIR")
    if override:
        return Path(override)
    if sys.platform == "win32":
        base = os.environ.get("LOCALAPPDATA")
        if base:
            return Path(base) / "metarepo"
        return Path.home() / "AppData" / "Local" / "metarepo"
    return Path.home() / ".cache" / "metarepo"


def local_cache_dir(project_id: str, *, create: bool = False) -> Path:
    if not _SAFE_SEGMENT_RE.match(project_id or ""):
        raise ValueError(f"unsafe project id for cache path: {project_id!r}")
    path = local_cache_root() / project_id
    if create:
        path.mkdir(parents=True, exist_ok=True)
    return path
