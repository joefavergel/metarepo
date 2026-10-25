"""External-command adapter configured in ``project.yaml -> extraction.commands`` (008/R2).

Entry shape: ``{name, media_types, command, mode: local|cloud, timeout_s}``. ``command`` is an argv list or a
string split like a POSIX shell (no shell is ever started). A ``{input}`` placeholder in any argument is
replaced by the file path; without one the file content is piped to stdin. The tool prints Markdown on
stdout. It runs in a scratch directory outside the synced tree, so tools that drop files in their working
directory never litter the metarepository.

This module never opens a connection. A command whose ``mode`` is ``cloud`` is only ever run when
``extraction.allow_cloud`` is true (selection enforces it, P11).
"""
from __future__ import annotations

import hashlib
import json
import os
import shlex
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any

from ..base import Adapter, AdapterError, ExtractOptions, ExtractResult, media_matches, normalize_body
from ...fsops import fs_path

PRIORITY = 100  # an explicitly configured tool outranks the built-in defaults


class CommandAdapter(Adapter):
    kind = "command"

    def __init__(self, spec: dict[str, Any]):
        self.spec = spec
        self.name = str(spec["name"])
        self.mode = "cloud" if spec.get("mode") == "cloud" else "local"
        self.media_patterns = tuple(str(m) for m in spec.get("media_types") or ())
        self.media_summary = self.media_patterns
        self.timeout_s = int(spec.get("timeout_s") or 300)
        raw = spec.get("command")
        self.argv = list(raw) if isinstance(raw, list) else shlex.split(str(raw or ""))
        self.install_hint = f"install or fix the command configured as extraction.commands[{self.name}]"

    def version(self) -> str:
        digest = hashlib.sha256(json.dumps(self.spec, sort_keys=True, default=str).encode("utf-8")).hexdigest()
        return f"config-{digest[:8]}"

    def available(self) -> bool:
        if not self.argv:
            return False
        exe = self.argv[0]
        return shutil.which(exe) is not None or (os.sep in exe and Path(exe).is_file())

    def handles(self, media_type: str) -> int:
        return PRIORITY if any(media_matches(p, media_type) for p in self.media_patterns) else 0

    def extract(self, path: Path, options: ExtractOptions) -> ExtractResult:
        placeholder = any("{input}" in arg for arg in self.argv)
        argv = [arg.replace("{input}", str(path)) for arg in self.argv]
        stdin = None if placeholder else open(fs_path(path), "rb")
        scratch = tempfile.mkdtemp(prefix="mr-extract-", dir=str(options.work_dir) if options.work_dir else None)
        try:
            proc = subprocess.run(argv, stdin=stdin, capture_output=True, cwd=scratch, check=False,
                                  timeout=self.timeout_s)
        except subprocess.TimeoutExpired:
            raise AdapterError(f"{self.name} timed out after {self.timeout_s} s") from None
        except OSError as exc:
            raise AdapterError(f"{self.name} could not be started: {exc}") from exc
        finally:
            if stdin is not None:
                stdin.close()
            shutil.rmtree(scratch, ignore_errors=True)
        if proc.returncode != 0:
            tail = proc.stderr.decode("utf-8", errors="replace").strip().replace("\n", " ")[-300:]
            raise AdapterError(f"{self.name} exited with status {proc.returncode}: {tail}")
        text = proc.stdout.decode("utf-8", errors="replace")
        return ExtractResult(body=normalize_body(text))


def from_config(entries: Any) -> list[CommandAdapter]:
    out = []
    for entry in entries or []:
        if isinstance(entry, dict) and entry.get("name") and entry.get("command") and entry.get("media_types"):
            out.append(CommandAdapter(entry))
    return out
