"""Small JSON-Schema subset validator for tool arguments.

Spark's built-in tool schemas are intentionally shallow (object/properties/
required/type/items/enum/minimum), so we validate that contract without
pulling in a second schema engine. Third-party MCP/plugin schemas may use
constructs we do not model; their handlers still validate their own payloads.
"""

from __future__ import annotations

import json
from typing import Any


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _matches_type(value: Any, expected: str) -> bool:
    if expected == "object":
        return isinstance(value, dict)
    if expected == "array":
        return isinstance(value, list)
    if expected == "string":
        return isinstance(value, str)
    if expected == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if expected == "number":
        return _is_number(value)
    if expected == "boolean":
        return isinstance(value, bool)
    if expected == "null":
        return value is None
    return True


def _type_error(value: Any, expected: Any, path: str) -> str | None:
    types = expected if isinstance(expected, list) else [expected]
    if any(_matches_type(value, str(item)) for item in types):
        return None
    return f"{path}: expected {' or '.join(str(t) for t in types)}, got {type(value).__name__}"


def _enum_error(value: Any, enum: Any, path: str) -> str | None:
    if not isinstance(enum, list) or value in enum:
        return None
    return f"{path}: must be one of {enum!r}"


def _object_error(value: dict[str, Any], schema: dict[str, Any], path: str) -> str | None:
    properties = schema.get("properties") or {}
    required = schema.get("required") or []
    if not isinstance(properties, dict):
        properties = {}
    for key in required if isinstance(required, list) else []:
        if key not in value:
            return f"{path}.{key}: required argument is missing"
    if schema.get("additionalProperties") is False:
        for key in value:
            if key not in properties:
                return f"{path}.{key}: unknown argument (not in tool schema)"
    for key, item in value.items():
        child = properties.get(key)
        if not isinstance(child, dict):
            continue
        error = validate_arguments(item, child, f"{path}.{key}")
        if error is not None:
            return error
    return None


def validate_arguments(
    value: Any,
    schema: Any,
    path: str = "arguments",
) -> str | None:
    """Return a human-readable first error, or ``None`` when value matches."""
    if not isinstance(schema, dict):
        return None
    expected = schema.get("type")
    if expected is not None:
        error = _type_error(value, expected, path)
        if error is not None:
            return error
    enum_error = _enum_error(value, schema.get("enum"), path)
    if enum_error is not None:
        return enum_error
    if isinstance(value, dict):
        error = _object_error(value, schema, path)
        if error is not None:
            return error
    elif isinstance(value, list):
        items = schema.get("items")
        min_items = schema.get("minItems")
        max_items = schema.get("maxItems")
        if isinstance(min_items, int) and len(value) < min_items:
            return f"{path}: expected at least {min_items} items"
        if isinstance(max_items, int) and len(value) > max_items:
            return f"{path}: expected at most {max_items} items"
        if isinstance(items, dict):
            for index, item in enumerate(value):
                error = validate_arguments(item, items, f"{path}[{index}]")
                if error is not None:
                    return error
    elif isinstance(value, str):
        min_length = schema.get("minLength")
        max_length = schema.get("maxLength")
        if isinstance(min_length, int) and len(value) < min_length:
            return f"{path}: expected at least {min_length} characters"
        if isinstance(max_length, int) and len(value) > max_length:
            return f"{path}: expected at most {max_length} characters"
    elif _is_number(value):
        minimum = schema.get("minimum")
        maximum = schema.get("maximum")
        if _is_number(minimum) and value < minimum:
            return f"{path}: must be >= {minimum}"
        if _is_number(maximum) and value > maximum:
            return f"{path}: must be <= {maximum}"
    return None


def argument_size_error(arguments: dict[str, Any], *, max_bytes: int = 2 * 1024 * 1024) -> str | None:
    """Reject pathological payloads before they reach approval or a tool.

    Large ``write_file`` bodies are legitimate, so the ceiling is deliberately
    generous; this only catches runaway repetitions/loops from small models.
    """
    try:
        encoded = json.dumps(arguments, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        return "arguments: cannot serialize to JSON"
    if len(encoded.encode("utf-8")) > max_bytes:
        return f"arguments: payload exceeds {max_bytes // 1024} KiB"
    return None
