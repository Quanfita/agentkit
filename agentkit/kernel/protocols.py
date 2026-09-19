"""Kernel Protocols —— 全部扩展点的形状定义。

一切通过 Python Protocol 表达，不引入继承树。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable

from .events import EventBus
from .state import RunContext
from .types import (
    Action, ContextItem, MemoryInput, MemoryItem,
    Message, ToolCall, ToolCalls, ToolResult, ToolSpec,
)


@dataclass(slots=True)
class PreparedInput:
    """Loop 交给模型的一切。"""
    messages: list[Message]
    tools: list[ToolSpec]


@runtime_checkable
class Model(Protocol):
    async def generate(
        self, messages: list[Message], tools: list[ToolSpec],
    ) -> Action: ...


@runtime_checkable
class Tool(Protocol):
    spec: ToolSpec
    async def run(self, arguments: dict[str, Any]) -> ToolResult: ...


@runtime_checkable
class ToolProvider(Protocol):
    """本地工具集 / MCP session / Skill 包 —— 全部长这样。"""
    async def tools(self) -> list[Tool]: ...
    async def close(self) -> None: ...


@runtime_checkable
class ToolExecutor(Protocol):
    """执行策略的唯一边界（V2 新增）。

    契约：
      - `execute()` 的返回与 `action.calls` **顺序一一对应**；
      - `execute_one()` 是 Retry / Timeout / Permission / Sandbox 的必要原语
        （没有它，retry 只能重跑整个 batch，会重复执行已成功的副作用工具）；
      - `close()` 只关闭 Executor 自己持有的资源，
        **绝不关闭 Toolbox** —— Toolbox 的生命周期归 Runtime。
    """
    async def execute(
        self, ctx: RunContext, action: ToolCalls,
    ) -> list[ToolResult]: ...

    async def execute_one(
        self, ctx: RunContext, call: ToolCall,
    ) -> ToolResult: ...

    async def close(self) -> None: ...


@runtime_checkable
class Memory(Protocol):
    """只有两个方法。

    multi-store / router / dedup / summary 都是 Harness 层的事。
    """
    async def recall(self, task: str) -> list[MemoryItem]: ...
    async def remember(self, run: MemoryInput) -> None: ...


@runtime_checkable
class ContextProvider(Protocol):
    async def provide(self, ctx: RunContext) -> list[ContextItem]: ...


@runtime_checkable
class Runtime(Protocol):
    """Loop 眼中世界的全部。

    Runtime 是适配器，不是容器：
    它不暴露 model / toolbox / memory / context 任何一个字段。
    """
    events: EventBus

    async def prepare(self, ctx: RunContext) -> PreparedInput: ...
    async def reason(self, ctx: RunContext, inp: PreparedInput) -> Action: ...
    async def act(self, ctx: RunContext, action: ToolCalls) -> list[ToolResult]: ...
    async def observe(
        self, ctx: RunContext, action: Action, results: list[ToolResult] | None,
    ) -> None: ...
    async def finish(self, ctx: RunContext) -> None: ...
    async def close(self) -> None: ...
