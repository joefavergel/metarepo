"""``config validate``: validate project.yaml and _system/state.json (002/R14, R15, AC1)."""
from __future__ import annotations

import argparse

from ..command import Command, Context
from ..config import validate_project, validate_state
from ..jsonout import EXIT_COMPAT, EngineError, Result, error


def run(ctx: Context, args: argparse.Namespace) -> Result:
    project = ctx.require_project()
    findings: list[dict[str, str]] = []
    result = Result()

    def add(file: str, path: str, message: str, severity: str) -> None:
        findings.append({"file": file, "path": path, "message": message, "severity": severity})
        entry = error("schema_invalid" if severity == "error" else "unknown_key", f"{file}: {path}: {message}", file)
        (result.errors if severity == "error" else result.warnings).append(entry)

    config = project.config()  # unreadable or non-YAML files stop here with exit 4
    cfg_findings = validate_project(config)
    for path, message in cfg_findings.errors:
        add("project.yaml", path, message, "error")
    for path, message in cfg_findings.warnings:
        add("project.yaml", path, message, "warning")
    try:
        state_findings = validate_state(project.state())
    except EngineError as exc:
        add("_system/state.json", "(root)", exc.message, "error")
    else:
        for path, message in state_findings.errors:
            add("_system/state.json", path, message, "error")
        for path, message in state_findings.warnings:
            add("_system/state.json", path, message, "warning")
    result.data = {"valid": not result.errors, "findings": findings}
    if result.errors:
        result.exit_code = EXIT_COMPAT
        result.text = "\n".join(f"{f['severity']}: {f['file']}: {f['path']}: {f['message']}" for f in findings)
    else:
        result.text = "project.yaml and _system/state.json are valid" + (
            f" ({len(findings)} warning(s))" if findings else "")
    return result


COMMAND = Command(
    name="config validate",
    help="Validate project.yaml (and _system/state.json) against their schemas",
    run=run,
    gate=False,
    owner="002",
)
