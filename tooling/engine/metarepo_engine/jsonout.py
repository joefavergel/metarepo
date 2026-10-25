"""JSON envelope, exit codes and the Result type shared by every command (003/R2, R3).

The envelope and the exit codes are a public API (003/ADR-001). Changing either is a
MAJOR version change and is covered by golden tests.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

EXIT_OK = 0
EXIT_BLOCKING = 1  # completed with blocking findings (lint / doctor)
EXIT_USAGE = 2  # usage error (also: command not implemented yet)
EXIT_PRECONDITION = 3  # not a metarepo, gate closed, lease held by another
EXIT_COMPAT = 4  # toolkit outside toolkit.requires, schema invalid, integrity error
EXIT_DEPENDENCY = 5  # missing dependency


def error(code: str, message: str, path: str | None = None) -> dict[str, Any]:
    out: dict[str, Any] = {"code": code, "message": message}
    if path is not None:
        out["path"] = path
    return out


def warning(code: str, message: str, path: str | None = None) -> dict[str, Any]:
    return error(code, message, path)


class EngineError(Exception):
    """Raised by commands (or the dispatcher) to stop with a specific code and exit status."""

    def __init__(self, code: str, message: str, exit_code: int = EXIT_PRECONDITION, path: str | None = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.exit_code = exit_code
        self.path = path

    def as_error(self) -> dict[str, Any]:
        return error(self.code, self.message, self.path)


@dataclass
class Result:
    """What a command returns. The dispatcher turns it into an envelope and an exit status."""

    data: dict[str, Any] = field(default_factory=dict)
    warnings: list[dict[str, Any]] = field(default_factory=list)
    errors: list[dict[str, Any]] = field(default_factory=list)
    blocking: bool = False  # True -> exit 1 (findings that block, e.g. lint errors)
    exit_code: int | None = None  # explicit override; normally derived
    targets: list[str] = field(default_factory=list)  # root-relative POSIX paths touched (event log)
    text: str | None = None  # optional human-readable rendering
    log_event: bool = True  # False: a writes_project command that changed nothing skips the event log

    def warn(self, code: str, message: str, path: str | None = None) -> None:
        self.warnings.append(warning(code, message, path))

    def derive_exit_code(self) -> int:
        if self.exit_code is not None:
            return self.exit_code
        if self.errors:
            return EXIT_PRECONDITION
        if self.blocking:
            return EXIT_BLOCKING
        return EXIT_OK


def envelope(command: str, version: str, result: Result, exit_code: int) -> dict[str, Any]:
    return {
        "ok": exit_code == EXIT_OK,
        "command": command,
        "version": version,
        "data": result.data,
        "warnings": result.warnings,
        "errors": result.errors,
    }


def dumps(env: dict[str, Any]) -> str:
    return json.dumps(env, ensure_ascii=False, indent=2, sort_keys=False, default=str)


def render_human(result: Result) -> str:
    """Plain-text rendering used when --json is absent."""
    if result.text is not None:
        return result.text
    if not result.data:
        return "ok"
    return json.dumps(result.data, ensure_ascii=False, indent=2, default=str)
