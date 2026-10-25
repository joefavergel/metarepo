"""``lease *`` commands and the writers that take leases."""
from __future__ import annotations

import pytest

from metarepo_engine.events import read_events


def run(cli, root, *argv, actor="member-a"):
    return cli(*argv, "--json", "--root", str(root), "--actor", actor) if "--actor" not in argv else cli(
        *argv, "--json", "--root", str(root))


@pytest.fixture
def root(make_project):
    return make_project()


def test_acquire_status_renew_release_cycle(cli, root):
    code, env, _ = cli("lease", "acquire", "wiki", "--operation", "feed", "--session", "s1", "--json",
                       "--root", str(root), "--actor", "member-a")
    assert code == 0, env
    nonce = env["data"]["lease"]["nonce"]
    assert env["data"]["took_over"] is None

    code, env, _ = cli("lease", "status", "--json", "--root", str(root))
    rows = {r["area"]: r for r in env["data"]["leases"]}
    assert rows["wiki"]["state"] == "held" and rows["intake"]["state"] == "free"
    assert "nonce" not in rows["wiki"]["lease"] and nonce not in str(env)

    code, env, _ = cli("lease", "acquire", "wiki", "--session", "s2", "--json", "--root", str(root),
                       "--actor", "member-b")
    assert code == 3 and env["errors"][0]["code"] == "lease_held" and "member-a" in env["errors"][0]["message"]

    code, env, _ = cli("lease", "renew", "wiki", "--lease", nonce, "--json", "--root", str(root))
    assert code == 0
    code, env, _ = cli("lease", "renew", "wiki", "--lease", "wrong", "--json", "--root", str(root))
    assert code == 3 and env["errors"][0]["code"] == "lease_lost"

    code, env, _ = cli("lease", "release", "wiki", "--lease", nonce, "--json", "--root", str(root))
    assert code == 0 and env["data"]["released"]
    code, env, _ = cli("lease", "release", "wiki", "--lease", nonce, "--json", "--root", str(root))
    assert code == 0 and env["data"]["already"]
    code, env, _ = cli("lease", "status", "wiki", "--json", "--root", str(root))
    assert env["data"]["leases"][0]["state"] == "released"
    cmds = [e["command"] for e in read_events(root)]
    assert cmds == ["lease acquire", "lease release"]  # one event each, no duplicates from the dispatcher


def test_acquire_dry_run_changes_nothing(cli, root):
    code, env, _ = cli("lease", "acquire", "wiki", "--dry-run", "--json", "--root", str(root))
    assert code == 0 and env["data"]["would_acquire"]
    assert not (root / "_system/locks/wiki.lease.json").exists() and read_events(root) == []


def test_owner_mode_through_the_cli(cli, make_project):
    root = make_project(governance={"wiki_write_mode": "owner", "wiki_owners": ["member-a"]})
    code, env, _ = cli("lease", "acquire", "wiki", "--json", "--root", str(root), "--actor", "member-b")
    assert code == 3 and env["errors"][0]["code"] == "not_wiki_owner"
    code, env, _ = cli("lease", "acquire", "wiki", "--json", "--root", str(root), "--actor", "member-a")
    assert code == 0


def test_render_templates_respects_a_foreign_config_lease(cli, root):
    cli("render-templates", "--json", "--root", str(root))
    yml = root / "project.yaml"
    yml.write_text(yml.read_text(encoding="utf-8").replace("Example objective.", "Changed objective."),
                   encoding="utf-8")
    code, env, _ = cli("lease", "acquire", "config", "--session", "other", "--json", "--root", str(root),
                       "--actor", "member-b")
    assert code == 0
    nonce = env["data"]["lease"]["nonce"]
    before = (root / "AGENTS.md").read_text(encoding="utf-8")
    code, env, _ = cli("render-templates", "--json", "--root", str(root))
    assert code == 3 and env["errors"][0]["code"] == "lease_held"
    assert (root / "AGENTS.md").read_text(encoding="utf-8") == before
    code, env, _ = cli("render-templates", "--lease", nonce, "--json", "--root", str(root))
    assert code == 0 and "AGENTS.md" in env["data"]["changed"]
    code, env, _ = cli("lease", "status", "config", "--json", "--root", str(root))
    assert env["data"]["leases"][0]["state"] == "held"  # a lease passed in is not released by the writer
