"""Naming helpers (002/R9, R10, R11; 002 plan section 3).

* ``slugify`` / ``slugify_filename``: NFC -> transliterate to ASCII -> lowercase kebab-case, at most 80
  characters in the stem, Windows reserved names guarded, ``item-<hash6>`` when nothing usable remains.
* ``check_relpath`` / ``safe_relpath``: root-relative paths stay within the limit (default 200) and are
  safe on Windows; an over-long path comes with a proposed shorter name.
"""
from __future__ import annotations

import hashlib
import re
import unicodedata
from dataclasses import dataclass
from pathlib import PurePosixPath

MAX_STEM = 80
DEFAULT_PATH_LIMIT = 200

WINDOWS_RESERVED = frozenset(
    {"con", "prn", "aux", "nul", *(f"com{i}" for i in range(1, 10)), *(f"lpt{i}" for i in range(1, 10))}
)

# Letters that do not decompose under NFKD but have a conventional ASCII form.
_FOLD = {
    "ß": "ss", "ẞ": "SS", "æ": "ae", "Æ": "AE", "œ": "oe", "Œ": "OE",
    "ø": "o", "Ø": "O", "đ": "d", "Đ": "D", "ð": "d", "Ð": "D",
    "þ": "th", "Þ": "TH", "ł": "l", "Ł": "L", "ı": "i",
}
_NON_ALNUM = re.compile(r"[^a-z0-9]+")


class NamingError(ValueError):
    def __init__(self, code: str, message: str, proposal: str | None = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.proposal = proposal


def nfc(text: str) -> str:
    return unicodedata.normalize("NFC", text)


def hash6(text: str) -> str:
    return hashlib.sha256(nfc(text).encode("utf-8")).hexdigest()[:6]


def transliterate(text: str) -> str:
    """NFC, fold special letters, strip diacritics, drop everything that is not ASCII."""
    text = nfc(text)
    text = "".join(_FOLD.get(ch, ch) for ch in text)
    decomposed = unicodedata.normalize("NFKD", text)
    stripped = "".join(ch for ch in decomposed if not unicodedata.combining(ch))
    return stripped.encode("ascii", "ignore").decode("ascii")


def is_reserved(stem: str) -> bool:
    return stem.lower().split(".")[0] in WINDOWS_RESERVED


def slugify(name: str, max_len: int = MAX_STEM) -> str:
    """Lowercase kebab-case ASCII slug. Falls back to ``item-<hash6>`` (e.g. for CJK-only names)."""
    slug = _NON_ALNUM.sub("-", transliterate(name).lower()).strip("-")
    slug = slug[:max_len].rstrip("-")
    if not slug:
        slug = f"item-{hash6(name)}"
    if is_reserved(slug):
        slug = f"{slug}-item"
    return slug


def _split_name(filename: str) -> tuple[str, str]:
    name = nfc(filename).strip()
    if name.startswith(".") and name.count(".") == 1:  # dotfile such as ".env": no extension
        return name.lstrip("."), ""
    stem, dot, ext = name.rpartition(".")
    if not dot:
        return name, ""
    return stem, ext


def slugify_filename(filename: str, max_stem: int = MAX_STEM) -> str:
    """Placed filename: slug of the stem plus the original (last) extension, lowercase ASCII (R9)."""
    stem, ext = _split_name(filename)
    ext = re.sub(r"[^a-z0-9]", "", transliterate(ext).lower())
    slug = slugify(stem or filename, max_stem)
    return f"{slug}.{ext}" if ext else slug


@dataclass(frozen=True)
class PathCheck:
    ok: bool
    length: int
    limit: int
    problems: tuple[str, ...]
    proposal: str | None


def _normalize_rel(rel: str) -> str:
    text = nfc(str(rel)).replace("\\", "/")
    return str(PurePosixPath(text)) if text else text


def check_relpath(rel: str, limit: int = DEFAULT_PATH_LIMIT) -> PathCheck:
    """Validate a root-relative path (R10) and propose a shorter one when it is too long."""
    norm = _normalize_rel(rel)
    problems: list[str] = []
    if not norm or norm == ".":
        problems.append("empty path")
    if norm.startswith("/") or re.match(r"^[A-Za-z]:", norm):
        problems.append("path must be relative to the project root")
    parts = norm.split("/")
    if ".." in parts:
        problems.append("path must not contain '..'")
    for part in parts:
        if part and (part.endswith((".", " "))):
            problems.append(f"segment {part!r} ends with a dot or space (not valid on Windows)")
        if part and is_reserved(part):
            problems.append(f"segment {part!r} is a reserved name on Windows")
        if re.search(r'[<>:"|?*\x00-\x1f]', part):
            problems.append(f"segment {part!r} contains characters not allowed on Windows")
    length = len(norm)
    proposal = None
    if length > limit:
        problems.append(f"path is {length} characters; the limit is {limit}")
        proposal = _shorten(norm, limit)
    return PathCheck(not problems, length, limit, tuple(problems), proposal)


def _shorten(norm: str, limit: int) -> str | None:
    folder, _, filename = norm.rpartition("/")
    stem, ext = _split_name(filename)
    suffix = f"-{hash6(norm)}"
    prefix = f"{folder}/" if folder else ""
    room = limit - len(prefix) - len(ext) - (1 if ext else 0) - len(suffix)
    if room < 1:
        return None
    short = stem[:room].rstrip("-.")
    candidate = f"{prefix}{short}{suffix}" + (f".{ext}" if ext else "")
    return candidate if len(candidate) <= limit else None


def safe_relpath(rel: str, limit: int = DEFAULT_PATH_LIMIT) -> str:
    """Return the normalized POSIX relative path or raise ``NamingError`` (with a proposal if too long)."""
    check = check_relpath(rel, limit)
    if not check.ok:
        code = "path_too_long" if check.proposal is not None or check.length > limit else "path_invalid"
        raise NamingError(code, "; ".join(check.problems), check.proposal)
    return _normalize_rel(rel)
