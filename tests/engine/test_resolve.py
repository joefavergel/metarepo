"""001/R5 engine resolution and interpreter discovery (001/T3.1)."""
from pathlib import Path

from metarepo_engine import resolve


def _skill(tmp_path, recorded=None):
    skill = tmp_path / "skills" / "demo"
    (skill / "references").mkdir(parents=True)
    (skill / "SKILL.md").write_text("---\nname: demo\ndescription: d\n---\n", encoding="utf-8")
    body = f"{resolve.MARKER_START}\npath: {recorded or resolve.UNRESOLVED}\n{resolve.MARKER_END}\n"
    (skill / "references" / "engine.md").write_text(body, encoding="utf-8")
    return skill


def _cli(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("# engine\n", encoding="utf-8")
    return path


def test_env_variable_wins_over_everything(tmp_path):
    skill = _skill(tmp_path)
    _cli(tmp_path / "engine" / "cli.py")  # bundled would also match
    env_cli = _cli(tmp_path / "elsewhere" / "cli.py")
    loc = resolve.find_engine(skill, {"METAREPO_ENGINE": str(env_cli)}, which=lambda n: "/usr/bin/metarepo")
    assert loc == resolve.EngineLocation("env", str(env_cli))


def test_env_variable_may_name_an_executable_on_path(tmp_path):
    loc = resolve.find_engine(None, {"METAREPO_ENGINE": "my-engine"}, which=lambda n: "/opt/bin/my-engine")
    assert loc == resolve.EngineLocation("env", "/opt/bin/my-engine")


def test_broken_env_override_is_not_silently_skipped(tmp_path):
    skill = _skill(tmp_path)
    _cli(tmp_path / "engine" / "cli.py")
    assert resolve.find_engine(skill, {"METAREPO_ENGINE": str(tmp_path / "nope.py")}, which=lambda n: None) is None


def test_bundled_engine_by_layout(tmp_path):
    skill = _skill(tmp_path)
    cli = _cli(tmp_path / "engine" / "cli.py")
    loc = resolve.find_engine(skill, {}, which=lambda n: None)
    assert loc == resolve.EngineLocation("bundled", str(cli.resolve()))


def test_bundled_engine_by_recorded_relative_path(tmp_path):
    skill = _skill(tmp_path, recorded="../../engine/cli.py")
    cli = _cli(tmp_path / "engine" / "cli.py")
    assert resolve.recorded_engine_path(skill) == "../../engine/cli.py"
    assert resolve.find_engine(skill, {}, which=lambda n: None).path == str(cli.resolve())


def test_bundled_engine_by_recorded_absolute_path(tmp_path):
    cli = _cli(tmp_path / "somewhere" / "cli.py")
    skill = _skill(tmp_path / "harness", recorded=str(cli))
    assert resolve.find_engine(skill, {}, which=lambda n: None) == resolve.EngineLocation("bundled", str(cli.resolve()))


def test_path_executable_is_last(tmp_path):
    skill = _skill(tmp_path)
    loc = resolve.find_engine(skill, {}, which=lambda n: "/usr/local/bin/metarepo" if n == "metarepo" else None)
    assert loc == resolve.EngineLocation("path", "/usr/local/bin/metarepo")


def test_nothing_found_returns_none_and_instructions_exist(tmp_path):
    assert resolve.find_engine(_skill(tmp_path), {}, which=lambda n: None) is None
    assert "INSTALL.md" in resolve.INSTALL_INSTRUCTIONS and "pyyaml" in resolve.INSTALL_INSTRUCTIONS


def test_command_for():
    py = resolve.command_for(resolve.EngineLocation("bundled", "/x/cli.py"), ["py", "-3"])
    assert py == ["py", "-3", "/x/cli.py"]
    assert resolve.command_for(resolve.EngineLocation("path", "/usr/bin/metarepo")) == ["/usr/bin/metarepo"]


def test_python_discovery_order_and_version_floor():
    installed = {"python3": (3, 9), "python": (3, 12), "py": (3, 11)}
    probe = lambda cmd: installed[cmd[0]]  # noqa: E731
    assert resolve.find_python(which=lambda n: n, probe=probe) == ["python"]
    installed["python3"] = (3, 10)
    assert resolve.find_python(which=lambda n: n, probe=probe) == ["python3"]
    assert resolve.find_python(which=lambda n: "py" if n == "py" else None, probe=probe) == ["py", "-3"]
    assert resolve.find_python(which=lambda n: None, probe=probe) is None
    assert resolve.find_python(which=lambda n: n, probe=lambda c: None) is None


def test_real_python_probe_reports_a_version():
    import sys

    assert resolve._probe_python([sys.executable]) == sys.version_info[:2]
