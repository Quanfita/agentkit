"""观测 Hook：tracer / cost / logger —— 全部通过 EventBus 挂载。"""
from __future__ import annotations

import logging

import pytest
from support import RuntimeHarness, make_ctx

from agentkit.kernel.events import EventBus
from agentkit.kernel.loop import agent_loop
from agentkit.kernel.types import Final, Message, ToolCall, ToolCalls, ToolResult
from agentkit.models.echo import EchoModel, ScriptedModel
from agentkit.observability import CostTracker, Logger, Tracer, describe
from agentkit.tools.function import FunctionTool


def add(a: int, b: int) -> str:
    """两数相加。"""
    return str(a + b)


@pytest.mark.anyio
async def test_tracer_prints_and_records_every_event():
    lines = []
    tracer = Tracer(printer=lines.append)
    harness = RuntimeHarness(EchoModel(), tools=[FunctionTool(add, name="add")])
    harness.events.on("*", tracer)
    runtime = harness.build_runtime()
    await agent_loop(runtime, make_ctx("任务"))

    assert tracer.seen == ["agent.start", "model.before", "model.after", "agent.end"]
    assert lines[0] == "[trace] agent.start task='任务'"
    assert any(line.startswith("[trace] model.before messages=1 tools=1") for line in lines)
    assert lines[-1].startswith("[trace] agent.end steps=0 done=True")


@pytest.mark.anyio
async def test_tracer_include_filter():
    tracer = Tracer(printer=lambda text: None, include=["model.after"])
    harness = RuntimeHarness(EchoModel())
    harness.events.on("*", tracer)
    await agent_loop(harness.build_runtime(), make_ctx())
    assert tracer.seen == ["model.after"]


@pytest.mark.anyio
async def test_logger_writes_to_standard_logging(caplog):
    harness = RuntimeHarness(EchoModel())
    harness.events.on("*", Logger(logger=logging.getLogger("agentkit.test")))
    with caplog.at_level(logging.INFO, logger="agentkit.test"):
        await agent_loop(harness.build_runtime(), make_ctx("任务"))
    assert "agent.start" in caplog.text and "task='任务'" in caplog.text


@pytest.mark.anyio
async def test_cost_tracker_counts_calls_and_estimated_tokens():
    cost = CostTracker()
    harness = RuntimeHarness(EchoModel())
    harness.events.on("model.before", cost)
    await agent_loop(harness.build_runtime(), make_ctx("12345678"))
    assert cost.calls == 1
    assert cost.estimated_tokens == len("12345678") // 4


@pytest.mark.anyio
async def test_budget_hook_stops_the_loop_without_touching_it():
    """预算熔断只改 ctx.stop —— 证明「加预算不碰 Loop」。"""
    messages = []

    async def endless(a: int, b: int) -> str:
        return "still going"

    model = ScriptedModel([ToolCalls([ToolCall("1", "endless", {"a": 1, "b": 2})])])
    cost = CostTracker(budget=1, printer=messages.append)
    harness = RuntimeHarness(model, tools=[FunctionTool(endless, name="endless")])
    harness.events.on("model.before", cost)

    ctx = make_ctx("永不收敛的任务", max_iterations=10)
    await agent_loop(harness.build_runtime(), ctx)

    assert cost.stopped is True
    assert ctx.stop is True and ctx.result == ""
    assert len(model.calls) < 10
    assert messages and messages[0].startswith("[cost] budget=1 exceeded")


@pytest.mark.anyio
async def test_compact_hook_shrinks_the_current_call_via_prepared_input():
    """改 inp.messages = 影响本次调用；改 ctx.messages = 影响后续 iteration。"""
    model = EchoModel()
    harness = RuntimeHarness(model, history_limit=100)

    def compact(event, **payload):
        inp = payload["inp"]
        if len(inp.messages) > 3:
            inp.messages = inp.messages[-3:]

    harness.events.on("model.before", compact)
    runtime = harness.build_runtime()
    ctx = make_ctx("第五问")
    for i in range(5):
        ctx.messages.insert(0, Message("user", f"旧{i}"))
    await agent_loop(runtime, ctx)

    assert [m.content for m in model.calls[0][0]] == ["旧1", "旧0", "第五问"]


@pytest.mark.anyio
async def test_compact_hook_on_ctx_messages_affects_later_iterations():
    seen = []
    model = ScriptedModel([ToolCalls([ToolCall("1", "add", {"a": 1, "b": 2})]), Final("3")])
    harness = RuntimeHarness(model, tools=[FunctionTool(add, name="add")], history_limit=100)

    def compact(event, **payload):
        ctx = payload["ctx"]
        if len(ctx.messages) > 2:
            ctx.messages = ctx.messages[-2:]

    harness.events.on("model.before", compact)
    runtime = harness.build_runtime()
    ctx = make_ctx("1+2")
    for i in range(5):
        ctx.messages.insert(0, Message("user", f"旧{i}"))
    await agent_loop(runtime, ctx)

    for messages, _ in model.calls:
        seen.append([m.role for m in messages])
    assert seen[0] == ["user"] * 6                 # 首次调用仍看到完整历史
    assert len(seen[1]) <= 4                       # 第二次调用看到的是被裁过的历史


def test_describe_covers_every_event_shape():
    ctx = make_ctx("任务")
    assert describe("agent.start", {"ctx": ctx}) == "task='任务'"
    assert describe("agent.error", {"error": ValueError("x")}) == "ValueError('x')"
    assert describe("iteration.done", {
        "ctx": ctx, "results": [ToolResult("ok"), ToolResult("bad", error=True)],
    }) == "step=0 results=['ok', 'error']"
    assert describe("unknown.event", {}) == ""


@pytest.mark.anyio
async def test_events_on_and_off_accept_plain_callables():
    class Hook:
        def __init__(self):
            self.seen = []

        def __call__(self, event, **payload):
            self.seen.append(event)

    bus = EventBus()
    hook = Hook()
    bus.on("*", hook)
    await bus.emit("ping")
    bus.off("*", hook)
    await bus.emit("ping")
    assert hook.seen == ["ping"]


@pytest.mark.anyio
async def test_tracer_sees_final_actions_as_such():
    lines = []
    harness = RuntimeHarness(EchoModel())
    harness.events.on("*", Tracer(printer=lines.append))
    await agent_loop(harness.build_runtime(), make_ctx("hello"))
    assert any("final chars=11" in line for line in lines)


@pytest.mark.anyio
async def test_tracer_describes_tool_calls():
    lines = []
    model = ScriptedModel([
        ToolCalls([ToolCall("1", "add", {"a": 1, "b": 2})]), Final("3"),
    ])
    harness = RuntimeHarness(model, tools=[FunctionTool(add, name="add")])
    harness.events.on("*", Tracer(printer=lines.append))
    await agent_loop(harness.build_runtime(), make_ctx("1+2"))
    assert any("tool_calls=['add']" in line for line in lines)
