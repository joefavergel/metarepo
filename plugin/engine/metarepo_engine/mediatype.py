"""Media-type detection: magic bytes first, extension second (003/R11).

Uses only the standard library and a small signature table, so the result is the same on every platform
(the ``mimetypes`` module reads system files and is deliberately not used). Google cloud-native
placeholders (``.gdoc`` and friends) are small pointer files, not content: they are reported with
``cloud_native=True`` so intake can ask for an exported copy.
"""
from __future__ import annotations

import zipfile
from dataclasses import dataclass
from pathlib import Path

from .fsops import fs_path

OCTET = "application/octet-stream"
TEXT = "text/plain"

CLOUD_NATIVE_EXTENSIONS = {
    ".gdoc": "document", ".gsheet": "spreadsheet", ".gslides": "presentation", ".gform": "form",
    ".gmap": "map", ".gsite": "site", ".gdraw": "drawing", ".gtable": "fusiontable", ".gjam": "jam",
    ".gscript": "script",
}

# (offset, signature, media type). First match wins; more specific signatures come first.
_MAGIC: tuple[tuple[int, bytes, str], ...] = (
    (0, b"%PDF-", "application/pdf"),
    (0, b"\x89PNG\r\n\x1a\n", "image/png"),
    (0, b"\xff\xd8\xff", "image/jpeg"),
    (0, b"GIF87a", "image/gif"),
    (0, b"GIF89a", "image/gif"),
    (0, b"II*\x00", "image/tiff"),
    (0, b"MM\x00*", "image/tiff"),
    (0, b"\x00\x00\x01\x00", "image/vnd.microsoft.icon"),
    (0, b"PK\x03\x04", "application/zip"),
    (0, b"PK\x05\x06", "application/zip"),
    (0, b"\x1f\x8b", "application/gzip"),
    (0, b"\xfd7zXZ\x00", "application/x-xz"),
    (0, b"7z\xbc\xaf\x27\x1c", "application/x-7z-compressed"),
    (0, b"Rar!\x1a\x07", "application/vnd.rar"),
    (257, b"ustar", "application/x-tar"),
    (0, b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1", "application/x-ole-storage"),
    (0, b"{\\rtf", "application/rtf"),
    (0, b"SQLite format 3\x00", "application/vnd.sqlite3"),
    (0, b"\xff\xfb", "audio/mpeg"),
    (0, b"\xff\xf3", "audio/mpeg"),
    (0, b"fLaC", "audio/flac"),
    (0, b"\x1aE\xdf\xa3", "video/x-matroska"),
    (0, b"wOFF", "font/woff"),
    (0, b"wOF2", "font/woff2"),
)

_OLE_BY_EXT = {".doc": "application/msword", ".xls": "application/vnd.ms-excel", ".ppt": "application/vnd.ms-powerpoint",
               ".msg": "application/vnd.ms-outlook"}
_ZIP_BY_EXT = {
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ".pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    ".odt": "application/vnd.oasis.opendocument.text",
    ".ods": "application/vnd.oasis.opendocument.spreadsheet",
    ".odp": "application/vnd.oasis.opendocument.presentation",
    ".epub": "application/epub+zip",
    ".jar": "application/java-archive",
}
_OOXML_MARKERS = (("word/document.xml", _ZIP_BY_EXT[".docx"]), ("xl/workbook.xml", _ZIP_BY_EXT[".xlsx"]),
                  ("ppt/presentation.xml", _ZIP_BY_EXT[".pptx"]))

_EXT = {
    ".pdf": "application/pdf", ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".gif": "image/gif",
    ".webp": "image/webp", ".svg": "image/svg+xml", ".tif": "image/tiff", ".tiff": "image/tiff", ".bmp": "image/bmp",
    ".heic": "image/heic", ".txt": "text/plain", ".md": "text/markdown", ".markdown": "text/markdown",
    ".csv": "text/csv", ".tsv": "text/tab-separated-values", ".json": "application/json", ".yaml": "application/yaml",
    ".yml": "application/yaml", ".xml": "application/xml", ".html": "text/html", ".htm": "text/html",
    ".rtf": "application/rtf", ".eml": "message/rfc822", ".ics": "text/calendar", ".vtt": "text/vtt",
    ".srt": "application/x-subrip", ".mp3": "audio/mpeg", ".wav": "audio/wav", ".m4a": "audio/mp4",
    ".ogg": "audio/ogg", ".flac": "audio/flac", ".opus": "audio/opus", ".aac": "audio/aac", ".mp4": "video/mp4",
    ".mov": "video/quicktime", ".mkv": "video/x-matroska", ".webm": "video/webm", ".avi": "video/x-msvideo",
    ".zip": "application/zip", ".gz": "application/gzip", ".tgz": "application/gzip", ".tar": "application/x-tar",
    ".7z": "application/x-7z-compressed", ".rar": "application/vnd.rar", ".bz2": "application/x-bzip2",
    ".xz": "application/x-xz", **_ZIP_BY_EXT, **_OLE_BY_EXT,
}
_TEXT_REFINE = {".md": "text/markdown", ".markdown": "text/markdown", ".csv": "text/csv", ".tsv": "text/tab-separated-values",
                ".json": "application/json", ".yaml": "application/yaml", ".yml": "application/yaml",
                ".xml": "application/xml", ".html": "text/html", ".htm": "text/html", ".svg": "image/svg+xml",
                ".vtt": "text/vtt", ".srt": "application/x-subrip", ".ics": "text/calendar", ".eml": "message/rfc822"}


@dataclass(frozen=True)
class MediaInfo:
    media_type: str
    cloud_native: bool
    how: str  # magic | extension | text | cloud-native | unknown
    extension: str  # lowercase, with the leading dot, or ""


def _structured_magic(head: bytes) -> str | None:
    """Signatures short enough to collide with ordinary text; they need their header fields to match too."""
    if head[:2] == b"BM" and len(head) >= 14 and head[6:10] == b"\x00\x00\x00\x00":
        return "image/bmp"
    if head[:3] == b"ID3" and len(head) >= 5 and head[3] in (2, 3, 4) and head[4] != 0xFF:
        return "audio/mpeg"
    if head[:3] == b"BZh" and len(head) >= 10 and head[3:4] in b"123456789" and head[4:10] == b"1AY&SY":
        return "application/x-bzip2"
    if head[:4] == b"OggS" and len(head) >= 6 and head[4] == 0:
        return "audio/ogg"
    return None


def _riff_or_ftyp(head: bytes) -> str | None:
    if head[:4] == b"RIFF" and len(head) >= 12:
        return {b"WAVE": "audio/wav", b"AVI ": "video/x-msvideo", b"WEBP": "image/webp"}.get(head[8:12])
    if len(head) >= 12 and head[4:8] == b"ftyp":
        brand = head[8:12]
        if brand == b"qt  ":
            return "video/quicktime"
        if brand in (b"M4A ", b"M4B "):
            return "audio/mp4"
        if brand in (b"heic", b"heix", b"mif1"):
            return "image/heic"
        return "video/mp4"
    return None


def _looks_like_text(head: bytes) -> bool:
    if not head:
        return True
    if head.startswith((b"\xef\xbb\xbf", b"\xff\xfe", b"\xfe\xff")):
        return True
    if b"\x00" in head:
        return False
    try:
        head.decode("utf-8")
        return True
    except UnicodeDecodeError as exc:
        # a multi-byte character cut by the 8 KiB window is still text
        return exc.start >= len(head) - 3


def _zip_subtype(path: Path, ext: str) -> str:
    try:
        with zipfile.ZipFile(fs_path(path)) as zf:
            names = set(zf.namelist())
            for marker, media in _OOXML_MARKERS:
                if marker in names:
                    return media
            if "mimetype" in names:
                declared = zf.read("mimetype")[:100].decode("ascii", "ignore").strip()
                if declared:
                    return declared
    except (zipfile.BadZipFile, OSError, KeyError, RuntimeError):
        pass
    return _ZIP_BY_EXT.get(ext, "application/zip")


def detect(path: Path) -> MediaInfo:
    path = Path(path)
    ext = path.suffix.lower()
    if ext in CLOUD_NATIVE_EXTENSIONS:
        return MediaInfo(f"application/vnd.google-apps.{CLOUD_NATIVE_EXTENSIONS[ext]}", True, "cloud-native", ext)
    try:
        with open(fs_path(path), "rb") as fh:
            head = fh.read(8192)
    except OSError:
        head = b""
    for offset, signature, media in _MAGIC:
        if head[offset: offset + len(signature)] == signature:
            if media == "application/zip":
                return MediaInfo(_zip_subtype(path, ext), False, "magic", ext)
            if media == "application/x-ole-storage":
                return MediaInfo(_OLE_BY_EXT.get(ext, media), False, "magic", ext)
            if media == "video/x-matroska" and ext == ".webm":
                return MediaInfo("video/webm", False, "magic", ext)
            return MediaInfo(media, False, "magic", ext)
    container = _structured_magic(head) or _riff_or_ftyp(head)
    if container:
        return MediaInfo(container, False, "magic", ext)
    if not head:  # empty or unreadable: nothing to sniff, trust the extension
        if ext in _EXT:
            return MediaInfo(_EXT[ext], False, "extension", ext)
        return MediaInfo(OCTET, False, "unknown", ext)
    if head.lstrip()[:5].lower() in (b"<?xml",):
        refined = _TEXT_REFINE.get(ext, "application/xml")
        return MediaInfo(refined, False, "text", ext)
    if head.lstrip()[:15].lower().startswith((b"<!doctype html", b"<html")):
        return MediaInfo("text/html", False, "text", ext)
    if _looks_like_text(head):
        if ext in _TEXT_REFINE:
            return MediaInfo(_TEXT_REFINE[ext], False, "text", ext)
        return MediaInfo(_EXT.get(ext) if _EXT.get(ext, "").startswith("text/") else TEXT, False, "text", ext)
    if ext in _EXT:
        return MediaInfo(_EXT[ext], False, "extension", ext)
    return MediaInfo(OCTET, False, "unknown", ext)
