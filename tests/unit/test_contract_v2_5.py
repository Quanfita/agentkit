"""V2.5 §七 Contract Gate 的单元测试。

只测 V2.5 收口的那几条语义：kw-only、Protocol 形状、ownership 三态、
lookup 异常冒泡、Cancellation 与进程级信号、SpyToolbox 生命周期。
"""
from __future__ import annotations

import asyncio
import inspect

import pytest
from support import RuntimeHarness, make_ctx, record

from agentkit.agent import Agent
from agentkit.executor.builtin import ParallelExecutor, SequentialExecutor
from agentkit.kernel import protocols
from agentkit.kernel.loop import agent_loop
from agentkit.kernel.protocols import (
    ContextProvider,
    Memory,
    Model,
    Tool,
    ToolExecutor,
    ToolProvider,
)
from agentkit.kernel.state import TerminationReason
from agentkit.kernel.types import (
    ContextItem,
    Final,
    MemoryItem,
    ToolCall,
    ToolCalls,
    ToolResult,
    ToolSpec,
)
from agentkit.toolbox import Toolbox
from agentkit.tools.function import FunctionTool

# ── §2.4 ToolResult 是 kw-only ─────────────────────────


def test_tool_result_is_keyword_only():
    with pytest.raises(TypeError):
        ToolResult("hello")                       # noqa: B018 — 故意的位置参数调用
    assert ToolResult(content="hello").content == "hello"
    assert ToolResult().tool_call_id == ""
    assert inspect.signature(ToolResult).parameters["content"].kind is (
        inspect.Parameter.KEYWORD_ONLY
    )


# ── §2.1 ToolExecutor 只有 execute / close ─────────────


def test_tool_executor_protocol_has_no_execute_one():
    names = {n for n in dir(ToolExecutor) if not n.startswith("_")}
    assert names >= {"execute", "close"}
    assert "execute_one" not in names
    assert getattr(ToolExecutor, "_is_protocol", False) is True

    for executor in (SequentialExecutor(Toolbox()), ParallelExecutor(Toolbox())):
        assert isinstance(executor, ToolExecutor)
        assert not hasattr(executor, "execute_one")


# ── §2.5 tool_call_id ownership 三态 ───────────────────


@pytest.mark.anyio
@pytest.mark.parametrize("produced", [
    ToolResult(content="x"),                              # 空
    ToolResult(tool_call_id="", content="x"),             # 显式空
    ToolResult(tool_call_id="another-call", content="x"),  # 错误值
])
async def test_dispatch_three_states_all_end_with_the_originating_call_id(produced):
    async def tool_fn(**kwargs) -> ToolResult:
        return produced

    toolbox = Toolbox([FunctionTool(tool_fn, name="t")])
    (result,) = await ParallelExecutor(toolbox).execute(
        make_ctx(), ToolCalls([ToolCall("originating-id", "t")]),
    )

    assert result.tool_call_id == "originating-id"
    assert result.content == "x"


# ── §2.2 lookup 异常冒泡 ───────────────────────────────


class SpyToolbox:
    """记录 method 调用的 Toolbox 替身（§2.6 的 SpyToolbox）。"""

    def __init__(self, tools: dict | None = None, lookup_error: Exception | None = None):
        self._tools = tools or {}
        self.lookup_error = lookup_error
        self.lookup_calls: list[str] = []
        self.close_called = False

    async def lookup(self, name: str):
        self.lookup_calls.append(name)
        if self.lookup_error is not None:
            raise self.lookup_error
        return self._tools.get(name)

    async def close(self) -> None:
        self.close_called = True


@pytest.mark.anyio
async def test_lookup_failure_propagates_instead_of_becoming_a_tool_result():
    """基础设施故障（注册表损坏 / MCP provider 挂掉）不能被伪装成 Observation。"""
    toolbox = SpyToolbox(lookup_error=RuntimeError("registry corrupted"))
    with pytest.raises(RuntimeError, match="registry corrupted"):
        await ParallelExecutor(toolbox).execute(
            make_ctx(), ToolCalls([ToolCall("1", "anything")]),
        )
    assert toolbox.lookup_calls == ["anything"]


@pytest.mark.anyio
async def test_executor_close_does_not_close_the_toolbox():
    toolbox = SpyToolbox()
    for executor in (SequentialExecutor(toolbox), ParallelExecutor(toolbox)):
        await executor.execute(make_ctx(), ToolCalls([]))
        await executor.close()

    assert toolbox.close_called is False        # §2.6 关键断言
    assert toolbox.lookup_calls == []           # 空 batch 不查表


# ── §2.3 Cancellation / 进程级信号 ─────────────────────


@pytest.mark.anyio
async def test_cancelled_error_does_not_enter_the_agent_error_model():
    async def cancelled() -> str:
        raise asyncio.CancelledError

    from agentkit.models.echo import ScriptedModel

    harness = RuntimeHarness(
        ScriptedModel([ToolCalls([ToolCall("1", "cancelled")]), Final("never")]),
        tools=[FunctionTool(cancelled, name="cancelled")],
    )
    rec = record(harness.events)
    ctx = make_ctx()
    with pytest.raises(asyncio.CancelledError):
        await agent_loop(harness.build_runtime(), ctx)

    assert ctx.error is None
    assert ctx.reason is None
    assert "agent.error" not in rec.seen
    assert rec.seen[-1] == "agent.end"           # cleanup 照跑


@pytest.mark.anyio
async def test_plain_exception_sets_error_and_reason():
    class Boom:
        async def generate(self, messages, tools):
            raise ValueError("模型炸了")

    harness = RuntimeHarness(Boom())
    rec = record(harness.events)
    ctx = make_ctx()
    with pytest.raises(ValueError, match="模型炸了"):
        await agent_loop(harness.build_runtime(), ctx)

    assert isinstance(ctx.error, ValueError)
    assert ctx.reason is TerminationReason.ERROR
    assert ctx.done is True
    assert "agent.error" in rec.seen


@pytest.mark.anyio
async def test_keyboard_interrupt_is_not_part_of_the_agent_error_model():
    class Interrupting:
        async def generate(self, messages, tools):
            raise KeyboardInterrupt

    harness = RuntimeHarness(Interrupting())
    rec = record(harness.events)
    ctx = make_ctx()
    with pytest.raises(KeyboardInterrupt):
        await agent_loop(harness.build_runtime(), ctx)

    assert ctx.error is None                     # 进程级信号不进 Agent 语义
    assert ctx.reason is None
    assert "agent.error" not in rec.seen
    assert rec.seen[-1] == "agent.end"


@pytest.mark.anyio
async def test_cancellation_during_finish_still_propagates():
    """finish 里的 CancelledError 也不能被 finally 的 except Exception 吞掉。"""
    class CancellingFinish:
        events = None

        def __init__(self, events):
            self.events = events

        async def prepare(self, ctx):
            from agentkit.kernel.protocols import PreparedInput
            return PreparedInput([], [])

        async def reason(self, ctx, inp):
            return Final("done")

        async def act(self, ctx, action):
            return []

        async def observe(self, ctx, action, results):
            return None

        async def finish(self, ctx):
            raise asyncio.CancelledError

        async def close(self):
            return None

    from agentkit.kernel.events import EventBus
    events = EventBus()
    with pytest.raises(asyncio.CancelledError):
        await agent_loop(CancellingFinish(events), make_ctx())


# ── §2.7 Fake implementation 通过 Contract 测试 ────────


class FakeModel:
    async def generate(self, messages, tools):
        return Final("fake")


class FakeTool:
    spec = ToolSpec("fake", "假工具")

    async def run(self, arguments):
        return ToolResult(content="ok")


class FakeProvider:
    async def tools(self):
        return [FakeTool()]

    async def close(self):
        return None


class FakeExecutor:
    async def execute(self, ctx, action):
        return [ToolResult(tool_call_id=c.id, content="fake") for c in action.calls]

    async def close(self):
        return None


class FakeMemory:
    async def recall(self, task):
        return [MemoryItem("记住的事")]

    async def remember(self, run):
        return None


class FakeContextProvider:
    async def provide(self, ctx):
        return [ContextItem("注入的上下文", source="fake")]


def test_minimal_fake_implementations_satisfy_every_protocol():
    """§2.7 步骤 4/5：每个 Protocol 的 minimal fake 必须结构匹配。"""
    checks = [
        (FakeModel(), Model),
        (FakeTool(), Tool),
        (FakeProvider(), ToolProvider),
        (FakeExecutor(), ToolExecutor),
        (FakeMemory(), Memory),
        (FakeContextProvider(), ContextProvider),
    ]
    for fake, protocol in checks:
        assert isinstance(fake, protocol), f"{type(fake).__name__} 不满足 {protocol.__name__}"
        assert protocol.__module__ == "agentkit.kernel.protocols"


@pytest.mark.anyio
async def test_fake_executor_drives_a_real_run_end_to_end():
    from agentkit.models.echo import ScriptedModel

    harness = RuntimeHarness(
        ScriptedModel([ToolCalls([ToolCall("1", "fake")]), Final("跑完")]),
        tools=[FakeTool()], executor=FakeExecutor(),
    )
    async with Agent(harness) as agent:
        ctx = await agent.run_ctx("用假执行器跑")

    assert ctx.result == "跑完"
    assert ctx.reason is TerminationReason.FINAL
    assert next(m for m in ctx.messages if m.role == "tool").content == "fake"


def test_kernel_protocol_module_is_still_the_only_protocol_home():
    assert {n for n in dir(protocols) if not n.startswith("_")} >= {
        "Model", "Tool", "ToolProvider", "ToolExecutor",
        "Memory", "ContextProvider", "Runtime", "PreparedInput",
    }
