"""Policy gate for intake (007/R9): scan text for ``policy.block_patterns`` and ``policy.block_terms``.

A finding names the rule and the location (line, column) and carries a *masked* excerpt: the matched value
is never echoed in full, so a report, a log line or a proposal file cannot leak it.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from .jsonout import EXIT_COMPAT, EngineError

MAX_FINDINGS_PER_RULE = 20


@dataclass(frozen=True)
class Finding:
    rule: str
    kind: str  # "pattern" | "term"
    line: int
    column: int
    masked: str

    @property
    def location(self) -> str:
        return f"line {self.line}, column {self.column}"

    def as_dict(self) -> dict[str, Any]:
        return {"rule": self.rule, "kind": self.kind, "location": self.location, "line": self.line,
                "column": self.column, "masked": self.masked}


def mask(value: str) -> str:
    """Never the whole value: short values are fully hidden, longer ones keep one character and the length."""
    value = value.strip()
    if len(value) <= 4:
        return "***"
    return f"{value[0]}***[{len(value)} chars]"


def compile_rules(policy: dict[str, Any] | None) -> tuple[list[tuple[str, re.Pattern[str]]], list[tuple[str, str]]]:
    policy = policy or {}
    patterns: list[tuple[str, re.Pattern[str]]] = []
    for i, rule in enumerate(policy.get("block_patterns") or []):
        name = str(rule.get("name") or f"block_patterns[{i}]")
        try:
            patterns.append((name, re.compile(rule["regex"], re.MULTILINE)))
        except (re.error, KeyError, TypeError) as exc:
            raise EngineError("policy_invalid", f"policy.block_patterns rule {name!r} is not a valid regular "
                              f"expression: {exc}", EXIT_COMPAT, "project.yaml") from exc
    terms = [(f"block_terms[{i}]", str(t).lower()) for i, t in enumerate(policy.get("block_terms") or []) if str(t)]
    return patterns, terms


def has_rules(policy: dict[str, Any] | None) -> bool:
    policy = policy or {}
    return bool(policy.get("block_patterns") or policy.get("block_terms"))


def scan_text(text: str, policy: dict[str, Any] | None) -> list[Finding]:
    patterns, terms = compile_rules(policy)
    findings: list[Finding] = []
    if not patterns and not terms:
        return findings
    line_starts = [0]
    for m in re.finditer(r"\n", text):
        line_starts.append(m.end())

    def where(offset: int) -> tuple[int, int]:
        lo, hi = 0, len(line_starts) - 1
        while lo < hi:
            mid = (lo + hi + 1) // 2
            if line_starts[mid] <= offset:
                lo = mid
            else:
                hi = mid - 1
        return lo + 1, offset - line_starts[lo] + 1

    for name, rx in patterns:
        for n, m in enumerate(rx.finditer(text)):
            if n >= MAX_FINDINGS_PER_RULE:
                break
            if m.end() == m.start():
                continue
            line, col = where(m.start())
            findings.append(Finding(name, "pattern", line, col, mask(m.group(0))))
    folded = text.lower()
    for name, term in terms:
        start, n = 0, 0
        while n < MAX_FINDINGS_PER_RULE:
            at = folded.find(term, start)
            if at < 0:
                break
            line, col = where(at)
            findings.append(Finding(name, "term", line, col, mask(text[at:at + len(term)])))
            start, n = at + len(term), n + 1
    findings.sort(key=lambda f: (f.line, f.column, f.rule))
    return findings
