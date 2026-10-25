"""Shared base for adapters that wrap an optional third-party library (P13: detected, never installed).

The library is imported lazily inside ``extract`` through ``importlib.import_module``, so that merely
listing adapters or running an unrelated command never pays for (or depends on) a heavy import.
"""
from __future__ import annotations

import importlib
from typing import Any

from ..base import Adapter, AdapterError, dist_version, module_available


class OptionalAdapter(Adapter):
    kind = "optional"
    mode = "local"
    modules: tuple[str, ...] = ()  # importable names, first present wins
    dist: str = ""  # distribution name for importlib.metadata
    pip_name: str = ""

    @property
    def install_hint(self) -> str:  # type: ignore[override]
        return f"python3 -m pip install {self.pip_name or self.dist}"

    def available(self) -> bool:
        return any(module_available(m) for m in self.modules)

    def version(self) -> str:
        return dist_version(self.dist)

    def load(self, *candidates: str) -> Any:
        names = candidates or self.modules
        for name in names:
            if module_available(name):
                try:
                    return importlib.import_module(name)
                except Exception as exc:  # noqa: BLE001 - third-party import failures vary widely
                    raise AdapterError(f"cannot import {name}: {exc}") from exc
        raise AdapterError(f"{self.name} is not installed")
