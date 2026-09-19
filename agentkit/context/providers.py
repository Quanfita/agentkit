"""内置 ContextProvider。

MemoryItem → ContextItem 的转换在这里发生，不在 Memory 内部。
"""
from __future__ import annotations

import inspect

from ..kernel.state import RunContext
from ..kernel.types import ContextItem


class SystemPrompt:
    def __init__(self, text: str):
        self.text = text

    async def provide(self, ctx: RunContext) -> list[ContextItem]:
        return [ContextItem(self.text, role="system", source="system")]


class MemoryContext:
    """把 MemoryItem 转成 ContextItem。

    转换在这里发生，不在 Memory 内部。
    """

    def __init__(self, memory):
        self.memory = memory

    async def provide(self, ctx: RunContext) -> list[ContextItem]:
        items = await self.memory.recall(ctx.task)
        return [
            ContextItem(i.content, role="system", source="memory", kind=i.kind)
            for i in items
        ]


class CallableProvider:
    """任意同步/异步函数都能当 ContextProvider。"""

    def __init__(self, fn):
        self.fn = fn

    async def provide(self, ctx: RunContext) -> list[ContextItem]:
        r = self.fn(ctx)
        if inspect.isawaitable(r):
            r = await r
        return r or []
