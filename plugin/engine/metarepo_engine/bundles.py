"""Containers as bundles: validate, then expand into local staging (007/R5, R5a, R6; 007/ADR-002).

A *bundle* is an archive (ZIP, TAR and its compressed forms from the standard library; 7z and RAR when a
tool is detected) or a folder dropped into ``00_inbox/``. Everything is validated **before** a single byte
is written (zip-slip, absolute or drive-lettered paths, Windows reserved names, links and special files,
encrypted members, total uncompressed size, compression ratio, nesting depth). A violation stops the whole
bundle with a clear reason; nothing is left behind in staging.

Staging lives outside the synced tree. The expansion writes ``<staging>/files/<member path>`` and returns a
manifest (also saved as ``<staging>/manifest.json``). Members are never trusted: names come from the
archive, files are streamed with a byte budget that does not rely on the declared sizes.
"""
from __future__ import annotations

import os
import re
import shutil
import stat
import subprocess
import tarfile
import tempfile
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import BinaryIO, Callable, Iterable

from .config import compile_forbidden, effective_forbidden_paths, is_forbidden
from .fsops import fs_path, nfc
from .hashing import sha256_file, source_id
from .jsonout import EXIT_PRECONDITION, EngineError
from .mediatype import detect
from .naming import hash6, is_reserved, slugify

JUNK_NAMES = frozenset({".ds_store", "thumbs.db", "desktop.ini"})
JUNK_DIRS = frozenset({"__macosx"})
CODE_MARKERS = frozenset({"package.json", "pyproject.toml", "setup.py", "cargo.toml", "go.mod", "pom.xml",
                          "makefile", "cmakelists.txt", "gemfile", "composer.json", "build.gradle"})
CODE_DIRS = frozenset({".git", ".hg", ".svn", "node_modules", ".venv", "venv"})
TAR_EXTENSIONS = (".tar", ".tgz", ".gz", ".bz2", ".xz", ".tbz2", ".txz")
EXTERNAL_TOOLS = {"7z": ("7zz", "7z", "7za"), "rar": ("unrar", "7zz", "7z")}
_BAD_CHARS = re.compile(r'[<>:"|?*\x00-\x1f]')
_DRIVE = re.compile(r"^[A-Za-z]:")
CHUNK = 1024 * 1024


class BundleError(EngineError):
    """A bundle that must not be expanded; ``code`` names the rule (exit 3)."""

    def __init__(self, code: str, message: str, path: str | None = None):
        super().__init__(code, message, EXIT_PRECONDITION, path)


def is_junk_path(parts: Iterable[str]) -> bool:
    parts = [p for p in parts if p]
    if not parts:
        return False
    if any(p.lower() in JUNK_DIRS for p in parts[:-1]) or parts[-1].lower() in JUNK_DIRS:
        return True
    last = parts[-1]
    return last.lower() in JUNK_NAMES or last.startswith("~$")


@dataclass
class Limits:
    max_bytes: int = 2048 * 1024 * 1024
    max_ratio: float = 100.0
    max_depth: int = 2
    forbidden: list = field(default_factory=list)

    @classmethod
    def from_config(cls, cfg: dict) -> "Limits":
        intake = cfg.get("intake") or {}
        return cls(max_bytes=int(float(intake.get("bundle_max_uncompressed_mb", 2048)) * 1024 * 1024),
                   max_ratio=float(intake.get("bundle_max_ratio", 100)),
                   max_depth=int(intake.get("bundle_max_depth", 2)),
                   forbidden=compile_forbidden(effective_forbidden_paths(cfg)))


# ----------------------------------------------------------------------------- detection
def external_tool(kind: str) -> str | None:
    for name in EXTERNAL_TOOLS.get(kind, ()):
        found = shutil.which(name)
        if found:
            return found
    return None


def archive_kind(path: Path) -> str | None:
    """``zip`` | ``tar`` | ``7z`` | ``rar`` for a container file, else None (Office files are not containers)."""
    if not path.is_file():
        return None
    media = detect(path).media_type
    if media == "application/zip":
        return "zip"
    if media == "application/x-7z-compressed":
        return "7z"
    if media == "application/vnd.rar":
        return "rar"
    name = path.name.lower()
    if media in ("application/x-tar", "application/gzip", "application/x-xz", "application/x-bzip2") or \
            name.endswith(TAR_EXTENSIONS):
        try:
            if tarfile.is_tarfile(fs_path(path)):
                return "tar"
        except (OSError, tarfile.TarError):
            return None
    return None


def archive_stem(name: str) -> str:
    low = name.lower()
    for double in (".tar.gz", ".tar.bz2", ".tar.xz"):
        if low.endswith(double):
            return name[: -len(double)]
    return Path(name).stem if "." in name else name


# ----------------------------------------------------------------------------- entries
@dataclass
class Entry:
    name: str
    size: int = 0
    is_dir: bool = False
    link: bool = False
    special: bool = False
    encrypted: bool = False
    opener: Callable[[], BinaryIO] | None = None


def _normalize_name(raw: str) -> list[str]:
    """Split an entry name into safe components or raise: absolute, drive letter, traversal."""
    name = raw.replace("\\", "/")
    if "\x00" in name:
        raise BundleError("bundle_path_traversal", f"entry name contains a NUL byte: {raw!r}")
    if name.startswith("/") or _DRIVE.match(name):
        raise BundleError("bundle_path_traversal", f"entry {raw!r} has an absolute or drive-lettered path "
                          "(archive path traversal); the bundle was refused")
    parts = [p for p in name.split("/") if p not in ("", ".")]
    if ".." in parts:
        raise BundleError("bundle_path_traversal", f"entry {raw!r} climbs out of the bundle with '..' "
                          "(zip-slip); the bundle was refused")
    return [nfc(p) for p in parts]


def _check_parts(parts: list[str], raw: str) -> None:
    for part in parts:
        if is_reserved(part) or _BAD_CHARS.search(part) or part.endswith((".", " ")):
            raise BundleError("bundle_invalid_name", f"entry {raw!r}: segment {part!r} is a reserved or invalid "
                              "name on Windows (P10); rename it inside the archive and drop it again")


@dataclass
class _State:
    limits: Limits
    staging: Path
    known: dict[str, str]
    dry_run: bool
    declared: int = 0
    written: int = 0
    members: list[dict] = field(default_factory=list)
    nested: list[dict] = field(default_factory=list)
    junk: list[str] = field(default_factory=list)
    excluded: list[dict] = field(default_factory=list)
    code_dirs: dict[str, set[str]] = field(default_factory=dict)
    seen: set[str] = field(default_factory=set)
    temp_dirs: list[Path] = field(default_factory=list)

    @property
    def files(self) -> Path:
        return self.staging / "files"


def _zip_entries(zf: zipfile.ZipFile) -> list[Entry]:
    out = []
    for info in zf.infolist():
        mode = (info.external_attr >> 16) & 0o170000
        out.append(Entry(info.filename, info.file_size, info.is_dir(), link=mode == stat.S_IFLNK,
                         special=mode not in (0, stat.S_IFREG, stat.S_IFDIR, stat.S_IFLNK),
                         encrypted=bool(info.flag_bits & 0x1),
                         opener=(lambda i=info: zf.open(i))))
    return out


def _tar_entries(tf: tarfile.TarFile, limits: Limits, archive_size: int) -> list[Entry]:
    out, total = [], 0
    for m in tf:  # iterate, do not getmembers(): lets a bomb be stopped early
        total += max(m.size, 0)
        if total > limits.max_bytes:
            raise BundleError("bundle_too_large", f"uncompressed size exceeds intake.bundle_max_uncompressed_mb "
                              f"({limits.max_bytes // (1024 * 1024)} MB); the bundle was refused")
        out.append(Entry(m.name, m.size, m.isdir(), link=m.issym() or m.islnk(),
                         special=not (m.isfile() or m.isdir() or m.issym() or m.islnk()),
                         opener=(lambda mm=m: tf.extractfile(mm))))
    return out


def _external_list(kind: str, path: Path) -> list[Entry]:
    tool = external_tool(kind)
    if tool is None:
        raise BundleError("bundle_unsupported", f".{kind} containers need an optional tool ("
                          f"{' or '.join(EXTERNAL_TOOLS[kind])}) that was not found; install it, or drop an "
                          "extracted folder or a ZIP instead (nothing is installed automatically)")
    proc = subprocess.run([tool, "l", "-slt", "-ba", "-p-", str(path)], capture_output=True, text=True,
                          timeout=120, stdin=subprocess.DEVNULL)
    if proc.returncode != 0:
        enc = "encrypted" in (proc.stdout + proc.stderr).lower() or "password" in (proc.stderr or "").lower()
        raise BundleError("bundle_encrypted" if enc else "bundle_corrupt",
                          "the archive is password-protected: ask for the password or skip it" if enc
                          else f"the archive could not be listed: {(proc.stderr or proc.stdout).strip()[:200]}")
    entries: list[Entry] = []
    for block in re.split(r"\n\s*\n", proc.stdout.strip()):
        fields = dict(line.split(" = ", 1) for line in block.splitlines() if " = " in line)
        if "Path" not in fields:
            continue
        attrs = fields.get("Attributes", "")
        entries.append(Entry(fields["Path"], int(fields.get("Size") or 0),
                             is_dir=fields.get("Folder") == "+" or attrs.startswith("D"),
                             link=bool(fields.get("Symbolic Link")) or "l" in attrs[:1].lower() and attrs[:1] != "D",
                             encrypted=fields.get("Encrypted") == "+"))
    return entries


# ----------------------------------------------------------------------------- validation
def _check_entries(entries: list[Entry], archive_size: int | None, st: _State, label: str) -> list[tuple[Entry, list[str]]]:
    """Validate every entry; return the files to keep with their normalized parts. Raises, writes nothing."""
    kept: list[tuple[Entry, list[str]]] = []
    seen: set[str] = set()
    total = 0
    excluded_dirs: set[str] = set()
    for e in entries:
        parts = _normalize_name(e.name)
        if not parts:
            continue
        rel = "/".join(parts)
        if is_junk_path(parts):
            if not e.is_dir:
                st.junk.append(rel)
            continue
        if e.link:
            raise BundleError("bundle_symlink", f"entry {e.name!r} is a symbolic or hard link; links are never "
                              "expanded (the bundle was refused)")
        if e.special:
            raise BundleError("bundle_special_file", f"entry {e.name!r} is a device, socket or other special file")
        if e.encrypted:
            raise BundleError("bundle_encrypted", f"{label} has encrypted members ({e.name!r}): ask the owner for "
                              "the password and drop an unencrypted copy, or skip this bundle")
        _check_parts(parts, e.name)
        low_parts = [p.lower() for p in parts]
        for i, p in enumerate(low_parts[: -1 if not e.is_dir else None]):
            if p in CODE_DIRS:
                excluded_dirs.add("/".join(parts[: i + 1]))
                st.code_dirs.setdefault("/".join(parts[:i]), set()).add(parts[i])
        if e.is_dir:
            if low_parts[-1] in CODE_DIRS:
                st.code_dirs.setdefault("/".join(parts[:-1]), set()).add(parts[-1])
            continue
        if low_parts[-1] in CODE_MARKERS:
            st.code_dirs.setdefault("/".join(parts[:-1]), set()).add(parts[-1])
        if is_forbidden(rel, st.limits.forbidden, is_dir=False):
            st.excluded.append({"path": rel, "reason": "forbidden_path"})
            continue
        key = rel.casefold()
        if key in seen:
            raise BundleError("bundle_name_collision", f"entries collide on case-insensitive file systems: {rel!r}")
        seen.add(key)
        total += max(e.size, 0)
        kept.append((e, parts))
    st.declared += total
    if st.declared > st.limits.max_bytes:
        raise BundleError("bundle_too_large", f"uncompressed size {st.declared // (1024 * 1024)} MB exceeds "
                          f"intake.bundle_max_uncompressed_mb ({st.limits.max_bytes // (1024 * 1024)} MB); "
                          "the bundle was refused")
    if archive_size is not None and total > 0 and total / max(archive_size, 1) > st.limits.max_ratio:
        raise BundleError("bundle_ratio", f"{label} expands {total / max(archive_size, 1):.0f}x its size; "
                          f"intake.bundle_max_ratio is {st.limits.max_ratio:g} (possible decompression bomb); "
                          "the bundle was refused")
    return kept


def _copy_limited(src: BinaryIO, dest: Path, st: _State) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    with open(fs_path(dest), "wb") as out:
        while True:
            chunk = src.read(CHUNK)
            if not chunk:
                break
            st.written += len(chunk)
            if st.written > st.limits.max_bytes:  # declared sizes can lie
                raise BundleError("bundle_too_large", "the archive expanded past "
                                  "intake.bundle_max_uncompressed_mb; the bundle was refused")
            out.write(chunk)


def _add_member(st: _State, staged: Path, member_path: str, depth: int) -> None:
    digest = sha256_file(staged)
    media = detect(staged)
    row = {"member_path": member_path, "staged": str(staged), "sha256": digest,
           "id": source_id(digest, st.known), "size": staged.stat().st_size, "media_type": media.media_type,
           "depth": depth, "cloud_native": media.cloud_native, "bundle": False, "expanded": False}
    kind = archive_kind(staged)
    if kind:
        row["bundle"] = True
        row["archive_kind"] = kind
    st.members.append(row)


def _process(entries: list[Entry], prefix: str, archive_size: int | None, st: _State, depth: int, label: str) -> None:
    if depth > st.limits.max_depth:
        raise BundleError("bundle_depth", f"{label}: nesting level {depth} exceeds intake.bundle_max_depth "
                          f"({st.limits.max_depth})")
    kept = _check_entries(entries, archive_size, st, label)
    for e, parts in kept:  # whole-bundle validation is done: now write
        rel = "/".join(([prefix] if prefix else []) + parts)
        if st.dry_run:
            st.members.append({"member_path": rel, "size": e.size, "depth": depth, "staged": None})
            continue
        dest = st.files / rel
        with e.opener() as fh:  # type: ignore[union-attr]
            _copy_limited(fh, dest, st)
        _add_member(st, dest, rel, depth)
    if st.dry_run:
        return
    for row in list(st.members):  # expand nested archives found at this level
        if row.get("depth") != depth or not row.get("bundle") or row.get("expanded"):
            continue
        if depth + 1 > st.limits.max_depth:
            row["reason"] = f"nested archive left unexpanded: bundle_max_depth is {st.limits.max_depth}"
            continue
        staged = Path(row["staged"])
        stem = archive_stem(staged.name)
        nested_prefix = "/".join(Path(row["member_path"]).parent.parts + (stem,)).lstrip("/")
        _expand_file(staged, nested_prefix, st, depth + 1, row["archive_kind"])
        row["expanded"] = True
        st.nested.append({"member_path": row["member_path"], "kind": row["archive_kind"], "expanded_to": nested_prefix})
    st.members[:] = [r for r in st.members if not (r.get("bundle") and r.get("expanded"))]


def _expand_file(path: Path, prefix: str, st: _State, depth: int, kind: str) -> None:
    label = path.name
    size = path.stat().st_size
    if kind == "zip":
        try:
            with zipfile.ZipFile(fs_path(path)) as zf:
                _process(_zip_entries(zf), prefix, size, st, depth, label)
        except (zipfile.BadZipFile, NotImplementedError, RuntimeError, EOFError) as exc:
            raise BundleError("bundle_corrupt", f"{label} is not a readable ZIP archive: {exc}") from exc
    elif kind == "tar":
        try:
            with tarfile.open(fs_path(path), "r:*") as tf:
                _process(_tar_entries(tf, st.limits, size), prefix, size, st, depth, label)
        except (tarfile.TarError, EOFError, OSError) as exc:
            raise BundleError("bundle_corrupt", f"{label} is not a readable TAR archive: {exc}") from exc
    else:
        entries = _external_list(kind, path)
        _check_entries(entries, size, _State(st.limits, st.staging, st.known, True), label)  # validate first
        if st.dry_run:
            _process(entries, prefix, size, st, depth, label)
            return
        tool = external_tool(kind)
        tmp = Path(tempfile.mkdtemp(prefix="ext-", dir=st.staging))
        st.temp_dirs.append(tmp)
        proc = subprocess.run([tool, "x", "-y", "-p-", f"-o{tmp}", str(path)], capture_output=True, text=True,
                              timeout=600, stdin=subprocess.DEVNULL)
        if proc.returncode != 0:
            raise BundleError("bundle_corrupt", f"{label} could not be extracted: {(proc.stderr or proc.stdout).strip()[:200]}")
        _process(_folder_entries(tmp), prefix, None, st, depth, label)


def _folder_entries(root: Path) -> list[Entry]:
    out: list[Entry] = []
    for dirpath, dirs, files in os.walk(root, followlinks=False):
        here = Path(dirpath)
        dirs.sort()
        for d in list(dirs):
            full = here / d
            rel = full.relative_to(root).as_posix()
            if full.is_symlink():
                out.append(Entry(rel, link=True))
                dirs.remove(d)
            else:
                out.append(Entry(rel + "/", is_dir=True))
        for f in sorted(files):
            full = here / f
            rel = full.relative_to(root).as_posix()
            mode = os.lstat(full).st_mode
            out.append(Entry(rel, os.lstat(full).st_size, link=stat.S_ISLNK(mode),
                             special=not (stat.S_ISREG(mode) or stat.S_ISLNK(mode)),
                             opener=(lambda p=full: open(fs_path(p), "rb"))))
    return out


# ----------------------------------------------------------------------------- public API
def validate_and_expand(source: Path, staging: Path, limits: Limits, *, known: dict[str, str] | None = None,
                        dry_run: bool = False) -> dict:
    """Validate ``source`` (archive file or folder) and expand it into ``staging/files``. Returns the manifest.

    On any violation the staging directory content created here is removed and a ``BundleError`` is raised.
    ``dry_run`` validates only: nothing is written.
    """
    source = Path(source)
    is_folder = source.is_dir()
    kind = "folder" if is_folder else archive_kind(source)
    if kind is None:
        raise BundleError("bundle_unsupported", f"{source.name} is not a supported container (ZIP, TAR, TAR.GZ, "
                          "7z/RAR with a tool, or a folder)")
    slug = slugify(archive_stem(source.name))
    digest = None if is_folder else sha256_file(source)  # refuses not-local placeholders (004/R12)
    # One sub-directory per container: several bundles share one staging directory per run.
    sub = Path(staging) / f"{slug}-{(digest or hash6(str(source.resolve())))[:6]}"
    st = _State(limits, sub, known or {}, dry_run)
    if not dry_run:
        if sub.exists():
            shutil.rmtree(sub)  # a rerun of the same container replaces its own staging only
        st.files.mkdir(parents=True, exist_ok=True)
    try:
        if is_folder:
            _process(_folder_entries(source), "", None, st, 1, source.name)
        else:
            _expand_file(source, "", st, 1, kind)
    except BaseException:
        if not dry_run:
            shutil.rmtree(st.staging, ignore_errors=True)
        for tmp in st.temp_dirs:
            shutil.rmtree(tmp, ignore_errors=True)
        raise
    for tmp in st.temp_dirs:
        shutil.rmtree(tmp, ignore_errors=True)
    code_folders = [{"path": p or ".", "markers": sorted(m)} for p, m in sorted(st.code_dirs.items())]
    for row in st.members:
        row["hint"] = None
        for p in st.code_dirs:
            if not p or row["member_path"].startswith(p + "/"):
                row["hint"] = "code"
                row["suggested_decision"] = "reject"
                row["suggested_on_reject"] = "move-to-shared"
                break
    top = sorted({Path(m["member_path"]).parts[0] for m in st.members if len(Path(m["member_path"]).parts) > 1})
    manifest = {
        "container": {"name": source.name, "kind": kind, "slug": slug,
                      "sha256": digest,
                      "id": None if is_folder else source_id(digest, known or {}),
                      "size": None if is_folder else source.stat().st_size},
        "group": slug, "category_hint": slug, "top_folders": top,
        "files_root": str(st.files), "members": st.members, "nested_archives": st.nested, "junk": sorted(st.junk),
        "excluded": st.excluded, "code_folders": code_folders, "uncompressed_bytes": st.declared,
        "limits": {"max_bytes": limits.max_bytes, "max_ratio": limits.max_ratio, "max_depth": limits.max_depth},
        "dry_run": dry_run, "staging": str(st.staging),
    }
    return manifest
