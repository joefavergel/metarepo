"""001/R12: every CI rule is proven to fire (001/T2.3), plus the repository's own state."""
import ast
import json
import re
import shutil
import sys
from pathlib import Path

import pytest

import ci_check

IGNORE = shutil.ignore_patterns("__pycache__", "*.pyc", ".pytest_cache", ".DS_Store")


@pytest.fixture
def repo_copy(tmp_path, repo):
    dest = tmp_path / "repo"
    for name in ("tooling", "plugin", ".claude-plugin", "tools"):
        shutil.copytree(repo / name, dest / name, ignore=IGNORE)
    return dest


def test_this_repository_passes_every_rule(repo):
    results = ci_check.run(repo)
    assert {k: v for k, v in results.items() if v} == {}


def test_main_exit_status(repo, capsys, monkeypatch):
    assert ci_check.main() == 0
    out = capsys.readouterr().out
    for name, _ in ci_check.RULES:
        assert f"ok   {name}" in out
    monkeypatch.setattr(ci_check, "RULES", (("always-fails", lambda root: ["nope"]),))
    assert ci_check.main() == 1


def edit(path: Path, fn):
    path.write_text(fn(path.read_text(encoding="utf-8")), encoding="utf-8")


# ---- manifests
def test_manifest_rules_fire(repo_copy):
    mp = repo_copy / ".claude-plugin" / "marketplace.json"
    data = json.loads(mp.read_text(encoding="utf-8"))
    for mutate, needle in [
        (lambda d: d.pop("owner"), "owner.name"),
        (lambda d: d.update(name="Bad Name"), "kebab"),
        (lambda d: d["plugins"][0].update(source="../plugin"), "source"),
        (lambda d: d["plugins"][0].update(source="./missing"), "does not exist"),
        (lambda d: d["plugins"][0].update(name="other"), "differs"),
        (lambda d: d.update(plugins=[]), "non-empty"),
    ]:
        broken = json.loads(json.dumps(data))
        mutate(broken)
        mp.write_text(json.dumps(broken), encoding="utf-8")
        assert any(needle in p for p in ci_check.check_manifests(repo_copy)), needle
    mp.write_text("{not json", encoding="utf-8")
    assert any("invalid JSON" in p for p in ci_check.check_manifests(repo_copy))


def test_plugin_manifest_rules_fire(repo_copy):
    pj = repo_copy / "plugin" / ".claude-plugin" / "plugin.json"
    base = json.loads(pj.read_text(encoding="utf-8"))
    for key, value, needle in [("version", "1.0", "semver"), ("name", "claude-thing", "reserved"),
                               ("name", "Not Kebab", "kebab"), ("description", "", "description")]:
        pj.write_text(json.dumps({**base, key: value}), encoding="utf-8")
        assert any(needle in p for p in ci_check.check_manifests(repo_copy)), needle


def test_hooks_manifest_rules_fire(repo_copy):
    hooks = repo_copy / "plugin" / "hooks"
    (hooks / "hooks.json").write_text(json.dumps({"PreToolUse": []}), encoding="utf-8")
    assert any("wrapper" in p for p in ci_check.check_manifests(repo_copy))
    (hooks / "hooks.json").write_text(json.dumps({"hooks": {}}), encoding="utf-8")
    (hooks / "guard_paths.py").unlink()
    assert any("guard_paths.py" in p for p in ci_check.check_manifests(repo_copy))


# ---- skills and command set
def test_skill_rules_fire(repo_copy):
    skills = repo_copy / "tooling" / "skills"
    md = skills / "intake" / "SKILL.md"
    original = md.read_text(encoding="utf-8")
    md.write_text(original.replace("name: intake", "name: other"), encoding="utf-8")
    assert any("frontmatter name" in p for p in ci_check.check_skills(repo_copy))
    md.write_text(re.sub(r"description: .*\n", "", original), encoding="utf-8")
    assert any("description is required" in p for p in ci_check.check_skills(repo_copy))
    md.write_text("no frontmatter\n", encoding="utf-8")
    assert any("frontmatter missing" in p for p in ci_check.check_skills(repo_copy))
    md.write_text(original + "\nUse ${CLAUDE_PLUGIN_ROOT}/engine\n", encoding="utf-8")
    assert any("harness-specific" in p for p in ci_check.check_skills(repo_copy))
    md.write_text(original, encoding="utf-8")
    (skills / "intake" / "references" / "engine.md").unlink()
    assert any("engine.md missing" in p for p in ci_check.check_skills(repo_copy))


def test_command_set_rule_fires(repo_copy):
    skills = repo_copy / "tooling" / "skills"
    shutil.rmtree(skills / "doctor")
    assert any("missing skills: doctor" in p for p in ci_check.check_skills(repo_copy))
    (skills / "surprise").mkdir()
    (skills / "surprise" / "SKILL.md").write_text("---\nname: surprise\ndescription: d\n---\n", encoding="utf-8")
    assert any("unexpected skills: surprise" in p for p in ci_check.check_skills(repo_copy))


def test_frontmatter_reader_handles_block_scalars_and_quotes():
    fm = ci_check.parse_frontmatter('---\nname: a\ndescription: >\n  one\n  two\nother: "q: x"\n---\nbody\n')
    assert fm == {"name": "a", "description": "one two", "other": "q: x"}
    assert ci_check.parse_frontmatter("---\nname: a\n") is None


# ---- plugin == tooling, versions
def test_plugin_sync_rule_fires(repo_copy):
    edit(repo_copy / "plugin" / "engine" / "cli.py", lambda t: t + "# drift\n")
    assert any("differs: plugin/engine/cli.py" in p for p in ci_check.check_plugin_sync(repo_copy))


def test_version_rules_fire(repo_copy):
    pj = repo_copy / "plugin" / ".claude-plugin" / "plugin.json"
    data = json.loads(pj.read_text(encoding="utf-8"))
    pj.write_text(json.dumps({**data, "version": "9.9.9"}), encoding="utf-8")
    assert any("!= tooling/VERSION" in p for p in ci_check.check_versions(repo_copy))
    pj.write_text(json.dumps(data), encoding="utf-8")
    (repo_copy / "tooling" / "VERSION").write_text("0.2.0\n", encoding="utf-8")
    problems = ci_check.check_versions(repo_copy)
    assert any("!= tooling/VERSION" in p for p in problems) and any("no entry for version 0.2.0" in p for p in problems)
    (repo_copy / "tooling" / "VERSION").write_text("two\n", encoding="utf-8")
    assert any("not semver" in p for p in ci_check.check_versions(repo_copy))
    (repo_copy / "tooling" / "CHANGELOG.md").unlink()
    assert any("CHANGELOG" in p for p in ci_check.check_versions(repo_copy))


# ---- templates
def test_template_rules_fire(repo_copy):
    tdir = repo_copy / "tooling" / "templates"
    (tdir / "bad.json").write_text("{", encoding="utf-8")
    (tdir / "bad.md.tmpl").write_text("fine ${ok}\nprice $5 is not a placeholder\n", encoding="utf-8")
    problems = ci_check.check_templates(repo_copy)
    assert any("bad.json" in p and "invalid JSON" in p for p in problems)
    assert any("bad.md.tmpl:2" in p and "invalid placeholder" in p for p in problems)
    (tdir / "good.md.tmpl").write_text("cost $$5 and ${name}\n", encoding="utf-8")
    assert not any("good.md.tmpl" in p for p in ci_check.check_templates(repo_copy))


# ---- denylist (001/AC6)
def test_denylisted_term_in_any_shipped_file_fails(repo_copy):
    sentinel = "zzdenyzz"
    (repo_copy / "tools" / "denylist.txt").write_text(f"# test\n{sentinel}\nre:\\bsecret-\\d+\\b\n", encoding="utf-8")
    assert ci_check.check_denylist(repo_copy) == []
    for rel in ("tooling/skills/intake/SKILL.md", "tooling/engine/metarepo_engine/jsonout.py",
                "plugin/hooks/guard_paths.py", "tooling/templates/skeleton.json"):
        target = repo_copy / rel
        if not target.exists():
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text("{}\n", encoding="utf-8")
        original = target.read_text(encoding="utf-8")
        target.write_text(original + f"\nSome {sentinel.upper()} text\n", encoding="utf-8")
        problems = ci_check.check_denylist(repo_copy)
        assert any(rel in p and "denylisted" in p for p in problems), rel
        target.write_text(original, encoding="utf-8")
    target = repo_copy / "tooling" / "INSTALL.md"
    original = target.read_text(encoding="utf-8")
    target.write_text(original + "\nsecret-42\n", encoding="utf-8")
    assert any("secret-42" in p for p in ci_check.check_denylist(repo_copy))
    target.write_text(original + f"\nnot{sentinel}here\n", encoding="utf-8")
    assert ci_check.check_denylist(repo_copy) == []  # whole-word matching


def test_denylist_file_itself_is_not_scanned_and_must_have_terms(repo_copy):
    (repo_copy / "tools" / "denylist.txt").write_text("# only comments\n", encoding="utf-8")
    assert any("no terms" in p for p in ci_check.check_denylist(repo_copy))
    (repo_copy / "tools" / "denylist.txt").unlink()
    assert any("missing" in p for p in ci_check.check_denylist(repo_copy))


def test_seed_denylist_is_present_and_loadable(repo):
    assert len(ci_check.load_denylist(repo / "tools" / "denylist.txt")) >= 1


# ---- no-artifact rule (001/AC3)
def test_find_artifacts_flags_toolkit_files(tmp_path):
    proj = tmp_path / "p"
    (proj / "30_wiki" / "skills").mkdir(parents=True)
    (proj / "30_wiki" / "skills" / "answer-from-wiki.md").write_text("# project file\n", encoding="utf-8")
    (proj / "AGENTS.md").write_text("# fine\n", encoding="utf-8")
    assert ci_check.find_artifacts(proj) == []
    (proj / "engine").mkdir()
    (proj / "shared").mkdir()
    (proj / "shared" / "SKILL.md").write_text("x", encoding="utf-8")
    (proj / "shared" / "tool.py").write_text("x", encoding="utf-8")
    (proj / "outputs").mkdir()
    (proj / "outputs" / "note.md").write_text("from metarepo_engine import x\n", encoding="utf-8")
    found = ci_check.find_artifacts(proj)
    assert "engine/" in found and "shared/SKILL.md" in found and "shared/tool.py" in found
    assert any(f.startswith("outputs/note.md") for f in found)


# ---- stdlib-only (R12) and Python floor (R10)
@pytest.mark.parametrize("script", ["ci_check.py", "build.py", "package.py"])
def test_dev_scripts_are_stdlib_only(repo, script):
    tree = ast.parse((repo / "tools" / script).read_text(encoding="utf-8"))
    local = {"build"}
    for node in ast.walk(tree):
        names = []
        if isinstance(node, ast.Import):
            names = [a.name.split(".")[0] for a in node.names]
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            names = [node.module.split(".")[0]]
        for name in names:
            assert name in sys.stdlib_module_names or name in local, f"{script} imports {name}"


BANNED = [r"\btomllib\b", r"datetime\.UTC\b", r"\bStrEnum\b", r"\btyping\.Self\b", r"import Self\b",
          r"\bfile_digest\b", r"\bitertools\.batched\b", r"\bexception_group\b", r"fromisoformat\([^)]*Z"]


def _py_files(repo):
    for base in ("tooling", "plugin", "tools", "tests"):
        for f in (repo / base).rglob("*.py"):
            if "__pycache__" not in f.parts:
                yield f


def test_python_310_floor(repo):
    for f in _py_files(repo):
        text = f.read_text(encoding="utf-8")
        ast.parse(text, filename=str(f), feature_version=(3, 10))
        if f.name == "test_ci_check.py":
            continue  # holds the banned patterns themselves
        for pat in BANNED:
            assert not re.search(pat, text), f"{f.relative_to(repo)} uses a post-3.10 API: {pat}"
