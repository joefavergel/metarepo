"""The dispatcher: argparse tree, UTF-8, envelope, exit codes, compatibility gate, event log.

``cli.py`` only calls ``main``. Commands live in ``commands/`` and are listed in its registry.
This module is the single owner of cross-cutting behaviour so that commands stay small.
"""
from __future__ import annotations

import argparse
import os
import sys
import traceback
from typing import Any, Sequence

from . import __version__, compat
from .command import Command, Context
from .config import INSTALL_HINT
from .events import append_event, configured_actor, resolve_actor
from .jsonout import (
    EXIT_COMPAT,
    EXIT_DEPENDENCY,
    EXIT_OK,
    EXIT_USAGE,
    EngineError,
    Result,
    dumps,
    envelope,
    error,
    render_human,
)
from .root import Project


class UsageError(Exception):
    def __init__(self, message: str, usage: str = ""):
        super().__init__(message)
        self.message = message
        self.usage = usage


class _Parser(argparse.ArgumentParser):
    def error(self, message: str):  # type: ignore[override]
        raise UsageError(message, self.format_usage())


def configure_stdio() -> None:
    """Force UTF-8 for stdio regardless of platform locale (003/R5)."""
    for stream in (sys.stdin, sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            try:
                reconfigure(encoding="utf-8")
            except (ValueError, OSError):  # pragma: no cover - detached streams
                pass


def _common(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--json", action="store_true", default=argparse.SUPPRESS,
                        help="print one JSON envelope on stdout")
    parser.add_argument("--root", default=argparse.SUPPRESS, metavar="PATH",
                        help="metarepository root (default: discovered from the current directory)")


def build_parser(commands: Sequence[Command]) -> argparse.ArgumentParser:
    parser = _Parser(prog="cli.py", description="metarepo engine: deterministic operations for a metarepository.")
    parser.add_argument("--version", action="version", version=f"metarepo engine {__version__}")
    _common(parser)
    top = parser.add_subparsers(dest="_group", metavar="<command>", parser_class=_Parser)
    top.required = True
    groups: dict[str, Any] = {}

    def add_leaf(sub, leaf_name: str, cmd: Command):
        leaf = sub.add_parser(leaf_name, help=cmd.help, description=cmd.help)
        _common(leaf)
        if cmd.mutating:
            leaf.add_argument("--dry-run", action="store_true", default=False,
                              help="print the planned operations without performing them")
            leaf.add_argument("--actor", default=None, help="identity recorded in the event log")
        if cmd.add_arguments:
            cmd.add_arguments(leaf)
        leaf.set_defaults(_cmd=cmd)

    for cmd in sorted(commands, key=lambda c: c.name):
        parts = cmd.name.split(" ")
        if len(parts) == 1:
            add_leaf(top, parts[0], cmd)
            continue
        group, leaf_name = parts
        if group not in groups:
            gp = top.add_parser(group, help=f"{group} commands")
            _common(gp)
            gsub = gp.add_subparsers(dest="_sub", metavar="<subcommand>", parser_class=_Parser)
            gsub.required = True
            groups[group] = gsub
        add_leaf(groups[group], leaf_name, cmd)
    return parser


def has_yaml() -> bool:
    try:
        import yaml  # noqa: F401
    except ImportError:
        return False
    return True


def _emit(command: str, result: Result, exit_code: int, json_mode: bool) -> None:
    for w in result.warnings:
        print(f"warning: {w.get('message', w)}", file=sys.stderr)
    for e in result.errors:
        print(f"error: {e.get('message', e)}", file=sys.stderr)
    if json_mode:
        print(dumps(envelope(command, __version__, result, exit_code)))
    elif not result.errors:
        print(render_human(result))
    sys.stdout.flush()


def _fail(command: str, code: str, message: str, exit_code: int, json_mode: bool, path: str | None = None) -> int:
    result = Result(errors=[error(code, message, path)], exit_code=exit_code)
    _emit(command, result, exit_code, json_mode)
    return exit_code


def _project_id(project: Project | None) -> str | None:
    if project is None:
        return None
    try:
        return project.project_id
    except Exception:  # noqa: BLE001 - an unreadable project.yaml must not stop the actor lookup
        return None


def run_command(cmd: Command, args: argparse.Namespace) -> int:
    json_mode = bool(getattr(args, "json", False))
    if not cmd.implemented:
        return _fail(cmd.name, "not_implemented",
                     f"'{cmd.name}' is not implemented yet (owned by spec {cmd.owner})", EXIT_USAGE, json_mode)
    if cmd.needs_yaml and not has_yaml():
        return _fail(cmd.name, "missing_dependency", INSTALL_HINT, EXIT_DEPENDENCY, json_mode)

    project: Project | None = None
    ctx_warnings: list[dict[str, Any]] = []
    if cmd.needs_project:
        try:
            project = Project.discover(getattr(args, "root", None))
        except EngineError as exc:
            return _fail(cmd.name, exc.code, exc.message, exc.exit_code, json_mode, exc.path)
        if cmd.gate:
            try:
                requires = (project.config().get("toolkit") or {}).get("requires")
                if not requires:
                    raise EngineError("config_invalid", "project.yaml has no toolkit.requires", EXIT_COMPAT,
                                      "project.yaml")
                verdict = compat.check(str(requires), __version__)
            except (EngineError, compat.CompatError) as exc:
                if cmd.writes_project:
                    if isinstance(exc, EngineError):
                        return _fail(cmd.name, exc.code, exc.message, exc.exit_code, json_mode, exc.path)
                    return _fail(cmd.name, "config_invalid", str(exc), EXIT_COMPAT, json_mode, "project.yaml")
                ctx_warnings.append(error("compat_unchecked", f"compatibility could not be checked: {exc}"))
            else:
                if not verdict.ok:
                    if cmd.writes_project:
                        return _fail(cmd.name, "toolkit_incompatible", verdict.message, EXIT_COMPAT, json_mode)
                    ctx_warnings.append(error("toolkit_incompatible", verdict.message))

    ctx = Context(
        project=project,
        json_mode=json_mode,
        dry_run=bool(getattr(args, "dry_run", False)),
        actor=configured_actor(getattr(args, "actor", None), _project_id(project)),
        version=__version__,
        warnings=ctx_warnings,
    )
    try:
        result = cmd.run(ctx, args)
    except EngineError as exc:
        result = Result(errors=[exc.as_error()], exit_code=exc.exit_code)
    except Exception as exc:  # noqa: BLE001 - last-resort guard so the envelope contract always holds
        if os.environ.get("METAREPO_DEBUG"):
            traceback.print_exc()
        result = Result(errors=[error("internal_error", f"{type(exc).__name__}: {exc}")], exit_code=EXIT_USAGE)
    result.warnings = ctx.warnings + result.warnings
    exit_code = result.derive_exit_code()

    # Log what was done: successful runs, and failed runs that already touched something.
    # A run that stopped before changing anything (no targets) leaves no event.
    if (cmd.writes_project and project is not None and not ctx.dry_run and result.log_event
            and (exit_code == EXIT_OK or result.targets)):
        try:
            append_event(
                project.root,
                command=cmd.name,
                targets=result.targets,
                result="ok" if exit_code == EXIT_OK else "error",
                actor=resolve_actor(ctx.actor),
                extra=result.event_extra or None,
            )
        except OSError as exc:
            result.warn("event_log_failed", f"could not append to _system/events.log: {exc}")
    _emit(cmd.name, result, exit_code, json_mode)
    return exit_code


def main(argv: Sequence[str] | None = None, commands: Sequence[Command] | None = None) -> int:
    configure_stdio()
    arg_list = list(sys.argv[1:] if argv is None else argv)
    json_mode = "--json" in arg_list
    if commands is None:
        from .commands import load_commands

        commands = load_commands()
    parser = build_parser(commands)
    try:
        args = parser.parse_args(arg_list)
    except UsageError as exc:
        sys.stderr.write(exc.usage)
        return _fail("cli", "usage", exc.message, EXIT_USAGE, json_mode)
    except SystemExit as exc:  # --help / --version
        code = exc.code
        return code if isinstance(code, int) else EXIT_OK
    return run_command(args._cmd, args)
