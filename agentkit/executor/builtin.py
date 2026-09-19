"""ToolExecutor 的实现 + 装饰器组合（V2.5 收口版）。

三条线，永不交叉：

    Toolbox      = "有哪些工具？"   （Discovery + Lookup）
    ToolExecutor = "怎么执行工具？" （Execution Policy）
    Tool         = "工具具体做什么？"（Capability）

公共契约只有 `execute()` 与 `close()`（见 `kernel.protocols.ToolExecutor`）。
per-call 原语 `dispatch()` 是**内部 helper**，不属于 Protocol：
实现可以自由决定内部是否按 call 分解。

装饰器组合语义（由「装饰器重写 per-call 策略」自然导出）：

    Timeout(Retry(X))    单 call 的整个 retry 过程共享一个 timeout
    Retry(Timeout(X))    每次 retry 各自拥有独立 timeout
    Retry(Parallel(X))   batch 内并发，失败 call 独立重试
    Timeout(Parallel(X)) batch 内并发，单 call timeout

异常模型（V2.5 冻结）：
    Tool.run() 异常            → ToolResult(error=True)
    Tool.run() CancelledError  → 穿透
    Toolbox.lookup() 异常      → 冒泡（基础设施故障不伪装成 Observation）
    Executor 编程错误          → 冒泡
    单 call 超时               → ToolResult(error=True)
    单 call 重试耗尽           → 保留最后一次 ToolResult(error=True)

生命周期：Executor **借用** Toolbox，不拥有它。`close()` 只关自己的资源。
"""
from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING

from ..kernel.state import RunContext
from ..kernel.types import ToolCall, ToolCalls, ToolResult

if TYPE_CHECKING:                      # 只用于注解：实现不 import Toolbox
    from ..kernel.protocols import ToolExecutor
    from ..toolbox import Toolbox

RunOne = Callable[[RunContext, ToolCall], Awaitable["ToolResult | None"]]


async def dispatch(toolbox: Toolbox, call: ToolCall) -> ToolResult:
    """单一 ToolCall 的 dispatch 原语。内部 helper，不属于 Protocol。

    `tool_call_id` ownership（V2.5 冻结）：

        ToolCall.id ──► Executor ──► ToolResult.tool_call_id

    强制覆盖，不是「为空补齐」：Tool 误填的值会被静默覆盖，
    否则 `ToolCall A → ToolResult B` 这类错误会悄悄穿透。
    """
    tool = await toolbox.lookup(call.name)      # 异常冒泡：基础设施故障
    if tool is None:
        return ToolResult(
            tool_call_id=call.id, content=f"unknown tool: {call.name}", error=True,
        )
    try:
        result = await tool.run(call.arguments)
    except asyncio.CancelledError:
        raise                                   # 取消必须穿透，不能吞
    except Exception as e:
        return ToolResult(
            tool_call_id=call.id, content=f"{type(e).__name__}: {e}", error=True,
        )
    result.tool_call_id = call.id               # 强制覆盖，不做校验
    return result


async def execute_serial(
    run_one: RunOne, ctx: RunContext, action: ToolCalls,
) -> list[ToolResult]:
    """装饰器默认的批量遍历：每个 call 都走 executor 自己的 per-call 策略。

    批内 stop 语义从这里自然导出：`ctx.stop` 置位后，尚未开始的 call
    不再执行（返回结果是 `action.calls` 的前缀）。
    """
    results: list[ToolResult] = []
    for call in action.calls:
        if ctx.stop:
            break
        result = await run_one(ctx, call)
        if result is None:                      # inner 没有执行这个 call
            break
        results.append(result)
    return results


async def execute_one_of(
    executor: ToolExecutor, ctx: RunContext, call: ToolCall,
) -> ToolResult | None:
    """用**公有 API** 表达 per-call 原语：单 call 的 action，取第一个结果。

    返回 `None` 表示 inner 没有执行（`ctx.stop` 前缀语义）。
    """
    results = await executor.execute(ctx, ToolCalls([call]))
    return results[0] if results else None


class SequentialExecutor:
    """串行执行：批内按 call 粒度响应 `ctx.stop`。"""

    def __init__(self, toolbox: Toolbox) -> None:
        self.toolbox = toolbox             # borrow，不拥有

    async def execute(self, ctx: RunContext, action: ToolCalls) -> list[ToolResult]:
        return await execute_serial(self._execute_one, ctx, action)

    async def _execute_one(self, ctx: RunContext, call: ToolCall) -> ToolResult:
        return await dispatch(self.toolbox, call)

    async def close(self) -> None:
        return None


class ParallelExecutor:
    """默认执行器：`asyncio.gather` 并发。

    `ctx.stop` 语义（V2 冻结）：只保证「尚未开始的 batch 不启动」，
    不保证已经进入并发执行的 ToolCall 被取消；
    需要在批内响应 stop 的场景，请用 `SequentialExecutor`。
    """

    def __init__(self, toolbox: Toolbox) -> None:
        self.toolbox = toolbox

    async def execute(self, ctx: RunContext, action: ToolCalls) -> list[ToolResult]:
        if ctx.stop:
            return []
        return await asyncio.gather(
            *(self._execute_one(ctx, c) for c in action.calls)
        )

    async def _execute_one(self, ctx: RunContext, call: ToolCall) -> ToolResult:
        return await dispatch(self.toolbox, call)

    async def close(self) -> None:
        return None


class RetryExecutor:
    """per-call retry。`max_attempts` 表示总执行次数（含首次）。

    只对 `result.error is True` 重试：**不会**重试 `CancelledError`，
    也不会重试异常形态的基础设施失败。成功的 call 不会被重复执行
    —— 重试发生在单个 call 内部，不是重跑整个 batch。
    """

    def __init__(self, inner, max_attempts: int = 3, backoff: float = 0.5) -> None:
        self.inner = inner
        self.max_attempts = max_attempts
        self.backoff = backoff

    async def execute(self, ctx: RunContext, action: ToolCalls) -> list[ToolResult]:
        return await execute_serial(self._execute_one, ctx, action)

    async def _execute_one(self, ctx: RunContext, call: ToolCall) -> ToolResult | None:
        last: ToolResult | None = None
        for attempt in range(1, self.max_attempts + 1):
            last = await execute_one_of(self.inner, ctx, call)
            if last is None:
                return None
            if not last.error:
                return last
            if attempt < self.max_attempts:
                await asyncio.sleep(self.backoff * attempt)
        return last

    async def close(self) -> None:
        await self.inner.close()


class TimeoutExecutor:
    """per-call timeout（默认粒度）。

    组合语义：
      `Timeout(Retry(X))` → 单 call 的整个 retry 过程共享一个 timeout
      `Retry(Timeout(X))` → 每次 retry 各自拥有独立 timeout
    """

    def __init__(self, inner, seconds: float = 30) -> None:
        self.inner = inner
        self.seconds = seconds

    async def execute(self, ctx: RunContext, action: ToolCalls) -> list[ToolResult]:
        return await execute_serial(self._execute_one, ctx, action)

    async def _execute_one(self, ctx: RunContext, call: ToolCall) -> ToolResult | None:
        try:
            return await asyncio.wait_for(
                execute_one_of(self.inner, ctx, call),
                timeout=self.seconds,
            )
        except asyncio.TimeoutError:
            return ToolResult(
                tool_call_id=call.id,
                content=f"timeout after {self.seconds}s",
                error=True,
            )

    async def close(self) -> None:
        await self.inner.close()
