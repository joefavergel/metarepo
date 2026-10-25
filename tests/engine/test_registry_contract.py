"""Registry-wide contract (003/AC1): every command has --help, --json, and --dry-run when mutating."""
import subprocess
import sys

import pytest

from metarepo_engine.commands import MODULES, load_commands
from metarepo_engine.dispatch import build_parser

COMMANDS = load_commands()
IDS = [c.name for c in COMMANDS]

# 003 spec section 3 command table (names as users type them).
SPEC_COMMANDS = {
    "status", "config validate", "config get", "init", "render-templates", "hash", "id", "inbox scan",
    "unbundle", "place", "extract", "extract-record", "extractors", "registry", "search", "wiki scaffold",
    "wiki gate", "wiki source-page", "lint", "sign", "lease acquire", "lease renew", "lease release",
    "lease status", "doctor", "remove", "repos list", "repos path", "repos clone", "repos status", "migrate",
    "install-skills", "output register",
}


def test_registry_has_every_spec_command_exactly_once():
    assert set(IDS) == SPEC_COMMANDS
    assert len(IDS) == len(set(IDS))
    assert len(MODULES) == len(set(MODULES)) and list(MODULES) == sorted(MODULES)


@pytest.mark.parametrize("cmd", COMMANDS, ids=IDS)
def test_help_json_and_dry_run_flags(cmd, capsys):
    argv = cmd.name.split(" ") + ["--help"]
    with pytest.raises(SystemExit) as exc:
        build_parser(COMMANDS).parse_args(argv)
    assert exc.value.code == 0
    help_text = capsys.readouterr().out
    assert "--json" in help_text and "--root" in help_text
    assert ("--dry-run" in help_text) == cmd.mutating


@pytest.mark.parametrize("cmd", COMMANDS, ids=IDS)
def test_json_envelope_for_every_command(cmd, cli, make_project, tmp_path, monkeypatch):
    root = make_project()
    monkeypatch.chdir(tmp_path)
    code, env, _ = cli(*cmd.name.split(" "), "--json", "--root", str(root))
    assert isinstance(env, dict), "stdout must be one JSON object"
    assert set(env) == {"ok", "command", "version", "data", "warnings", "errors"}
    assert env["ok"] == (code == 0)
    assert code in range(0, 6)
    if not cmd.implemented:
        assert code == 2
        assert env["errors"][0]["code"] == "not_implemented"


def test_stubs_are_marked_with_owner():
    for cmd in COMMANDS:
        if not cmd.implemented:
            assert cmd.owner in cmd.help


def test_cli_py_help_runs_as_a_script(repo):
    out = subprocess.run([sys.executable, str(repo / "tooling" / "engine" / "cli.py"), "--help"],
                         capture_output=True, text=True)
    assert out.returncode == 0
    assert "install-skills" in out.stdout


def test_cli_py_has_no_logic(repo):
    lines = [l for l in (repo / "tooling/engine/cli.py").read_text(encoding="utf-8").splitlines()
             if l.strip() and not l.startswith("#")]
    assert len(lines) <= 12
    assert not any(l.lstrip().startswith(("def ", "class ")) for l in lines)


def test_envelope_is_single_json_object(cli, make_project):
    stubs = [c for c in COMMANDS if not c.implemented]
    if not stubs:
        pytest.skip("every command is implemented")
    root = make_project()
    code, out, err = cli(*stubs[0].name.split(" "), "--json", "--root", str(root))
    assert code == 2 and isinstance(out, dict)
    assert "not implemented" in err
