"""Leases (004/R1-R8, AC1-AC3, AC5)."""
from __future__ import annotations

import ast
import json
import random
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from metarepo_engine import leases
from metarepo_engine.events import read_events
from metarepo_engine.jsonout import EngineError

GOV = {"wiki_write_mode": "lease", "wiki_owners": [], "lease_ttl_minutes": 30, "lease_settle_seconds": 0}


def acquire(root, who="member-a", area="wiki", session=None, **kw):
    kw.setdefault("gov", GOV)
    return leases.acquire(root, area, who, "test", session=session or who, host="host-1", **kw)


@pytest.fixture
def root(make_project):
    return make_project()


# ----------------------------------------------------------------- basic life cycle
def test_acquire_writes_the_r2_file(root):
    got = acquire(root)
    data = json.loads((root / "_system/locks/wiki.lease.json").read_text(encoding="utf-8"))
    assert set(data) == {"area", "holder", "acquired_at", "renewed_at", "expires_at", "operation", "nonce"}
    assert data["holder"] == {"actor": "member-a", "host": "host-1", "session": "member-a"}
    assert data["nonce"] == got.lease.nonce and got.took_over is None


def test_second_holder_is_refused_with_details(root):
    acquire(root)
    with pytest.raises(leases.LeaseHeld) as exc:
        acquire(root, "member-b")
    assert exc.value.exit_code == 3 and "member-a" in exc.value.message and "test" in exc.value.message
    assert "expires" in exc.value.message


def test_same_session_acquire_is_idempotent(root):
    first = acquire(root)
    again = acquire(root)
    assert again.lease.nonce == first.lease.nonce


def test_release_keeps_and_reuses_the_file(root):
    first = acquire(root)
    released = leases.release(root, first.lease)
    assert released.released_at
    path = root / "_system/locks/wiki.lease.json"
    assert path.is_file() and json.loads(path.read_text())["released_at"]
    assert leases.status(root, "wiki")["state"] == "released"
    second = acquire(root, "member-b")
    assert second.lease.nonce != first.lease.nonce and second.took_over is None
    assert "released_at" not in json.loads(path.read_text())


def test_release_twice_is_a_noop(root):
    got = acquire(root)
    leases.release(root, got.lease)
    leases.release(root, got.lease)


def test_release_by_a_stranger_does_not_clobber(root):
    got = acquire(root)
    stranger = leases.Lease(**{**got.lease.__dict__, "nonce": "0" * 32})
    with pytest.raises(leases.LeaseLost):
        leases.release(root, stranger)
    assert leases.verify(root, got.lease)


def test_events_log_acquire_and_release(root):
    got = acquire(root)
    leases.release(root, got.lease)
    cmds = [e["command"] for e in read_events(root)]
    assert cmds == ["lease acquire", "lease release"]
    assert read_events(root)[0]["targets"] == ["_system/locks/wiki.lease.json"]


def test_unknown_area_and_corrupt_file(root):
    with pytest.raises(EngineError):
        acquire(root, area="nope")
    (root / "_system/locks/wiki.lease.json").write_text("{not json", encoding="utf-8")
    with pytest.raises(EngineError) as exc:
        acquire(root)
    assert exc.value.code == "lease_corrupt" and exc.value.exit_code == 4


# ----------------------------------------------------------------- AC2: expiry and takeover
def test_ac2_expired_lease_is_taken_over_and_logged(root):
    t0 = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)
    clock = {"now": t0}
    env = leases.LeaseEnv(clock=lambda: clock["now"], sleep=lambda s: None)
    first = acquire(root, env=env, ttl=30)
    clock["now"] = t0 + timedelta(minutes=29)
    with pytest.raises(leases.LeaseHeld):
        acquire(root, "member-b", env=env)
    clock["now"] = t0 + timedelta(minutes=31)
    assert not leases.verify(root, first.lease, env=env)
    second = acquire(root, "member-b", env=env)
    assert second.took_over["holder"]["actor"] == "member-a"
    assert second.took_over["operation"] == "test"
    takeovers = [e for e in read_events(root) if e["command"] == "lease takeover"]
    assert len(takeovers) == 1 and takeovers[0]["previous"]["holder"]["actor"] == "member-a"
    with pytest.raises(leases.LeaseLost):  # the old holder finds out
        leases.require_lease(root, "wiki", first.lease, env=env)


def test_renew_extends_and_verifies_nonce(root):
    t0 = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)
    clock = {"now": t0}
    env = leases.LeaseEnv(clock=lambda: clock["now"], sleep=lambda s: None)
    got = acquire(root, env=env, ttl=30)
    assert not leases.needs_renewal(got.lease, t0 + timedelta(minutes=14))
    assert leases.needs_renewal(got.lease, t0 + timedelta(minutes=15))
    clock["now"] = t0 + timedelta(minutes=20)
    renewed = leases.renew(root, got.lease, env=env)
    assert leases.parse_ts(renewed.expires_at) == t0 + timedelta(minutes=50)
    clock["now"] = t0 + timedelta(minutes=55)
    with pytest.raises(leases.LeaseLost):  # expired: must acquire again
        leases.renew(root, renewed, env=env)


def test_handle_ensure_renews_at_half_life(root):
    t0 = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)
    clock = {"now": t0}
    env = leases.LeaseEnv(clock=lambda: clock["now"], sleep=lambda s: None)
    handle = leases.acquire_handle(root, "wiki", "member-a", "test", env=env, gov=GOV, session="s", host="h")
    clock["now"] = t0 + timedelta(minutes=16)
    lease = handle.ensure()
    assert lease.renewed_at == leases.fmt_ts(clock["now"])


# ----------------------------------------------------------------- settle uses configuration
def test_settle_seconds_come_from_governance_and_env(root, monkeypatch):
    waited = []
    env = leases.LeaseEnv(sleep=waited.append)
    monkeypatch.delenv(leases.SETTLE_ENV)
    leases.acquire(root, "wiki", "a", "t", gov={**GOV, "lease_settle_seconds": 7}, env=env, session="a")
    assert waited == [7.0]
    monkeypatch.setenv(leases.SETTLE_ENV, "2")
    waited.clear()
    leases.acquire(root, "intake", "a", "t", gov={**GOV, "lease_settle_seconds": 7}, env=env, session="a")
    assert waited == [2.0]


def test_ttl_comes_from_governance(root):
    got = acquire(root, gov={**GOV, "lease_ttl_minutes": 5})
    span = leases.parse_ts(got.lease.expires_at) - leases.parse_ts(got.lease.acquired_at)
    assert span == timedelta(minutes=5)


def test_defaults_read_from_project_yaml(root):
    assert leases.governance(root)["lease_settle_seconds"] == 10
    assert leases.governance(root)["lease_ttl_minutes"] == 30


# ----------------------------------------------------------------- AC3: owner mode
def test_ac3_owner_mode_blocks_non_owners_only_for_wiki(root):
    gov = {**GOV, "wiki_write_mode": "owner", "wiki_owners": ["member-a"]}
    with pytest.raises(EngineError) as exc:
        acquire(root, "member-b", gov=gov)
    assert exc.value.code == "not_wiki_owner" and exc.value.exit_code == 3
    assert "member-a" in exc.value.message and "personal/" in exc.value.message
    assert acquire(root, "member-a", gov=gov).lease.area == "wiki"
    assert acquire(root, "member-b", area="intake", gov=gov).lease.area == "intake"  # R7 only concerns wiki
    assert not (root / "_system/locks/wiki.lease.json").read_text().count("member-b")


# ----------------------------------------------------------------- AC1: contention with delayed visibility
class Cloud:
    """Shared drive double: my own writes are visible at once, other members' after ``delay`` seconds;
    the drive's order is the order of write times (last writer wins)."""

    def __init__(self, delay):
        self.delay = delay
        self.writes: list[tuple[float, str, str, bytes]] = []
        self.lock = threading.Lock()

    def member(self, name):
        cloud = self

        class Store:
            def read(self, path):
                now = time.monotonic()
                with cloud.lock:
                    seen = [w for w in cloud.writes
                            if w[2] == str(path) and (w[1] == name or w[0] + cloud.delay <= now)]
                return max(seen, key=lambda w: w[0])[3] if seen else None

            def write(self, path, data):
                with cloud.lock:
                    cloud.writes.append((time.monotonic(), name, str(path), data))

        return Store()


def _contend(root, names, offsets, delay=0.15, settle=0.5):
    cloud = Cloud(delay)
    results = {}

    def work(name, offset):
        time.sleep(offset)
        env = leases.LeaseEnv(store=cloud.member(name))
        try:
            leases.acquire(root, "wiki", name, "feed", session=name, host="h", gov=GOV, settle=settle, env=env,
                           log=False)
            results[name] = "won"
        except leases.LeaseHeld:
            results[name] = "backed-off"

    threads = [threading.Thread(target=work, args=(n, o)) for n, o in zip(names, offsets)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    return results


def test_ac1_two_contenders_inside_the_latency_window_exactly_one_wins(root):
    results = _contend(root, ["a", "b"], [0.0, 0.05])
    assert sorted(results.values()) == ["backed-off", "won"]


def test_ac1_property_n_contenders_at_most_one_holder(root):
    rng = random.Random(4)
    for _ in range(4):
        names = [f"m{i}" for i in range(5)]
        results = _contend(root, names, [rng.uniform(0, 0.06) for _ in names])
        assert list(results.values()).count("won") == 1


def test_ac1_two_real_processes_exactly_one_wins(root, monkeypatch):
    """Two OS processes through the real CLI and the real file system."""
    import os
    import subprocess
    import sys

    cli_py = Path(__file__).resolve().parents[2] / "tooling" / "engine" / "cli.py"
    env = {**os.environ, "METAREPO_SETTLE_SECONDS": "1"}
    procs = [subprocess.Popen([sys.executable, str(cli_py), "lease", "acquire", "wiki", "--json", "--root",
                               str(root), "--actor", name, "--session", name],
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env) for name in ("a", "b")]
    codes = sorted(p.wait(timeout=60) for p in procs)
    assert codes == [0, 3]


# ----------------------------------------------------------------- require_lease / hot files
def test_require_lease_variants(root):
    got = acquire(root)
    assert leases.require_lease(root, "wiki", got.lease).nonce == got.lease.nonce
    assert leases.require_lease(root, "wiki", got.lease.nonce)
    with pytest.raises(EngineError) as exc:
        leases.require_lease(root, "wiki", None)
    assert exc.value.code == "lease_required" and exc.value.exit_code == 3
    with pytest.raises(leases.LeaseLost):
        leases.require_lease(root, "wiki", "deadbeef")
    with pytest.raises(leases.LeaseLost):
        leases.require_lease(root, "intake", got.lease)  # a wiki lease does not cover intake


def test_area_for_paths():
    assert leases.area_for("30_wiki/pages/x.md") == "wiki"
    assert leases.area_for("20_extracts/a.md") == "intake"
    assert leases.area_for("10_sources/a.pdf") == "intake"
    assert leases.area_for("00_inbox/a.pdf") == "intake"
    assert leases.area_for("project.yaml") == "config"
    assert leases.area_for("AGENTS.md") == "config"
    assert leases.area_for("outputs/x.md") is None and leases.area_for("shared/x") is None


def test_update_hot_file_requires_a_covering_lease(root):
    (root / "30_wiki" / "index.md").write_text("old\n", encoding="utf-8")
    wiki = acquire(root)
    leases.update_hot_file(root, wiki.lease, "30_wiki/index.md", lambda b: (b or b"") + b"new\n")
    assert (root / "30_wiki/index.md").read_text() == "old\nnew\n"
    with pytest.raises(leases.LeaseLost):  # wiki lease does not cover project.yaml
        leases.update_hot_file(root, wiki.lease, "project.yaml", lambda b: b)
    with pytest.raises(EngineError) as exc:
        leases.update_hot_file(root, wiki.lease, "30_wiki/page.md", lambda b: b)
    assert exc.value.code == "not_a_hot_file"
    leases.release(root, wiki.lease)
    with pytest.raises(leases.LeaseLost):
        leases.update_hot_file(root, wiki.lease, "30_wiki/index.md", lambda b: b"x")
    assert (root / "30_wiki/index.md").read_text() == "old\nnew\n"


def test_held_releases_on_error(root):
    with pytest.raises(RuntimeError):
        with leases.held(root, "wiki", "a", "t", gov=GOV, session="a"):
            raise RuntimeError("boom")
    assert leases.status(root, "wiki")["state"] == "released"


# ----------------------------------------------------------------- AC5: static test
HOT_TOKENS = ("config_path", "state_path", "project.yaml", "state.json", "index.md", "log.md", "STATE_REL",
              "PROJECT_FILE")
WRITERS = {"atomic_write_text", "atomic_write_bytes", "create_new_bytes", "write_if_absent", "write_text",
           "write_bytes", "replace"}
GUARDS = ("require_lease", ".ensure(", "reverify(", "update_hot_file", "leases.held", "_write_all")
# Creating a project does not need a lease: nobody else can hold it yet (005 scaffolds, 002 scaffold.py).
EXEMPT = {"scaffold.py", "leases.py"}
PACKAGE = Path(__file__).resolve().parents[2] / "tooling" / "engine" / "metarepo_engine"


def _writers_of_hot_files():
    for path in sorted(PACKAGE.rglob("*.py")):
        if path.name in EXEMPT or "__pycache__" in path.parts:
            continue
        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source)
        for fn in [n for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]:
            for call in [n for n in ast.walk(fn) if isinstance(n, ast.Call)]:
                name = call.func.attr if isinstance(call.func, ast.Attribute) else getattr(call.func, "id", "")
                if name not in WRITERS:
                    continue
                target = ast.get_source_segment(source, call) or ""
                if any(tok in target for tok in HOT_TOKENS):
                    yield path.name, fn.name, ast.get_source_segment(source, fn) or ""


def test_ac5_every_hot_file_writer_requires_a_verified_lease():
    seen = list(_writers_of_hot_files())
    assert seen, "the scan found no hot-file writers at all; the static test is broken"
    unguarded = [(m, f) for m, f, body in seen if not any(g in body for g in GUARDS)]
    assert unguarded == [], f"hot-file writers without a lease check: {unguarded}"


def test_ac5_the_scan_catches_an_unguarded_writer(tmp_path, monkeypatch):
    bad = tmp_path / "bad.py"
    bad.write_text("def f(project):\n    atomic_write_text(project.state_path, 'x')\n", encoding="utf-8")
    import sys

    monkeypatch.setattr(sys.modules[__name__], "PACKAGE", tmp_path)
    assert [(m, f) for m, f, _ in _writers_of_hot_files()] == [("bad.py", "f")]


def test_hot_files_are_the_four_of_r8():
    assert set(leases.HOT_FILES) == {"30_wiki/index.md", "30_wiki/log.md", "_system/state.json", "project.yaml"}
