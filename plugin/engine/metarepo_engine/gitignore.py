"""Gitignore-syntax matcher for ``policy.forbidden_paths`` (013/R2).

Implemented subset: ``*``, ``?``, ``[...]`` (with ``!``/``^`` negation and ranges), ``**`` (leading
``**/``, inner ``/**/`` and trailing ``/**``), a trailing ``/`` (directories only), a leading ``/`` or an
inner ``/`` (anchored to the root), ``!`` negation, ``\\`` escapes, ``#`` comments and blank lines. The last
matching pattern decides; a path inside an ignored folder is ignored whatever later patterns say (as in git:
a file cannot be re-included once its parent folder is excluded). Matching is case-insensitive because the
cloud drives this toolkit runs on are.

This is a check, not a sync filter: it only classifies paths that already exist in the tree.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable


def _class_to_regex(glob: str, start: int) -> tuple[str, int] | None:
    """Translate ``[...]`` starting at ``start``; None when there is no closing bracket."""
    i = start + 1
    negate = False
    if i < len(glob) and glob[i] in "!^":
        negate = True
        i += 1
    body: list[str] = []
    first = True
    while i < len(glob):
        c = glob[i]
        if c == "]" and not first:
            inner = "".join(body)
            if not inner:
                return None
            return ("[^/" if negate else "[") + inner + "]", i + 1
        if c == "\\" and i + 1 < len(glob):
            body.append(re.escape(glob[i + 1]))
            i += 2
        elif c == "[" or c == "\\":
            body.append("\\" + c)
            i += 1
        else:
            body.append(c)
            i += 1
        first = False
    return None


def glob_to_regex(glob: str) -> str:
    out: list[str] = []
    i, n = 0, len(glob)
    while i < n:
        c = glob[i]
        if c == "*" and glob.startswith("**", i):
            before_ok = i == 0 or glob[i - 1] == "/"
            after = glob[i + 2] if i + 2 < n else ""
            if before_ok and after == "/":
                out.append("(?:.*/)?")  # zero or more leading/inner folders
                i += 3
                continue
            if before_ok and after == "":
                out.append(".+")  # everything inside
                i += 2
                continue
            out.append("[^/]*")  # `a**b` behaves like `*`
            i += 2
            continue
        if c == "*":
            out.append("[^/]*")
        elif c == "?":
            out.append("[^/]")
        elif c == "[":
            cls = _class_to_regex(glob, i)
            if cls is None:
                out.append(re.escape(c))
            else:
                out.append(cls[0])
                i = cls[1]
                continue
        elif c == "\\" and i + 1 < n:
            out.append(re.escape(glob[i + 1]))
            i += 2
            continue
        else:
            out.append(re.escape(c))
        i += 1
    return "".join(out)


@dataclass(frozen=True)
class Rule:
    raw: str
    negated: bool
    dir_only: bool
    anchored: bool
    regex: re.Pattern[str]

    def matches(self, rel_parts: tuple[str, ...], is_dir: bool) -> bool:
        if self.dir_only and not is_dir:
            return False
        subject = "/".join(rel_parts) if self.anchored else rel_parts[-1]
        return self.regex.fullmatch(subject) is not None


def parse_rule(line: str) -> Rule | None:
    text = line.rstrip("\n")
    stripped = text.rstrip(" ")
    if stripped.endswith("\\") and len(text) > len(stripped):  # escaped trailing space
        stripped += " "
    text = stripped
    if not text.strip() or text.startswith("#"):
        return None
    negated = False
    if text.startswith("!"):
        negated = True
        text = text[1:]
    elif text.startswith("\\!") or text.startswith("\\#"):
        text = text[1:]
    dir_only = text.endswith("/")
    text = text.rstrip("/")
    anchored = "/" in text
    text = text.lstrip("/")
    if not text:
        return None
    return Rule(line, negated, dir_only, anchored, re.compile(glob_to_regex(text), re.IGNORECASE | re.DOTALL))


class IgnoreSet:
    """An ordered list of gitignore patterns."""

    def __init__(self, patterns: Iterable[str]):
        self.rules: list[Rule] = [r for r in (parse_rule(p) for p in patterns) if r is not None]

    def _decide(self, parts: tuple[str, ...], is_dir: bool) -> bool:
        state = False
        for rule in self.rules:
            if rule.matches(parts, is_dir):
                state = not rule.negated
        return state

    def is_ignored(self, rel: str, *, is_dir: bool = False) -> bool:
        """True when ``rel`` (root-relative, POSIX) or one of its parent folders is ignored."""
        parts = tuple(p for p in rel.replace("\\", "/").split("/") if p and p != ".")
        for i in range(len(parts)):
            if self._decide(parts[: i + 1], i < len(parts) - 1 or is_dir):
                return True
        return False

    def matching_rule(self, rel: str, *, is_dir: bool = False) -> str | None:
        """The pattern that excludes ``rel`` (for messages), or None."""
        parts = tuple(p for p in rel.replace("\\", "/").split("/") if p and p != ".")
        for i in range(len(parts)):
            sub = parts[: i + 1]
            node_dir = i < len(parts) - 1 or is_dir
            state, hit = False, None
            for rule in self.rules:
                if rule.matches(sub, node_dir):
                    state, hit = not rule.negated, rule.raw
            if state:
                return hit
        return None
