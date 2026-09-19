"""Kernel 契约：数据类型、EventBus、Protocol 结构匹配。"""
from __future__ import annotations

import asyncio

import pytest
from support import make_ctx, record, runtime_for

from agentkit.context.providers import SystemPrompt
from agentkit.kernel.events import EventBus
from agentkit.kernel.protocols import (
    ContextProvider,
    Memory,
    Model,
    Runtime,
    Tool,
    ToolProvider,
)
from agentkit.kernel.state import RunContext
from agentkit.kernel.types import (
    ContextItem,
    Final,
    MemoryInput,
    MemoryItem,
    Message,
    ToolCall,
    ToolCalls,
    ToolResult,
    ToolSpec,
)
from agentkit.memory.simple import InMemoryMemory, NullMemory
from agentkit.models.echo import EchoModel
from agentkit.tools.function import FunctionTool


def test_message_defaults_are_not_shared():
    a = Message("user", "a")
    b = Message("user", "b")
    a.tool_calls.append(ToolCall("1", "t"))
    assert b.tool_calls == []


def test_toolspec_default_parameters_are_not_shared():
    a = ToolSpec("a")
    b = ToolSpec("b")
    a.parameters["properties"]["x"] = {"type": "string"}
    assert b.parameters == {"type": "object", "properties": {}}


def test_run_context_defaults():
    ctx = RunContext(task="t")
    assert (ctx.step, ctx.max_iterations, ctx.done, ctx.stop) == (0, 16, False, False)
    assert ctx.result == "" and ctx.error is None and ctx.scratch == {}


def test_action_union_members():
    assert isinstance(Final("x"), Final | ToolCalls)
    assert isinstance(ToolCalls([]), Final | ToolCalls)


def test_memory_input_is_about_a_run_not_messages():
    run = MemoryInput(task="t", messages=[Message("user", "t")], result="r")
    assert run.messages[0].role == "user"
    assert MemoryItem("x").kind == "memory"
    assert ContextItem("x").role == "system"


@pytest.mark.anyio
async def test_structural_protocol_matching():
    assert isinstance(FunctionTool(lambda: "x", name="noop"), Tool)
    assert isinstance(EchoModel(), Model)
    assert isinstance(NullMemory(), Memory)
    assert isinstance(InMemoryMemory(), Memory)
    assert isinstance(SystemPrompt("s"), ContextProvider)
    assert isinstance(runtime_for(EchoModel()), Runtime)

    class Provider:
        async def tools(self):
            return []

        async def close(self):
            return None

    assert isinstance(Provider(), ToolProvider)


@pytest.mark.anyio
async def test_eventbus_wildcard_runs_after_named_handlers():
    bus = EventBus()
    order = []

    def named(event, **payload):
        order.append("named")

    async def wildcard(event, **payload):
        order.append("wildcard")

    bus.on("ping", named).on("*", wildcard)
    await bus.emit("ping")
    assert order == ["named", "wildcard"]


@pytest.mark.anyio
async def test_eventbus_off_stops_delivery():
    bus = EventBus()
    rec = record(bus)
    bus.off("*", rec)
    await bus.emit("x")
    assert rec.seen == []


@pytest.mark.anyio
async def test_eventbus_raises_handler_error_by_default():
    bus = EventBus()

    def boom(event, **payload):
        raise RuntimeError("handler down")

    bus.on("x", boom)
    with pytest.raises(RuntimeError, match="handler down"):
        await bus.emit("x")


@pytest.mark.anyio
async def test_eventbus_ignore_mode_keeps_going(capsys):
    bus = EventBus(on_handler_error="ignore")
    seen = []

    def boom(event, **payload):
        raise RuntimeError("handler down")

    bus.on("x", boom).on("x", lambda event, **payload: seen.append(event))
    await bus.emit("x")
    assert seen == ["x"]
    assert "handler down" in capsys.readouterr().err


@pytest.mark.anyio
async def test_eventbus_never_swallows_cancellation():
    bus = EventBus(on_handler_error="ignore")

    async def cancel(event, **payload):
        raise asyncio.CancelledError

    bus.on("x", cancel)
    with pytest.raises(asyncio.CancelledError):
        await bus.emit("x")


@pytest.mark.anyio
async def test_ctx_scratch_is_the_shared_hook_blackboard():
    seen = {}

    def handler(event, **payload):
        ctx = payload["ctx"]
        ctx.scratch["hook"] = "ran"
        seen.update(ctx.scratch)

    bus = EventBus()
    bus.on("agent.start", handler)
    ctx = make_ctx()
    await runtime_for(EchoModel(), events=bus).events.emit("agent.start", ctx=ctx)
    assert seen == {"hook": "ran"} and ctx.scratch == {"hook": "ran"}


def test_tool_result_error_defaults_false():
    assert ToolResult(content="ok").error is False
    assert ToolResult(content="bad", error=True).metadata == {}


def test_tool_call_arguments_default_is_per_instance():
    a, b = ToolCall("1", "t"), ToolCall("2", "t")
    a.arguments["x"] = 1
    assert b.arguments == {}
