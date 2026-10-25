"""The /create-metarepo skill (005/T4): structure, and that the interview keys match the answers schema."""
import re
from pathlib import Path

from metarepo_engine.commands.init import ANSWER_KEYS

SKILL = Path(__file__).resolve().parents[2] / "tooling" / "skills" / "create-metarepo"


def test_skill_is_implemented_not_placeholder():
    body = " ".join((SKILL / "SKILL.md").read_text(encoding="utf-8").split())
    assert "placeholder" not in body.lower() and "CLAUDE_PLUGIN_ROOT" not in body
    for needle in ("init <target> --answers", "--dry-run", "/join-metarepo", "/init-wiki", "00_inbox/",
                   "AskUserQuestion", "create nothing"):
        assert needle in body, needle


def test_interview_keys_exist_in_answers_schema():
    text = (SKILL / "references" / "interview.md").read_text(encoding="utf-8")
    keys = set(re.findall(r"`([a-z_]+)`", text)) & {
        "name", "description", "notes", "repos", "language", "sync_provider", "objective", "scope", "team",
        "wiki_write_mode", "intake_confirm", "wiki_owners", "slug", "url", "default_branch"}
    top = {k for k in keys if k not in ("url", "default_branch")}
    assert top <= ANSWER_KEYS and {"name", "description", "notes", "repos"} <= top
