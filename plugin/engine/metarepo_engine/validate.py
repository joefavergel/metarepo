"""A small JSON-Schema-subset validator (002 plan section 1; avoids a ``jsonschema`` dependency).

Supported keywords: ``type`` (string or list), ``enum``, ``const``, ``pattern``, ``minLength``,
``minimum``, ``maximum``, ``minItems``, ``required``, ``properties``, ``items``,
``additionalProperties`` (``true`` or a sub-schema; absent means "unknown keys are allowed but reported
as warnings", per 002/R14), ``description`` and ``$comment`` (ignored).

Paths in findings use dotted notation (``intake.confirm``, ``repos.0.name``).
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

_TYPE_NAMES = {
    "string": "a string",
    "integer": "an integer",
    "number": "a number",
    "boolean": "true or false",
    "array": "a list",
    "object": "a mapping",
    "null": "null",
}


@dataclass
class Findings:
    errors: list[tuple[str, str]] = field(default_factory=list)  # (path, message)
    warnings: list[tuple[str, str]] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors


def _type_ok(value: Any, name: str) -> bool:
    if name == "string":
        return isinstance(value, str)
    if name == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if name == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if name == "boolean":
        return isinstance(value, bool)
    if name == "array":
        return isinstance(value, list)
    if name == "object":
        return isinstance(value, dict)
    if name == "null":
        return value is None
    raise ValueError(f"unsupported schema type: {name}")


def _describe(types: list[str]) -> str:
    return " or ".join(_TYPE_NAMES[t] for t in types)


def _join(path: str, key: Any) -> str:
    return f"{path}.{key}" if path else str(key)


def validate(instance: Any, schema: dict[str, Any]) -> Findings:
    findings = Findings()
    _check(instance, schema, "", findings)
    return findings


def _check(value: Any, schema: dict[str, Any], path: str, out: Findings) -> None:
    where = path or "(root)"
    if "type" in schema:
        types = schema["type"] if isinstance(schema["type"], list) else [schema["type"]]
        if not any(_type_ok(value, t) for t in types):
            hint = ""
            if "string" in types and isinstance(value, bool):
                hint = " (YAML reads unquoted yes/no/on/off as booleans; quote the value)"
            out.errors.append((where, f"must be {_describe(types)}, got {type(value).__name__}{hint}"))
            return
    if "const" in schema and value != schema["const"]:
        out.errors.append((where, f"must be {schema['const']!r}"))
    if "enum" in schema and value not in schema["enum"]:
        out.errors.append((where, "must be one of: " + ", ".join(repr(v) for v in schema["enum"])))
    if isinstance(value, str):
        if "minLength" in schema and len(value) < schema["minLength"]:
            out.errors.append((where, f"must not be shorter than {schema['minLength']} character(s)"))
        if "pattern" in schema and not re.search(schema["pattern"], value):
            out.errors.append((where, f"does not match the required pattern {schema['pattern']!r}"))
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if "minimum" in schema and value < schema["minimum"]:
            out.errors.append((where, f"must be >= {schema['minimum']}"))
        if "maximum" in schema and value > schema["maximum"]:
            out.errors.append((where, f"must be <= {schema['maximum']}"))
    if isinstance(value, list):
        if "minItems" in schema and len(value) < schema["minItems"]:
            out.errors.append((where, f"must contain at least {schema['minItems']} item(s)"))
        if "items" in schema:
            for i, item in enumerate(value):
                _check(item, schema["items"], _join(path, i), out)
    if isinstance(value, dict):
        for req in schema.get("required", []):
            if req not in value:
                out.errors.append((_join(path, req), "is required"))
        props = schema.get("properties", {})
        extra = schema.get("additionalProperties")
        for key, item in value.items():
            if key in props:
                _check(item, props[key], _join(path, key), out)
            elif isinstance(extra, dict):
                _check(item, extra, _join(path, key), out)
            elif extra is True:
                continue
            elif "properties" in schema:
                out.warnings.append((_join(path, key), "unknown key (kept as is)"))
