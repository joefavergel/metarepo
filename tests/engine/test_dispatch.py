"""Dispatcher behaviour: envelope, exit codes, UTF-8, compatibility gate, dependency check, event log."""
import json
import subprocess
import sys


from metarepo_engine import dispatch
from metarepo_engine.command import Command
from metarepo_engine.events import read_events
from metarepo_engine.jsonout import EngineError, Result


def _read_cmd(name="probe read", **kw):
    return Command(name=name, help="read probe", run=lambda ctx, a: Result(data={"root": str(ctx.root)}), **kw)


def _write_cmd(name="probe write", **kw):
    def run(ctx, args):
        return Result(data={"dry_run": ctx.dry_run}, targets=["shared/x.txt"])

    return Command(name=name, help="write probe", run=run, mutating=True, writes_project=True, **kw)


def test_envelope_shape_and_success(cli, make_project):
    root = make_project()
    code, env, _ = cli("probe", "read", "--json", "--root", str(root), commands=[_read_cmd()])
    assert code == 0
    assert list(env) == ["ok", "command", "version", "data", "warnings", "errors"]
    assert env["ok"] is True and env["command"] == "probe read" and env["errors"] == []
    assert env["data"]["root"] == str(root.resolve())


def test_global_flags_work_before_and_after_the_subcommand(cli, make_project):
    root = make_project()
    cmds = [_read_cmd()]
    a = cli("--json", "--root", str(root), "probe", "read", commands=cmds)
    b = cli("probe", "read", "--root", str(root), "--json", commands=cmds)
    assert a[0] == b[0] == 0
    assert a[1]["data"] == b[1]["data"]


def test_exit_code_usage_on_unknown_command_json(cli):
    code, env, err = cli("nope", "--json", commands=[_read_cmd()])
    assert code == 2 and env["ok"] is False and env["errors"][0]["code"] == "usage"
    assert "usage:" in err


def test_exit_code_precondition_when_not_a_metarepo(cli, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    code, env, _ = cli("probe", "read", "--json", commands=[_read_cmd()])
    assert code == 3 and env["errors"][0]["code"] == "not_a_metarepo"


def test_root_discovered_by_walking_up(cli, make_project, monkeypatch):
    root = make_project()
    deep = root / "10_sources" / "a" / "b"
    deep.mkdir(parents=True)
    monkeypatch.chdir(deep)
    code, env, _ = cli("probe", "read", "--json", commands=[_read_cmd()])
    assert code == 0 and env["data"]["root"] == str(root.resolve())


def test_blocking_findings_exit_1(cli, make_project):
    cmd = Command(name="probe block", help="x", run=lambda c, a: Result(blocking=True, data={"n": 1}))
    root = make_project()
    code, env, _ = cli("probe", "block", "--json", "--root", str(root), commands=[cmd])
    assert code == 1 and env["ok"] is False


def test_engine_error_maps_to_exit_code(cli, make_project):
    def run(ctx, args):
        raise EngineError("custom", "boom", 4, "a/b.txt")

    cmd = Command(name="probe err", help="x", run=run)
    root = make_project()
    code, env, _ = cli("probe", "err", "--json", "--root", str(root), commands=[cmd])
    assert code == 4 and env["errors"] == [{"code": "custom", "message": "boom", "path": "a/b.txt"}]


def test_unexpected_exception_still_yields_an_envelope(cli, make_project):
    def run(ctx, args):
        raise RuntimeError("bug")

    cmd = Command(name="probe bug", help="x", run=run)
    root = make_project()
    code, env, _ = cli("probe", "bug", "--json", "--root", str(root), commands=[cmd])
    assert code == 2 and env["errors"][0]["code"] == "internal_error"


# ---- compatibility gate (001/R7, 001/AC4)
def test_write_refused_outside_range_with_exit_4(cli, make_project, monkeypatch):
    root = make_project(requires=">=0.3,<0.4")
    monkeypatch.setattr(dispatch, "__version__", "0.4.0")
    code, env, _ = cli("probe", "write", "--json", "--root", str(root), commands=[_write_cmd()])
    assert code == 4
    assert env["errors"][0]["code"] == "toolkit_incompatible"
    assert "migrate" in env["errors"][0]["message"]
    assert read_events(root) == []  # refused: nothing logged


def test_read_runs_outside_range_with_warning(cli, make_project, monkeypatch):
    root = make_project(requires=">=0.3,<0.4")
    monkeypatch.setattr(dispatch, "__version__", "0.4.0")
    code, env, err = cli("probe", "read", "--json", "--root", str(root), commands=[_read_cmd()])
    assert code == 0
    assert env["warnings"][0]["code"] == "toolkit_incompatible"
    assert "warning:" in err


def test_older_toolkit_says_update(cli, make_project, monkeypatch):
    root = make_project(requires=">=0.3,<0.4")
    monkeypatch.setattr(dispatch, "__version__", "0.2.0")
    code, env, _ = cli("probe", "write", "--json", "--root", str(root), commands=[_write_cmd()])
    assert code == 4 and "update the toolkit" in env["errors"][0]["message"]


def test_gate_can_be_disabled_per_command(cli, make_project, monkeypatch):
    root = make_project(requires=">=0.3,<0.4")
    monkeypatch.setattr(dispatch, "__version__", "0.4.0")
    code, env, _ = cli("probe", "write", "--json", "--root", str(root), commands=[_write_cmd(gate=False)])
    assert code == 0


def test_invalid_requires_blocks_writes_only(cli, make_project):
    root = make_project(requires="banana")
    w = cli("probe", "write", "--json", "--root", str(root), commands=[_write_cmd()])
    r = cli("probe", "read", "--json", "--root", str(root), commands=[_read_cmd()])
    assert w[0] == 4 and w[1]["errors"][0]["code"] == "config_invalid"
    assert r[0] == 0 and r[1]["warnings"][0]["code"] == "compat_unchecked"


# ---- dry-run and event log (003/R4, R13)
def test_dry_run_flag_and_no_event(cli, make_project):
    root = make_project()
    code, env, _ = cli("probe", "write", "--dry-run", "--json", "--root", str(root), commands=[_write_cmd()])
    assert code == 0 and env["data"]["dry_run"] is True
    assert read_events(root) == []


def test_write_appends_one_event(cli, make_project):
    root = make_project()
    code, _, _ = cli("probe", "write", "--actor", "member-a", "--json", "--root", str(root),
                     commands=[_write_cmd()])
    assert code == 0
    (event,) = read_events(root)
    assert event["command"] == "probe write" and event["actor"] == "member-a"
    assert event["targets"] == ["shared/x.txt"] and event["result"] == "ok"
    assert set(event) >= {"ts", "actor", "host", "command", "targets", "result"}


# ---- dependency detection (003/R16)
def test_missing_pyyaml_only_status_style_commands_run(cli, make_project, monkeypatch):
    root = make_project()
    monkeypatch.setattr(dispatch, "has_yaml", lambda: False)
    needs = cli("probe", "read", "--json", "--root", str(root), commands=[_read_cmd()])
    free = cli("probe", "free", "--json", commands=[_read_cmd("probe free", needs_yaml=False, needs_project=False)])
    assert needs[0] == 5 and needs[1]["errors"][0]["code"] == "missing_dependency"
    assert "pip install pyyaml" in needs[1]["errors"][0]["message"]
    assert free[0] == 0


# ---- UTF-8 (003/R5)
def test_utf8_output_regardless_of_locale(repo, tmp_path):
    import os

    env = {**os.environ, "LC_ALL": "C", "LANG": "C", "PYTHONIOENCODING": "ascii", "PYTHONUTF8": "0"}
    script = (
        "import sys; sys.path.insert(0, %r);"
        "from metarepo_engine import dispatch; from metarepo_engine.command import Command;"
        "from metarepo_engine.jsonout import Result;"
        "c = Command(name='u', help='x', run=lambda ctx, a: Result(data={'t': 'caf\\u00e9 \\u4e2d\\u6587'}),"
        " needs_project=False, needs_yaml=False);"
        "sys.exit(dispatch.main(['u', '--json'], commands=[c]))" % str(repo / "tooling" / "engine")
    )
    out = subprocess.run([sys.executable, "-c", script], capture_output=True, env=env)
    assert out.returncode == 0
    assert json.loads(out.stdout.decode("utf-8"))["data"]["t"] == "café 中文"
