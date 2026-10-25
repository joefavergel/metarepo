"""001/R7 compatibility decision table."""
import pytest

from metarepo_engine import compat


@pytest.mark.parametrize(
    "requires, installed, status",
    [
        (">=0.3,<0.4", "0.3.0", compat.OK),
        (">=0.3,<0.4", "0.3.9", compat.OK),
        (">=0.3,<0.4", "0.4.0", compat.MIGRATE_PROJECT),  # 001/AC4
        (">=0.3,<0.4", "0.2.9", compat.UPDATE_TOOLKIT),
        (">=1.2,<2", "1.2.0", compat.OK),
        (">=1.2,<2", "1.9.9", compat.OK),
        (">=1.2,<2", "2.0.0", compat.MIGRATE_PROJECT),
        (">=1.2,<2", "1.1.9", compat.UPDATE_TOOLKIT),
        (">=0.1", "5.0.0", compat.OK),
        ("==0.1.0", "0.1.1", compat.UPDATE_TOOLKIT),
        (">=0.1, <0.2", "0.1.0", compat.OK),
        (">=0.1,<0.2", "0.1.0+local", compat.OK),
    ],
)
def test_decision_table(requires, installed, status):
    result = compat.check(requires, installed)
    assert result.status == status
    assert result.ok == (status == compat.OK)
    if status == compat.MIGRATE_PROJECT:
        assert "migrate" in result.message
    if status == compat.UPDATE_TOOLKIT:
        assert "update" in result.message


@pytest.mark.parametrize("bad", ["", "   ", "0.3", "~=0.3", ">=abc", ">=0.3,,<0.4", None, 3])
def test_bad_requires(bad):
    with pytest.raises(compat.CompatError):
        compat.check(bad, "0.1.0")


def test_lines_and_requires_for():
    assert compat.line_of("0.1.0") == "0.1"
    assert compat.line_of("1.4.2") == "1"
    assert compat.requires_for("0.1.0") == ">=0.1,<0.2"
    assert compat.requires_for("2.3.1") == ">=2.3,<3"
    # what a fresh project is written with must accept the toolkit that wrote it
    for v in ("0.1.0", "0.7.3", "1.0.0", "3.2.1"):
        assert compat.check(compat.requires_for(v), v).ok
