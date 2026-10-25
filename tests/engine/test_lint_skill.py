"""The /lint-wiki skill (012/R4, T6): structure, vocabulary and a row in the reference for every check id."""
import re
from pathlib import Path

from metarepo_engine.lint import CATALOG

SKILL = Path(__file__).resolve().parents[2] / "tooling" / "skills" / "lint-wiki"


def test_skill_is_implemented_not_placeholder():
    body = " ".join((SKILL / "SKILL.md").read_text(encoding="utf-8").split())
    assert "placeholder" not in body.lower() and "CLAUDE_PLUGIN_ROOT" not in body
    for needle in ("lint --json", "--only", "references/checks.md", "references/engine.md", "block", "masked",
                   "/feed-wiki", "/intake --retriage", "extract --force", "/doctor", "read-only",
                   "allow_policy_downgrade", "lint.severity", "own lease"):
        assert needle in body, needle


def test_reference_lists_every_check_with_its_default_severity():
    text = (SKILL / "references" / "checks.md").read_text(encoding="utf-8")
    rows = {m.group(1): m.group(2).strip("* ") for m in re.finditer(r"^\| `([a-z-]+)` \| ([^|]+) \|", text, re.M)}
    assert rows == CATALOG
