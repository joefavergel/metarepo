"""Spec 006 at engine level: per-user config (R5), repos list|path|clone|status (R6, AC2, AC3, AC4), actor lookup."""
import os
import subprocess
import sys
from pathlib import Path

import pytest

from metarepo_engine import events, repos, syncinfo, userconfig
from metarepo_engine.commands import init as init_cmd

PID = "7f3c1b2a-5d4e-4f6a-9b8c-0123456789ab"


def git(*args, cwd=None):
    env = dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@e.x", GIT_COMMITTER_NAME="t",
               GIT_COMMITTER_EMAIL="t@e.x")
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, env=env)


@pytest.fixture
def upstream(tmp_path):
    src = tmp_path / "upstream"
    src.mkdir()
    git("init", "-q", "-b", "main", cwd=src)
    (src / "a.txt").write_text("x", encoding="utf-8")
    git("add", ".", cwd=src)
    git("commit", "-q", "-m", "init", cwd=src)
    return src


@pytest.fixture(autouse=True)
def _cfg(tmp_path, monkeypatch):
    monkeypatch.setenv("METAREPO_CONFIG_DIR", str(tmp_path / "cfg"))
    monkeypatch.delenv("METAREPO_ACTOR", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("USERPROFILE", str(tmp_path / "home"))


@pytest.fixture
def proj(make_project, upstream):
    return make_project(repos=[{"name": "app", "url": str(upstream), "default_branch": "main"}],
                        team=[{"id": "member-a"}])


# ---- T2: per-user config path per OS, atomic save/load
def test_config_path_posix_and_windows(monkeypatch, tmp_path):
    monkeypatch.delenv("METAREPO_CONFIG_DIR")
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    assert userconfig.user_config_path("p1") == tmp_path / "xdg" / "metarepo" / "p1.yaml"
    monkeypatch.delenv("XDG_CONFIG_HOME")
    assert userconfig.user_config_path("p1") == Path.home() / ".config" / "metarepo" / "p1.yaml"
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setenv("APPDATA", str(tmp_path / "roam"))
    assert userconfig.user_config_path("p1") == tmp_path / "roam" / "metarepo" / "p1.yaml"
    with pytest.raises(ValueError):
        userconfig.user_config_path("../x")


def test_save_load_roundtrip_keeps_unknown_keys():
    userconfig.save(PID, {"actor": "member-a", "repos": {"app": "/x"}, "future": 1})
    assert userconfig.load(PID) == {"actor": "member-a", "repos": {"app": "/x"}, "future": 1}
    assert userconfig.load("other") == {}


# ---- actor: --actor > METAREPO_ACTOR > per-user config > OS user
def test_actor_precedence(monkeypatch):
    userconfig.save(PID, {"actor": "from-config"})
    assert events.resolve_actor(None, PID) == "from-config"
    assert events.resolve_actor("explicit", PID) == "explicit"
    monkeypatch.setenv("METAREPO_ACTOR", "from-env")
    assert events.resolve_actor(None, PID) == "from-env"
    monkeypatch.delenv("METAREPO_ACTOR")
    assert events.resolve_actor(None, "unknown-project")  # OS user fallback


def test_event_log_uses_config_actor(cli, proj):
    userconfig.save(PID, {"actor": "member-a"})
    code, env, _ = cli("lease", "acquire", "config", "--root", str(proj), "--json")
    assert code == 0
    assert events.read_events(proj)[-1]["actor"] == "member-a"


# ---- config user
def test_config_user_set_show_idempotent(cli, proj):
    code, env, _ = cli("config", "user", "--root", str(proj), "--set", "actor=member-a", "--set", "harness=cowork",
                       "--json")
    assert code == 0 and env["data"]["changed"] and env["data"]["actor_in_team"]
    stored = userconfig.load(PID)
    assert stored["actor"] == "member-a" and stored["metarepo_root"] == str(proj.resolve())
    mtime = userconfig.user_config_path(PID).stat().st_mtime_ns
    code, env, _ = cli("config", "user", "--root", str(proj), "--set", "actor=member-a", "--json")
    assert env["data"]["changed"] is False and userconfig.user_config_path(PID).stat().st_mtime_ns == mtime
    code, env, _ = cli("config", "user", "--root", str(proj), "--set", "actor=new-person", "--dry-run", "--json")
    assert env["data"]["dry_run"] and userconfig.load(PID)["actor"] == "member-a"
    assert any(w["code"] == "actor_not_in_team" for w in env["warnings"])
    code, env, _ = cli("config", "user", "--root", str(proj), "--set", "actor=Bad Id", "--json")
    assert code == 2
    assert not list(proj.rglob("*.yaml.tmp*")) and not (proj / "member-a.yaml").exists()


# ---- AC3
def test_repos_path_ac3(cli, proj, tmp_path):
    code, env, err = cli("repos", "path", "app", "--root", str(proj), "--json")
    assert code == 3 and env["errors"][0]["code"] == "repo_not_recorded" and "repos clone app" in env["errors"][0]["message"]
    code, env, _ = cli("repos", "path", "nope", "--root", str(proj), "--json")
    assert code == 3 and env["errors"][0]["code"] == "repo_unknown"
    clone = tmp_path / "clones" / "app"
    userconfig.save(PID, {"repos": {"app": str(clone)}})
    code, env, _ = cli("repos", "path", "app", "--root", str(proj), "--json")
    assert code == 3 and env["errors"][0]["code"] == "repo_path_missing"
    clone.mkdir(parents=True)
    code, env, _ = cli("repos", "path", "app", "--root", str(proj), "--json")
    assert code == 0 and env["data"]["path"] == str(clone)
    code, out, _ = cli("repos", "path", "app", "--root", str(proj))
    assert out.strip() == str(clone)


def test_repos_list(cli, proj, tmp_path):
    code, env, _ = cli("repos", "list", "--root", str(proj), "--json")
    assert code == 0 and env["data"]["repos"][0]["name"] == "app" and env["data"]["repos"][0]["path"] is None
    userconfig.save(PID, {"repos": {"app": "/somewhere"}})
    code, env, _ = cli("repos", "list", "--root", str(proj), "--json")
    assert env["data"]["repos"][0]["path"] == "/somewhere"


# ---- clone: R6, AC2, AC4
def test_clone_records_and_is_idempotent_ac4(cli, proj, tmp_path):
    dest = tmp_path / "work" / "app"
    code, env, _ = cli("repos", "clone", "app", "--dest", str(dest), "--root", str(proj), "--dry-run", "--json")
    assert code == 0 and env["data"]["action"] == "clone" and not dest.exists() and userconfig.load(PID) == {}
    code, env, _ = cli("repos", "clone", "app", "--dest", str(dest), "--root", str(proj), "--json")
    assert code == 0 and env["data"]["action"] == "clone" and (dest / "a.txt").is_file()
    assert userconfig.load(PID)["repos"]["app"] == str(dest.absolute())
    cfg_file = userconfig.user_config_path(PID)
    before = (cfg_file.read_bytes(), cfg_file.stat().st_mtime_ns)
    code, env, _ = cli("repos", "clone", "app", "--root", str(proj), "--json")  # recorded path is the default
    assert code == 0 and env["data"]["action"] == "adopt"
    assert (cfg_file.read_bytes(), cfg_file.stat().st_mtime_ns) == before
    code, env, _ = cli("repos", "status", "--root", str(proj), "--json")
    row = env["data"]["repos"][0]
    assert code == 0 and row["state"] == "ok" and row["is_git"] and row["remote_matches"] and row["branch"] == "main"
    assert not (proj / "code").exists() or not list((proj / "code").glob("**/.git"))


def test_clone_adopts_existing_clone_and_rejects_foreign(cli, proj, upstream, tmp_path):
    mine = tmp_path / "mine"
    git("clone", "-q", str(upstream), str(mine))
    code, env, _ = cli("repos", "clone", "app", "--dest", str(mine), "--root", str(proj), "--json")
    assert code == 0 and env["data"]["action"] == "adopt"
    other = tmp_path / "other"
    other.mkdir()
    (other / "file").write_text("x", encoding="utf-8")
    code, env, _ = cli("repos", "clone", "app", "--dest", str(other), "--root", str(proj), "--json")
    assert code == 3 and env["errors"][0]["code"] == "clone_target_exists"
    foreign = tmp_path / "foreign"
    git("init", "-q", str(foreign))
    git("remote", "add", "origin", "https://example.org/else/where.git", cwd=foreign)
    code, env, _ = cli("repos", "clone", "app", "--dest", str(foreign), "--root", str(proj), "--json")
    assert code == 3 and env["errors"][0]["code"] == "clone_target_exists"


def test_clone_inside_tree_refused_ac2(cli, proj, tmp_path):
    for dest in (proj / "code" / "app", proj / "shared" / "x", proj, proj.parent):
        code, env, _ = cli("repos", "clone", "app", "--dest", str(dest), "--root", str(proj), "--json")
        assert code == 3 and env["errors"][0]["code"] == "clone_inside_tree", dest
    assert not (proj / "code").exists() and userconfig.load(PID) == {}


@pytest.mark.skipif(sys.platform == "win32", reason="symlink creation needs privileges on Windows")
def test_clone_inside_tree_via_symlink_refused(cli, proj, tmp_path):
    link = tmp_path / "innocent"
    link.symlink_to(proj, target_is_directory=True)
    code, env, _ = cli("repos", "clone", "app", "--dest", str(link / "app"), "--root", str(proj), "--json")
    assert code == 3 and env["errors"][0]["code"] == "clone_inside_tree"


def test_clone_default_path_and_failure_reported(cli, make_project, tmp_path):
    p = make_project(repos=[{"name": "bad", "url": str(tmp_path / "no-such-repo")}])
    code, env, _ = cli("repos", "clone", "bad", "--root", str(p), "--dry-run", "--json")
    assert env["data"]["path"] == str(Path.home().absolute() / "code" / "example-project" / "bad")
    dest = tmp_path / "w" / "bad"
    code, env, _ = cli("repos", "clone", "bad", "--dest", str(dest), "--root", str(p), "--json")
    assert code == 1 and env["errors"][0]["code"] == "clone_failed"
    assert userconfig.load(PID) == {}


def test_status_states(cli, proj, tmp_path):
    def state():
        return cli("repos", "status", "app", "--root", str(proj), "--json")[1]["data"]["repos"][0]["state"]
    assert state() == "not-recorded"
    userconfig.save(PID, {"repos": {"app": str(tmp_path / "gone")}})
    assert state() == "missing"
    plain = tmp_path / "plain"
    plain.mkdir()
    userconfig.save(PID, {"repos": {"app": str(plain)}})
    assert state() == "not-git"
    userconfig.save(PID, {"repos": {"app": str(proj / "code" / "app")}})
    code, env, _ = cli("repos", "status", "--root", str(proj), "--json")
    assert env["data"]["repos"][0]["state"] == "inside-tree" and env["warnings"][0]["code"] == "repo_inside_tree"


def test_status_subfolder_of_other_repo_is_not_a_clone(cli, proj, upstream):
    sub = upstream / "sub"
    sub.mkdir()
    userconfig.save(PID, {"repos": {"app": str(sub)}})
    row = cli("repos", "status", "--root", str(proj), "--json")[1]["data"]["repos"][0]
    assert row["state"] == "not-git"


def test_normalize_url():
    n = repos.normalize_url
    assert n("git@host.org:o/n.git") == n("https://host.org/o/n") == n("ssh://git@HOST.org:22/o/n.git/")
    assert n("https://host.org/o/n") != n("https://host.org/o/m")


# ---- shared sync helper (005 switch)
def test_sync_detection_from_shared_file():
    assert syncinfo.detect_provider(Path("/Users/x/Dropbox/p")) == "dropbox"
    assert syncinfo.detect_provider(Path("/Users/x/Library/CloudStorage/OneDrive-Corp/p")) == "onedrive"
    assert syncinfo.detect_provider(Path("/Users/x/Mi unidad/p")) == "google-drive"
    assert syncinfo.detect_provider(Path("/Users/x/plain/p")) is None
    assert init_cmd.detect_sync_provider(Path("/Users/x/Dropbox/p")) == "dropbox"
    a = syncinfo.availability("google-drive")
    assert {"path": "30_wiki/", "mode": "offline"} in a["folders"] and a["howto"]
    assert syncinfo.availability(None)["howto"] is None
