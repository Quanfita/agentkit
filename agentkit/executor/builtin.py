"""ToolExecutor 的四个基础实现 + 装饰器组合。

三条线，永不交叉：

    Toolbox      = "有哪些工具？"   （Discovery + Lookup）
    ToolExecutor = "怎么执行工具？" （Execution Policy）
    Tool         = "工具具体做什么？"（Capability）

生命周期契约（写进每个 Executor 作者的义务）：
    Executor **借用** Toolbox，不拥有它。
    `close()` 只关闭自己持有的资源（HTTP client / sandbox / 子进程池），
    **绝不允许关闭 Toolbox** —— Toolbox 的生命周期归 Runtime。

装饰器组合语义（由「装饰器重写 execute_one」自然导出）：

    Timeout(Retry(X))    单 call 的整个 retry 过程共享一个 timeout
    Retry(Timeout(X))    每次 retry 各自拥有独立 timeout
    Retry(Parallel(X))   batch 内并发，失败 call 独立重试
    Timeout(Parallel(X)) batch 内并发，单 call timeout
"""
from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from ..kernel.state import RunContext
from ..kernel.types import ToolCall, ToolCalls, ToolResult

if TYPE_CHECKING:                      # 只用于注解：Executor 不 import Toolbox 实现
    from ..toolbox import Toolbox


async def dispatch(toolbox: Toolbox, call: ToolCall) -> ToolResult:
    """单工具派发：Lookup → 执行 → 归一化成 ToolResult。

    异常模型（V2 冻结）：
      未知工具 / 工具内部异常 → ToolResult(error=True)
      CancelledError          → 穿透，不捕获
      Executor 自身的基础设施失败（超时等）→ 由装饰器转成 error 结果
    """
    tool = await toolbox.lookup(call.name)
    if tool is None:
        return ToolResult(
            tool_call_id=call.id, content=f"unknown tool: {call.name}", error=True,
        )
    try:
        result = await tool.run(call.arguments)
    except asyncio.CancelledError:
        raise                              # 取消必须穿透，不能吞
    except Exception as e:
        return ToolResult(
            tool_call_id=call.id, content=f"{type(e).__name__}: {e}", error=True,
        )
    if not result.tool_call_id:
        result.tool_call_id = call.id      # 归因信息由执行层补齐
    return result


async def serial_execute(executor, ctx: RunContext, action: ToolCalls) -> list[ToolResult]:
    """装饰器的默认批量遍历：每个 call 都走 `executor.execute_one`。

    SequentialExecutor 的批内 stop 语义从这里自然导出：`ctx.stop` 置位后，
    尚未开始的 call 不再执行（返回结果是 action.calls 的前缀）。
    """
    results: list[ToolResult] = []
    for call in action.calls:
        if ctx.stop:
            break
        results.append(await executor.execute_one(ctx, call))
    return results


class SequentialExecutor:
    """串行执行：批内按 call 粒度响应 `ctx.stop`。"""

    def __init__(self, toolbox: Toolbox) -> None:
        self.toolbox = toolbox             # borrow，不拥有

    async def execute(self, ctx: RunContext, action: ToolCalls) -> list[ToolResult]:
        return await serial_execute(self, ctx, action)

    async def execute_one(self, ctx: RunContext, call: ToolCall) -> ToolResult:
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
            *(self.execute_one(ctx, c) for c in action.calls)
        )

    async def execute_one(self, ctx: RunContext, call: ToolCall) -> ToolResult:
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
        return await serial_execute(self, ctx, action)

    async def execute_one(self, ctx: RunContext, call: ToolCall) -> ToolResult:
        last: ToolResult | None = None
        for attempt in range(1, self.max_attempts + 1):
            last = await self.inner.execute_one(ctx, call)
            if not last.error:
                return last
            if attempt < self.max_attempts:
                await asyncio.sleep(self.backoff * attempt)
        assert last is not None
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
        return await serial_execute(self, ctx, action)

    async def execute_one(self, ctx: RunContext, call: ToolCall) -> ToolResult:
        try:
            return await asyncio.wait_for(
                self.inner.execute_one(ctx, call),
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
