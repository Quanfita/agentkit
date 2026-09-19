"""从函数签名生成 JSON Schema。"""
from __future__ import annotations

import inspect
from collections.abc import Callable
from typing import Any

_JSON_TYPE = {
    str: "string", int: "integer", float: "number",
    bool: "boolean", list: "array", dict: "object",
}
_STR_TYPE = {
    "str": "string", "int": "integer", "float": "number",
    "bool": "boolean", "list": "array", "dict": "object",
}


def _type_of(annotation: Any) -> str:
    if annotation in _JSON_TYPE:
        return _JSON_TYPE[annotation]
    if isinstance(annotation, str):
        # PEP 563 下 `a: int` 与 `a: "int"` 分别存成 "int" 与 '"int"'
        return _STR_TYPE.get(annotation.strip().strip("'\""), "string")
    return "string"


def schema_from_signature(fn: Callable) -> dict[str, Any]:
    props: dict[str, Any] = {}
    required: list[str] = []
    for name, p in inspect.signature(fn).parameters.items():
        if name in ("self", "cls"):
            continue
        props[name] = {"type": _type_of(p.annotation)}
        if p.default is inspect.Parameter.empty:
            required.append(name)
    schema: dict[str, Any] = {"type": "object", "properties": props}
    if required:
        schema["required"] = required
    return schema
