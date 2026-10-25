"""002/R14, R15: project.yaml and state.json validation (002/AC1) and the subset validator."""
import copy
import json

import pytest
import yaml

from metarepo_engine import config as cfgmod
from metarepo_engine import validate as v
from metarepo_engine.scaffold import fixture_config, new_state

# The example of 002 section 4 (id made concrete).
SPEC_EXAMPLE = """
schema_version: 1
id: 7f3c1b2a-5d4e-4f6a-9b8c-0123456789ab
name: "Example Project"
slug: example-project
description: "One paragraph."
objective: >
  What the project must achieve.
scope:
  in:  ["topic A", "topic B"]
  out: ["topic C"]
language:
  content: en
toolkit:
  requires: ">=0.1,<0.2"
  created_with: 0.1.0
sync:
  provider: google-drive
  notes: ""
repos:
  - name: app
    url: https://example.com/org/app.git
    default_branch: main
    role: primary
team:
  - id: member-a
    display_name: "Member A"
    roles: [author, reviewer]
governance:
  wiki_write_mode: lease
  wiki_owners: []
  lease_ttl_minutes: 30
  lease_settle_seconds: 10
  audit_expected: false
intake:
  confirm: always
  auto_feed: true
  triage_budget_tokens: 3000
  on_reject: leave-in-inbox
  archive_bundles: true
  bundle_max_depth: 2
  max_source_depth: 4
  bundle_max_uncompressed_mb: 2048
  bundle_max_ratio: 100
  categories: []
extraction:
  allow_cloud: false
  preferences: {}
  large_table_rows: 200
  max_body_mb: 2
  quality_thresholds: {}
  commands: []
policy:
  block_patterns: []
  block_terms: []
  path_limit: 200
  forbidden_paths: []
lint:
  severity: {}
  allow_policy_downgrade: false
doctor:
  inbox_max_age_days: 14
  large_file_mb: 200
outputs:
  default_format: markdown
extra_folders: []
"""


def errors(cfg):
    return cfgmod.validate_project(cfg).errors


def test_ac1_accepts_the_spec_example():
    findings = cfgmod.validate_project(yaml.safe_load(SPEC_EXAMPLE))
    assert findings.errors == [] and findings.warnings == []


def test_accepts_the_minimal_and_fixture_configs():
    assert errors(fixture_config()) == []
    minimal = {k: fixture_config()[k] for k in
               ("schema_version", "id", "name", "slug", "objective", "scope", "language", "toolkit")}
    assert errors(minimal) == []


def test_ac1_rejects_missing_objective():
    cfg = yaml.safe_load(SPEC_EXAMPLE)
    del cfg["objective"]
    assert ("objective", "is required") in errors(cfg)


def test_ac1_rejects_non_kebab_slug():
    for bad in ("Example Project", "example_project", "-x", "x-", "UPPER", "a--b", ""):
        cfg = yaml.safe_load(SPEC_EXAMPLE)
        cfg["slug"] = bad
        assert any(p == "slug" for p, _ in errors(cfg)), bad


def test_ac1_rejects_unknown_wiki_write_mode():
    cfg = yaml.safe_load(SPEC_EXAMPLE)
    cfg["governance"]["wiki_write_mode"] = "anyone"
    assert any(p == "governance.wiki_write_mode" and "one of" in m for p, m in errors(cfg))


def test_ac1_rejects_owner_mode_without_owners():
    cfg = yaml.safe_load(SPEC_EXAMPLE)
    cfg["governance"]["wiki_write_mode"] = "owner"
    assert any(p == "governance.wiki_owners" for p, _ in errors(cfg))
    cfg["governance"]["wiki_owners"] = ["member-a"]
    assert errors(cfg) == []
    cfg["governance"]["wiki_owners"] = ["someone-else"]
    findings = cfgmod.validate_project(cfg)
    assert findings.errors == [] and any("not listed in team" in m for _, m in findings.warnings)


@pytest.mark.parametrize("key", ["id", "name", "slug", "objective", "scope", "language", "toolkit", "schema_version"])
def test_every_required_key_is_enforced(key):
    cfg = yaml.safe_load(SPEC_EXAMPLE)
    del cfg[key]
    assert (key, "is required") in errors(cfg)


def test_nested_required_keys():
    cfg = yaml.safe_load(SPEC_EXAMPLE)
    del cfg["language"]["content"]
    del cfg["toolkit"]["requires"]
    assert {("language.content", "is required"), ("toolkit.requires", "is required")} <= set(errors(cfg))


@pytest.mark.parametrize(
    "path,value",
    [
        (("id",), "not-a-uuid"),
        (("schema_version",), 2),
        (("language", "content"), "english please"),
        (("sync", "provider"), "usb-stick"),
        (("intake", "confirm"), "never"),
        (("intake", "on_reject"), "burn"),
        (("outputs", "default_format"), "pdf"),
        (("governance", "lease_ttl_minutes"), 0),
        (("governance", "lease_ttl_minutes"), "30"),
        (("governance", "audit_expected"), "yes please"),
        (("intake", "max_source_depth"), 0),
        (("extraction", "allow_cloud"), "false"),
        (("toolkit", "requires"), 5),
    ],
)
def test_type_enum_and_range_rejections(path, value):
    cfg = yaml.safe_load(SPEC_EXAMPLE)
    target = cfg
    for part in path[:-1]:
        target = target[part]
    target[path[-1]] = value
    assert any(p == ".".join(path) for p, _ in errors(cfg)), (path, value)


def test_unquoted_yes_no_hint():
    cfg = yaml.safe_load(SPEC_EXAMPLE.replace("content: en", "content: no"))
    (msg,) = [m for p, m in errors(cfg) if p == "language.content"]
    assert "quote" in msg


def test_unknown_keys_are_preserved_and_reported_as_warnings():
    cfg = yaml.safe_load(SPEC_EXAMPLE)
    cfg["custom_top"] = {"a": 1}
    cfg["intake"]["custom_inner"] = True
    before = copy.deepcopy(cfg)
    findings = cfgmod.validate_project(cfg)
    assert findings.errors == []
    assert {p for p, _ in findings.warnings} == {"custom_top", "intake.custom_inner"}
    assert cfg == before  # preserved untouched
    # free-form maps do not warn
    cfg2 = yaml.safe_load(SPEC_EXAMPLE)
    cfg2["extraction"]["preferences"] = {"application/pdf": ["a", "b"]}
    cfg2["extraction"]["quality_thresholds"] = {"anything": 1}
    cfg2["lint"]["severity"] = {"some-check": "warn"}
    assert cfgmod.validate_project(cfg2).warnings == []


def test_cross_field_rules():
    cfg = yaml.safe_load(SPEC_EXAMPLE)
    cfg["toolkit"]["requires"] = "banana"
    cfg["repos"].append({"name": "app", "url": "https://example.com/x.git"})
    cfg["team"].append({"id": "member-a"})
    cfg["policy"]["block_patterns"] = [{"name": "bad", "regex": "("}]
    cfg["extraction"]["commands"] = [{"name": "x", "media_types": ["audio/x"], "command": "t"},
                                     {"name": "x", "media_types": ["audio/y"], "command": ["t", "-q"]}]
    msgs = dict(errors(cfg))
    assert "toolkit.requires" in msgs and "repos" in msgs and "team" in msgs
    assert "policy.block_patterns.0.regex" in msgs and "extraction.commands" in msgs


def test_extra_folders_rules():
    for bad in ("a/b", "00_inbox", "outputs", "_hidden", "..", " x"):
        cfg = yaml.safe_load(SPEC_EXAMPLE)
        cfg["extra_folders"] = [{"path": bad, "tier": "explicit"}]
        assert errors(cfg), bad
    cfg = yaml.safe_load(SPEC_EXAMPLE)
    cfg["extra_folders"] = [{"path": "25_datasets", "tier": "on-demand", "purpose": "data"}]
    assert errors(cfg) == []
    cfg["extra_folders"][0]["tier"] = "free"
    assert errors(cfg)


def test_effective_applies_defaults_without_mutating():
    cfg = {k: fixture_config()[k] for k in ("schema_version", "id", "name", "slug", "objective", "scope",
                                              "language", "toolkit")}
    cfg["intake"] = {"confirm": "when-uncertain"}
    snapshot = copy.deepcopy(cfg)
    eff = cfgmod.effective(cfg)
    assert eff["intake"]["confirm"] == "when-uncertain" and eff["intake"]["max_source_depth"] == 4
    assert eff["governance"]["lease_ttl_minutes"] == 30 and eff["policy"]["path_limit"] == 200
    assert cfg == snapshot
    eff["intake"]["categories"].append("x")
    assert cfgmod.DEFAULTS["intake"]["categories"] == []


def test_get_path():
    data = {"a": {"b": [{"c": 1}, {"c": 2}]}}
    assert cfgmod.get_path(data, "a.b.1.c") == 2
    assert cfgmod.get_path(data, "a.b.-1.c") == 2
    for bad in ("a.x", "a.b.9", "a.b.x", "z"):
        with pytest.raises(KeyError):
            cfgmod.get_path(data, bad)


# ---- state.json
def test_state_schema_accepts_the_spec_shape_and_rejects_garbage():
    assert cfgmod.validate_state(new_state()).errors == []
    spec = {"schema_version": 1, "metarepo": {"configured": True, "created_at": "2026-10-24T12:00:00Z",
                                                "created_with": "0.1.0"},
            "wiki": {"configured": False, "configured_at": None, "bundle": "30_wiki"},
            "toolkit": {"created_with": "0.1.0", "last_written_with": "0.1.0", "migrations": []},
            "last_doctor": None, "last_lint": None}
    assert cfgmod.validate_state(spec).errors == []
    bad = copy.deepcopy(spec)
    bad["wiki"]["configured"] = "no"
    del bad["metarepo"]
    paths = {p for p, _ in cfgmod.validate_state(bad).errors}
    assert paths == {"wiki.configured", "metarepo"}
    assert cfgmod.validate_state([]).errors


# ---- validator unit tests
def test_validator_keywords():
    schema = {"type": "object", "required": ["a"], "properties": {
        "a": {"type": "integer", "minimum": 1, "maximum": 3},
        "b": {"type": "string", "enum": ["x", "y"], "pattern": "^[xy]$"},
        "c": {"type": "array", "minItems": 1, "items": {"type": "number"}},
        "d": {"type": ["string", "null"]},
        "e": {"const": 7},
        "f": {"type": "object", "additionalProperties": {"type": "integer"}},
    }}
    good = {"a": 2, "b": "x", "c": [1, 2.5], "d": None, "e": 7, "f": {"k": 1}}
    assert v.validate(good, schema).errors == []
    for bad, path in [({"a": 0}, "a"), ({"a": 4}, "a"), ({"a": True}, "a"), ({"a": 1, "b": "z"}, "b"),
                      ({"a": 1, "c": []}, "c"), ({"a": 1, "c": ["x"]}, "c.0"), ({"a": 1, "d": 3}, "d"),
                      ({"a": 1, "e": 8}, "e"), ({"a": 1, "f": {"k": "s"}}, "f.k"), ({}, "a"), ([], "(root)")]:
        assert any(p == path for p, _ in v.validate(bad, schema).errors), (bad, path)
    assert v.validate({"a": 1, "zzz": 1}, schema).warnings == [("zzz", "unknown key (kept as is)")]
    with pytest.raises(ValueError):
        v.validate(1, {"type": "complex"})


# ---- CLI
def test_cli_config_validate_ok_and_warnings(cli, make_project):
    root = make_project(custom_key=1)
    code, env, _ = cli("config", "validate", "--json", "--root", str(root))
    assert code == 0 and env["data"]["valid"] is True
    assert [w["code"] for w in env["warnings"]] == ["unknown_key"]


def test_cli_config_validate_invalid_exits_4(cli, make_project):
    root = make_project(slug="Not Kebab", governance={"wiki_write_mode": "owner"})
    code, env, _ = cli("config", "validate", "--json", "--root", str(root))
    assert code == 4 and env["ok"] is False
    paths = {f["path"] for f in env["data"]["findings"] if f["severity"] == "error"}
    assert paths == {"slug", "governance.wiki_owners"}


def test_cli_config_validate_broken_yaml_and_state(cli, make_project):
    root = make_project(yaml_text="a: [unclosed\n")
    code, env, _ = cli("config", "validate", "--json", "--root", str(root))
    assert code == 4 and env["errors"][0]["code"] == "config_invalid"
    root2 = make_project()
    (root2 / "_system" / "state.json").write_text('{"schema_version": 1}', encoding="utf-8")
    code, env, _ = cli("config", "validate", "--json", "--root", str(root2))
    assert code == 4 and {f["file"] for f in env["data"]["findings"]} == {"_system/state.json"}


def test_cli_config_validate_runs_even_when_toolkit_is_out_of_range(cli, make_project):
    root = make_project(requires=">=9.0,<10")
    code, _, _ = cli("config", "validate", "--json", "--root", str(root))
    assert code == 0


def test_cli_config_get(cli, make_project):
    root = make_project(repos=[{"name": "app", "url": "https://example.com/a.git"}])
    code, env, _ = cli("config", "get", "repos.0.name", "--json", "--root", str(root))
    assert code == 0 and env["data"] == {"key": "repos.0.name", "value": "app"}
    code, env, _ = cli("config", "get", "governance.lease_ttl_minutes", "--json", "--root", str(root))
    assert env["data"]["value"] == 30  # default applied
    code, env, _ = cli("config", "get", "governance.lease_ttl_minutes", "--no-defaults", "--json", "--root", str(root))
    assert code == 2 and env["errors"][0]["code"] == "key_not_found"
    code, out, _ = cli("config", "get", "name", "--root", str(root))
    assert code == 0 and out.strip() == "Example Project"
    code, out, _ = cli("config", "get", "scope", "--root", str(root))
    assert json.loads(out) == {"in": ["topic-a"], "out": []}


def test_schema_files_are_valid_json_and_match_runtime_loader():
    for name in ("project", "state"):
        assert cfgmod.load_schema(name)["type"] == "object"
