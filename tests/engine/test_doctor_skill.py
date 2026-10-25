"""The /doctor skill (013/R6, T6): structure, vocabulary and a remedy for every check id."""
import re
from pathlib import Path

from metarepo_engine.doctor import CHECKS

SKILL = Path(__file__).resolve().parents[2] / "tooling" / "skills" / "doctor"


def test_skill_is_implemented_not_placeholder():
    body = " ".join((SKILL / "SKILL.md").read_text(encoding="utf-8").split())
    assert "placeholder" not in body.lower() and "CLAUDE_PLUGIN_ROOT" not in body
    for needle in ("doctor --json", "--local", "--fix-safe", "--yes", "--dest", "10_sources/", "references/remedies.md",
                   "plain", "never apply", "separately for every action"):
        assert needle.lower() in body.lower() or needle in body, needle


def test_every_check_has_a_plain_language_remedy_with_provider_notes():
    text = (SKILL / "references" / "remedies.md").read_text(encoding="utf-8")
    headings = " ".join(re.findall(r"^## (.+)$", text, re.M))
    for check in CHECKS:
        assert check in headings, check
    for provider in ("Google Drive", "OneDrive", "Dropbox"):
        assert provider in text
    assert "export" in text.lower() and "00_inbox/" in text  # cloud-native remedy (R6)


def test_skill_documents_every_action_kind():
    body = (SKILL / "SKILL.md").read_text(encoding="utf-8")
    for kind in ("remove", "move-out", "release-lease", "render-template"):
        assert f"`{kind}`" in body
