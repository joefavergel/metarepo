"""The /join-metarepo skill (006/T4): structure, engine commands it names exist, R-coverage, explicit [TO VERIFY]."""
import re
from pathlib import Path

from metarepo_engine.commands import load_commands

SKILL = Path(__file__).resolve().parents[2] / "tooling" / "skills" / "join-metarepo"


def body():
    return " ".join((SKILL / "SKILL.md").read_text(encoding="utf-8").split())


def test_skill_is_implemented_and_covers_requirements():
    text = body()
    assert "placeholder" not in text.lower() and "CLAUDE_PLUGIN_ROOT" not in text
    for needle in ("R1", "R2", "R3", "R4", "R6", "R7", "R8", "R9", "R10", "claimed identity", "inside the synced folder",
                   "Ask before", "AGENTS.md", "doctor --local", "dry-run", "stop"):
        assert needle in text, needle


def test_engine_commands_named_by_the_skill_exist_and_are_implemented():
    names = {c.name: c for c in load_commands()}
    used = set(re.findall(r"<engine> ([a-z]+(?: [a-z]+)?)", (SKILL / "SKILL.md").read_text(encoding="utf-8")))
    for u in used:
        parts = u.split()
        cand = [n for n in names if n == u or n == parts[0]]
        assert cand, u
    for n in ("repos list", "repos path", "repos clone", "repos status", "config user"):
        assert names[n].implemented


def test_references_exist_and_keep_unverified_items_explicit():
    for ref in ("harness-wiring.md", "sync-providers.md", "engine.md"):
        assert (SKILL / "references" / ref).is_file()
    assert "[TO VERIFY]" in (SKILL / "references" / "sync-providers.md").read_text(encoding="utf-8")
