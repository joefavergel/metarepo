"""Shared pytest setup: import paths, a fixture-metarepo builder and an in-process CLI runner."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
ENGINE = REPO / "tooling" / "engine"
for p in (ENGINE, REPO / "tools", Path(__file__).resolve().parent):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))


@pytest.fixture(autouse=True)
def _isolated_cache(tmp_path_factory, monkeypatch):
    """Never touch the real per-member cache or identity."""
    monkeypatch.setenv("METAREPO_CACHE_DIR", str(tmp_path_factory.mktemp("cache")))
    monkeypatch.setenv("METAREPO_ACTOR", "test-actor")
    monkeypatch.delenv("METAREPO_ENGINE", raising=False)


@pytest.fixture
def repo() -> Path:
    return REPO


@pytest.fixture
def make_project(tmp_path):
    from helpers import make_project as _make

    def factory(**kwargs):
        return _make(tmp_path / "proj", **kwargs)

    return factory


@pytest.fixture
def cli(capsys):
    """Run the CLI in-process: returns (exit_code, parsed_json_or_text, stderr)."""
    from metarepo_engine import dispatch

    def run(*argv, commands=None):
        code = dispatch.main(list(argv), commands=commands)
        captured = capsys.readouterr()
        out = captured.out
        if "--json" in argv:
            try:
                out = json.loads(out)
            except ValueError:
                pass
        return code, out, captured.err

    return run
