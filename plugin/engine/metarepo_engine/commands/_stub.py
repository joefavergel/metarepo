"""Placeholder commands for work owned by later specs (003/T3.1).

A stub answers ``not_implemented`` with exit status 2. The owning spec replaces the stub module's
``COMMAND`` with a real implementation; the registry line in ``commands/__init__.py`` stays.
"""
from __future__ import annotations

import argparse

from ..command import Command, Context
from ..jsonout import EXIT_USAGE, Result, error


def _not_implemented(name: str, owner: str):
    def run(ctx: Context, args: argparse.Namespace) -> Result:  # pragma: no cover - dispatcher short-circuits
        return Result(
            errors=[error("not_implemented", f"'{name}' is not implemented yet (owned by spec {owner})")],
            exit_code=EXIT_USAGE,
        )

    return run


def stub(
    name: str,
    help: str,
    owner: str,
    *,
    mutating: bool = False,
    writes_project: bool = False,
    needs_project: bool = True,
    gate: bool = True,
    needs_yaml: bool = True,
) -> Command:
    return Command(
        name=name,
        help=f"{help} [not implemented yet; spec {owner}]",
        run=_not_implemented(name, owner),
        mutating=mutating or writes_project,
        writes_project=writes_project,
        needs_project=needs_project,
        gate=gate,
        needs_yaml=needs_yaml,
        owner=owner,
        implemented=False,
    )
