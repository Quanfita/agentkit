"""DefaultRuntime（V1 极简实现，V2 接线 ToolExecutor）。"""
from __future__ import annotations

from ..context.engine import ContextEngine  # V3：兼容重导出（实现已移到 agentkit.context.engine）
from ..executor.builtin import ParallelExecutor
from ..kernel.events import EventBus
from ..kernel.protocols import (
    Memory,
    Model,
    PreparedInput,
)
from ..kernel.state import RunContext
from ..kernel.types import (
    Action,
    Final,
    MemoryInput,
    Message,
    ToolCalls,
    ToolResult,
)


class DefaultRuntime:
    """把 model / toolbox / context / executor / memory 适配成 Runtime。

    注意：这些是构造参数，不是公开属性。
    Loop 看不到它们。

    `executor` 缺省是 `ParallelExecutor(toolbox)`；
    Toolbox 的生命周期归 Runtime，Executor 只是借用它。
    """

    def __init__(
        self,
        model: Model,
        toolbox,                                  # Toolbox
        context: ContextEngine,
        executor=None,                            # ToolExecutor
        memory: Memory | None = None,
        events: EventBus | None = None,
    ) -> None:
        self._model = model
        self._toolbox = toolbox
        self._context = context
        self._memory = memory
        self.events = events or EventBus()
        self._executor = executor or ParallelExecutor(toolbox)

    async def prepare(self, ctx: RunContext) -> PreparedInput:
        messages = await self._context.build(ctx)
        tools = await self._toolbox.specs()
        return PreparedInput(messages=messages, tools=tools)

    async def reason(self, ctx: RunContext, inp: PreparedInput) -> Action:
        return await self._model.generate(inp.messages, inp.tools)

    async def act(self, ctx: RunContext, action: ToolCalls) -> list[ToolResult]:
        # 二次确认 stop（Loop 已检查过一次；这里是批内检查入口）
        if ctx.stop:
            return []
        await self.events.emit("executor.before", ctx=ctx, action=action)
        results = await self._executor.execute(ctx, action)
        await self.events.emit("executor.after", ctx=ctx, results=results)
        return results

    async def observe(
        self, ctx: RunContext, action: Action, results: list[ToolResult] | None,
    ) -> None:
        if isinstance(action, Final):
            msg = Message("assistant", action.content)
            ctx.messages.append(msg)
            ctx.last_assistant = msg
            return

        # P0-A：与 tool_calls 同时出现的文本必须写进 assistant message
        assistant_msg = Message("assistant", action.content, tool_calls=list(action.calls))
        ctx.messages.append(assistant_msg)
        ctx.last_assistant = assistant_msg

        # strict=False 是刻意的：批内 stop 会让 results 短于 calls（§3.8），
        # 已开始的 call 才写回消息。
        for call, res in zip(action.calls, results or (), strict=False):
            text = f"[tool_error] {res.content}" if res.error else res.content
            ctx.messages.append(Message("tool", text, tool_call_id=call.id))

    async def finish(self, ctx: RunContext) -> None:
        if self._memory is not None:
            await self._memory.remember(MemoryInput(
                task=ctx.task,
                messages=ctx.messages,
                result=ctx.result,
            ))

    async def close(self) -> None:
        await self._executor.close()
        await self._toolbox.close()
