"""JSON schemas of the agent's tool arguments, shaped for models."""

from typing import Any

from pydantic import BaseModel


def llm_schema(model: type[BaseModel]) -> dict[str, Any]:
    """The model's JSON schema with references inlined and titles dropped:
    some constrained decoders handle neither ``$ref`` nor wasted tokens well."""
    schema = model.model_json_schema()
    defs = schema.pop("$defs", {})
    return _strip(_inline(schema, defs))


def _inline(node: Any, defs: dict[str, Any]) -> Any:
    if isinstance(node, dict):
        if "$ref" in node:
            return _inline(defs[node["$ref"].rsplit("/", 1)[-1]], defs)
        return {k: _inline(v, defs) for k, v in node.items()}
    if isinstance(node, list):
        return [_inline(v, defs) for v in node]
    return node


def _strip(node: Any) -> Any:
    if isinstance(node, list):
        return [_strip(v) for v in node]
    if not isinstance(node, dict):
        return node
    out: dict[str, Any] = {}
    for key, value in node.items():
        if key in ("title", "default"):
            continue
        if key == "properties" and isinstance(value, dict):
            # Property *names* may be "title"; only schema keywords are dropped.
            out[key] = {name: _strip(sub) for name, sub in value.items()}
        else:
            out[key] = _strip(value)
    return out
