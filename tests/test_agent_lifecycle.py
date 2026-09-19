"""Agent 生命周期：懒构建 Runtime / 关闭链 / async with / 绕过 Harness。"""
from __future__ import annotations

import pytest
from support import RuntimeHarness

from agentkit.agent import Agent
from agentkit.harness.base import Harness
from agentkit.kernel.events import EventBus
from agentkit.kernel.protocols import Runtime
from agentkit.kernel.types import Final
from agentkit.models.echo import EchoModel


class CountingHarness(RuntimeHarness):
    def __init__(self, model, **kwargs):
        super().__init__(model, **kwargs)
        self.builds = 0

    def build_runtime(self):
        self.builds += 1
        return super().build_runtime()


@pytest.mark.anyio
async def test_runtime_is_built_once_lazily():
    harness = CountingHarness(EchoModel())
    agent = Agent(harness)
    assert harness.builds == 0
    await agent.run("一")
    await agent.run("二")
    assert harness.builds == 1


@pytest.mark.anyio
async def test_close_walks_the_ownership_chain_once():
    class Provider:
        def __init__(self):
            self.closed = 0

        async def tools(self):
            return []

        async def close(self):
            self.closed += 1

    provider = Provider()
    harness = CountingHarness(EchoModel())
    harness.toolbox.add_provider(provider)
    agent = Agent(harness)
    await agent.run("任务")
    await agent.close()

    assert provider.closed == 1          # runtime.close() → toolbox.close()
    assert harness.close_count == 1
    assert agent._runtime is None


@pytest.mark.anyio
async def test_close_is_safe_without_a_single_run():
    harness = CountingHarness(EchoModel())
    agent = Agent(harness)
    await agent.close()
    assert harness.close_count == 1


@pytest.mark.anyio
async def test_close_after_close_does_not_touch_the_runtime_twice():
    class Provider:
        def __init__(self):
            self.closed = 0

        async def tools(self):
            return []

        async def close(self):
            self.closed += 1

    provider = Provider()
    harness = RuntimeHarness(EchoModel())
    harness.toolbox.add_provider(provider)
    agent = Agent(harness)
    await agent.run("x")
    await agent.close()
    await agent.close()
    assert provider.closed == 1


@pytest.mark.anyio
async def test_run_after_close_rebuilds_the_runtime():
    harness = CountingHarness(EchoModel())
    agent = Agent(harness)
    assert await agent.run("前") == "echo: 前"
    await agent.close()
    assert await agent.run("后") == "echo: 后"
    assert harness.builds == 2
    await agent.close()


@pytest.mark.anyio
async def test_async_with_closes_on_exception():
    harness = RuntimeHarness(EchoModel())
    with pytest.raises(ValueError):
        async with Agent(harness):
            raise ValueError("boom")
    assert harness.close_count == 1


def test_harness_base_is_a_two_method_contract():
    assert Harness().close is not None
    with pytest.raises(NotImplementedError):
        Harness().build_runtime()


@pytest.mark.anyio
async def test_custom_runtime_bypasses_harness_and_default_runtime():
    """极致用法：不装 Harness 的内容，直接实现 Runtime（文档 §8.3）。"""

    class MyRuntime:
        events = EventBus()

        async def prepare(self, ctx):
            from agentkit.kernel.protocols import PreparedInput
            return PreparedInput([], [])

        async def reason(self, ctx, inp):
            return Final("我不需要模型。")

        async def act(self, ctx, action):
            return []

        async def observe(self, ctx, action, results):
            return None

        async def finish(self, ctx):
            return None

        async def close(self):
            return None

    class MyHarness(Harness):
        def build_runtime(self):
            return MyRuntime()

    assert isinstance(MyRuntime(), Runtime)
    async with Agent(MyHarness()) as agent:
        assert await agent.run("随便问") == "我不需要模型。"


@pytest.mark.anyio
async def test_events_are_shared_across_runs_of_one_agent():
    seen = []
    harness = RuntimeHarness(EchoModel())
    harness.events.on("agent.start", lambda event, **payload: seen.append(payload["ctx"].task))
    async with Agent(harness) as agent:
        await agent.run("一")
        await agent.run("二")
    assert seen == ["一", "二"]


def test_agent_constructor_takes_only_the_harness():
    harness = RuntimeHarness(EchoModel())
    assert Agent(harness).harness is harness


def test_agent_has_no_capability_arguments():
    import inspect
    params = list(inspect.signature(Agent.__init__).parameters)
    assert params == ["self", "harness"]
