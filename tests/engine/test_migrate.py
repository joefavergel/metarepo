"""001/R8 migrate scaffolding with a sample migration (001/T3.4)."""
import json
from pathlib import Path

import pytest
import yaml

from helpers import tree_snapshot
from metarepo_engine import dispatch, migrate as migrate_mod
from metarepo_engine.events import read_events
from metarepo_engine.jsonout import EngineError
from metarepo_engine.root import Project

YAML_WITH_COMMENTS = """\
# project configuration (hand edited)
schema_version: 1
id: 7f3c1b2a-5d4e-4f6a-9b8c-0123456789ab
name: Example Project
slug: example-project
objective: Example objective.
scope: {in: [], out: []}
language:
  content: en
toolkit:
  requires: ">=0.1,<0.2"   # keep in step with the toolkit
  created_with: 0.1.0
"""

SAMPLE = '''
def migrate(root, dry_run):
    target = root / "shared" / "note.txt"
    changed = ["shared/note.txt"]
    if not dry_run:
        target.write_text(target.read_text(encoding="utf-8") + "migrated\\n", encoding="utf-8")
    return changed
'''


@pytest.fixture
def mig_dir(tmp_path):
    d = tmp_path / "migrations"
    d.mkdir()
    (d / "0.1_to_0.2.py").write_text(SAMPLE, encoding="utf-8")
    return d


@pytest.fixture
def project(make_project):
    root = make_project(yaml_text=YAML_WITH_COMMENTS)
    (root / "shared" / "note.txt").write_text("original\n", encoding="utf-8")
    return Project(root)


def test_discover_and_chain(mig_dir):
    available = migrate_mod.discover(mig_dir)
    assert list(available) == ["0.1"]
    steps = migrate_mod.plan_chain("0.1", "0.2", available)
    assert [(s.from_line, s.to_line) for s in steps] == [("0.1", "0.2")]
    with pytest.raises(EngineError) as exc:
        migrate_mod.plan_chain("0.1", "0.3", available)
    assert exc.value.code == "no_migration_path"


def test_up_to_date_does_nothing(project, mig_dir):
    report = migrate_mod.run_migrations(project, installed="0.1.0", dry_run=False, confirmed=True, directory=mig_dir)
    assert report.status == "up-to-date"


def test_older_toolkit_is_told_to_update(project, mig_dir):
    project.config()["toolkit"]["requires"] = ">=0.3,<0.4"
    with pytest.raises(EngineError) as exc:
        migrate_mod.run_migrations(project, installed="0.1.0", dry_run=True, confirmed=False, directory=mig_dir)
    assert exc.value.code == "update_toolkit" and exc.value.exit_code == 4


def test_dry_run_changes_nothing(project, mig_dir):
    before = tree_snapshot(project.root)
    report = migrate_mod.run_migrations(project, installed="0.2.0", dry_run=True, confirmed=True, directory=mig_dir)
    assert report.status == "planned"
    assert report.steps == [{"from": "0.1", "to": "0.2", "file": "0.1_to_0.2.py", "changes": ["shared/note.txt"]}]
    assert report.new_requires == ">=0.2,<0.3"
    assert tree_snapshot(project.root) == before


def test_unconfirmed_run_only_plans(project, mig_dir):
    before = tree_snapshot(project.root)
    report = migrate_mod.run_migrations(project, installed="0.2.0", dry_run=False, confirmed=False, directory=mig_dir)
    assert report.status == "planned"
    assert tree_snapshot(project.root) == before


def test_confirmed_run_backs_up_applies_bumps_and_records(project, mig_dir, tmp_path):
    report = migrate_mod.run_migrations(project, installed="0.2.0", dry_run=False, confirmed=True, directory=mig_dir)
    assert report.status == "applied"
    root = project.root
    assert (root / "shared" / "note.txt").read_text(encoding="utf-8") == "original\nmigrated\n"
    # backups of every changed file exist in the local cache, outside the project, with the old content
    backup = Path(report.backup_dir)
    assert root not in backup.parents
    assert (backup / "shared" / "note.txt").read_text(encoding="utf-8") == "original\n"
    assert (backup / "project.yaml").read_text(encoding="utf-8") == YAML_WITH_COMMENTS
    assert (backup / "_system" / "state.json").is_file()
    # requires bumped, comments and other lines preserved byte-for-byte
    text = (root / "project.yaml").read_text(encoding="utf-8")
    assert text == YAML_WITH_COMMENTS.replace('">=0.1,<0.2"', '">=0.2,<0.3"')
    assert yaml.safe_load(text)["toolkit"]["requires"] == ">=0.2,<0.3"
    state = json.loads((root / "_system" / "state.json").read_text(encoding="utf-8"))
    assert state["toolkit"]["last_written_with"] == "0.2.0"
    assert state["toolkit"]["migrations"][0]["from"] == "0.1" and state["toolkit"]["migrations"][0]["to"] == "0.2"


def test_unannounced_change_is_an_error(project, tmp_path):
    d = tmp_path / "bad"
    d.mkdir()
    (d / "0.1_to_0.2.py").write_text(
        "def migrate(root, dry_run):\n"
        "    if not dry_run:\n"
        "        (root / 'shared' / 'sneaky.txt').write_text('x', encoding='utf-8')\n"
        "        return ['shared/sneaky.txt']\n"
        "    return []\n", encoding="utf-8")
    with pytest.raises(EngineError) as exc:
        migrate_mod.run_migrations(project, installed="0.2.0", dry_run=False, confirmed=True, directory=d)
    assert exc.value.code == "migration_unplanned_change"


def test_bump_requires_text_cases():
    flow = 'toolkit:\n  requires: ">=0.1,<0.2"\n'
    assert migrate_mod.bump_requires_text(flow, ">=1.0,<2") == 'toolkit:\n  requires: ">=1.0,<2"\n'
    unquoted = "toolkit:\r\n  requires: '>=0.1,<0.2'\r\nname: x\r\n"
    assert migrate_mod.bump_requires_text(unquoted, ">=0.2,<0.3") == "toolkit:\r\n  requires: '>=0.2,<0.3'\r\nname: x\r\n"
    with pytest.raises(EngineError):
        migrate_mod.bump_requires_text("toolkit: {requires: '>=0.1,<0.2'}\n", ">=0.2,<0.3")
    with pytest.raises(EngineError):  # a requires line outside the toolkit block must not be touched
        migrate_mod.bump_requires_text("other:\n  requires: x\ntoolkit:\n  created_with: 1\n", ">=0.2,<0.3")


# ---- through the CLI (dispatcher gate, confirmation, event log)
def test_cli_flow(cli, project, mig_dir, monkeypatch):
    monkeypatch.setattr(dispatch, "__version__", "0.2.0")
    monkeypatch.setattr(migrate_mod, "migrations_dir", lambda: mig_dir)
    root = str(project.root)
    # a normal write command is refused while the project is behind ...
    # ... but migrate itself runs (gate disabled for it), first as a dry run
    code, env, _ = cli("migrate", "--dry-run", "--json", "--root", root)
    assert code == 0 and env["data"]["status"] == "planned"
    # without --yes nothing is applied and the exit status says confirmation is needed
    code, env, _ = cli("migrate", "--json", "--root", root)
    assert code == 3 and env["errors"][0]["code"] == "confirmation_required"
    assert read_events(project.root) == []
    code, env, _ = cli("migrate", "--yes", "--json", "--root", root)
    assert code == 0 and env["data"]["status"] == "applied"
    (event,) = read_events(project.root)
    assert event["command"] == "migrate" and "project.yaml" in event["targets"]
    code, env, _ = cli("migrate", "--yes", "--json", "--root", root)
    assert code == 0 and env["data"]["status"] == "up-to-date"
    assert len(read_events(project.root)) == 1  # the no-op is not logged
