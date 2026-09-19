"""RetryExecutor —— per-call retry（V3 §九 目录结构拆出的模块）。

拆文件只动位置，不动语义：与 V2.5 冻结的行为逐字一致。

只对 `result.error is True` 重试：**不会**重试 `CancelledError`，
也不会重试异常形态的基础设施失败。成功的 call 不会被重复执行
—— 重试发生在单个 call 内部，不是重跑整个 batch。
"""
from __future__ import annotations

import asyncio

from ..kernel.state import RunContext
from ..kernel.types import ToolCall, ToolCalls, ToolResult
from .builtin import execute_one_of, execute_serial


class RetryExecutor:
    """per-call retry。`max_attempts` 表示总执行次数（含首次）。"""

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
