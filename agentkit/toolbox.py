"""Toolbox —— Tool Discovery + Lookup。

执行策略（并行/重试/权限/沙箱）留在未来的 ToolExecutor。
V1 只提供默认 asyncio.gather 并发。
"""
from __future__ import annotations

import asyncio

from .kernel.protocols import Tool, ToolProvider
from .kernel.types import ToolCall, ToolCalls, ToolResult, ToolSpec


class Toolbox:
    """Tool Discovery + Lookup。

    执行策略（并行/重试/权限/沙箱）留在未来的 ToolExecutor。
    V1 只提供默认 asyncio.gather 并发。
    """

    def __init__(self, tools: list[Tool] | None = None) -> None:
        self._local: dict[str, Tool] = {t.spec.name: t for t in (tools or [])}
        self._providers: list[ToolProvider] = []
        self._index: dict[str, Tool] | None = None

    def register(self, tool: Tool) -> Toolbox:
        self._local[tool.spec.name] = tool
        self._index = None
        return self

    def add_provider(self, provider: ToolProvider) -> Toolbox:
        self._providers.append(provider)
        self._index = None
        return self

    async def _ensure(self) -> dict[str, Tool]:
        if self._index is None:
            idx = dict(self._local)
            for p in self._providers:
                for t in await p.tools():
                    if t.spec.name in idx:
                        raise ValueError(f"duplicate tool: {t.spec.name}")
                    idx[t.spec.name] = t
            self._index = idx
        return self._index

    async def refresh(self) -> None:
        """MCP server 工具列表变化后调用。"""
        self._index = None
        await self._ensure()

    async def specs(self) -> list[ToolSpec]:
        return [t.spec for t in (await self._ensure()).values()]

    async def execute(self, action: ToolCalls) -> list[ToolResult]:
        idx = await self._ensure()
        return await asyncio.gather(*(self._run(idx, c) for c in action.calls))

    async def _run(self, idx: dict[str, Tool], call: ToolCall) -> ToolResult:
        if call.name not in idx:
            return ToolResult(f"unknown tool: {call.name}", error=True)
        try:
            return await idx[call.name].run(call.arguments)
        except asyncio.CancelledError:
            # 取消必须穿透，不能吞
            raise
        except Exception as e:
            return ToolResult(f"{type(e).__name__}: {e}", error=True)

    async def close(self) -> None:
        for p in self._providers:
            try:
                await p.close()
            except Exception:
                pass
