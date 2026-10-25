"""002/R9, R10, R11: naming helpers (002/T4)."""
import pytest

from metarepo_engine import naming


@pytest.mark.parametrize(
    "name, expected",
    [
        ("Hello World", "hello-world"),
        ("  --Trim__Me--  ", "trim-me"),
        ("Caf\u00e9 cr\u00e8me", "cafe-creme"),
        ("Stra\u00dfe \u00c6ther \u00d8re", "strasse-aether-ore"),
        ("na\u00efve  r\u00e9sum\u00e9", "naive-resume"),
        ("e\u0301cole", "ecole"),  # NFD input
        ("\u00e9cole", "ecole"),  # NFC input gives the same slug
        ("A.B,C;D", "a-b-c-d"),
        ("under_score", "under-score"),
        ("UPPER123", "upper123"),
        ("\u4e2d\u6587", f"item-{naming.hash6(chr(0x4e2d) + chr(0x6587))}"),  # CJK fallback
        ("???", f"item-{naming.hash6('???')}"),
        ("", f"item-{naming.hash6('')}"),
        ("con", "con-item"),
        ("NUL", "nul-item"),
        ("com1", "com1-item"),
        ("lpt9", "lpt9-item"),
        ("console", "console"),
        ("a" * 120, "a" * 80),
        (("word-" * 30), ("word-" * 30)[:80].rstrip("-")),
    ],
)
def test_slugify_table(name, expected):
    assert naming.slugify(name) == expected


def test_slugify_is_idempotent_and_ascii_kebab():
    import re

    for name in ["Caf\u00e9 au lait", "Report Q3 (final).v2", "a  b", "\u4e2d\u6587 doc", "con", "x" * 200]:
        once = naming.slugify(name)
        assert naming.slugify(once) == once
        assert re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", once) and len(once) <= 80


@pytest.mark.parametrize(
    "name, expected",
    [
        ("Annual Report 2024.PDF", "annual-report-2024.pdf"),
        ("2410.12345v2.pdf", "2410-12345v2.pdf"),
        ("archive.tar.GZ", "archive-tar.gz"),
        ("Caf\u00e9 menu.DOCX", "cafe-menu.docx"),
        ("noextension", "noextension"),
        ("trailing.", "trailing"),
        (".env", "env"),
        ("CON.txt", "con-item.txt"),
        ("nul", "nul-item"),
        ("\u4e2d\u6587.pdf", f"item-{naming.hash6(chr(0x4e2d) + chr(0x6587))}.pdf"),
        ("photo.J\u00d6PG", "photo.jopg"),
        (("x" * 100) + ".pdf", ("x" * 80) + ".pdf"),
    ],
)
def test_slugify_filename_table(name, expected):
    assert naming.slugify_filename(name) == expected


def test_nfc_and_transliteration_agree_for_composed_and_decomposed():
    assert naming.transliterate("e\u0301") == naming.transliterate("\u00e9") == "e"
    assert naming.nfc("e\u0301") == "\u00e9"
    assert naming.hash6("e\u0301") == naming.hash6("\u00e9")


def test_path_limit_boundary_and_proposal():
    ok = "a/" + "b" * 198  # exactly 200
    assert naming.check_relpath(ok).ok and naming.check_relpath(ok).length == 200
    over = "10_sources/papers/" + ("long-name-" * 20) + ".pdf"
    assert len(over) > 200
    check = naming.check_relpath(over)
    assert not check.ok and check.proposal is not None
    assert len(check.proposal) <= 200 and check.proposal.startswith("10_sources/papers/")
    assert check.proposal.endswith(".pdf") and naming.hash6(over) in check.proposal
    assert naming.check_relpath(check.proposal).ok


def test_custom_limit_and_unfixable_path():
    assert not naming.check_relpath("a/" + "b" * 60, limit=50).ok
    deep = "/".join(["d" * 40] * 6) + "/f.pdf"
    check = naming.check_relpath(deep)
    assert not check.ok and check.proposal is None  # folders alone exceed the limit


def test_safe_relpath_returns_normalized_or_raises_with_proposal():
    assert naming.safe_relpath("a\\b\\c.pdf") == "a/b/c.pdf"
    with pytest.raises(naming.NamingError) as exc:
        naming.safe_relpath("x/" + "y" * 250 + ".md")
    assert exc.value.code == "path_too_long" and exc.value.proposal and len(exc.value.proposal) <= 200


@pytest.mark.parametrize(
    "bad",
    ["", "/abs/path.pdf", "C:/win/path.pdf", "a/../b.pdf", "a/con/b.pdf", "a/nul.txt", "a/trailing./b",
     "a/name?.pdf", 'a/q".pdf', "a/b |c.pdf", "a/endswith /x"],
)
def test_safe_relpath_rejects_unsafe_paths(bad):
    with pytest.raises(naming.NamingError) as exc:
        naming.safe_relpath(bad)
    assert exc.value.code == "path_invalid"


def test_reserved_name_detection():
    assert naming.is_reserved("CON") and naming.is_reserved("com3.txt") and not naming.is_reserved("connect")
    assert len(naming.WINDOWS_RESERVED) == 22
