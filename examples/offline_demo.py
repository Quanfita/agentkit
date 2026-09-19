"""离线 Demo —— 不需要任何 API Key，把框架的扩展点全跑一遍。

演示四件事，全部只靠 EventBus / Provider 列表完成，kernel 一行不动：
1. 自定义 ContextProvider（注入环境事实）
2. 预算 Hook（CostTracker 置 ctx.stop，Loop 自己停下）
3. 压缩 Hook（在 model.before 里裁剪 ctx.messages）
4. 自定义 Runtime（完全不装模型）

用法：python examples/offline_demo.py
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

if __package__ in (None, ""):   # 允许直接 `python examples/xxx.py`，不必先 pip install -e .
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agentkit.agent import Agent
from agentkit.context.providers import CallableProvider, SystemPrompt
from agentkit.harness.base import Harness
from agentkit.kernel.events import EventBus
from agentkit.kernel.protocols import PreparedInput
from agentkit.kernel.types import ContextItem, Final, Message, ToolCall, ToolCalls
from agentkit.memory.simple import InMemoryMemory
from agentkit.models.echo import ScriptedModel
from agentkit.observability import CostTracker, Logger, Tracer
from agentkit.runtime.default import ContextEngine, DefaultRuntime
from agentkit.toolbox import Toolbox
from agentkit.tools.function import tool


@tool
def remember_fact(fact: str) -> str:
    """把一条事实写进工作记忆。"""
    return f"记住了：{fact}"


class DemoHarness(Harness):
    def __init__(self) -> None:
        self.events = EventBus()
        self.tracer = Tracer(printer=lambda line: print(line, file=sys.stderr))
        self.cost = CostTracker(budget=10_000, chars_per_token=4)
        self.events.on("*", self.tracer)
        self.events.on("*", Logger())
        self.events.on("model.before", self.cost)

        # 压缩 Hook：上下文超过 3 条就从最老的开始裁
        def compact(event, **payload):
            ctx = payload["ctx"]
            if len(ctx.messages) > 3:
                ctx.messages = ctx.messages[:1] + ctx.messages[-2:]

        self.events.on("model.before", compact)

        self.memory = InMemoryMemory()
        self.toolbox = Toolbox([remember_fact])
        self.model = ScriptedModel([
            ToolCalls([ToolCall("c1", "remember_fact", {"fact": "微内核只有两个扩展点"})]),
            Final("已把结论写进记忆。"),
        ])
        self.context = ContextEngine([
            SystemPrompt("You are a demo agent."),
            CallableProvider(lambda ctx: [
                ContextItem(f"当前任务长度：{len(ctx.task)} 字", source="env"),
            ]),
            CallableProvider(lambda ctx: [ContextItem("提示：先查记忆再回答", kind="hint")]),
        ])

    def build_runtime(self) -> DefaultRuntime:
        return DefaultRuntime(
            model=self.model,
            toolbox=self.toolbox,
            context=self.context,
            memory=self.memory,
            events=self.events,
        )

    async def close(self) -> None:
        await self.toolbox.close()


class NoModelRuntime:
    """第二种扩展点：完全绕过 DefaultRuntime。"""

    events = EventBus()

    async def prepare(self, ctx):
        return PreparedInput([Message("user", ctx.task)], [])

    async def reason(self, ctx, inp):
        return Final(f"[确定性问题] {ctx.task} 的答案是 42。")

    async def act(self, ctx, action):
        return []

    async def observe(self, ctx, action, results):
        return None

    async def finish(self, ctx):
        return None

    async def close(self):
        return None


class NoModelHarness(Harness):
    def build_runtime(self):
        return NoModelRuntime()


async def main() -> None:
    print("── 场景 1：工具 + 记忆 + 预算/压缩 Hook ──")
    async with Agent(DemoHarness()) as agent:
        print(await agent.run("微内核有几个扩展点？"))

    print("\n── 场景 2：自定义 Runtime，不用任何模型 ──")
    async with Agent(NoModelHarness()) as agent:
        print(await agent.run("6 × 7"))


if __name__ == "__main__":
    asyncio.run(main())
