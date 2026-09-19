"""DefaultRuntime + ContextEngine（V1 极简实现，V2 接线 ToolExecutor）。"""
from __future__ import annotations

from ..executor.builtin import ParallelExecutor
from ..kernel.events import EventBus
from ..kernel.protocols import (
    ContextProvider,
    Memory,
    Model,
    PreparedInput,
)
from ..kernel.state import RunContext
from ..kernel.types import (
    Action,
    ContextItem,
    Final,
    MemoryInput,
    Message,
    ToolCalls,
    ToolResult,
)


class ContextEngine:
    """V1 极简 Context 装配器。

    不做 budget，不做 compact，不做 token 计数。
    唯一扩展点是 providers 列表。

    拼装顺序（V2 冻结为 stable partition）：
      [ctx.system（调用级，可选）]
      [provider 产出的 system 消息（按 providers 顺序）]
      [history（ctx.messages 尾部 history_limit 条）]
      [provider 产出的非 system 消息（按 providers 顺序）]

    允许多个 system message：把它们转成 provider-native format 是
    Model Adapter 的责任，这里不假设任何厂商行为。

    当 Harness 需要预算/压缩时，走 EventBus：
      events.on("model.before", compact_hook)
        - 改 payload["inp"].messages  → 影响本次调用
        - 改 payload["ctx"].messages  → 影响后续 iteration
    """

    def __init__(
        self,
        providers: list[ContextProvider] | None = None,
        history_limit: int = 40,
    ) -> None:
        self.providers = list(providers or [])
        self.history_limit = history_limit

    def add(self, provider: ContextProvider) -> ContextEngine:
        self.providers.append(provider)
        return self

    async def build(self, ctx: RunContext) -> list[Message]:
        items: list[ContextItem] = []
        for p in self.providers:
            items.extend(await p.provide(ctx))

        system = [
            Message("system", i.content) for i in items if i.role == "system"
        ]
        if ctx.system:
            system.insert(0, Message("system", ctx.system))
        extra = [Message(i.role, i.content)
                 for i in items if i.role != "system"]
        history = ctx.messages[-self.history_limit:]
        return system + history + extra


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
