"""003/R11 media types: magic bytes first, extension second, cloud-native placeholders."""
import io
import zipfile

import pytest

from metarepo_engine import mediatype as mt


def write(tmp_path, name, data):
    path = tmp_path / name
    path.write_bytes(data)
    return path


def make_zip(entries):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, data in entries.items():
            zf.writestr(name, data)
    return buf.getvalue()


MAGIC_CASES = [
    ("a.pdf", b"%PDF-1.7\n...", "application/pdf"),
    ("renamed-as-txt.txt", b"%PDF-1.4\n...", "application/pdf"),  # magic beats extension
    ("a.png", b"\x89PNG\r\n\x1a\n\x00\x00", "image/png"),
    ("a.jpg", b"\xff\xd8\xff\xe0\x00\x10JFIF", "image/jpeg"),
    ("noext", b"GIF89a\x01\x00", "image/gif"),
    ("a.tif", b"II*\x00\x08\x00", "image/tiff"),
    ("a.bmp", b"BM" + b"\x10\x00\x00\x00" + b"\x00\x00\x00\x00" + b"\x36\x00\x00\x00", "image/bmp"),
    ("a.gz", b"\x1f\x8b\x08\x00", "application/gzip"),
    ("a.7z", b"7z\xbc\xaf\x27\x1c\x00\x04", "application/x-7z-compressed"),
    ("a.rar", b"Rar!\x1a\x07\x00", "application/vnd.rar"),
    ("a.bz2", b"BZh9" + b"1AY&SY" + b"\x00", "application/x-bzip2"),
    ("a.tar", b"\x00" * 257 + b"ustar\x0000", "application/x-tar"),
    ("a.mp3", b"ID3\x04\x00\x00\x00\x00\x00\x00", "audio/mpeg"),
    ("a.mp3", b"\xff\xfb\x90\x00", "audio/mpeg"),
    ("a.ogg", b"OggS\x00\x02\x00\x00", "audio/ogg"),
    ("a.flac", b"fLaC\x00\x00\x00\x22", "audio/flac"),
    ("a.wav", b"RIFF\x24\x00\x00\x00WAVEfmt ", "audio/wav"),
    ("a.avi", b"RIFF\x24\x00\x00\x00AVI LIST", "video/x-msvideo"),
    ("a.webp", b"RIFF\x24\x00\x00\x00WEBPVP8 ", "image/webp"),
    ("a.mp4", b"\x00\x00\x00\x18ftypmp42\x00\x00\x00\x00", "video/mp4"),
    ("a.mov", b"\x00\x00\x00\x14ftypqt  \x00\x00\x00\x00", "video/quicktime"),
    ("a.m4a", b"\x00\x00\x00\x18ftypM4A \x00\x00\x00\x00", "audio/mp4"),
    ("a.mkv", b"\x1aE\xdf\xa3\x01\x00", "video/x-matroska"),
    ("a.webm", b"\x1aE\xdf\xa3\x01\x00", "video/webm"),
    ("a.rtf", b"{\\rtf1\\ansi", "application/rtf"),
    ("a.db", b"SQLite format 3\x00" + b"\x00" * 20, "application/vnd.sqlite3"),
    ("a.woff2", b"wOF2\x00\x01", "font/woff2"),
]


@pytest.mark.parametrize("name,data,expected", MAGIC_CASES)
def test_magic_detection(tmp_path, name, data, expected):
    info = mt.detect(write(tmp_path, name, data))
    assert info.media_type == expected and info.how == "magic" and not info.cloud_native


def test_office_and_archive_subtypes_inside_zip(tmp_path):
    docx = make_zip({"[Content_Types].xml": "x", "word/document.xml": "x"})
    xlsx = make_zip({"[Content_Types].xml": "x", "xl/workbook.xml": "x"})
    pptx = make_zip({"[Content_Types].xml": "x", "ppt/presentation.xml": "x"})
    odt = make_zip({"mimetype": "application/vnd.oasis.opendocument.text", "content.xml": "x"})
    plain = make_zip({"a.txt": "hello"})
    assert mt.detect(write(tmp_path, "a.docx", docx)).media_type.endswith("wordprocessingml.document")
    assert mt.detect(write(tmp_path, "mystery.bin", xlsx)).media_type.endswith("spreadsheetml.sheet")  # content wins
    assert mt.detect(write(tmp_path, "a.pptx", pptx)).media_type.endswith("presentationml.presentation")
    assert mt.detect(write(tmp_path, "a.odt", odt)).media_type == "application/vnd.oasis.opendocument.text"
    assert mt.detect(write(tmp_path, "a.zip", plain)).media_type == "application/zip"
    assert mt.detect(write(tmp_path, "a.epub", plain)).media_type == "application/epub+zip"  # extension refines zip
    assert mt.detect(write(tmp_path, "broken.docx", b"PK\x03\x04garbage")).media_type.endswith("wordprocessingml.document")


def test_legacy_office_ole_uses_extension(tmp_path):
    ole = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"\x00" * 50
    assert mt.detect(write(tmp_path, "a.doc", ole)).media_type == "application/msword"
    assert mt.detect(write(tmp_path, "a.xls", ole)).media_type == "application/vnd.ms-excel"
    assert mt.detect(write(tmp_path, "a.ppt", ole)).media_type == "application/vnd.ms-powerpoint"
    assert mt.detect(write(tmp_path, "a.bin", ole)).media_type == "application/x-ole-storage"


@pytest.mark.parametrize(
    "name,data,expected",
    [
        ("a.md", b"# Title\n\ntext", "text/markdown"),
        ("a.csv", b"a,b\n1,2\n", "text/csv"),
        ("a.json", b'{"a": 1}', "application/json"),
        ("a.txt", "café".encode("utf-8"), "text/plain"),
        ("noext", b"plain words", "text/plain"),
        ("a.html", b"<!DOCTYPE html><html></html>", "text/html"),
        ("page", b"<html><body>x</body></html>", "text/html"),
        ("a.xml", b'<?xml version="1.0"?><a/>', "application/xml"),
        ("a.svg", b'<?xml version="1.0"?><svg/>', "image/svg+xml"),
        ("a.svg", b"<svg xmlns='x'/>", "image/svg+xml"),
        ("a.vtt", b"WEBVTT\n\n00:00.000 --> 00:01.000\nHi", "text/vtt"),

        ("utf16.txt", "hi".encode("utf-16"), "text/plain"),
    ],
)
def test_text_detection(tmp_path, name, data, expected):
    info = mt.detect(write(tmp_path, name, data))
    assert info.media_type == expected and info.how == "text"


def test_short_signatures_do_not_misclassify_ordinary_text(tmp_path):
    for text in (b"BMW annual report", b"ID3 specification notes", b"BZhang wrote this", b"OggS is a marker"):
        assert mt.detect(write(tmp_path, "note.txt", text)).media_type == "text/plain", text


def test_multibyte_character_cut_by_the_sniff_window_is_still_text(tmp_path):
    data = b"a" * 8191 + "é".encode("utf-8")  # the two bytes of the last character straddle 8192
    assert mt.detect(write(tmp_path, "a.txt", data)).media_type == "text/plain"


def test_extension_is_the_fallback_for_unrecognised_binary(tmp_path):
    info = mt.detect(write(tmp_path, "a.heic", b"\x00\x01\x02\x03" * 10))
    assert info.media_type == "image/heic" and info.how == "extension"
    info = mt.detect(write(tmp_path, "blob.xyz", b"\x00\x01\x02\x03" * 10))
    assert info.media_type == "application/octet-stream" and info.how == "unknown"


@pytest.mark.parametrize("ext,kind", [(".gdoc", "document"), (".gsheet", "spreadsheet"), (".gslides", "presentation"),
                                      (".gform", "form"), (".gmap", "map"), (".gsite", "site"), (".GDOC", "document")])
def test_google_cloud_native_placeholders(tmp_path, ext, kind):
    info = mt.detect(write(tmp_path, f"Report{ext}", b'{"url": "https://example.com/x"}'))
    assert info.cloud_native and info.how == "cloud-native" and info.media_type == f"application/vnd.google-apps.{kind}"


def test_empty_files_trust_the_extension(tmp_path):
    assert mt.detect(write(tmp_path, "a.pdf", b"")).media_type == "application/pdf"
    assert mt.detect(write(tmp_path, "a.txt", b"")).media_type == "text/plain"
    assert mt.detect(write(tmp_path, "noext", b"")).media_type == "application/octet-stream"


def test_missing_file_does_not_crash(tmp_path):
    info = mt.detect(tmp_path / "gone.pdf")
    assert (info.media_type, info.how) == ("application/pdf", "extension")
