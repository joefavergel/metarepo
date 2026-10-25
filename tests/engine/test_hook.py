"""001/R9 PreToolUse hook (001/T3.5): decisions with fixture payloads."""
import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

HOOK = Path(__file__).resolve().parents[2] / "plugin" / "hooks" / "guard_paths.py"
spec = importlib.util.spec_from_file_location("guard_paths", HOOK)
guard = importlib.util.module_from_spec(spec)
spec.loader.exec_module(guard)


def payload(path, tool="Write", cwd=None, key="file_path"):
    out = {"hook_event_name": "PreToolUse", "tool_name": tool, "tool_input": {key: str(path), "content": "x"}}
    if cwd is not None:
        out["cwd"] = str(cwd)
    return out


@pytest.mark.parametrize("folder", ["10_sources", "20_extracts", "_system"])
def test_denies_protected_folders_inside_a_metarepo(make_project, folder):
    root = make_project()
    reason = guard.decide(payload(root / folder / "sub" / "file.md"))
    assert reason and folder in reason and "engine" in reason


@pytest.mark.parametrize("tool,key", [("Write", "file_path"), ("Edit", "file_path"), ("MultiEdit", "file_path"),
                                      ("NotebookEdit", "notebook_path")])
def test_every_file_writing_tool_shape_is_covered(make_project, tool, key):
    root = make_project()
    assert guard.decide(payload(root / "10_sources" / "a.pdf", tool=tool, key=key))


def test_allows_everything_else_inside_a_metarepo(make_project):
    root = make_project()
    for rel in ("00_inbox/a.pdf", "30_wiki/page.md", "outputs/x.md", "shared/tools/a.py", "personal/me/n.md",
                "AGENTS.md", "project.yaml", "10_sources_notes.md", "my_system/x"):
        assert guard.decide(payload(root / rel)) is None, rel


def test_noop_outside_a_metarepo(tmp_path):
    (tmp_path / "10_sources").mkdir()
    assert guard.decide(payload(tmp_path / "10_sources" / "a.pdf")) is None
    assert guard.decide(payload(tmp_path / "10_sources" / "deep" / "new" / "a.pdf")) is None


def test_relative_path_resolved_against_cwd(make_project):
    root = make_project()
    assert guard.decide(payload("10_sources/a.pdf", cwd=root))
    assert guard.decide(payload("../20_extracts/a.md", cwd=root / "outputs"))
    assert guard.decide(payload("30_wiki/a.md", cwd=root)) is None
    assert guard.decide(payload("../10_sources/a.md", cwd=root / "outputs"))


def test_nested_project_uses_nearest_root(make_project, tmp_path):
    from helpers import make_project as build

    outer = make_project()
    inner = build(outer / "shared" / "inner")
    assert guard.decide(payload(inner / "10_sources" / "a.pdf"))
    assert guard.decide(payload(inner / "outputs" / "a.md")) is None


def test_case_variants_are_denied(make_project):
    root = make_project()
    assert guard.decide(payload(root / "10_SOURCES" / "a.pdf"))


def test_symlink_into_protected_folder_is_denied(make_project, tmp_path):
    root = make_project()
    link = root / "shared" / "alias"
    try:
        link.symlink_to(root / "10_sources", target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks unavailable")
    assert guard.decide(payload(link / "a.pdf"))


def test_windows_style_paths_with_an_injected_root():
    root_of = lambda target: "C:/Users/me/Shared Drive/proj"  # noqa: E731
    ident = lambda p: p  # noqa: E731
    deny = guard.decide(payload(r"C:\Users\me\Shared Drive\proj\10_sources\a.pdf"), resolve=ident, root_of=root_of)
    assert deny and "10_sources" in deny
    deny = guard.decide(payload(r"c:\users\ME\shared drive\proj\_system\state.json"), resolve=ident, root_of=root_of)
    assert deny and "_system" in deny
    assert guard.decide(payload(r"C:\Users\me\Shared Drive\proj\30_wiki\a.md"), resolve=ident, root_of=root_of) is None
    assert guard.decide(payload(r"C:\Users\me\Shared Drive\other\10_sources\a.md"), resolve=ident, root_of=root_of) is None
    rel = payload(r"10_sources\a.pdf", cwd=r"C:\Users\me\Shared Drive\proj")
    assert guard.decide(rel, resolve=ident, root_of=root_of)


def test_split_parts():
    assert guard.split_parts(r"C:\a\b/c//d") == ["C:", "a", "b", "c", "d"]
    assert guard.split_parts("/a/b/") == ["a", "b"]


@pytest.mark.parametrize("bad", [{}, {"tool_input": None}, {"tool_input": {"file_path": ""}},
                                 {"tool_input": {"command": "ls"}}, {"tool_input": {"file_path": 3}}])
def test_malformed_payloads_are_ignored(bad):
    assert guard.decide(bad) is None


def _run_hook(stdin_text):
    return subprocess.run([sys.executable, str(HOOK)], input=stdin_text, capture_output=True, text=True)


def test_script_emits_the_documented_deny_json(make_project):
    root = make_project()
    out = _run_hook(json.dumps(payload(root / "20_extracts" / "a.md")))
    assert out.returncode == 0
    decision = json.loads(out.stdout)["hookSpecificOutput"]
    assert decision["hookEventName"] == "PreToolUse" and decision["permissionDecision"] == "deny"
    assert "20_extracts" in decision["permissionDecisionReason"]


def test_script_is_silent_when_allowing_and_fails_open(make_project, tmp_path):
    root = make_project()
    allow = _run_hook(json.dumps(payload(root / "outputs" / "a.md")))
    assert allow.returncode == 0 and allow.stdout == ""
    for garbage in ("", "not json", "[]", "null", "42"):
        res = _run_hook(garbage)
        assert res.returncode == 0 and res.stdout == "", garbage


def test_hooks_json_registers_the_guard(repo):
    cfg = json.loads((repo / "plugin" / "hooks" / "hooks.json").read_text(encoding="utf-8"))
    (entry,) = cfg["hooks"]["PreToolUse"]
    assert set(entry["matcher"].split("|")) == {"Write", "Edit", "MultiEdit", "NotebookEdit"}
    assert "guard_paths.py" in entry["hooks"][0]["command"] and entry["hooks"][0]["type"] == "command"
