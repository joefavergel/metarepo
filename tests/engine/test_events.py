"""003/R13 events log; concurrent appends (003/T1.6)."""
import json
import subprocess
import sys
import textwrap

from metarepo_engine import events

APPENDER = textwrap.dedent(
    """
    import sys
    from pathlib import Path
    sys.path.insert(0, sys.argv[1])
    from metarepo_engine import events
    root, tag, count = Path(sys.argv[2]), sys.argv[3], int(sys.argv[4])
    for i in range(count):
        events.append_event(root, command="probe", targets=[f"shared/{tag}-{i}-" + "x" * 3000],
                            result="ok", actor=tag)
    """
)


def test_event_record_shape(make_project):
    root = make_project()
    rec = events.append_event(root, command="place", targets=["10_sources/a.pdf"], result="ok", actor="member-a",
                              extra={"size": 3})
    assert set(rec) == {"ts", "actor", "host", "command", "targets", "result", "size"}
    assert rec["ts"].endswith("Z") and len(rec["ts"]) == 20
    (read,) = events.read_events(root)
    assert read == rec
    raw = (root / "_system" / "events.log").read_text(encoding="utf-8")
    assert raw.endswith("\n") and raw.count("\n") == 1  # exactly one line


def test_unicode_targets_and_actor_are_kept(make_project):
    root = make_project()
    events.append_event(root, command="x", targets=["shared/café-中文.md"], result="ok", actor="é")
    assert events.read_events(root)[0]["targets"] == ["shared/café-中文.md"]


def test_log_is_append_only_across_calls(make_project):
    root = make_project()
    for i in range(5):
        events.append_event(root, command="c", targets=[str(i)], result="ok", actor="a")
    assert [e["targets"] for e in events.read_events(root)] == [[str(i)] for i in range(5)]


def test_concurrent_appends_from_two_processes_yield_valid_lines(make_project, repo):  # T1.6
    root = make_project()
    count = 150
    engine = str(repo / "tooling" / "engine")
    procs = [subprocess.Popen([sys.executable, "-c", APPENDER, engine, str(root), tag, str(count)])
             for tag in ("p1", "p2")]
    assert [p.wait(timeout=120) for p in procs] == [0, 0]
    lines = (root / "_system" / "events.log").read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2 * count
    parsed = [json.loads(line) for line in lines]  # every line is intact JSON
    for tag in ("p1", "p2"):
        mine = [e for e in parsed if e["actor"] == tag]
        assert len(mine) == count
        assert [int(e["targets"][0].split("-")[1]) for e in mine] == list(range(count))  # per-writer order kept


def test_actor_resolution(monkeypatch):
    monkeypatch.setenv("METAREPO_ACTOR", "from-env")
    assert events.resolve_actor("explicit") == "explicit"
    assert events.resolve_actor(None) == "from-env"
    monkeypatch.delenv("METAREPO_ACTOR")
    assert events.resolve_actor(None)  # falls back to the OS user name or "unknown"
