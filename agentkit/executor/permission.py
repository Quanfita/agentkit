"""PermissionExecutor + 内置 Policy（V3 §6.3）。

权限拒绝是 **Executor policy failure，不是 tool failure**：

    ToolResult(
        tool_call_id=call.id,
        content=f"[blocked by policy] {call.name}",
        error=True,                                 # 模型视角：这次调用没成功
        metadata={"blocked": True, "reason": "permission"},   # 人类视角：策略拦截
    )

- 从**模型视角**：`error=True`，模型应换一条路；
- 从**人类视角**：不是工具崩溃，是策略拦截，用 `metadata.blocked` 区分；
- **V2.5 契约不变**：仍是 ToolResult，不引入新的异常类型
  （V4 再考虑是否引入更精细的 `ExecutorDecision`）。

Policy 通过**构造注入**，不通过 Executor 读 ctx 里的隐式状态。

生命周期：与 V2.5 其余装饰器一致 —— **借用** inner，不拥有它。
"""
from __future__ import annotations

import inspect
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING

from ..kernel.state import RunContext
from ..kernel.types import ToolCall, ToolCalls, ToolResult
from .builtin import execute_one_of, execute_serial

if TYPE_CHECKING:                      # 只用于注解：实现不 import 它们
    from ..kernel.protocols import ToolExecutor

# 询问用户的回调：同步或异步都可以，返回可判真值（True / "y" / …）。
PromptResult = bool | str | Awaitable[bool | str]
PromptFn = Callable[[ToolCall, RunContext], PromptResult]

BLOCKED_REASON = "permission"

# 字符串返回值里的"拒绝"写法：`"n"` 在 Python 里是真值，
# 权限门禁不能把「用户说了 n」放行，所以字符串单独判一次。
_DENIALS = frozenset({"", "n", "no", "false", "0"})


def _verdict_to_bool(verdict: object) -> bool:
    if isinstance(verdict, str):
        return verdict.strip().lower() not in _DENIALS
    return bool(verdict)


def blocked_result(call: ToolCall) -> ToolResult:
    """策略拒绝的**唯一**返回形状（V3 冻结）。"""
    return ToolResult(
        tool_call_id=call.id,
        content=f"[blocked by policy] {call.name}",
        error=True,
        metadata={"blocked": True, "reason": BLOCKED_REASON},
    )


class AllowListPolicy:
    """只允许 `names` 中的工具；其余一律拒绝。"""

    def __init__(self, names: set[str] | frozenset[str] | list[str]) -> None:
        self.names = frozenset(names)

    async def allow(self, call: ToolCall, ctx: RunContext) -> bool:
        return call.name in self.names


class DenyListPolicy:
    """拒绝 `names` 中的工具；其余一律允许。"""

    def __init__(self, names: set[str] | frozenset[str] | list[str]) -> None:
        self.names = frozenset(names)

    async def allow(self, call: ToolCall, ctx: RunContext) -> bool:
        return call.name not in self.names


class InteractivePolicy:
    """询问用户（CLI 场景）：`prompt_fn(call, ctx)` 的返回值决定放行。

    `prompt_fn` 可以是同步函数，也可以返回 awaitable（`input()` 包装成
    async 时不必把 executor 改成线程池）。

    返回值判读：`bool` 按真值；字符串另判 —— `""` / `"n"` / `"no"` /
    `"false"` / `"0"`（大小写与首尾空白无关）视为拒绝，其余字符串视为允许。
    直接 `bool("n")` 会是 True，权限门禁不能这样放行。
    """

    def __init__(self, prompt_fn: PromptFn) -> None:
        self.prompt_fn = prompt_fn

    async def allow(self, call: ToolCall, ctx: RunContext) -> bool:
        verdict = self.prompt_fn(call, ctx)
        if inspect.isawaitable(verdict):
            verdict = await verdict
        return _verdict_to_bool(verdict)


class PermissionExecutor:
    """执行前的权限门禁。

    形态与 V2.5 其余装饰器一致：`execute()` 遍历 + `_execute_one()` 承载
    per-call 策略；被放行的 call 用**公有 API** `execute_one_of()` 交给 inner。
    """

    def __init__(self, inner: ToolExecutor, policy) -> None:
        self.inner = inner
        self.policy = policy

    async def execute(self, ctx: RunContext, action: ToolCalls) -> list[ToolResult]:
        return await execute_serial(self._execute_one, ctx, action)

    async def _execute_one(self, ctx: RunContext, call: ToolCall) -> ToolResult | None:
        if not await self.policy.allow(call, ctx):      # policy 异常 → 冒泡
            return blocked_result(call)
        return await execute_one_of(self.inner, ctx, call)

    async def close(self) -> None:
        await self.inner.close()
