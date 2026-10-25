"""``doctor`` (spec 013): gitignore matcher (R2), every check on its own fixture (AC1), fixes (R3, R4, AC2),
read-only behaviour (AC3), ``last_doctor`` (R5). The skill (R6) is covered by test_doctor_skill.py."""
from __future__ import annotations

import hashlib
import json
import os
import time
from pathlib import Path

import pytest
import yaml

from metarepo_engine import doctor as core
from metarepo_engine import readable
from metarepo_engine.gitignore import IgnoreSet

BASIC = {"name": "Example Project", "description": "A short description.", "language": "en"}
WITH_REPO = {**BASIC, "repos": [{"url": "https://example.com/o/r.git"}]}


# ------------------------------------------------------------------ R2 / T1: matcher
MATCHER = [
    # (patterns, path, is_dir, ignored)
    ([".git/"], ".git", True, True),
    ([".git/"], ".git", False, False),                 # trailing slash: directories only
    ([".git/"], "a/b/.git/config", False, True),       # inside an ignored folder
    (["*.pyc"], "a/b/x.pyc", False, True),
    (["*.pyc"], "a/b/x.py", False, False),
    (["/build"], "build", True, True),                 # leading slash anchors
    (["/build"], "sub/build", True, False),
    (["build"], "sub/build", True, True),              # no slash: any depth
    (["doc/frotz"], "doc/frotz", False, True),         # inner slash anchors
    (["doc/frotz"], "a/doc/frotz", False, False),
    (["*.log", "!keep.log"], "keep.log", False, False),        # negation, last match wins
    (["*.log", "!keep.log"], "other.log", False, True),
    (["!keep.log", "*.log"], "keep.log", False, True),         # order matters
    (["logs/", "!logs/keep.log"], "logs/keep.log", False, True),   # cannot re-include below an ignored folder
    (["a/**/b"], "a/b", False, True),
    (["a/**/b"], "a/x/y/b", False, True),
    (["a/**/b"], "x/a/b", False, False),
    (["**/tmp"], "x/y/tmp", True, True),
    (["abc/**"], "abc/d/e", False, True),
    (["abc/**"], "abc", True, False),
    (["file?.txt"], "file1.txt", False, True),
    (["file?.txt"], "file10.txt", False, False),
    (["[ab]*.txt"], "bat.txt", False, True),
    (["[!ab]*.txt"], "bat.txt", False, False),
    (["[a-c]x"], "bx", False, True),
    (["\\#hash"], "#hash", False, True),
    (["# comment", "", "  "], "anything", False, False),
    (["~$*"], "~$report.docx", False, True),
    (["*.PEM"], "key.pem", False, True),               # case-insensitive (cloud drives are)
    (["id_rsa*"], "shared/id_rsa.pub", False, True),
    ([".env", ".env.*"], "x/.env.local", False, True),
]


@pytest.mark.parametrize("patterns,path,is_dir,expected", MATCHER)
def test_gitignore_matcher_table(patterns, path, is_dir, expected):
    assert IgnoreSet(patterns).is_ignored(path, is_dir=is_dir) is expected


def test_defaults_and_project_additions_both_apply(cli, tmp_path):
    root = fresh(cli, tmp_path)
    cfg = yaml.safe_load((root / "project.yaml").read_text(encoding="utf-8"))
    cfg["policy"] = {"forbidden_paths": ["*.draft", "!keep.draft"]}
    (root / "project.yaml").write_text(yaml.safe_dump(cfg, sort_keys=False), encoding="utf-8")
    touch(root, "shared/a.draft")
    touch(root, "shared/keep.draft")
    touch(root, "shared/x.pyc")
    ids = {f["path"] for f in run(cli, root)["findings"] if f["id"] == "forbidden-content"}
    assert ids == {"shared/a.draft", "shared/x.pyc"}


# ------------------------------------------------------------------ helpers
def fresh(cli, tmp_path, answers=BASIC, sub="Shared drives/proj"):
    target = tmp_path / sub
    target.mkdir(parents=True)
    f = tmp_path / "answers.json"
    f.write_text(json.dumps(answers), encoding="utf-8")
    code, env, _ = cli("init", str(target), "--answers", str(f), "--json")
    assert code == 0, env
    return target


def touch(root: Path, rel: str, text: str = "x\n") -> Path:
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")
    return p


def run(cli, root, *extra, expect=None):
    code, env, err = cli("doctor", "--root", str(root), "--json", *extra)
    assert isinstance(env, dict), (env, err)
    if expect is not None:
        assert code == expect, (code, env["errors"], [(f["id"], f["path"]) for f in env["data"].get("findings", [])])
    return env["data"] if env["ok"] or code == 1 else env


def by_id(data, check):
    return [f for f in data["findings"] if f["id"] == check]


def tree_hash(root: Path, skip: tuple[str, ...] = ()) -> dict[str, str]:
    out = {}
    for p in sorted(root.rglob("*")):
        rel = p.relative_to(root).as_posix()
        if any(rel.startswith(s) for s in skip):
            continue
        if p.is_file():
            out[rel] = hashlib.sha256(p.read_bytes()).hexdigest()
        elif p.is_dir():
            out[rel + "/"] = ""
    return out


# ------------------------------------------------------------------ AC1: fresh metarepo is clean (005/AC1 half)
def test_fresh_metarepo_reports_nothing_but_optional_extractor_notes(cli, tmp_path):
    root = fresh(cli, tmp_path)
    data = run(cli, root, expect=0)
    for f in data["findings"]:
        assert f["id"] == "runtime" and f["severity"] == "info" and f["details"].get("extractor"), f
    assert data["summary"]["error"] == 0 and data["summary"]["warn"] == 0
    assert set(data["checks"]) == set(core.CHECKS)
    skipped = {k for k, v in data["checks"].items() if v["status"] == "skipped"}
    assert skipped == set()  # no repos configured: nothing to skip


def test_fresh_metarepo_with_repos_reports_missing_clone_as_info(cli, tmp_path):
    root = fresh(cli, tmp_path, WITH_REPO)
    data = run(cli, root, expect=0)
    others = [f for f in data["findings"] if f["id"] not in ("runtime", "repo-missing")]
    assert not others
    assert all(f["severity"] == "info" for f in data["findings"] if f["id"] == "repo-missing")
    for check in ("repo-inside-tree", "repo-missing"):
        st = data["checks"][check]
        assert st["status"] in ("skipped", "ok", "findings")
    assert "code/README.md" in {p.relative_to(root).as_posix() for p in root.rglob("*") if p.is_file()}


# ------------------------------------------------------------------ AC1: one fixture per check
def test_contract_missing_folder_and_file(cli, tmp_path):
    root = fresh(cli, tmp_path)
    (root / "outputs" / "_templates" / ".keep").unlink()
    (root / "outputs" / "_templates").rmdir()
    (root / "outputs" / ".keep").unlink()
    (root / "outputs").rmdir()
    (root / "30_wiki").rename(root / "30_Wiki_old")
    (root / "AGENTS.md").unlink()
    data = run(cli, root, expect=1)
    found = {f["path"]: f for f in by_id(data, "contract-missing")}
    assert {"outputs", "30_wiki", "AGENTS.md"} <= set(found)
    assert all(f["severity"] == "error" for f in found.values())
    assert [a["kind"] for a in found["AGENTS.md"]["actions"]] == ["render-template"]
    assert found["outputs"]["actions"] == []  # a folder is restored by a person, not guessed


def test_contract_missing_wiki_file_only_when_wiki_is_configured(cli, tmp_path):
    root = fresh(cli, tmp_path)
    assert not by_id(run(cli, root), "contract-missing")  # gate closed: nothing expected in 30_wiki
    state = json.loads((root / "_system/state.json").read_text(encoding="utf-8"))
    state["wiki"]["configured"] = True
    (root / "_system/state.json").write_text(json.dumps(state), encoding="utf-8")
    assert [f["path"] for f in by_id(run(cli, root), "contract-missing")] == ["30_wiki/WIKI.md"]


def test_forbidden_content_file_and_folder(cli, tmp_path):
    root = fresh(cli, tmp_path)
    touch(root, "shared/.env")
    touch(root, "shared/tools/node_modules/pkg/index.js")
    touch(root, "personal/.DS_Store")
    data = run(cli, root, expect=1)
    paths = {f["path"] for f in by_id(data, "forbidden-content")}
    assert paths == {"shared/.env", "shared/tools/node_modules", "personal/.DS_Store"}  # folder reported once
    assert all(f["severity"] == "error" for f in by_id(data, "forbidden-content"))


def test_agents_md_shadowed_in_project_and_above(cli, tmp_path):
    root = fresh(cli, tmp_path, sub="parent/proj")
    touch(root, "CLAUDE.md")
    touch(root, ".claude/CLAUDE.md")
    touch(tmp_path / "parent", "CLAUDE.local.md")
    data = run(cli, root, expect=0)
    shadow = by_id(data, "agents-md-shadowed")
    assert {f["path"] for f in shadow if f["path"]} == {"CLAUDE.md", ".claude/CLAUDE.md"}
    assert any(f["path"] is None and f["details"]["location"].endswith("parent/CLAUDE.local.md") for f in shadow)
    assert {f["severity"] for f in shadow} == {"warn"}


def test_nested_git_dir_and_file(cli, tmp_path):
    root = fresh(cli, tmp_path)
    touch(root, "shared/tools/app/.git/HEAD", "ref\n")
    touch(root, "shared/other/.git", "gitdir: ../elsewhere\n")
    data = run(cli, root, expect=1)
    assert {f["path"] for f in by_id(data, "nested-git")} == {"shared/tools/app/.git", "shared/other/.git"}
    assert not by_id(data, "forbidden-content")  # reported once, as nested-git
    assert all(f["severity"] == "error" for f in by_id(data, "nested-git"))


def test_conflict_copy_cloud_native_and_possible_copy(cli, tmp_path):
    root = fresh(cli, tmp_path)
    touch(root, "shared/plan.md", "original\n")
    touch(root, "shared/plan (Alex's conflicted copy 2026-10-24).md", "copy\n")
    data = run(cli, root)
    copies = by_id(data, "conflict-copy")
    assert len(copies) == 1 and copies[0]["severity"] == "warn"
    assert copies[0]["details"]["original"] == "shared/plan.md"
    assert [a["kind"] for a in copies[0]["actions"]] == ["remove"]


def test_cloud_native_only_in_inbox_and_sources(cli, tmp_path):
    root = fresh(cli, tmp_path)
    touch(root, "00_inbox/notes.gdoc", "{}")
    touch(root, "10_sources/sheet.gsheet", "{}")
    touch(root, "shared/other.gdoc", "{}")
    data = run(cli, root)
    assert {f["path"] for f in by_id(data, "cloud-native")} == {"00_inbox/notes.gdoc", "10_sources/sheet.gsheet"}
    assert {f["severity"] for f in by_id(data, "cloud-native")} == {"warn"}
    assert all(a == [] for a in (f["actions"] for f in by_id(data, "cloud-native")))  # export is a human step


def test_not_local_placeholder(cli, tmp_path, monkeypatch):
    root = fresh(cli, tmp_path)
    touch(root, "30_wiki/page.md")
    touch(root, "shared/page.md")
    real = readable.classify

    def fake(path):
        return readable.Readability(readable.NOT_LOCAL, "dataless") if Path(path).name == "page.md" else real(path)

    monkeypatch.setattr(core.readable, "classify", fake)
    data = run(cli, root)
    found = by_id(data, "not-local")
    assert [f["path"] for f in found] == ["30_wiki/page.md"]  # shared/ may stay online-only (004/R13)
    assert found[0]["severity"] == "info"


def test_path_too_long_and_bad_names(cli, tmp_path):
    root = fresh(cli, tmp_path)
    touch(root, "shared/" + "a" * 60 + ".md")
    touch(root, "shared/nul.txt")
    touch(root, "shared/trailing.")
    nfd = "café.md"
    touch(root, "shared/" + nfd)
    cfg = yaml.safe_load((root / "project.yaml").read_text(encoding="utf-8"))
    cfg["policy"] = {"path_limit": 40}
    report = core.scan(root, cfg, version="0.1.0")
    long_ = [f for f in report.findings if f.id == "path-too-long"]
    assert any(f.path.endswith("a" * 60 + ".md") for f in long_) and {f.severity for f in long_} == {"warn"}
    names = {f.path: f for f in report.findings if f.id == "bad-name"}
    assert {"shared/nul.txt", "shared/trailing.", "shared/" + nfd} <= set(names)
    assert "NFC" in names["shared/" + nfd].message
    assert "Windows" in names["shared/nul.txt"].message


def test_temp_stragglers(cli, tmp_path):
    root = fresh(cli, tmp_path)
    touch(root, "shared/.report.md.tmp-12345")
    data = run(cli, root)
    found = by_id(data, "temp-stragglers")
    assert [f["path"] for f in found] == ["shared/.report.md.tmp-12345"] and found[0]["severity"] == "info"
    assert [a["kind"] for a in found[0]["actions"]] == ["remove"]


def test_stale_lease(cli, tmp_path):
    root = fresh(cli, tmp_path)
    lease = {"area": "wiki", "holder": {"actor": "a", "host": "h", "session": "s"},
             "acquired_at": "2020-01-01T00:00:00Z", "renewed_at": "2020-01-01T00:00:00Z",
             "expires_at": "2020-01-01T00:30:00Z", "operation": "feed-wiki", "nonce": "n1"}
    touch(root, "_system/locks/wiki.lease.json", json.dumps(lease))
    data = run(cli, root)
    found = by_id(data, "stale-lease")
    assert [f["path"] for f in found] == ["_system/locks/wiki.lease.json"] and found[0]["severity"] == "info"
    assert found[0]["actions"][0]["kind"] == "release-lease"
    touch(root, "_system/locks/intake.lease.json", "{not json")
    assert len(by_id(run(cli, root), "stale-lease")) == 2  # unreadable lease files are reported too


def test_toolkit_incompatible_both_directions(cli, tmp_path):
    root = fresh(cli, tmp_path)
    for requires, status in ((">=9.0,<10", "update-toolkit"), (">=0.0.1,<0.0.2", "migrate-project")):
        cfg = yaml.safe_load((root / "project.yaml").read_text(encoding="utf-8"))
        cfg["toolkit"]["requires"] = requires
        (root / "project.yaml").write_text(yaml.safe_dump(cfg, sort_keys=False), encoding="utf-8")
        data = run(cli, root, expect=1)  # doctor itself still runs outside the range
        found = by_id(data, "toolkit-incompatible")
        assert len(found) == 1 and found[0]["severity"] == "error" and found[0]["details"]["status"] == status
        assert "migrate" in found[0]["remedy"] or "update" in found[0]["remedy"]


def test_toolkit_artifacts(cli, tmp_path):
    root = fresh(cli, tmp_path)
    touch(root, "shared/copy/metarepo_engine/doctor.py", "x")
    touch(root, "shared/copy/skills/doctor/SKILL.md", "x")
    touch(root, ".claude/skills/x/SKILL.md", "x")
    touch(root, ".claude-plugin/plugin.json", "{}")
    data = run(cli, root)
    paths = {f["path"] for f in by_id(data, "toolkit-artifacts")}
    assert {"shared/copy/metarepo_engine", "shared/copy/skills/doctor/SKILL.md", ".claude/skills",
            ".claude-plugin"} <= paths
    assert {f["severity"] for f in by_id(data, "toolkit-artifacts")} == {"warn"}


def test_runtime_missing_pyyaml_is_an_error(cli, tmp_path, monkeypatch):
    import sys

    root = fresh(cli, tmp_path)
    cfg = yaml.safe_load((root / "project.yaml").read_text(encoding="utf-8"))
    monkeypatch.setitem(sys.modules, "yaml", None)  # `import yaml` now raises ImportError
    report = core.scan(root, cfg, version="0.1.0", local_only=True)
    errs = [f for f in report.findings if f.id == "runtime" and f.severity == "error"]
    assert errs and "pip install pyyaml" in errs[0].remedy


def test_runtime_old_python_is_an_error(cli, tmp_path, monkeypatch):
    import sys
    from collections import namedtuple

    root = fresh(cli, tmp_path)
    cfg = yaml.safe_load((root / "project.yaml").read_text(encoding="utf-8"))
    VI = namedtuple("VI", "major minor micro releaselevel serial")
    monkeypatch.setattr(sys, "version_info", VI(3, 8, 0, "final", 0))
    report = core.scan(root, cfg, version="0.1.0", local_only=True)
    assert any(f.id == "runtime" and f.severity == "error" and "3.10" in f.message for f in report.findings)


def test_code_folder_content(cli, tmp_path):
    root = fresh(cli, tmp_path, WITH_REPO)
    touch(root, "code/app/main.py")
    touch(root, "code/app/.git/HEAD")
    touch(root, "code/stray.txt")
    data = run(cli, root, expect=1)
    found = {f["path"]: f for f in by_id(data, "code-folder-content")}
    assert set(found) == {"code/app", "code/stray.txt"} and all(f["severity"] == "error" for f in found.values())
    assert not by_id(data, "nested-git")  # the clone is reported once, as a whole
    assert found["code/app"]["actions"][0]["kind"] == "move-out"


def test_repo_checks_use_clone_paths_when_006_provides_them(cli, tmp_path, monkeypatch):
    import types
    import sys

    root = fresh(cli, tmp_path, WITH_REPO)
    inside = root / "shared" / "clone"
    inside.mkdir(parents=True)
    fake = types.ModuleType("metarepo_engine.repos")
    fake.local_clone_paths = lambda r, cfg: {"r": inside}
    monkeypatch.setitem(sys.modules, "metarepo_engine.repos", fake)
    import metarepo_engine
    monkeypatch.setattr(metarepo_engine, "repos", fake, raising=False)
    cfg = yaml.safe_load((root / "project.yaml").read_text(encoding="utf-8"))
    report = core.scan(root, cfg, version="0.1.0")
    inside_f = [f for f in report.findings if f.id == "repo-inside-tree"]
    assert [f.path for f in inside_f] == ["shared/clone"] and inside_f[0].severity == "error"
    fake.local_clone_paths = lambda r, cfg: {}
    report = core.scan(root, cfg, version="0.1.0")
    missing = [f for f in report.findings if f.id == "repo-missing"]
    assert len(missing) == 1 and missing[0].severity == "info"


def test_large_file_outside_sources_only(cli, tmp_path):
    root = fresh(cli, tmp_path)
    touch(root, "shared/big.bin", "x" * 40000)
    touch(root, "10_sources/big.bin", "x" * 40000)
    cfg = yaml.safe_load((root / "project.yaml").read_text(encoding="utf-8"))
    cfg["doctor"] = {"large_file_mb": 0.03}
    report = core.scan(root, cfg, version="0.1.0")
    assert [f.path for f in report.findings if f.id == "large-file"] == ["shared/big.bin"]


def test_inbox_age(cli, tmp_path):
    root = fresh(cli, tmp_path)
    old = touch(root, "00_inbox/old.pdf")
    touch(root, "00_inbox/new.pdf")
    ts = time.time() - 30 * 86400
    os.utime(old, (ts, ts))
    data = run(cli, root)
    found = by_id(data, "inbox-age")
    assert [f["path"] for f in found] == ["00_inbox/old.pdf"] and found[0]["severity"] == "info"


def test_secrets_suspect(cli, tmp_path):
    root = fresh(cli, tmp_path)
    touch(root, "shared/server.pem")
    touch(root, "personal/id_rsa")
    touch(root, "shared/.env.local")
    touch(root, "shared/readme.md")
    data = run(cli, root)
    assert {f["path"] for f in by_id(data, "secrets-suspect")} == {"shared/server.pem", "personal/id_rsa",
                                                                    "shared/.env.local"}
    assert {f["severity"] for f in by_id(data, "secrets-suspect")} == {"warn"}


# ------------------------------------------------------------------ R1: --local
def test_local_limits_to_machine_checks(cli, tmp_path):
    root = fresh(cli, tmp_path)
    touch(root, "shared/.env")
    touch(root, "CLAUDE.md")
    data = run(cli, root, "--local", expect=0)
    assert data["scope"] == "local"
    assert set(data["checks"]) == set(core.LOCAL_CHECKS)
    assert not [f for f in data["findings"] if f["id"] not in core.LOCAL_CHECKS]


# ------------------------------------------------------------------ AC3 / R5: read-only, last_doctor
def test_doctor_modifies_nothing_without_fix_safe_ac3(cli, tmp_path):
    root = fresh(cli, tmp_path)
    touch(root, "shared/.env")
    touch(root, "shared/tools/app/.git/HEAD")
    before = tree_hash(root)
    code, env, _ = cli("doctor", "--root", str(root), "--dry-run", "--json")
    assert code == 1 and env["data"]["recorded"] is False
    assert tree_hash(root) == before  # the whole tree, including _system/
    # a real run: the tree outside _system/ is untouched; the only change in state.json is last_doctor
    state_before = json.loads((root / "_system/state.json").read_text(encoding="utf-8"))
    run(cli, root, expect=1)
    assert tree_hash(root, skip=("_system",)) == {k: v for k, v in before.items() if not k.startswith("_system")}
    state_after = json.loads((root / "_system/state.json").read_text(encoding="utf-8"))
    assert {k: v for k, v in state_after.items() if k != "last_doctor"} == {
        k: v for k, v in state_before.items() if k != "last_doctor"}
    assert (root / "shared/.env").exists() and (root / "shared/tools/app/.git/HEAD").exists()


def test_last_doctor_records_counts_r5(cli, tmp_path):
    root = fresh(cli, tmp_path)
    touch(root, "shared/.env")
    run(cli, root, expect=1)
    last = json.loads((root / "_system/state.json").read_text(encoding="utf-8"))["last_doctor"]
    assert last["scope"] == "full" and last["counts"]["error"] >= 1 and last["toolkit"]


def test_last_doctor_is_best_effort(cli, tmp_path):
    root = fresh(cli, tmp_path)
    future = "2999-01-01T00:00:00Z"
    lease = {"area": "config", "holder": {"actor": "someone", "host": "h", "session": "s"},
             "acquired_at": "2026-01-01T00:00:00Z", "renewed_at": "2026-01-01T00:00:00Z",
             "expires_at": future, "operation": "migrate", "nonce": "n"}
    touch(root, "_system/locks/config.lease.json", json.dumps(lease))
    code, env, _ = cli("doctor", "--root", str(root), "--json")
    assert code == 0 and env["data"]["recorded"] is False
    assert [w["code"] for w in env["warnings"]] == ["last_doctor_not_recorded"]
    assert json.loads((root / "_system/state.json").read_text(encoding="utf-8"))["last_doctor"] is None


# ------------------------------------------------------------------ R3 / R4 / AC2: fixes
def test_fix_safe_proposes_but_acts_only_on_confirmation_ac2(cli, tmp_path):
    root = fresh(cli, tmp_path)
    touch(root, "shared/tools/app/.git/HEAD", "ref\n")
    touch(root, "shared/tools/app/main.py", "print(1)\n")
    data = run(cli, root, "--fix-safe", expect=1)
    actions = [a for f in by_id(data, "nested-git") for a in f["actions"]]
    assert len(actions) == 1 and actions[0]["kind"] == "move-out" and actions[0]["needs_dest"]
    assert data["actions"] == [a for f in data["findings"] for a in f["actions"]] and data["applied"] == []
    assert (root / "shared/tools/app/.git/HEAD").exists()  # proposals changed nothing
    aid = actions[0]["id"]
    out = tmp_path / "outside"
    # confirmed but no destination: skipped, nothing moved
    data = run(cli, root, "--fix-safe", "--yes", aid)
    assert [r["status"] for r in data["applied"]] == ["skipped"]
    assert (root / "shared/tools/app/.git/HEAD").exists()
    # a destination inside the synced tree is refused
    data = run(cli, root, "--fix-safe", "--yes", aid, "--dest", f"{aid}={root / 'personal'}")
    assert [r["status"] for r in data["applied"]] == ["failed"]
    assert (root / "shared/tools/app/.git/HEAD").exists()
    # confirmed with a destination outside: moved, logged, and the finding is gone
    data = run(cli, root, "--fix-safe", "--yes", aid, "--dest", f"{aid}={out}", expect=0)
    assert [r["status"] for r in data["applied"]] == ["applied"]
    assert (out / ".git" / "HEAD").read_text(encoding="utf-8") == "ref\n"
    assert not (root / "shared/tools/app/.git").exists() and (root / "shared/tools/app/main.py").exists()
    assert not by_id(data, "nested-git")
    events = [json.loads(l) for l in (root / "_system/events.log").read_text(encoding="utf-8").splitlines()]
    assert any(e["command"] == "doctor" and e["action"] == "move-out" for e in events)


def test_fix_safe_remove_forbidden_file_with_confirmation(cli, tmp_path):
    root = fresh(cli, tmp_path)
    touch(root, "shared/.env", "SECRET=1\n")
    touch(root, "shared/keep.md")
    code, env, _ = cli("doctor", "--root", str(root), "--fix-safe", "--yes", "nope-1", "--json")
    assert code == 2 and env["errors"][0]["code"] == "unknown_action"
    data = run(cli, root, "--fix-safe")
    aid = next(a["id"] for a in data["actions"] if a["path"] == "shared/.env")
    data = run(cli, root, "--fix-safe", "--yes", aid, "--dry-run")
    assert data["applied"][0]["status"] == "planned" and (root / "shared/.env").exists()
    data = run(cli, root, "--fix-safe", "--yes", aid)
    assert data["applied"][0]["status"] == "applied" and not (root / "shared/.env").exists()
    assert (root / "shared/keep.md").exists()
    events = [json.loads(l) for l in (root / "_system/events.log").read_text(encoding="utf-8").splitlines()]
    removed = [e for e in events if e["command"] == "doctor" and e["action"] == "remove"]
    assert removed and removed[0]["targets"] == ["shared/.env"] and len(removed[0]["sha256"]) == 64


def test_yes_requires_fix_safe(cli, tmp_path):
    root = fresh(cli, tmp_path)
    code, env, _ = cli("doctor", "--root", str(root), "--yes", "x", "--json")
    assert code == 2 and env["errors"][0]["code"] == "usage"


def test_nothing_under_10_sources_is_ever_proposed_r4(cli, tmp_path):
    root = fresh(cli, tmp_path)
    touch(root, "10_sources/.env", "x")
    touch(root, "10_sources/node_modules/p.js", "x")
    touch(root, "10_sources/a/.git/HEAD", "x")
    touch(root, "10_sources/doc.pdf", "x")
    touch(root, "10_sources/doc (Alex's conflicted copy 2026-10-24).pdf", "y")
    touch(root, "10_sources/.x.tmp-1", "x")
    data = run(cli, root, "--fix-safe")
    assert data["findings"], "the fixtures must still be reported"
    assert data["actions"] == []
    # even a hand-forged confirmation cannot reach 10_sources
    code, env, _ = cli("doctor", "--root", str(root), "--fix-safe", "--yes", "forbidden-content-remove-abc123",
                       "--json")
    assert code == 2


def test_conflicts_are_never_merged_and_possible_copies_get_no_action(cli, tmp_path):
    root = fresh(cli, tmp_path)
    touch(root, "shared/plan.md", "original\n")
    copy = touch(root, "shared/plan (Alex's conflicted copy 2026-10-24).md", "copy\n")
    data = run(cli, root, "--fix-safe")
    aid = by_id(data, "conflict-copy")[0]["actions"][0]["id"]
    run(cli, root, "--fix-safe", "--yes", aid)
    assert not copy.exists() and (root / "shared/plan.md").read_text(encoding="utf-8") == "original\n"


def test_fix_safe_releases_stale_lease_and_renders_missing_template(cli, tmp_path):
    root = fresh(cli, tmp_path)
    lease = {"area": "wiki", "holder": {"actor": "a", "host": "h", "session": "s"},
             "acquired_at": "2020-01-01T00:00:00Z", "renewed_at": "2020-01-01T00:00:00Z",
             "expires_at": "2020-01-01T00:30:00Z", "operation": "feed-wiki", "nonce": "n1"}
    touch(root, "_system/locks/wiki.lease.json", json.dumps(lease))
    (root / "AGENTS.md").unlink()
    data = run(cli, root, "--fix-safe")
    ids = {a["kind"]: a["id"] for a in data["actions"]}
    assert set(ids) == {"release-lease", "render-template"}
    data = run(cli, root, "--fix-safe", "--yes", ids["release-lease"], "--yes", ids["render-template"], expect=0)
    assert {r["status"] for r in data["applied"]} == {"applied"}
    assert (root / "AGENTS.md").is_file() and "project-notes" in (root / "AGENTS.md").read_text(encoding="utf-8")
    assert not by_id(data, "stale-lease") and not by_id(data, "contract-missing")


def test_render_template_never_overwrites(cli, tmp_path):
    root = fresh(cli, tmp_path)
    (root / "AGENTS.md").unlink()
    data = run(cli, root, "--fix-safe")
    aid = by_id(data, "contract-missing")[0]["actions"][0]["id"]
    (root / "AGENTS.md").write_text("mine\n", encoding="utf-8")  # appears between scan and confirmation
    from metarepo_engine.root import Project
    cfg = Project(root).config()
    row = core.apply_action(lambda: None, root, cfg, core.Action(aid, "render-template", "AGENTS.md", "x"),
                            dest=None, actor="t")
    assert row["status"] == "applied" and (root / "AGENTS.md").read_text(encoding="utf-8") == "mine\n"


def test_not_a_metarepo_exits_3(cli, tmp_path):
    code, env, _ = cli("doctor", "--root", str(tmp_path), "--json")
    assert code == 3 and env["errors"][0]["code"] == "not_a_metarepo"


def test_exit_status_follows_errors_only(cli, tmp_path):
    root = fresh(cli, tmp_path)
    touch(root, "CLAUDE.md")  # a warning
    assert cli("doctor", "--root", str(root), "--json")[0] == 0
    touch(root, "shared/.env")  # an error
    assert cli("doctor", "--root", str(root), "--json")[0] == 1
