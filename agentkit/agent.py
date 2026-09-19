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

    async def run(
        self,
        task: str,
        *,
        max_iterations: int = 16,
        system: str = "",
    ) -> str:
        ctx = RunContext(
            task=task,
            system=system,
            messages=[Message("user", task)],
            max_iterations=max_iterations,
        )
        await agent_loop(self.runtime, ctx)
        return ctx.result

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
