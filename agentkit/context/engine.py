"""ContextEngine（V1 极简装配器，V3 接上可选 `ContextTransform`）。

拼装顺序（V2 冻结为 stable partition，V3 不变）：

    [ctx.system（调用级，可选）]
    [provider 产出的 system 消息（按 providers 顺序）]
    [history（ctx.messages 尾部 history_limit 条）]
    [provider 产出的非 system 消息（按 providers 顺序）]

V3 新增的 `transform` 是最后一步，作用于**上面拼接完成的完整 messages**：

    messages = 拼接结果
    if transform is not None:
        messages = await transform.apply(messages)

`transform` 是 `agentkit.api.ContextTransform`（纯函数式 Message → Message），
ContextEngine 不解释它的策略，也不给它 `RunContext`。Kernel 不感知这一步。

允许多个 system message：把它们转成 provider-native format 是
Model Adapter 的责任，这里不假设任何厂商行为。

Harness 若需要事件驱动的预算/压缩，仍可走 EventBus：
  events.on("model.before", compact_hook)
    - 改 payload["inp"].messages  → 影响本次调用
    - 改 payload["ctx"].messages  → 影响后续 iteration
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from ..kernel.protocols import ContextProvider
from ..kernel.state import RunContext
from ..kernel.types import ContextItem, Message

if TYPE_CHECKING:
    from ..api.context import ContextTransform


class ContextEngine:
    """providers 顺序拼接 + 尾部 history + 可选 transform。

    providers 是唯一的上下文来源；budget / compact / dedupe 全部是外部的
    `ContextTransform` 实现，不是 ContextEngine 的内置逻辑。
    """

    def __init__(
        self,
        providers: list[ContextProvider] | None = None,
        history_limit: int = 40,
        transform: ContextTransform | None = None,
    ) -> None:
        self.providers = list(providers or [])
        self.history_limit = history_limit
        self.transform = transform

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
        messages = system + history + extra
        if self.transform is not None:
            messages = await self.transform.apply(messages)
        return messages
