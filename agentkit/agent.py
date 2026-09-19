"""Agent —— 构造函数只有一个参数。"""
from __future__ import annotations

from .harness.base import Harness
from .kernel.loop import agent_loop
from .kernel.state import RunContext
from .kernel.types import Message


class Agent:
    """构造函数只有一个参数。守住这条，架构就不会烂。"""

    def __init__(self, harness: Harness) -> None:
        self.harness = harness
        self._runtime = None

    @property
    def runtime(self):
        if self._runtime is None:
            self._runtime = self.harness.build_runtime()
        return self._runtime

    async def run(self, task: str, **kw) -> str:
        """跑一次，只要结果文本。"""
        return (await self.run_ctx(task, **kw)).result

    async def run_ctx(self, task: str, **kw) -> RunContext:
        """跑一次，拿走整个 RunContext（含 reason / step / messages）。

        `**kw` 直接透传给 `RunContext`，因此 `max_iterations` 的默认值
        仍然由 Kernel 契约（16）决定，Agent 不重复声明。
        """
        ctx = RunContext(task=task, messages=[Message("user", task)], **kw)
        await agent_loop(self.runtime, ctx)
        return ctx

    async def close(self) -> None:
        if self._runtime is not None:
            try:
                await self._runtime.close()
            finally:
                self._runtime = None
        await self.harness.close()

    async def __aenter__(self) -> Agent:
        return self

    async def __aexit__(self, *exc) -> None:
        await self.close()
