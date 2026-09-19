"""ContextEngine / ContextProvider / Memory / DefaultRuntime 的装配语义。"""
from __future__ import annotations

import pytest
from support import RuntimeHarness, make_ctx

from agentkit.context.providers import CallableProvider, MemoryContext, SystemPrompt
from agentkit.kernel.state import RunContext
from agentkit.kernel.types import (
    ContextItem,
    MemoryInput,
    MemoryItem,
    Message,
    ToolCall,
    ToolCalls,
    ToolResult,
)
from agentkit.memory.simple import InMemoryMemory, NullMemory
from agentkit.models.echo import EchoModel
from agentkit.runtime.default import ContextEngine
from agentkit.tools.function import FunctionTool


@pytest.mark.anyio
async def test_null_memory_is_a_no_op():
    mem = NullMemory()
    assert await mem.recall("t") == []
    assert await mem.remember(MemoryInput(task="t", result="r")) is None


@pytest.mark.anyio
async def test_in_memory_memory_keeps_results_only():
    mem = InMemoryMemory(max_items=2, top_k=1)
    await mem.remember(MemoryInput(task="a", result=""))
    assert mem.items == []                       # 没有结果的 run 不入库
    for task in ("a", "b", "c"):
        await mem.remember(MemoryInput(task=task, result=f"r-{task}"))
    assert [i.content for i in mem.items] == ["r-b", "r-c"]
    assert [i.kind for i in mem.items] == ["episodic", "episodic"]
    assert [i.content for i in await mem.recall("anything")] == ["r-c"]   # top_k=1


@pytest.mark.anyio
async def test_system_prompt_provider_shape():
    (item,) = await SystemPrompt("规则").provide(make_ctx())
    assert item == ContextItem("规则", role="system", source="system")


@pytest.mark.anyio
async def test_memory_context_translates_items_without_leaking_message():
    class Mem:
        async def recall(self, task):
            return [MemoryItem("上次的结论", kind="episodic", metadata={"task": task})]

        async def remember(self, run):
            raise AssertionError("not written here")

    (item,) = await MemoryContext(Mem()).provide(make_ctx("旧任务"))
    assert item == ContextItem(
        "上次的结论", role="system", source="memory", kind="episodic",
    )


@pytest.mark.anyio
async def test_callable_provider_accepts_sync_and_async():
    sync = CallableProvider(lambda ctx: [ContextItem(f"sync:{ctx.task}")])
    async def areminder(ctx):
        return [ContextItem(f"async:{ctx.task}")]

    ctx = make_ctx("T")
    assert (await sync.provide(ctx))[0].content == "sync:T"
    assert (await CallableProvider(areminder).provide(ctx))[0].content == "async:T"
    assert await CallableProvider(lambda ctx: None).provide(ctx) == []


@pytest.mark.anyio
async def test_context_engine_ordering_and_history_limit():
    engine = ContextEngine([
        SystemPrompt("harness 规则"),
        CallableProvider(lambda ctx: [ContextItem("追加提示", role="user", source="extra")]),
    ], history_limit=2)

    ctx = make_ctx("当前问题", system="run 级规则")
    ctx.messages = [
        Message("user", "旧1"), Message("assistant", "旧2"), Message("user", "当前问题"),
    ]
    built = await engine.build(ctx)

    assert [(m.role, m.content) for m in built] == [
        ("system", "run 级规则"),
        ("system", "harness 规则"),
        ("assistant", "旧2"),
        ("user", "当前问题"),
        ("user", "追加提示"),
    ]


@pytest.mark.anyio
async def test_context_engine_add_is_chainable():
    engine = ContextEngine()
    assert engine.add(SystemPrompt("a")) is engine
    assert [i.content for i in await engine.providers[0].provide(make_ctx())] == ["a"]


@pytest.mark.anyio
async def test_default_runtime_prepare_hands_over_messages_and_specs():
    mem = InMemoryMemory()
    harness = RuntimeHarness(
        EchoModel(),
        tools=[FunctionTool(lambda a: a, name="id")],
        memory=mem,
        providers=[SystemPrompt("S"), MemoryContext(mem)],
    )
    runtime = harness.build_runtime()
    await mem.remember(MemoryInput(task="旧", result="记得这件事"))
    ctx = make_ctx("新任务")

    prepared = await runtime.prepare(ctx)
    assert [m.content for m in prepared.messages][:2] == ["S", "记得这件事"]
    assert [t.name for t in prepared.tools] == ["id"]


@pytest.mark.anyio
async def test_default_runtime_observe_writes_both_message_shapes():
    runtime = RuntimeHarness(EchoModel()).build_runtime()
    ctx = RunContext(task="t")           # 空历史：observe 负责写消息

    await runtime.observe(ctx, ToolCalls([ToolCall("c1", "t")]), [ToolResult(content="ok")])
    assert ctx.messages[0].role == "assistant"
    assert ctx.messages[0].tool_calls == [ToolCall("c1", "t")]
    assert ctx.messages[1] == Message("tool", "ok", tool_call_id="c1")
    assert ctx.last_assistant is ctx.messages[0]


@pytest.mark.anyio
async def test_default_runtime_observe_marks_errors_for_the_model():
    runtime = RuntimeHarness(EchoModel()).build_runtime()
    ctx = RunContext(task="t")
    await runtime.observe(
        ctx, ToolCalls([ToolCall("c1", "t")]), [ToolResult(content="boom", error=True)],
    )
    assert ctx.messages[1].content == "[tool_error] boom"


@pytest.mark.anyio
async def test_default_runtime_close_closes_the_toolbox_providers():
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
    runtime = harness.build_runtime()
    await runtime.close()
    assert provider.closed == 1


@pytest.mark.anyio
async def test_default_runtime_without_memory_does_not_crash_finish():
    runtime = RuntimeHarness(EchoModel(), memory=None).build_runtime()
    assert await runtime.finish(make_ctx()) is None
