"""Toolbox —— Tool Discovery + Lookup。

执行策略（并行/重试/超时/权限/沙箱）不在这一层：见 `agentkit/executor/`。
Toolbox 只回答一个问题：「有哪些工具，怎么拿到它」。
"""
from __future__ import annotations

from .executor.builtin import ParallelExecutor
from .kernel.protocols import Tool, ToolProvider
from .kernel.state import RunContext
from .kernel.types import ToolCalls, ToolResult, ToolSpec


class Toolbox:
    """Tool Discovery + Lookup。

    - `specs()` / `lookup()` 是 Runtime 与 Executor 用的两个入口；
    - `execute()` 是 V1 兼容入口，已 deprecated（见方法 docstring）。
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

    async def lookup(self, name: str) -> Tool | None:
        """单点 Lookup —— ToolExecutor 的唯一取工具入口。"""
        return (await self._ensure()).get(name)

    async def execute(self, action: ToolCalls) -> list[ToolResult]:
        """[Deprecated] V1 兼容入口：委托 `ParallelExecutor`。

        等价于 `ParallelExecutor(self).execute(<无 stop 的 ctx>, action)`。
        新代码请实现/选择 `ToolExecutor` 交给 `DefaultRuntime`；
        本方法将在 V3 / V4 移除（不会在 V2 移除）。
        """
        executor = ParallelExecutor(self)
        return await executor.execute(RunContext(task=""), action)

    async def close(self) -> None:
        for p in self._providers:
            try:
                await p.close()
            except Exception:
                pass
