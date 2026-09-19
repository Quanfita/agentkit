"""第三方 `PermissionPolicy` 实现 —— 唯一 import 来源是 `agentkit.api`。

这条 Protocol 故意把 `RunContext` 放在**显式参数**上：策略需要 Run 状态时，
从参数拿，而不是从环境变量、单例或 Executor 的隐式状态里拿。
这份实现演示了它的一个真实用法 —— stop 一旦被请求，就不该再批准新的副作用。
"""
from __future__ import annotations

from collections.abc import Iterable

from agentkit.api import RunContext, ToolCall


class FakeThirdPartyPermissionPolicy:
    """白名单策略：只批准 `allowed` 里列出的工具，且 Run 未请求停止。

    拒绝时只返回 `False` —— 把拒绝翻成 `error=True` 的 `ToolResult`
    是 `PermissionExecutor` 的责任，Policy 不构造 ToolResult。
    """

    def __init__(self, allowed: Iterable[str]) -> None:
        self.allowed = frozenset(allowed)
        self.denied: list[str] = []
        self.checked = 0

    async def allow(self, call: ToolCall, ctx: RunContext) -> bool:
        self.checked += 1
        ok = call.name in self.allowed and not ctx.stop
        if not ok:
            self.denied.append(call.name)
        return ok
