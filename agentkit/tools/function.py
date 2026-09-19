"""FunctionTool + @tool —— 把普通函数变成 Tool。"""
from __future__ import annotations

import inspect
import json
from typing import Any

from ..kernel.types import ToolResult, ToolSpec
from .schema import schema_from_signature


class FunctionTool:
    def __init__(self, fn, *, name=None, description=None, parameters=None):
        self.fn = fn
        self.spec = ToolSpec(
            name=name or fn.__name__,
            description=description or (inspect.getdoc(fn) or "").strip(),
            parameters=parameters or schema_from_signature(fn),
        )

    async def run(self, arguments: dict[str, Any]) -> ToolResult:
        try:
            r = self.fn(**arguments)
            if inspect.isawaitable(r):
                r = await r
        except Exception as e:
            return ToolResult(f"{type(e).__name__}: {e}", error=True)

        if isinstance(r, ToolResult):
            return r
        if isinstance(r, str):
            return ToolResult(r)
        return ToolResult(json.dumps(r, ensure_ascii=False, default=str))


def tool(fn=None, *, name=None, description=None, parameters=None):
    def wrap(f):
        return FunctionTool(f, name=name, description=description, parameters=parameters)
    return wrap(fn) if fn else wrap
