"""agent_loop 行为：0 工具 / 1 工具 / 工具报错 / max_iterations / stop 检查点。"""
from __future__ import annotations

import asyncio

import pytest
from support import RuntimeHarness, make_ctx, record, runtime_for

from agentkit.agent import Agent
from agentkit.kernel.loop import agent_loop
from agentkit.kernel.types import Final, Message, ToolCall, ToolCalls, ToolResult
from agentkit.memory.simple import InMemoryMemory
from agentkit.models.echo import EchoModel, ScriptedModel
from agentkit.tools.function import FunctionTool


def add(a: int, b: int) -> str:
    """两数相加。"""
    return str(a + b)


def explode() -> str:
    raise ValueError("tool blew up")


def calls(*names: str) -> ToolCalls:
    return ToolCalls([ToolCall(str(i), n) for i, n in enumerate(names)])


async def run_ctx(runtime, ctx):
    return await agent_loop(runtime, ctx)


@pytest.mark.anyio
async def test_zero_tools_run_sets_result_from_final():
    runtime = runtime_for(EchoModel())
    ctx = make_ctx("读一下 README")
    await run_ctx(runtime, ctx)
    assert ctx.result == "echo: 读一下 README"
    assert ctx.done and ctx.step == 0 and ctx.error is None


@pytest.mark.anyio
async def test_one_tool_round_trip_feeds_result_back_to_model():
    model = ScriptedModel([
        ToolCalls([ToolCall("c1", "add", {"a": 20, "b": 22})]),
        Final("答案是 42"),
    ])
    runtime = runtime_for(model, tools=[FunctionTool(add, name="add")])
    ctx = make_ctx("20+22 等于几")
    await run_ctx(runtime, ctx)

    assert ctx.result == "答案是 42"
    assert ctx.step == 1
    roles = [m.role for m in ctx.messages]
    assert roles == ["user", "assistant", "tool", "assistant"]
    assert ctx.messages[2].content == "42"
    assert ctx.messages[2].tool_call_id == "c1"
    assert ctx.messages[1].tool_calls == [ToolCall("c1", "add", {"a": 20, "b": 22})]
    assert model.calls[1][0][-1].content == "42"


@pytest.mark.anyio
async def test_tool_error_is_visible_to_model_and_loop_survives():
    model = ScriptedModel([calls("explode"), Final("已降级处理")])
    runtime = runtime_for(model, tools=[FunctionTool(explode, name="explode")])
    ctx = make_ctx()
    await run_ctx(runtime, ctx)

    assert ctx.result == "已降级处理"
    tool_msg = next(m for m in ctx.messages if m.role == "tool")
    assert tool_msg.content.startswith("[tool_error] ValueError: tool blew up")
    assert model.calls[1][0][-1].content.startswith("[tool_error]")


@pytest.mark.anyio
async def test_unknown_tool_is_an_error_result_not_a_crash():
    model = ScriptedModel([calls("nope"), Final("ok")])
    runtime = runtime_for(model)
    ctx = make_ctx()
    await run_ctx(runtime, ctx)
    assert ctx.result == "ok"
    assert ctx.messages[2].content == "[tool_error] unknown tool: nope"


@pytest.mark.anyio
async def test_tool_result_passthrough_and_multi_call_order():
    async def slow(tag: str) -> ToolResult:
        await asyncio.sleep(0.02)
        return ToolResult(f"慢:{tag}")

    async def fast(tag: str) -> ToolResult:
        return ToolResult(f"快:{tag}", error=True)

    model = ScriptedModel([
        ToolCalls([ToolCall("a", "slow", {"tag": "1"}), ToolCall("b", "fast", {"tag": "2"})]),
        Final("done"),
    ])
    runtime = runtime_for(model, tools=[
        FunctionTool(slow, name="slow"), FunctionTool(fast, name="fast"),
    ])
    ctx = make_ctx()
    await run_ctx(runtime, ctx)
    assert [m.content for m in ctx.messages if m.role == "tool"] == [
        "慢:1", "[tool_error] 快:2",
    ]


@pytest.mark.anyio
async def test_max_iterations_caps_tool_loop_and_leaves_result_empty():
    model = ScriptedModel([calls("add")])
    tool = FunctionTool(add, name="add")
    runtime = runtime_for(model, tools=[tool])
    ctx = make_ctx("永不收敛", max_iterations=3)
    await run_ctx(runtime, ctx)

    assert ctx.step == 3
    assert ctx.done is False
    assert ctx.result == ""
    assert len(model.calls) == 3


@pytest.mark.anyio
async def test_result_is_only_set_for_final():
    model = ScriptedModel([calls("add")])
    runtime = runtime_for(model, tools=[FunctionTool(add, name="add")])
    ctx = make_ctx(max_iterations=2)
    await run_ctx(runtime, ctx)
    assert ctx.result == ""
    assert ctx.last_assistant is not None
    assert ctx.last_assistant.tool_calls


@pytest.mark.anyio
async def test_stop_before_first_iteration_skips_reason_and_act():
    model = EchoModel()
    events = runtime_for(model).events
    events.on("agent.start", lambda event, **payload: setattr(payload["ctx"], "stop", True))

    harness = RuntimeHarness(model, events=events)
    runtime = harness.build_runtime()
    ctx = make_ctx()
    await run_ctx(runtime, ctx)

    assert model.calls == []
    assert ctx.result == "" and ctx.step == 0


@pytest.mark.anyio
async def test_stop_set_by_model_before_hook_skips_reason():
    model = EchoModel()
    harness = RuntimeHarness(model)
    runtime = harness.build_runtime()
    seen = []

    def hook(event, **payload):
        seen.append(event)
        payload["ctx"].stop = True

    runtime.events.on("model.before", hook)
    ctx = make_ctx()
    await run_ctx(runtime, ctx)

    assert seen == ["model.before"]
    assert model.calls == []          # 检查点 2 拦住了副作用动作
    assert ctx.result == ""


@pytest.mark.anyio
async def test_stop_after_reason_skips_tool_batch():
    model = ScriptedModel([calls("add"), Final("不该走到这里")])
    harness = RuntimeHarness(model, tools=[FunctionTool(add, name="add")])
    runtime = harness.build_runtime()

    def hook(event, **payload):
        if isinstance(payload.get("action"), ToolCalls):
            payload["ctx"].stop = True

    runtime.events.on("model.after", hook)
    ctx = make_ctx()
    await run_ctx(runtime, ctx)

    assert len(model.calls) == 1       # 模型被调过一次
    assert ctx.step == 0               # act 被检查点 3 拦住
    assert ctx.result == ""
    assert all(m.role != "tool" for m in ctx.messages)


@pytest.mark.anyio
async def test_act_has_its_own_stop_guard():
    tool = FunctionTool(add, name="add")
    harness = RuntimeHarness(EchoModel(), tools=[tool])
    runtime = harness.build_runtime()
    ctx = make_ctx()
    ctx.stop = True
    assert await runtime.act(ctx, calls("add")) == []


@pytest.mark.anyio
async def test_events_sequence_for_one_tool_round():
    model = ScriptedModel([calls("add"), Final("42")])
    harness = RuntimeHarness(model, tools=[FunctionTool(add, name="add")])
    runtime = harness.build_runtime()
    rec = record(runtime.events)
    await run_ctx(runtime, make_ctx())

    assert rec.seen == [
        "agent.start",
        "model.before", "model.after", "iteration.done",
        "model.before", "model.after",
        "agent.end",
    ]


@pytest.mark.anyio
async def test_model_error_propagates_and_is_recorded():
    class Boom:
        async def generate(self, messages, tools):
            raise RuntimeError("provider 500")

    memory = InMemoryMemory()
    harness = RuntimeHarness(Boom(), memory=memory)
    runtime = harness.build_runtime()
    rec = record(runtime.events)
    ctx = make_ctx("任务")

    with pytest.raises(RuntimeError, match="provider 500"):
        await run_ctx(runtime, ctx)

    assert isinstance(ctx.error, RuntimeError)
    assert ctx.done is True
    assert "agent.error" in rec.seen
    assert rec.seen[-1] == "agent.end"
    assert rec.ctx_of("agent.error") is ctx


@pytest.mark.anyio
async def test_cancelled_error_from_tool_penetrates_the_loop():
    async def cancelled() -> str:
        raise asyncio.CancelledError

    model = ScriptedModel([calls("cancelled"), Final("never")])
    runtime = runtime_for(model, tools=[FunctionTool(cancelled, name="cancelled")])
    ctx = make_ctx()
    with pytest.raises(asyncio.CancelledError):
        await run_ctx(runtime, ctx)
    assert isinstance(ctx.error, asyncio.CancelledError)
    assert ctx.result == ""


@pytest.mark.anyio
async def test_finish_runs_even_on_failure():
    class Boom:
        async def generate(self, messages, tools):
            raise RuntimeError("x")

    class RecordingMemory:
        def __init__(self):
            self.runs = []

        async def recall(self, task):
            return []

        async def remember(self, run):
            self.runs.append(run)

    memory = RecordingMemory()
    harness = RuntimeHarness(Boom(), memory=memory)
    runtime = harness.build_runtime()
    with pytest.raises(RuntimeError):
        await run_ctx(runtime, make_ctx("失败任务"))

    assert len(memory.runs) == 1
    assert memory.runs[0].task == "失败任务"
    assert memory.runs[0].result == ""
    assert [m.role for m in memory.runs[0].messages] == ["user"]


@pytest.mark.anyio
async def test_failed_run_does_not_pollute_in_memory_memory():
    class Boom:
        async def generate(self, messages, tools):
            raise RuntimeError("x")

    memory = InMemoryMemory()
    runtime = RuntimeHarness(Boom(), memory=memory).build_runtime()
    with pytest.raises(RuntimeError):
        await run_ctx(runtime, make_ctx("失败任务"))
    assert memory.items == []


@pytest.mark.anyio
async def test_agent_returns_result_and_records_memory():
    memory = InMemoryMemory()
    harness = RuntimeHarness(EchoModel(), memory=memory)
    async with Agent(harness) as agent:
        assert await agent.run("记住我") == "echo: 记住我"
    assert [i.content for i in memory.items] == ["echo: 记住我"]
    assert memory.items[0].metadata["task"] == "记住我"


@pytest.mark.anyio
async def test_each_run_starts_from_a_fresh_context():
    """Loop State 是单次运行的：跨次延续由 Memory / ContextProvider 负责。"""
    model = EchoModel()
    harness = RuntimeHarness(model)
    async with Agent(harness) as agent:
        await agent.run("第一问")
        await agent.run("第二问")

    assert [m.role for m in model.calls[1][0]] == ["user"]
    assert model.calls[1][0][-1].content == "第二问"


@pytest.mark.anyio
async def test_cross_run_continuity_is_delivered_through_memory():
    from agentkit.context.providers import MemoryContext
    from agentkit.memory.simple import InMemoryMemory

    model = EchoModel()
    memory = InMemoryMemory()
    harness = RuntimeHarness(model, memory=memory, providers=[MemoryContext(memory)])
    async with Agent(harness) as agent:
        await agent.run("第一问")
        await agent.run("第二问")

    second = model.calls[1][0]
    assert [m.role for m in second] == ["system", "user"]
    assert second[0].content == "echo: 第一问"


@pytest.mark.anyio
async def test_run_accepts_system_and_max_iterations():
    model = EchoModel()
    harness = RuntimeHarness(model)
    async with Agent(harness) as agent:
        await agent.run("hi", system="你是测试助手", max_iterations=1)
    first = model.calls[0][0]
    assert first[0] == Message("system", "你是测试助手")
