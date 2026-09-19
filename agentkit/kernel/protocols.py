"""Kernel Protocols —— 全部扩展点的形状定义。

一切通过 Python Protocol 表达，不引入继承树。

生命周期所有权（V2.5 冻结）：

    Runtime
      ├── owns Model
      ├── owns Toolbox
      ├── owns ContextEngine
      └── owns ToolExecutor

    ToolExecutor
      ├── borrows Toolbox（只读使用，不负责关闭）
      └── owns 自身资源（HTTP client / sandbox / 子进程池）

    关闭顺序：
      Runtime.close()
        ├── executor.close()     ← 只关自身资源
        └── toolbox.close()      ← 关闭所有 Provider
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable

from .events import EventBus
from .state import RunContext
from .types import (
    Action, ContextItem, MemoryInput, MemoryItem,
    Message, ToolCalls, ToolResult, ToolSpec,
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
    """Tool 执行策略协议。

    生命周期契约：
      - Toolbox 归 Runtime 拥有；Executor 只借用，不负责关闭。
      - Executor.close() 只能关闭自己持有的资源。
      - Executor.close() 不得调用 Toolbox.close()。

    tool_call_id 契约：
      - 返回的每个 ToolResult.tool_call_id 必须等于输入 call.id。
      - 若 inner Tool 设置了错误值，Executor 负责覆盖。

    异常契约：
      - Tool.run() 内部异常 → ToolResult(error=True)，不冒泡。
      - Toolbox.lookup() 异常 → Executor infrastructure error，冒泡。
      - Executor 自身编程错误 → 冒泡。
      - asyncio.CancelledError → 穿透，不捕获，不转 ToolResult。
      - 单 call 超时 → ToolResult(error=True)。
      - 单 call 重试耗尽 → 保留最后一次 ToolResult(error=True)。

    execute() 是唯一公共执行入口。
    实现可自由选择内部是否使用 per-call 原语。
    """

    async def execute(
        self, ctx: RunContext, action: ToolCalls,
    ) -> list[ToolResult]: ...

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
