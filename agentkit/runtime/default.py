"""DefaultRuntime + ContextEngine（V1 极简实现）。"""
from __future__ import annotations

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

    拼装顺序：run 级 system（ctx.system）→ provider 产出的 system
    → 历史 messages（受 history_limit 约束）→ provider 产出的其他 role。

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
    """把 model / toolbox / context / memory 适配成 Runtime。

    注意：这些是构造参数，不是公开属性。
    Loop 看不到它们。
    """

    def __init__(
        self,
        model: Model,
        toolbox,                                  # Toolbox
        context: ContextEngine,
        memory: Memory | None = None,
        events: EventBus | None = None,
    ) -> None:
        self._model = model
        self._toolbox = toolbox
        self._context = context
        self._memory = memory
        self.events = events or EventBus()

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
        return await self._toolbox.execute(action)

    async def observe(
        self, ctx: RunContext, action: Action, results: list[ToolResult] | None,
    ) -> None:
        if isinstance(action, Final):
            msg = Message("assistant", action.content)
            ctx.messages.append(msg)
            ctx.last_assistant = msg
            return

        assistant_msg = Message("assistant", "", tool_calls=list(action.calls))
        ctx.messages.append(assistant_msg)
        ctx.last_assistant = assistant_msg

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
        await self._toolbox.close()
