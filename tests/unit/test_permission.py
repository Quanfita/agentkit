"""PermissionExecutor + 内置 Policy（V3 §6.3）的语义门禁。"""
from __future__ import annotations

import pytest
from support import make_ctx

from agentkit.executor.builtin import ParallelExecutor
from agentkit.executor.permission import (
    AllowListPolicy,
    DenyListPolicy,
    InteractivePolicy,
    PermissionExecutor,
)
from agentkit.executor.retry import RetryExecutor
from agentkit.executor.timeout import TimeoutExecutor
from agentkit.kernel.protocols import ToolExecutor
from agentkit.kernel.state import RunContext
from agentkit.kernel.types import ToolCall, ToolCalls, ToolResult, ToolSpec
from agentkit.toolbox import Toolbox
from agentkit.tools.function import FunctionTool


def add(a: int, b: int) -> str:
    """两数相加。"""
    return str(a + b)


class CountingTool:
    """记录被真正执行了几次。"""

    def __init__(self, name: str = "counter") -> None:
        self.spec = ToolSpec(name=name, description="counts executions")
        self.runs = 0

    async def run(self, arguments: dict) -> ToolResult:
        self.runs += 1
        return ToolResult(content="ok")


class SpyProvider:
    """Toolbox 的 Provider：close() 被执行说明 Toolbox 被关了。"""

    def __init__(self) -> None:
        self.close_count = 0

    async def tools(self) -> list:
        return []

    async def close(self) -> None:
        self.close_count += 1


class BoomPolicy:
    async def allow(self, call: ToolCall, ctx: RunContext) -> bool:
        raise RuntimeError("policy backend down")


def call(name: str = "add", id_: str = "c1") -> ToolCall:
    return ToolCall(id=id_, name=name, arguments={"a": 1, "b": 2})


async def run(executor, *calls: ToolCall, ctx: RunContext | None = None):
    return await executor.execute(ctx or make_ctx("run"), ToolCalls(list(calls)))


def test_permission_executor_is_a_tool_executor():
    ex = PermissionExecutor(ParallelExecutor(Toolbox()), AllowListPolicy({"add"}))
    assert isinstance(ex, ToolExecutor)


@pytest.mark.anyio
async def test_allow_list_permits_listed_and_blocks_others():
    policy = AllowListPolicy({"add"})
    ctx = make_ctx("run")
    assert await policy.allow(call("add"), ctx) is True
    assert await policy.allow(call("rm"), ctx) is False


@pytest.mark.anyio
async def test_deny_list_blocks_listed_and_permits_others():
    policy = DenyListPolicy({"rm"})
    ctx = make_ctx("run")
    assert await policy.allow(call("rm"), ctx) is False
    assert await policy.allow(call("add"), ctx) is True


@pytest.mark.anyio
async def test_interactive_policy_accepts_sync_verdicts():
    ctx = make_ctx("run")
    assert await InteractivePolicy(lambda c, cx: True).allow(call(), ctx) is True
    assert await InteractivePolicy(lambda c, cx: False).allow(call(), ctx) is False


@pytest.mark.anyio
async def test_interactive_policy_accepts_async_and_string_verdicts():
    seen: list[tuple[ToolCall, RunContext]] = []

    async def ask(c, cx) -> str:
        seen.append((c, cx))
        return "y"

    ctx = make_ctx("run")
    assert await InteractivePolicy(ask).allow(call(), ctx) is True
    assert len(seen) == 1
    assert seen[0][0] == call()
    assert seen[0][1] is ctx

    async def refuse(c, cx) -> str:
        return "n"

    assert await InteractivePolicy(refuse).allow(call(), ctx) is False


@pytest.mark.anyio
async def test_interactive_policy_reads_denial_strings_as_denial():
    # `bool("n") is True`：门禁必须自己判字符串，否则用户答 n 也会放行。
    def policy(verdict):
        return InteractivePolicy(lambda c, cx, v=verdict: v)

    for verdict in ("n", "N", " no ", "false", "0", ""):
        assert await policy(verdict).allow(call(), make_ctx()) is False
    for verdict in ("y", "Y", "yes", "anything"):
        assert await policy(verdict).allow(call(), make_ctx()) is True


@pytest.mark.anyio
async def test_blocked_call_returns_the_frozen_shape():
    counter = CountingTool()
    ex = PermissionExecutor(ParallelExecutor(Toolbox([counter])), DenyListPolicy({"counter"}))
    (result,) = await run(ex, call("counter"))

    assert result.tool_call_id == "c1"
    assert result.content == "[blocked by policy] counter"
    assert result.error is True
    assert result.metadata == {"error_class": "policy_denied", "policy": "deny_list"}
    assert counter.runs == 0                      # 拒绝的 call 不触达 Tool


@pytest.mark.anyio
async def test_allowed_call_reaches_the_inner_executor():
    counter = CountingTool()
    ex = PermissionExecutor(ParallelExecutor(Toolbox([counter])), AllowListPolicy({"counter"}))
    (result,) = await run(ex, call("counter"))

    assert (result.content, result.error, result.metadata) == ("ok", False, {})
    assert counter.runs == 1


@pytest.mark.anyio
async def test_blocked_call_is_a_result_not_an_interruption():
    counter = CountingTool()
    toolbox = Toolbox([counter, FunctionTool(add)])
    ex = PermissionExecutor(ParallelExecutor(toolbox), DenyListPolicy({"counter"}))
    results = await run(ex, call("counter", "c1"), call("add", "c2"))

    assert len(results) == 2                      # 拒绝不中断 batch
    assert results[0].metadata["error_class"] == "policy_denied"
    assert results[1].content == "3"


@pytest.mark.anyio
async def test_policy_receives_the_call_and_the_ctx_explicitly():
    seen: list[tuple[ToolCall, RunContext]] = []

    class Recording:
        async def allow(self, c, ctx):
            seen.append((c, ctx))
            return True

    ex = PermissionExecutor(ParallelExecutor(Toolbox([CountingTool()])), Recording())
    ctx = make_ctx("run")
    await run(ex, call("counter"), ctx=ctx)

    assert seen == [(call("counter"), ctx)]


@pytest.mark.anyio
async def test_policy_failure_propagates_and_never_runs_the_tool():
    counter = CountingTool()
    ex = PermissionExecutor(ParallelExecutor(Toolbox([counter])), BoomPolicy())
    with pytest.raises(RuntimeError, match="policy backend down"):
        await run(ex, call("counter"))
    assert counter.runs == 0


@pytest.mark.anyio
async def test_close_forwards_to_inner_without_closing_the_borrowed_toolbox():
    provider = SpyProvider()
    toolbox = Toolbox([CountingTool()]).add_provider(provider)
    ex = PermissionExecutor(ParallelExecutor(toolbox), AllowListPolicy({"counter"}))

    await ex.close()

    assert provider.close_count == 0              # Executor 借用 Toolbox，不拥有它
    assert await toolbox.lookup("counter") is not None


@pytest.mark.anyio
async def test_blocked_call_does_not_consume_retry_budget_when_wrapped_by_retry():
    """V3.1 修复 ①：确定性拒绝不再被 RetryExecutor 重试（V3 时这里断言次数是 3）。"""
    calls: list[ToolCall] = []

    class Counting:
        async def allow(self, c, ctx):
            calls.append(c)
            return False

    inner = CountingTool()
    ex = RetryExecutor(
        PermissionExecutor(ParallelExecutor(Toolbox([inner])), Counting()),
        max_attempts=3, backoff=0,
    )
    (result,) = await run(ex, call("counter"))

    assert len(calls) == 1                        # policy_denied → 一次执行即返回
    assert result.error is True
    assert result.metadata["error_class"] == "policy_denied"
    assert inner.runs == 0


@pytest.mark.anyio
async def test_permission_composes_with_timeout_in_the_v2_5_order():
    counter = CountingTool()
    blocked = TimeoutExecutor(
        PermissionExecutor(ParallelExecutor(Toolbox([counter])), DenyListPolicy({"counter"})),
        seconds=5,
    )
    allowed = TimeoutExecutor(
        PermissionExecutor(ParallelExecutor(Toolbox([counter])), AllowListPolicy({"counter"})),
        seconds=5,
    )

    (denied,) = await run(blocked, call("counter"))
    (ok,) = await run(allowed, call("counter"))

    assert denied.error is True
    assert denied.metadata == {"error_class": "policy_denied", "policy": "deny_list"}
    assert (ok.content, ok.error) == ("ok", False)
    assert counter.runs == 1
