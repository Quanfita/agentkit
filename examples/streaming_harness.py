"""Streaming 示范（文档 §4.3）—— 非框架代码，Streaming 完全在 Kernel 之外。

两个要点：
1. `StreamingRuntime` 只在 `reason()` 里把 Delta 组装成 Action，
   并顺手发 `model.delta` 事件给 UI；
2. Kernel 对此一无所知 —— `agent_loop` 一个字都没改。

用法：python examples/streaming_harness.py
（离线可跑；真机把 ScriptedStreamingModel 换成 OpenAIModel / AnthropicModel。）
"""
from __future__ import annotations

import asyncio
import json
import sys
from collections.abc import AsyncIterator
from pathlib import Path

if __package__ in (None, ""):   # 允许直接 `python examples/xxx.py`，不必先 pip install -e .
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agentkit.agent import Agent
from agentkit.context.providers import SystemPrompt
from agentkit.harness.base import Harness
from agentkit.kernel.events import EventBus
from agentkit.kernel.types import Final, ToolCall, ToolCalls
from agentkit.models.base import Delta, StreamingModel, TextDelta, ToolCallDelta
from agentkit.runtime.default import ContextEngine, DefaultRuntime
from agentkit.toolbox import Toolbox
from agentkit.tools.function import FunctionTool


def add(a: int, b: int) -> str:
    """两数相加。"""
    return str(a + b)


class ScriptedStreamingModel:
    """按剧本吐 Delta 的离线流式模型。"""

    def __init__(self, turns: list[list[Delta]]) -> None:
        self.turns = turns
        self.calls = 0

    def stream(self, messages, tools) -> AsyncIterator[Delta]:
        turn = self.turns[min(self.calls, len(self.turns) - 1)]
        self.calls += 1
        return self._emit(turn)

    async def _emit(self, turn: list[Delta]) -> AsyncIterator[Delta]:
        for delta in turn:
            await asyncio.sleep(0)          # 模拟分块到达
            yield delta


class StreamingRuntime(DefaultRuntime):
    """§4.3：Delta → Action 的组装只发生在 Runtime 层。"""

    async def reason(self, ctx, inp):
        if not isinstance(self._model, StreamingModel):
            return await super().reason(ctx, inp)

        text_parts: list[str] = []
        tc_buf: dict[int, dict] = {}

        async for d in self._model.stream(inp.messages, inp.tools):
            if isinstance(d, TextDelta):
                await self.events.emit("model.delta", text=d.text)
                text_parts.append(d.text)
            elif isinstance(d, ToolCallDelta):
                buf = tc_buf.setdefault(d.index,
                                        {"id": "", "name": "", "args": ""})
                buf["id"] = d.id or buf["id"]
                buf["name"] = d.name or buf["name"]
                buf["args"] += d.args_delta or ""

        if tc_buf:
            return ToolCalls(
                calls=[
                    ToolCall(id=b["id"], name=b["name"],
                             arguments=json.loads(b["args"] or "{}"))
                    for b in tc_buf.values()
                ],
                content="".join(text_parts),
            )
        return Final("".join(text_parts))


class StreamingHarness(Harness):
    def __init__(self) -> None:
        self.events = EventBus()
        self.events.on("model.delta", lambda event, **p: print(p["text"], end="", flush=True))
        self.events.on("agent.end", lambda event, **p: print())

        self.toolbox = Toolbox([FunctionTool(add, name="add")])
        self.model = ScriptedStreamingModel([
            [
                TextDelta("我来算："),
                ToolCallDelta(index=0, id="c1", name="add", args_delta='{"a": 20,'),
                ToolCallDelta(index=0, args_delta=' "b": 22}'),
            ],
            [TextDelta("20 + 22 = "), TextDelta("42。")],
        ])
        self.context = ContextEngine([SystemPrompt("You are a streaming demo agent.")])

    def build_runtime(self) -> StreamingRuntime:
        return StreamingRuntime(
            model=self.model,
            toolbox=self.toolbox,
            context=self.context,
            events=self.events,
        )

    async def close(self) -> None:
        await self.toolbox.close()


async def main() -> None:
    async with Agent(StreamingHarness()) as agent:
        ctx = await agent.run_ctx("20+22 等于几")
    print(f"[result] {ctx.result}")
    print(f"[reason] {ctx.reason} steps={ctx.step}")


if __name__ == "__main__":
    asyncio.run(main())
