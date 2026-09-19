"""修复 ① Permission × Retry —— 复现测试（V3.1 Commit A，故意保持红）。

问题（V3 缺陷）：

    PermissionDenied
        ↓
    error=True 的 ToolResult
        ↓
    RetryExecutor 对 error=True 重试
        ↓
    确定性拒绝被计进重试预算

V3.1 冻结 M2：RetryExecutor 只对**瞬时**执行失败重试；判断依据是
`ToolResult.metadata["error_class"]`：

    "policy_denied"        → 不重试
    其他值 / 无值           → 按默认语义（重试）

`error_class` 是第三方工具需要知道的字段名，因此它是冻结契约的一部分。
"""
from __future__ import annotations

import pytest

from agentkit.executor.builtin import ParallelExecutor
from agentkit.executor.permission import AllowListPolicy, PermissionExecutor
from agentkit.executor.retry import RetryExecutor
from agentkit.kernel.state import RunContext
from agentkit.kernel.types import ToolCall, ToolCalls, ToolResult
from agentkit.toolbox import Toolbox
from agentkit.tools.function import FunctionTool

pytestmark = pytest.mark.anyio


class CountingInner:
    """按 `error_class` 应答的 inner executor（记录每次尝试的 call id）。"""

    def __init__(self, error_class: str | None, *, error: bool = True) -> None:
        self.error_class = error_class
        self.error = error
        self.attempts: list[str] = []
        self.closed = 0

    async def execute(self, ctx: RunContext, action: ToolCalls) -> list[ToolResult]:
        self.attempts.extend(call.id for call in action.calls)
        metadata = {"error_class": self.error_class} if self.error_class else {}
        return [
            ToolResult(
                tool_call_id=call.id,
                content=f"[blocked by policy] {call.name}" if self.error else "ok",
                error=self.error,
                metadata=metadata,
            )
            for call in action.calls
        ]

    async def close(self) -> None:
        self.closed += 1


def one_call() -> ToolCalls:
    return ToolCalls([ToolCall(id="c1", name="bash", arguments={})])


async def test_policy_denied_does_not_consume_retry_budget():
    """Policy 拒绝不应被 RetryExecutor 重试：一次执行即返回。"""
    inner = CountingInner("policy_denied")
    executor = RetryExecutor(inner, max_attempts=3, backoff=0)

    result = await executor.execute(RunContext(task="t"), one_call())

    assert len(inner.attempts) == 1                    # 期望 1；V3 时是 3
    assert result[0].error is True
    assert result[0].metadata["error_class"] == "policy_denied"


async def test_undefined_error_class_keeps_the_default_retry_semantics():
    """未定义值走默认重试（保守：不破坏 V2 语义）。"""
    inner = CountingInner("transient")
    executor = RetryExecutor(inner, max_attempts=3, backoff=0)

    result = await executor.execute(RunContext(task="t"), one_call())

    assert len(inner.attempts) == 3
    assert result[0].error is True


async def test_missing_error_class_keeps_the_default_retry_semantics():
    """没有任何 error_class → 默认重试。"""
    inner = CountingInner(None)
    executor = RetryExecutor(inner, max_attempts=2, backoff=0)

    await executor.execute(RunContext(task="t"), one_call())

    assert len(inner.attempts) == 2


async def test_permission_executor_emits_the_frozen_error_class():
    """PermissionExecutor 输出必须带冻结字段与唯一冻结值（M2）。"""
    log: list[str] = []

    async def tool_fn(path: str) -> str:
        log.append(path)
        return "ok"

    toolbox = Toolbox([FunctionTool(tool_fn, name="write_file")])
    executor = PermissionExecutor(
        ParallelExecutor(toolbox), policy=AllowListPolicy({"read_file"}),
    )

    (result,) = await executor.execute(RunContext(task="t"), ToolCalls([
        ToolCall("c1", "write_file", {"path": "/x"}),
    ]))

    assert log == []                                   # 工具未被触达
    assert result.error is True
    assert result.metadata["error_class"] == "policy_denied"
    assert result.metadata["policy"] == "allow_list"    # 额外信息（不参与判断）
    assert "blocked" not in result.metadata             # 弱类型判断（V3 的 metadata.blocked）已废弃
