"""V2 §9.2 语义测试 —— 4 个 P0 修复 + 执行层 + Streaming 的语义门禁。"""
from __future__ import annotations

import asyncio
import importlib.util
import json
from pathlib import Path

import pytest
from support import RuntimeHarness, make_ctx, record, runtime_for

from agentkit.agent import Agent
from agentkit.context.providers import CallableProvider, MemoryContext, SystemPrompt
from agentkit.executor.builtin import (
    ParallelExecutor,
    RetryExecutor,
    SequentialExecutor,
    TimeoutExecutor,
)
from agentkit.kernel.loop import agent_loop
from agentkit.kernel.state import RunContext, TerminationReason
from agentkit.kernel.types import (
    ContextItem,
    Final,
    Message,
    ToolCall,
    ToolCalls,
    ToolResult,
)
from agentkit.models.base import StreamingModel, TextDelta, ToolCallDelta
from agentkit.models.echo import EchoModel, ScriptedModel
from agentkit.observability import describe
from agentkit.toolbox import Toolbox
from agentkit.tools.function import FunctionTool

ROOT = Path(__file__).resolve().parents[1]


async def _aiter(items):
    for item in items:
        yield item


def load_streaming_example():
    """§4.3 的 StreamingRuntime 是文档指定的 examples/ 代码，按路径加载它。"""
    spec = importlib.util.spec_from_file_location(
        "streaming_harness_example", ROOT / "examples" / "streaming_harness.py",
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def add(a: int, b: int) -> str:
    """两数相加。"""
    return str(a + b)


def call(name: str, **arguments) -> ToolCalls:
    return ToolCalls([ToolCall("c1", name, arguments)])


# ── P0-A：ToolCalls.content 不能丢 ──────────────────────


@pytest.mark.anyio
async def test_openai_adapter_keeps_content_next_to_tool_calls():
    from types import SimpleNamespace

    from agentkit.models.openai import OpenAIModel

    raw = SimpleNamespace(
        id="c9", function=SimpleNamespace(name="add", arguments='{"a": 1, "b": 2}'),
    )
    msg = SimpleNamespace(content="我打算先调用工具。", tool_calls=[raw])

    class Completions:
        async def create(self, **kwargs):
            return SimpleNamespace(choices=[SimpleNamespace(message=msg)])

    model = OpenAIModel("gpt-x", client=SimpleNamespace(
        chat=SimpleNamespace(completions=Completions()),
    ))
    action = await model.generate([Message("user", "1+2")], [])

    assert isinstance(action, ToolCalls)
    assert action.content == "我打算先调用工具。"
    assert action.calls == [ToolCall("c9", "add", {"a": 1, "b": 2})]


def test_ollama_adapter_keeps_content_next_to_tool_calls():
    from agentkit.models.ollama import _to_ollama

    msg = Message("assistant", "先说一句", tool_calls=[ToolCall("c1", "add", {"a": 1})])
    assert _to_ollama(msg)["content"] == "先说一句"


@pytest.mark.anyio
async def test_observe_writes_action_content_into_the_assistant_message():
    runtime = runtime_for(EchoModel())
    ctx = RunContext(task="t")
    action = ToolCalls([ToolCall("c1", "add", {"a": 1, "b": 2})], content="我先算一下")

    await runtime.observe(ctx, action, [ToolResult(content="3")])

    assert ctx.messages[0] == Message("assistant", "我先算一下",
                                      tool_calls=[ToolCall("c1", "add", {"a": 1, "b": 2})])
    assert ctx.last_assistant is ctx.messages[0]


# ── P0-B：终止原因 ──────────────────────────────────────


@pytest.mark.anyio
async def test_run_ctx_returns_a_run_context():
    harness = RuntimeHarness(EchoModel())
    async with Agent(harness) as agent:
        ctx = await agent.run_ctx("任务")

    assert isinstance(ctx, RunContext)
    assert ctx.task == "任务"
    assert ctx.result == "echo: 任务"
    assert ctx.reason is TerminationReason.FINAL
    assert [m.role for m in ctx.messages] == ["user", "assistant"]


@pytest.mark.anyio
async def test_reason_final_allows_empty_result():
    harness = RuntimeHarness(ScriptedModel([Final("")]))
    async with Agent(harness) as agent:
        ctx = await agent.run_ctx("随便")
    assert ctx.result == "" and ctx.reason is TerminationReason.FINAL
    assert ctx.done is True and ctx.error is None


@pytest.mark.anyio
async def test_reason_max_iterations_after_exactly_n_rounds():
    model = ScriptedModel([call("add", a=1, b=2)])
    runs = []

    async def counted(a: int, b: int) -> str:
        runs.append(1)
        return "3"

    harness = RuntimeHarness(model, tools=[FunctionTool(counted, name="add")])
    async with Agent(harness) as agent:
        ctx = await agent.run_ctx("永不收敛", max_iterations=3)

    assert len(model.calls) == 3          # 恰好 3 轮 model → tool
    assert len(runs) == 3
    assert ctx.step == 3
    assert ctx.reason is TerminationReason.MAX_ITERATIONS
    assert ctx.done is False and ctx.result == ""


@pytest.mark.anyio
async def test_final_on_the_last_allowed_round_wins_over_max_iterations():
    """§1.5 优先级：最后一次 model 调用返回 Final 时，即使额度用尽也是 FINAL。"""
    model = ScriptedModel([call("add", a=1, b=2), Final("赶到终点了")])
    harness = RuntimeHarness(model, tools=[FunctionTool(add, name="add")])
    async with Agent(harness) as agent:
        ctx = await agent.run_ctx("卡点收尾", max_iterations=2)

    assert len(model.calls) == 2
    assert ctx.step == 1
    assert ctx.reason is TerminationReason.FINAL
    assert ctx.result == "赶到终点了"


@pytest.mark.anyio
async def test_reason_stopped_when_a_hook_sets_stop():
    harness = RuntimeHarness(EchoModel())
    harness.events.on("agent.start", lambda event, **p: setattr(p["ctx"], "stop", True))
    async with Agent(harness) as agent:
        ctx = await agent.run_ctx("被预算打断")

    assert ctx.reason is TerminationReason.STOPPED
    assert ctx.done is False and ctx.result == ""


@pytest.mark.anyio
async def test_run_ctx_propagates_errors_to_the_caller():
    class Boom:
        async def generate(self, messages, tools):
            raise RuntimeError("provider 500")

    harness = RuntimeHarness(Boom())
    async with Agent(harness) as agent:
        with pytest.raises(RuntimeError, match="provider 500"):
            await agent.run_ctx("会炸")


@pytest.mark.anyio
async def test_error_reason_is_recorded_on_the_context():
    class Boom:
        async def generate(self, messages, tools):
            raise RuntimeError("provider 500")

    runtime = RuntimeHarness(Boom()).build_runtime()
    ctx = make_ctx()
    with pytest.raises(RuntimeError):
        await agent_loop(runtime, ctx)

    assert ctx.reason is TerminationReason.ERROR
    assert ctx.done is True and isinstance(ctx.error, RuntimeError)


# ── P0-C：finish 异常保护 ───────────────────────────────


class _EmptyContext:
    """最小 ContextEngine：不注入任何 provider 产物。"""

    async def build(self, ctx):
        return ctx.messages[:]


class FinishBoom:
    """finish 抛错的内层 runtime，用来验证 loop 的 finally 保护。"""

    def __init__(self, model, events, error=None):
        from agentkit.runtime.default import DefaultRuntime
        self.events = events
        self._inner = DefaultRuntime(
            model=model, toolbox=Toolbox(), context=_EmptyContext(), events=events,
        )
        self._error = error or RuntimeError("memory 写失败")

    async def prepare(self, ctx):
        return await self._inner.prepare(ctx)

    async def reason(self, ctx, inp):
        return await self._inner.reason(ctx, inp)

    async def act(self, ctx, action):
        return await self._inner.act(ctx, action)

    async def observe(self, ctx, action, results):
        await self._inner.observe(ctx, action, results)

    async def finish(self, ctx):
        raise self._error

    async def close(self):
        await self._inner.close()


@pytest.mark.anyio
async def test_finish_error_is_recorded_and_announced():
    from agentkit.kernel.events import EventBus

    events = EventBus()
    runtime = FinishBoom(EchoModel(), events)
    rec = record(events)

    ctx = make_ctx("任务")
    await agent_loop(runtime, ctx)          # finish 失败不向外抛

    assert isinstance(ctx.error, RuntimeError)
    assert [e for e in rec.seen if e in ("agent.finish_error", "agent.end")] == [
        "agent.finish_error", "agent.end",
    ]
    assert rec.ctx_of("agent.finish_error") is ctx


@pytest.mark.anyio
async def test_finish_error_does_not_overwrite_the_original_error():
    from agentkit.kernel.events import EventBus

    class Boom:
        async def generate(self, messages, tools):
            raise ValueError("原始的错")

    events = EventBus()
    runtime = FinishBoom(Boom(), events)
    rec = record(events)

    ctx = make_ctx("任务")
    with pytest.raises(ValueError, match="原始的错"):
        await agent_loop(runtime, ctx)

    assert isinstance(ctx.error, ValueError)
    assert ctx.reason is TerminationReason.ERROR
    assert rec.seen[-2:] == ["agent.finish_error", "agent.end"]


# ── ContextEngine 的 system 语义（§5）───────────────────


@pytest.mark.anyio
async def test_call_level_system_is_the_first_system_message():
    harness = RuntimeHarness(
        EchoModel(),
        providers=[SystemPrompt("harness 规则"), CallableProvider(
            lambda ctx: [ContextItem("追加提示", role="user", source="extra")],
        )],
    )
    async with Agent(harness) as agent:
        await agent.run("任务", system="调用级规则")

    first = harness.model.calls[0][0]
    assert [m.role for m in first] == ["system", "system", "user", "user"]
    assert [m.content for m in first] == ["调用级规则", "harness 规则", "任务", "追加提示"]


@pytest.mark.anyio
async def test_multiple_system_messages_are_allowed_without_a_call_level_one():
    harness = RuntimeHarness(EchoModel(), providers=[
        SystemPrompt("A"), SystemPrompt("B"), MemoryContext(_OneItemMemory()),
    ])
    async with Agent(harness) as agent:
        await agent.run("任务")

    contents = [m.content for m in harness.model.calls[0][0]]
    assert contents == ["A", "B", "记忆项", "任务"]


class _OneItemMemory:
    async def recall(self, task):
        from agentkit.kernel.types import MemoryItem
        return [MemoryItem("记忆项")]

    async def remember(self, run):
        return None


# ── 执行层语义（§3）─────────────────────────────────────


def recording_tool(log: list, name: str = "probe", result: str = "ok", fail: bool = False):
    async def fn(**kwargs) -> ToolResult:
        log.append(name)
        if fail:
            return ToolResult(content=f"{name} failed", error=True)
        return ToolResult(content=f"{name}:{result}")

    return FunctionTool(fn, name=name)


@pytest.mark.anyio
async def test_executor_events_are_emitted():
    harness = RuntimeHarness(EchoModel(), tools=[FunctionTool(add, name="add")])
    rec = record(harness.events)
    runtime = harness.build_runtime()
    ctx = make_ctx("1+2")

    await runtime.act(ctx, call("add", a=1, b=2))

    assert rec.seen == ["executor.before", "executor.after"]
    assert rec.payloads[0][1]["action"].calls[0].name == "add"
    assert [r.content for r in rec.payloads[1][1]["results"]] == ["3"]


@pytest.mark.anyio
async def test_dispatch_binds_tool_call_id():
    async def tool_fn(**kwargs) -> str:
        return "value"

    toolbox = Toolbox([FunctionTool(tool_fn, name="t")])
    (result,) = await ParallelExecutor(toolbox).execute(
        RunContext(task="t"), ToolCalls([ToolCall("abc", "t")]),
    )
    assert result.tool_call_id == "abc" and result.content == "value"


@pytest.mark.anyio
async def test_dispatch_keeps_a_tool_supplied_tool_call_id():
    async def tool_fn(**kwargs) -> ToolResult:
        return ToolResult(tool_call_id="self-declared", content="value")

    toolbox = Toolbox([FunctionTool(tool_fn, name="t")])
    (result,) = await ParallelExecutor(toolbox).execute(
        RunContext(task="t"), ToolCalls([ToolCall("abc", "t")]),
    )
    assert result.tool_call_id == "self-declared"


@pytest.mark.anyio
async def test_sequential_and_parallel_produce_the_same_results():
    log: list[str] = []
    toolbox = Toolbox([recording_tool(log, "a"), recording_tool(log, "b", fail=True)])
    action = ToolCalls([ToolCall("1", "a"), ToolCall("2", "b"), ToolCall("3", "a")])

    seq = await SequentialExecutor(toolbox).execute(RunContext(task="t"), action)
    par = await ParallelExecutor(toolbox).execute(RunContext(task="t"), action)

    assert [(r.tool_call_id, r.content, r.error) for r in seq] == [
        (r.tool_call_id, r.content, r.error) for r in par
    ]
    assert [r.content for r in seq] == ["a:ok", "b failed", "a:ok"]
    assert [r.error for r in seq] == [False, True, False]


@pytest.mark.anyio
async def test_retry_executor_only_retries_the_failed_call():
    """关键：成功的 call 绝不能被重跑（副作用工具会灾难性重复）。"""
    log: list[str] = []

    async def flaky(**kwargs) -> ToolResult:
        log.append("flaky")
        return ToolResult(content="still bad", error=True)

    async def healthy(**kwargs) -> ToolResult:
        log.append("healthy")
        return ToolResult(content="fine")

    toolbox = Toolbox([FunctionTool(flaky, name="flaky"), FunctionTool(healthy, name="healthy")])
    action = ToolCalls([ToolCall("1", "flaky"), ToolCall("2", "healthy")])
    results = await RetryExecutor(
        SequentialExecutor(toolbox), max_attempts=3, backoff=0,
    ).execute(RunContext(task="t"), action)

    assert log.count("healthy") == 1                     # 关键断言
    assert log.count("flaky") == 3                       # 首次 + 2 次 retry
    assert [r.error for r in results] == [True, False]
    assert [r.tool_call_id for r in results] == ["1", "2"]


@pytest.mark.anyio
async def test_retry_executor_stops_retrying_after_success():
    log: list[str] = []

    async def recovering(**kwargs) -> ToolResult:
        log.append("try")
        return ToolResult(content="bad" if len(log) < 2 else "good",
                          error=len(log) < 2)

    toolbox = Toolbox([FunctionTool(recovering, name="recovering")])
    (result,) = await RetryExecutor(
        SequentialExecutor(toolbox), max_attempts=5, backoff=0,
    ).execute(RunContext(task="t"), ToolCalls([ToolCall("1", "recovering")]))
    assert result.content == "good" and result.error is False
    assert len(log) == 2


@pytest.mark.anyio
async def test_retry_executor_never_retries_cancellation():
    log: list[str] = []

    async def cancelled(**kwargs) -> ToolResult:
        log.append("cancelled")
        raise asyncio.CancelledError

    toolbox = Toolbox([FunctionTool(cancelled, name="cancelled")])
    with pytest.raises(asyncio.CancelledError):
        await RetryExecutor(
            SequentialExecutor(toolbox), max_attempts=3, backoff=0,
        ).execute(RunContext(task="t"), ToolCalls([ToolCall("1", "cancelled")]))
    assert len(log) == 1


@pytest.mark.anyio
async def test_timeout_executor_is_per_call_by_default():
    """关键：一个 call 超时，不影响同一批里其他 call 的结果。"""
    async def slow(**kwargs) -> str:
        await asyncio.sleep(0.3)
        return "slow"

    async def quick(**kwargs) -> str:
        return "quick"

    toolbox = Toolbox([FunctionTool(slow, name="slow"), FunctionTool(quick, name="quick")])
    executor = TimeoutExecutor(SequentialExecutor(toolbox), seconds=0.05)
    results = await executor.execute(
        RunContext(task="t"),
        ToolCalls([ToolCall("1", "slow"), ToolCall("2", "quick")]),
    )

    assert results[0].error is True
    assert results[0].content == "timeout after 0.05s"
    assert results[0].tool_call_id == "1"
    assert results[1] == ToolResult(tool_call_id="2", content="quick")


@pytest.mark.anyio
async def test_timeout_executor_leaves_fast_calls_alone():
    log: list[str] = []
    toolbox = Toolbox([recording_tool(log)])
    results = await TimeoutExecutor(SequentialExecutor(toolbox), seconds=5).execute(
        RunContext(task="t"), ToolCalls([ToolCall("1", "probe")]),
    )
    assert results == [ToolResult(tool_call_id="1", content="probe:ok")]
    assert log == ["probe"]


@pytest.mark.anyio
async def test_timeout_retry_and_retry_timeout_have_different_semantics():
    """§3.5 关键：组合顺序决定 timeout 是共享还是各自独立。"""
    shared_log: list[str] = []

    async def slow_fail(**kwargs) -> ToolResult:
        shared_log.append("try")
        await asyncio.sleep(0.05)
        return ToolResult(content="bad", error=True)

    def build(log):
        return Toolbox([FunctionTool(slow_fail, name="t")]), ToolCalls([ToolCall("1", "t")])

    shared_box, shared_action = build(shared_log)
    shared = TimeoutExecutor(
        RetryExecutor(SequentialExecutor(shared_box), max_attempts=4, backoff=0),
        seconds=0.12,
    )
    (shared_result,) = await shared.execute(RunContext(task="t"), shared_action)

    per_call_log: list[str] = []

    async def slow_fail_2(**kwargs) -> ToolResult:
        per_call_log.append("try")
        await asyncio.sleep(0.05)
        return ToolResult(content="bad", error=True)

    per_call_box = Toolbox([FunctionTool(slow_fail_2, name="t")])
    per_call = RetryExecutor(
        TimeoutExecutor(SequentialExecutor(per_call_box), seconds=1),
        max_attempts=4, backoff=0,
    )
    (per_call_result,) = await per_call.execute(RunContext(task="t"), shared_action)

    # 共享 timeout：整个 retry 过程被砍断，重试没跑满
    assert shared_result.content == "timeout after 0.12s"
    assert len(shared_log) < 4
    # 独立 timeout：每次重试都拿到完整额度，重试跑满
    assert per_call_result.content == "bad"
    assert per_call_result.error is True
    assert len(per_call_log) == 4


@pytest.mark.anyio
async def test_sequential_executor_stops_before_the_next_call():
    log: list[str] = []
    ctx = RunContext(task="t")

    async def stopping(**kwargs) -> ToolResult:
        log.append("first")
        ctx.stop = True
        return ToolResult(content="done")

    toolbox = Toolbox([FunctionTool(stopping, name="first"), recording_tool(log, "second")])
    results = await SequentialExecutor(toolbox).execute(
        ctx, ToolCalls([ToolCall("1", "first"), ToolCall("2", "second")]),
    )
    assert [r.tool_call_id for r in results] == ["1"]     # 未开始的 call 不执行
    assert log == ["first"]


@pytest.mark.anyio
async def test_parallel_executor_does_not_start_a_stopped_batch():
    log: list[str] = []
    toolbox = Toolbox([recording_tool(log)])
    ctx = RunContext(task="t")
    ctx.stop = True

    assert await ParallelExecutor(toolbox).execute(ctx, ToolCalls([ToolCall("1", "probe")])) == []
    assert log == []


@pytest.mark.anyio
async def test_executor_unknown_tool_and_tool_exception_are_error_results():
    log: list[str] = []
    toolbox = Toolbox([recording_tool(log, "boom", fail=True)])
    action = ToolCalls([ToolCall("1", "ghost"), ToolCall("2", "boom")])

    results = await ParallelExecutor(toolbox).execute(RunContext(task="t"), action)
    assert results[0] == ToolResult(tool_call_id="1", content="unknown tool: ghost", error=True)
    assert results[1] == ToolResult(tool_call_id="2", content="boom failed", error=True)


@pytest.mark.anyio
async def test_cancellation_and_infrastructure_errors_are_not_confused():
    """§3.6：工具失败 → error 结果；基础设施失败 → 异常。"""
    async def cancelled(**kwargs) -> ToolResult:
        raise asyncio.CancelledError

    toolbox = Toolbox([FunctionTool(cancelled, name="cancelled")])
    with pytest.raises(asyncio.CancelledError):
        await SequentialExecutor(toolbox).execute(
            RunContext(task="t"), ToolCalls([ToolCall("1", "cancelled")]),
        )


@pytest.mark.anyio
async def test_stop_checkpoint_still_wins_over_the_executor():
    harness = RuntimeHarness(ScriptedModel([call("add", a=1, b=2), Final("x")]),
                             tools=[FunctionTool(add, name="add")])
    runtime = harness.build_runtime()
    rec = record(harness.events)
    harness.events.on("model.after", lambda event, **p: setattr(p["ctx"], "stop", True))

    ctx = make_ctx()
    await agent_loop(runtime, ctx)

    assert "executor.before" not in rec.seen
    assert ctx.reason is TerminationReason.STOPPED


# ── Toolbox 的 V2 收缩（§3.10）──────────────────────────


@pytest.mark.anyio
async def test_toolbox_lookup_returns_tool_or_none():
    log: list[str] = []
    toolbox = Toolbox([recording_tool(log)])
    assert (await toolbox.lookup("probe")).spec.name == "probe"
    assert await toolbox.lookup("ghost") is None


@pytest.mark.anyio
async def test_toolbox_execute_still_works_as_a_deprecated_shim():
    log: list[str] = []
    toolbox = Toolbox([recording_tool(log)])
    (result,) = await toolbox.execute(ToolCalls([ToolCall("1", "probe")]))
    assert result.content == "probe:ok" and result.tool_call_id == "1"
    assert "Deprecated" in Toolbox.execute.__doc__


# ── Streaming（§4）──────────────────────────────────────


def test_delta_types_and_streaming_protocol():
    from agentkit.models.base import Delta

    assert Delta is not None
    assert TextDelta("x").text == "x"
    delta = ToolCallDelta(index=0, id="c1", name="add", args_delta="{}")
    assert (delta.index, delta.id, delta.name, delta.args_delta) == (0, "c1", "add", "{}")
    assert not isinstance(EchoModel(), StreamingModel)


@pytest.mark.anyio
async def test_openai_stream_normalizes_chunks():
    from types import SimpleNamespace

    from agentkit.models.openai import OpenAIModel

    def chunk(content=None, tool_calls=None):
        return SimpleNamespace(choices=[SimpleNamespace(delta=SimpleNamespace(
            content=content, tool_calls=tool_calls,
        ))])

    fn = lambda **kw: SimpleNamespace(**kw)   # noqa: E731
    chunks = [
        chunk(content="我"),
        chunk(content="来算"),
        chunk(tool_calls=[fn(index=0, id="c1", function=fn(name="add", arguments='{"a": 20,'))]),
        chunk(tool_calls=[fn(index=0, id=None, function=fn(name=None, arguments='"b": 22}'))]),
        SimpleNamespace(choices=[]),
    ]

    class Completions:
        async def create(self, **kwargs):
            assert kwargs["stream"] is True
            return _aiter(chunks)

    model = OpenAIModel("gpt-x", client=SimpleNamespace(
        chat=SimpleNamespace(completions=Completions()),
    ))
    deltas = [d async for d in model.stream([Message("user", "1+2")], [])]

    assert deltas[0] == TextDelta("我")
    assert deltas[1] == TextDelta("来算")
    assert deltas[2] == ToolCallDelta(index=0, id="c1", name="add", args_delta='{"a": 20,')
    assert deltas[3] == ToolCallDelta(index=0, id=None, name=None, args_delta='"b": 22}')
    assert len(deltas) == 4


@pytest.mark.anyio
async def test_anthropic_stream_normalizes_events():
    from types import SimpleNamespace

    from agentkit.models.anthropic import AnthropicModel

    events = [
        SimpleNamespace(type="message_start"),
        SimpleNamespace(
            type="content_block_start", index=0,
            content_block=SimpleNamespace(type="text", text=""),
        ),
        SimpleNamespace(
            type="content_block_delta", index=0,
            delta=SimpleNamespace(type="text_delta", text="20 + 22"),
        ),
        SimpleNamespace(
            type="content_block_start", index=1,
            content_block=SimpleNamespace(type="tool_use", id="tu1", name="add"),
        ),
        SimpleNamespace(
            type="content_block_delta", index=1,
            delta=SimpleNamespace(type="input_json_delta", partial_json='{"a": 20,'),
        ),
        SimpleNamespace(
            type="content_block_delta", index=1,
            delta=SimpleNamespace(type="input_json_delta", partial_json='"b": 22}'),
        ),
        SimpleNamespace(type="content_block_stop", index=1),
    ]

    class Messages:
        async def create(self, **kwargs):
            assert kwargs["stream"] is True
            return _aiter(events)

    model = AnthropicModel("claude-x", client=SimpleNamespace(messages=Messages()))
    deltas = [d async for d in model.stream([Message("user", "1+2")], [])]

    assert deltas == [
        TextDelta("20 + 22"),
        ToolCallDelta(index=1, id="tu1", name="add"),
        ToolCallDelta(index=1, args_delta='{"a": 20,'),
        ToolCallDelta(index=1, args_delta='"b": 22}'),
    ]


@pytest.mark.anyio
async def test_ollama_stream_normalizes_ndjson():
    from agentkit.models.ollama import OllamaModel

    lines = [
        '{"message": {"content": "我"}}',
        '{"message": {"content": "来算"}}',
        json.dumps({"message": {"tool_calls": [
            {"id": "c1", "function": {"name": "add", "arguments": {"a": 20, "b": 22}}},
        ]}}),
        "",
    ]

    class Response:
        def raise_for_status(self):
            return None

        async def aiter_lines(self):
            for line in lines:
                yield line

    class Stream:
        async def __aenter__(self):
            return Response()

        async def __aexit__(self, *exc):
            return None

    class Client:
        def __init__(self):
            self.payload = None

        def stream(self, method, path, json):
            self.payload = json
            return Stream()

    client = Client()
    model = OllamaModel("qwen3:8b", client=client)
    deltas = [d async for d in model.stream([Message("user", "1+2")], [])]

    assert client.payload["stream"] is True
    assert deltas[:2] == [TextDelta("我"), TextDelta("来算")]
    assert deltas[2] == ToolCallDelta(index=0, id="c1", name="add",
                                      args_delta='{"a": 20, "b": 22}')


@pytest.mark.anyio
async def test_streaming_runtime_assembles_deltas_into_an_action():
    """§4.3：StreamingRuntime 把 Delta 流组装成 Action，Kernel 毫不知情。"""
    example = load_streaming_example()
    harness = example.StreamingHarness()
    deltas = []

    async with Agent(harness) as agent:
        harness.events.on("model.delta", lambda event, **p: deltas.append(p["text"]))
        ctx = await agent.run_ctx("20+22 等于几")

    assert ctx.result == "20 + 22 = 42。"
    assert ctx.reason is TerminationReason.FINAL
    assert ctx.step == 1                     # 流式 tool call 被真正执行了
    assert deltas == ["我来算：", "20 + 22 = ", "42。"]
    tool_message = next(m for m in ctx.messages if m.role == "tool")
    assert tool_message.content == "42" and tool_message.tool_call_id == "c1"


@pytest.mark.anyio
async def test_streaming_runtime_falls_back_for_non_streaming_models():
    example = load_streaming_example()
    harness = RuntimeHarness(EchoModel())
    runtime = example.StreamingRuntime(
        model=harness.model, toolbox=harness.toolbox, context=harness.context,
    )
    ctx = make_ctx("回退")
    action = await runtime.reason(ctx, await runtime.prepare(ctx))

    assert action == Final("echo: 回退")


# ── 观测层覆盖新事件（§7）───────────────────────────────


def test_describe_covers_the_new_v2_events():
    ctx = make_ctx("任务")
    assert describe("executor.before", {
        "ctx": ctx, "action": ToolCalls([ToolCall("1", "add")]),
    }) == "calls=['add']"
    assert describe("executor.after", {
        "ctx": ctx, "results": [ToolResult(content="ok"), ToolResult(error=True)],
    }) == "results=['ok', 'error']"
    assert describe("model.delta", {"text": "片段"}) == "text='片段'"
    assert describe("agent.finish_error", {"error": RuntimeError("x")}) == "RuntimeError('x')"


@pytest.mark.anyio
async def test_tracer_reports_executor_events_in_order():
    from agentkit.observability import Tracer

    lines: list[str] = []
    harness = RuntimeHarness(
        ScriptedModel([call("add", a=1, b=2), Final("3")]),
        tools=[FunctionTool(add, name="add")],
    )
    harness.events.on("*", Tracer(printer=lines.append))
    async with Agent(harness) as agent:
        await agent.run("1+2")

    assert any(line.startswith("[trace] executor.before calls=['add']") for line in lines)
    assert any(line.startswith("[trace] executor.after results=['ok']") for line in lines)
