"""Toolbox：Discovery / Lookup / 并发 / refresh / 错误与取消语义。"""
from __future__ import annotations

import asyncio

import pytest

from agentkit.kernel.types import ToolCall, ToolCalls, ToolResult
from agentkit.toolbox import Toolbox
from agentkit.tools.function import FunctionTool


class FakeProvider:
    def __init__(self, tools, fail_close=False):
        self._tools = list(tools)
        self.fail_close = fail_close
        self.close_count = 0

    async def tools(self):
        return list(self._tools)

    async def close(self):
        self.close_count += 1
        if self.fail_close:
            raise RuntimeError("provider close failed")


def echo(name: str = "local"):
    def fn(text: str = "") -> str:
        return f"{name}:{text}"
    return FunctionTool(fn, name=name)


def calls_for(*names: str) -> ToolCalls:
    return ToolCalls([ToolCall(str(i), n, {"text": n}) for i, n in enumerate(names)])


@pytest.mark.anyio
async def test_specs_merge_local_and_provider_tools_in_order():
    box = Toolbox([echo("a")]).add_provider(FakeProvider([echo("b"), echo("c")]))
    assert [s.name for s in await box.specs()] == ["a", "b", "c"]


@pytest.mark.anyio
async def test_duplicate_tool_name_raises():
    box = Toolbox([echo("a")]).add_provider(FakeProvider([echo("a")]))
    with pytest.raises(ValueError, match="duplicate tool: a"):
        await box.specs()


@pytest.mark.anyio
async def test_register_invalidates_the_lookup_index():
    box = Toolbox([echo("a")])
    await box.specs()
    box.register(echo("z"))
    assert [s.name for s in await box.specs()] == ["a", "z"]
    out = await box.execute(calls_for("z"))
    assert out[0].content == "z:z"


@pytest.mark.anyio
async def test_refresh_picks_up_new_server_tools():
    provider = FakeProvider([echo("a")])
    box = Toolbox().add_provider(provider)
    await box.specs()
    provider._tools.append(echo("hot"))
    assert [s.name for s in await box.specs()] == ["a"]      # 缓存未过期
    await box.refresh()
    assert [s.name for s in await box.specs()] == ["a", "hot"]


@pytest.mark.anyio
async def test_execute_runs_calls_concurrently():
    spans = {}

    def make(tag: str, delay: float):
        async def fn(text: str = "") -> str:
            spans[f"{tag}:start"] = asyncio.get_running_loop().time()
            await asyncio.sleep(delay)
            spans[f"{tag}:end"] = asyncio.get_running_loop().time()
            return tag
        return FunctionTool(fn, name=tag)

    box = Toolbox([make("a", 0.05), make("b", 0.05)])
    await box.execute(calls_for("a", "b"))

    assert spans["b:start"] < spans["a:end"]      # 两者时间窗重叠 = 真并发
    assert spans["a:start"] < spans["b:end"]


@pytest.mark.anyio
async def test_execute_preserves_call_order_with_reversed_completion():
    async def slow(text: str = "") -> str:
        await asyncio.sleep(0.03)
        return "slow"

    async def fast(text: str = "") -> str:
        return "fast"

    box = Toolbox([FunctionTool(slow, name="slow"), FunctionTool(fast, name="fast")])
    out = await box.execute(calls_for("slow", "fast"))
    assert [r.content for r in out] == ["slow", "fast"]


@pytest.mark.anyio
async def test_unknown_tool_becomes_error_result():
    box = Toolbox()
    out = await box.execute(calls_for("ghost"))
    assert out == [ToolResult(
        tool_call_id="0", content="unknown tool: ghost", error=True,
    )]


@pytest.mark.anyio
async def test_tool_exception_becomes_error_result_with_type_name():
    def boom(text: str = "") -> str:
        raise KeyError("missing")

    box = Toolbox([FunctionTool(boom, name="boom")])
    (out,) = await box.execute(calls_for("boom"))
    assert out.error is True and out.content == "KeyError: 'missing'"


@pytest.mark.anyio
async def test_tool_result_passes_through_untouched():
    def detailed(text: str = "") -> ToolResult:
        return ToolResult(content="payload", metadata={"k": 1})

    box = Toolbox([FunctionTool(detailed, name="detailed")])
    (out,) = await box.execute(calls_for("detailed"))
    assert out.content == "payload" and out.metadata == {"k": 1}


@pytest.mark.anyio
async def test_cancelled_error_penetrates_toolbox():
    async def cancelled(text: str = "") -> str:
        raise asyncio.CancelledError

    box = Toolbox([FunctionTool(cancelled, name="cancelled")])
    with pytest.raises(asyncio.CancelledError):
        await box.execute(calls_for("cancelled"))


@pytest.mark.anyio
async def test_close_closes_every_provider_and_survives_failures():
    dead = FakeProvider([], fail_close=True)
    live = FakeProvider([])
    box = Toolbox().add_provider(dead).add_provider(live)
    await box.close()
    assert (dead.close_count, live.close_count) == (1, 1)
