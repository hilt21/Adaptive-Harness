"""Versioned JSON Schema contracts shipped with Adaptive Harness."""

from __future__ import annotations

import json
from importlib.resources import files
from typing import TYPE_CHECKING, Any, cast

from jsonschema import Draft202012Validator
from referencing import Registry, Resource

if TYPE_CHECKING:
    from adaptive_harness.core.gateway import Capability

_SCHEMA_FILES = {
    "capabilities": "capabilities-1.0.schema.json",
    "config": "config-1.0.schema.json",
    "installation": "installation-2.0.schema.json",
    "module-manifest": "module-manifest-1.0.schema.json",
    "modules-lock": "modules-lock-1.0.schema.json",
    "task-envelope": "task-envelope-1.0.schema.json",
    "task-record": "task-record-1.0.schema.json",
}


def load_schema(name: str) -> dict[str, Any]:
    """Load one published schema by its stable logical name."""
    try:
        filename = _SCHEMA_FILES[name]
    except KeyError as error:
        raise ValueError(f"unknown schema: {name}") from error
    schema_path = files(__package__).joinpath(filename)
    value = json.loads(schema_path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"schema root must be an object: {name}")
    return cast(dict[str, Any], value)


def validator_for(name: str) -> Draft202012Validator:
    """Build a validator with all packaged schema references registered."""
    schemas = [load_schema(schema_name) for schema_name in _SCHEMA_FILES]
    registry = Registry()
    for schema in schemas:
        registry = registry.with_resource(
            schema["$id"], Resource.from_contents(schema)
        )
    return Draft202012Validator(load_schema(name), registry=registry)


def load_capabilities(document: dict[str, Any]) -> tuple[Capability, ...]:
    """Validate and load a canonical capabilities document."""
    from adaptive_harness.core.gateway import Capability

    validator_for("capabilities").validate(document)
    raw_capabilities = cast(list[dict[str, Any]], document["capabilities"])
    capabilities = tuple(
        Capability.from_dict(item) for item in raw_capabilities
    )
    if len({item.id for item in capabilities}) != len(capabilities):
        raise ValueError("capability ids must be unique")
    return capabilities


__all__ = ["load_capabilities", "load_schema", "validator_for"]
