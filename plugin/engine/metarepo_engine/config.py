"""project.yaml loading (002/R14). Schema validation is added by ``validate_config`` below."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from .jsonout import EXIT_COMPAT, EXIT_DEPENDENCY, EngineError

INSTALL_HINT = (
    "PyYAML is required. Install it with: python3 -m pip install pyyaml "
    "(Windows: py -3 -m pip install pyyaml). The toolkit never installs software on its own."
)


def yaml_module():
    try:
        import yaml  # type: ignore
    except ImportError as exc:  # pragma: no cover - exercised via dispatcher test with a blocked import
        raise EngineError("missing_dependency", INSTALL_HINT, EXIT_DEPENDENCY) from exc
    return yaml


def load_project_yaml(path: Path) -> dict[str, Any]:
    yaml = yaml_module()
    try:
        text = Path(path).read_text(encoding="utf-8")
    except OSError as exc:
        raise EngineError("config_unreadable", f"cannot read {path.name}: {exc}", EXIT_COMPAT, path.name) from exc
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise EngineError("config_invalid", f"{path.name} is not valid YAML: {exc}", EXIT_COMPAT, path.name) from exc
    if not isinstance(data, dict):
        raise EngineError("config_invalid", f"{path.name} must contain a mapping", EXIT_COMPAT, path.name)
    return data
