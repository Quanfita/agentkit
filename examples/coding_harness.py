"""文档 §8.1 的 30 行组装 —— 真实需要 OPENAI_API_KEY 的最小 Coding Agent。

用法：
    set OPENAI_API_KEY=sk-...        (Windows)
    python examples/coding_harness.py "读一下 README.md 并总结"
"""
from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

if __package__ in (None, ""):   # 允许直接 `python examples/xxx.py`，不必先 pip install -e .
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agentkit.agent import Agent
from agentkit.context.providers import MemoryContext, SystemPrompt
from agentkit.contrib.local_tools import make_local_tools
from agentkit.harness.base import Harness
from agentkit.kernel.events import EventBus
from agentkit.memory.simple import InMemoryMemory
from agentkit.models.openai import OpenAIModel
from agentkit.runtime.default import ContextEngine, DefaultRuntime
from agentkit.skills.directory import DirectorySkills
from agentkit.toolbox import Toolbox


class CodingHarness(Harness):
    def __init__(self, model_name: str = "gpt-4o-mini") -> None:
        self.events = EventBus()
        self.events.on("*", lambda e, **_: print(f"[trace] {e}"))

        self.memory = InMemoryMemory()
        self.skills = DirectorySkills("./skills")
        self.toolbox = Toolbox(make_local_tools(".", allow_shell=False))

        self.context = ContextEngine([
            SystemPrompt("You are a careful coding agent."),
            self.skills,
            MemoryContext(self.memory),
        ])
        self.model = OpenAIModel(model_name)

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
        await self.model.close()


async def main(task: str) -> None:
    async with Agent(CodingHarness()) as agent:
        print(await agent.run(task))


if __name__ == "__main__":
    if not os.environ.get("OPENAI_API_KEY"):
        sys.exit("需要 OPENAI_API_KEY。离线体验请跑：python examples/offline_demo.py")
    asyncio.run(main(sys.argv[1] if len(sys.argv) > 1 else "读一下 README.md 并总结"))
