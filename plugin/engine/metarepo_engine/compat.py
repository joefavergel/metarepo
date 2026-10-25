"""Toolkit compatibility range (001/R7).

``project.yaml -> toolkit.requires`` is a comma-separated list of clauses such as ``>=0.3,<0.4``.
``check`` classifies the installed version against it:

* ``ok``               installed version satisfies every clause;
* ``update-toolkit``   installed version is below the range: the member must update the toolkit;
* ``migrate-project``  installed version is above the range: the project must be migrated
                       (or the member must install a toolkit inside the range).
"""
from __future__ import annotations

import re
from dataclasses import dataclass

OK = "ok"
UPDATE_TOOLKIT = "update-toolkit"
MIGRATE_PROJECT = "migrate-project"

_CLAUSE_RE = re.compile(r"^(>=|<=|==|!=|>|<)\s*(\d+(?:\.\d+){0,2})$")
_VERSION_RE = re.compile(r"^(\d+)(?:\.(\d+))?(?:\.(\d+))?")


class CompatError(ValueError):
    """The ``requires`` string or a version could not be parsed."""


Version = tuple[int, int, int]


def parse_version(text: str) -> Version:
    m = _VERSION_RE.match(str(text).strip())
    if not m:
        raise CompatError(f"not a version: {text!r}")
    return (int(m.group(1)), int(m.group(2) or 0), int(m.group(3) or 0))


def parse_requires(text: str) -> list[tuple[str, Version]]:
    if not isinstance(text, str) or not text.strip():
        raise CompatError("toolkit.requires is empty")
    clauses: list[tuple[str, Version]] = []
    for part in text.split(","):
        part = part.strip()
        m = _CLAUSE_RE.match(part)
        if not m:
            raise CompatError(f"bad toolkit.requires clause: {part!r}")
        clauses.append((m.group(1), parse_version(m.group(2))))
    return clauses


def _holds(op: str, installed: Version, bound: Version) -> bool:
    return {
        ">=": installed >= bound,
        ">": installed > bound,
        "<=": installed <= bound,
        "<": installed < bound,
        "==": installed == bound,
        "!=": installed != bound,
    }[op]


@dataclass(frozen=True)
class CompatResult:
    status: str
    installed: str
    requires: str
    message: str

    @property
    def ok(self) -> bool:
        return self.status == OK


def check(requires: str, installed: str) -> CompatResult:
    clauses = parse_requires(requires)
    inst = parse_version(installed)
    failed = [(op, v) for op, v in clauses if not _holds(op, inst, v)]
    if not failed:
        return CompatResult(OK, installed, requires, "toolkit version is within the project's range")
    below = any(op in (">=", ">", "==", "!=") for op, _ in failed)
    above = any(op in ("<", "<=") for op, _ in failed)
    if above and not below:
        return CompatResult(
            MIGRATE_PROJECT,
            installed,
            requires,
            f"installed toolkit {installed} is newer than this project's range '{requires}': "
            "migrate the project (`migrate --dry-run`, then `migrate --yes`) if a migration exists, "
            "or install a toolkit version inside the range",
        )
    return CompatResult(
        UPDATE_TOOLKIT,
        installed,
        requires,
        f"installed toolkit {installed} is older than this project's range '{requires}': "
        "update the toolkit (plugin or portable bundle) to a version inside the range",
    )


def line_of(version: str) -> str:
    """Compatibility line of a version: ``M`` for M >= 1, ``0.m`` for the 0.x series."""
    major, minor, _ = parse_version(version)
    return str(major) if major >= 1 else f"0.{minor}"


def requires_for(version: str) -> str:
    """The ``requires`` range written for a project created or migrated with ``version``."""
    major, minor, _ = parse_version(version)
    if major >= 1:
        return f">={major}.{minor},<{major + 1}"
    return f">=0.{minor},<0.{minor + 1}"
