"""TimeoutExecutor —— per-call timeout（V3 §九 目录结构拆出的模块）。

拆文件只动位置，不动语义：与 V2.5 冻结的行为逐字一致。

组合语义（由「装饰器重写 per-call 策略」自然导出）：

    Timeout(Retry(X))    单 call 的整个 retry 过程共享一个 timeout
    Retry(Timeout(X))    每次 retry 各自拥有独立 timeout
"""
from __future__ import annotations

import asyncio

from ..kernel.state import RunContext
from ..kernel.types import ToolCall, ToolCalls, ToolResult
from .builtin import execute_one_of, execute_serial


class TimeoutExecutor:
    """per-call timeout（默认粒度）。"""

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
