"""V2 §9.3 可替换性测试 —— 关键门禁。

DefaultRuntime 必须接受**任意** ToolExecutor；Executor 借用 Toolbox，
不拥有它的生命周期。这两条是 V2「执行策略可替换」的全部价值所在。
"""
from __future__ import annotations

import pytest
from support import RuntimeHarness, record

from agentkit.agent import Agent
from agentkit.executor.builtin import (
    ParallelExecutor,
    RetryExecutor,
    SequentialExecutor,
    TimeoutExecutor,
)
from agentkit.kernel.protocols import ToolExecutor
from agentkit.kernel.state import RunContext, TerminationReason
from agentkit.kernel.types import Final, ToolCall, ToolCalls, ToolResult
from agentkit.models.echo import ScriptedModel
from agentkit.toolbox import Toolbox
from agentkit.tools.function import FunctionTool


def add(a: int, b: int) -> str:
    """两数相加。"""
    return str(a + b)


def calls(*names: str) -> ToolCalls:
    return ToolCalls([ToolCall(str(i), n) for i, n in enumerate(names)])


def add_call(a: int = 20, b: int = 22) -> ToolCalls:
    return ToolCalls([ToolCall("0", "add", {"a": a, "b": b})])


class FakeExecutor:
    """完全自定义的执行策略：不碰 Toolbox，直接给结果。"""

    def __init__(self, results: list[ToolResult], events: list[str] | None = None):
        self.results = results
        self.seen: list[str] = []
        self.closed = 0
        self.events = events if events is not None else self.seen

    async def execute(self, ctx: RunContext, action: ToolCalls) -> list[ToolResult]:
        self.seen.append(f"execute:{len(action.calls)}")
        return list(self.results)

    async def execute_one(self, ctx: RunContext, call: ToolCall) -> ToolResult:
        self.seen.append(f"one:{call.name}")
        return ToolResult(tool_call_id=call.id, content=f"fake:{call.name}")

    async def close(self) -> None:
        self.closed += 1


class CountingProvider:
    """记录 close 次数的 ToolProvider —— 用来证明 Executor 不动 Toolbox。"""

    def __init__(self, tools=()):
        self._tools = list(tools)
        self.closed = 0

    async def tools(self):
        return list(self._tools)

    async def close(self):
        self.closed += 1


@pytest.mark.anyio
async def test_runtime_accepts_an_arbitrary_tool_executor():
    """关键：DefaultRuntime 只认 Protocol，不认具体实现。"""
    executor = FakeExecutor([ToolResult(tool_call_id="0", content="伪造结果")])
    model = ScriptedModel([calls("add"), Final("收到")])
    harness = RuntimeHarness(model, executor=executor, tools=[FunctionTool(add, name="add")])

    async with Agent(harness) as agent:
        ctx = await agent.run_ctx("1+2")

    assert isinstance(executor, ToolExecutor)          # 结构匹配协议
    assert executor.seen == ["execute:1"]              # 走的是我的执行器
    assert ctx.result == "收到"
    tool_message = next(m for m in ctx.messages if m.role == "tool")
    assert tool_message.content == "伪造结果"
    assert "add" not in executor.seen                  # 真实工具从未被执行


@pytest.mark.anyio
async def test_runtime_defaults_to_the_parallel_executor():
    """§3.7：不传 executor 时缺省是 ParallelExecutor(toolbox)。"""
    harness = RuntimeHarness(
        ScriptedModel([Final("不需要工具")]), tools=[FunctionTool(add, name="add")],
    )
    runtime = harness.build_runtime()

    assert isinstance(runtime._executor, ParallelExecutor)
    assert runtime._executor.toolbox is harness.toolbox


@pytest.mark.anyio
@pytest.mark.parametrize("build", [
    lambda box: SequentialExecutor(box),
    lambda box: ParallelExecutor(box),
    lambda box: RetryExecutor(ParallelExecutor(box), max_attempts=2, backoff=0),
    lambda box: TimeoutExecutor(RetryExecutor(ParallelExecutor(box), max_attempts=2, backoff=0),
                                seconds=5),
])
async def test_swapping_executors_keeps_the_run_correct(build):
    """串行 / 并行 / Retry(Parallel) / Timeout(Retry(Parallel)) 都跑出同一结果。"""
    log: list[str] = []

    async def counted(a: int, b: int) -> str:
        log.append("add")
        return str(a + b)

    model = ScriptedModel([add_call(), Final("3")])
    harness = RuntimeHarness(
        model, tools=[FunctionTool(counted, name="add")], executor_factory=build,
    )

    async with Agent(harness) as agent:
        ctx = await agent.run_ctx("1+2")

    assert log == ["add"]                      # 成功路径下重试不会重复执行
    assert ctx.result == "3"
    assert ctx.reason is TerminationReason.FINAL
    assert next(m for m in ctx.messages if m.role == "tool").content == "42"


@pytest.mark.anyio
async def test_a_partial_batch_is_tolerated_by_observe():
    """§3.8 的副产品：批内 stop 会让结果短于 calls，observe 必须能处理前缀。"""
    executor = FakeExecutor([ToolResult(tool_call_id="0", content="只做了第一个")])
    harness = RuntimeHarness(
        ScriptedModel([
            ToolCalls([ToolCall("0", "add", {"a": 1, "b": 2}),
                       ToolCall("1", "add", {"a": 3, "b": 4})]),
            Final("收尾"),
        ]),
        executor=executor, tools=[FunctionTool(add, name="add")],
    )

    async with Agent(harness) as agent:
        ctx = await agent.run_ctx("两个加法")

    tool_messages = [m for m in ctx.messages if m.role == "tool"]
    assert [m.content for m in tool_messages] == ["只做了第一个"]
    assert ctx.result == "收尾"


@pytest.mark.anyio
async def test_executor_close_does_not_close_the_toolbox():
    """关键：Executor.close() 只关自己的资源，Toolbox 归 Runtime。"""
    provider = CountingProvider()
    box = Toolbox().add_provider(provider)
    executor = ParallelExecutor(box)

    await executor.execute(RunContext(task="t"), ToolCalls([]))
    await executor.close()

    assert provider.closed == 0                            # 关键断言
    assert await box.specs() == []                         # Toolbox 仍然可用


@pytest.mark.anyio
async def test_runtime_close_does_close_both_executor_and_toolbox():
    provider = CountingProvider()
    box = Toolbox().add_provider(provider)
    executor = FakeExecutor([])
    harness = RuntimeHarness(ScriptedModel([Final("x")]), executor=executor)
    harness.toolbox = box

    runtime = harness.build_runtime()
    await runtime.close()

    assert (executor.closed, provider.closed) == (1, 1)


@pytest.mark.anyio
async def test_executor_borrows_the_toolbox_and_keeps_it_usable():
    """Executor 借用而非拥有：关掉它之后 Toolbox 照常工作。"""
    box = Toolbox([FunctionTool(add, name="add")])
    executor = SequentialExecutor(box)
    await executor.close()

    assert (await box.lookup("add")) is not None
    (result,) = await ParallelExecutor(box).execute(
        RunContext(task="t"), ToolCalls([ToolCall("1", "add", {"a": 20, "b": 22})]),
    )
    assert result.content == "42"


@pytest.mark.anyio
async def test_decorator_close_propagates_to_the_inner_executor():
    closed: list[str] = []

    class Inner:
        async def execute(self, ctx, action):
            return []

        async def execute_one(self, ctx, call):
            return ToolResult(tool_call_id=call.id, content="inner")

        async def close(self):
            closed.append("inner")

    await TimeoutExecutor(RetryExecutor(Inner(), max_attempts=2), seconds=5).close()
    assert closed == ["inner"]


@pytest.mark.anyio
async def test_retry_keeps_side_effecting_calls_single_shot_across_executors():
    """不管外面套几层装饰器，成功的副作用工具都只执行一次。"""
    writes: list[str] = []

    async def write_file(path: str) -> str:
        writes.append(path)
        return f"wrote {path}"

    box = Toolbox([FunctionTool(write_file, name="write_file")])
    executor = TimeoutExecutor(
        RetryExecutor(ParallelExecutor(box), max_attempts=4, backoff=0), seconds=5,
    )
    action = ToolCalls([ToolCall("1", "write_file", {"path": "a.txt"}),
                        ToolCall("2", "write_file", {"path": "b.txt"})])

    results = await executor.execute(RunContext(task="t"), action)
    assert sorted(writes) == ["a.txt", "b.txt"]           # 每个副作用只发生一次
    assert [r.error for r in results] == [False, False]


@pytest.mark.anyio
async def test_executor_events_fire_once_per_batch_with_a_custom_executor():
    executor = FakeExecutor([ToolResult(tool_call_id="0", content="x")])
    harness = RuntimeHarness(
        ScriptedModel([calls("add"), Final("done")]), executor=executor,
        tools=[FunctionTool(add, name="add")],
    )
    rec = record(harness.events)

    async with Agent(harness) as agent:
        await agent.run_ctx("1+2")

    assert rec.seen.count("executor.before") == 1
    assert rec.seen.count("executor.after") == 1
    assert rec.seen.index("executor.before") < rec.seen.index("executor.after")


@pytest.mark.anyio
async def test_toolbox_execute_and_executor_path_agree():
    """V1 兼容入口与 V2 默认执行器在无 stop 场景下行为一致。"""
    log: list[str] = []

    async def probe(tag: str = "") -> str:
        log.append(tag)
        return f"probe:{tag}"

    box = Toolbox([FunctionTool(probe, name="probe")])
    action = ToolCalls([ToolCall("1", "probe", {"tag": "a"}),
                        ToolCall("2", "probe", {"tag": "b"})])

    legacy = await box.execute(action)
    modern = await ParallelExecutor(box).execute(RunContext(task="t"), action)

    assert [(r.tool_call_id, r.content, r.error) for r in legacy] == [
        (r.tool_call_id, r.content, r.error) for r in modern
    ]
    assert log == ["a", "b", "a", "b"]
