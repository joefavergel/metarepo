"""Command registry. See tooling/engine/CONTRIBUTING-commands.md.

Each module in this package exposes one ``COMMAND`` (a ``metarepo_engine.command.Command``).
Registering a command is ONE line in ``MODULES`` below; ``cli.py`` and the dispatcher never change.
Keep the list sorted so parallel branches add distinct lines.
"""
from __future__ import annotations

import importlib

from ..command import Command

MODULES: tuple[str, ...] = (
    "config_get",
    "config_user",
    "config_validate",
    "doctor",
    "extract",
    "extract_record",
    "extractors",
    "hash_file",
    "inbox_scan",
    "init",
    "install_skills",
    "lease_acquire",
    "lease_release",
    "lease_renew",
    "lease_status",
    "lint",
    "migrate",
    "output_register",
    "place",
    "registry",
    "remove",
    "render_templates",
    "repos_clone",
    "repos_list",
    "repos_path",
    "repos_status",
    "search",
    "sign",
    "source_id",
    "status",
    "unbundle",
    "wiki_gate",
    "wiki_scaffold",
    "wiki_source_page",
)


def load_commands() -> list[Command]:
    """Import every registered module and return its COMMAND. Fails loudly on duplicates."""
    commands: list[Command] = []
    seen: set[str] = set()
    for module_name in MODULES:
        module = importlib.import_module(f"{__name__}.{module_name}")
        command = getattr(module, "COMMAND", None)
        if not isinstance(command, Command):
            raise RuntimeError(f"commands/{module_name}.py must define COMMAND")
        if command.name in seen:
            raise RuntimeError(f"duplicate command name: {command.name}")
        seen.add(command.name)
        commands.append(command)
    return commands
