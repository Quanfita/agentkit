"""修复 ② Budget × Current Task —— 复现测试（V3.1 Commit A，故意保持红）。

Invariant（V3.1 冻结，写进 `context/engine.py` docstring）：

    当前用户任务消息（`ctx.messages` 里最后一条 user 消息）
    在任何 transform 后必须存在。

注意：这是 **invariant**，不是 algorithm。具体实现（保尾部 / 提权 / 其他）是算法。

问题（V3 行为）：

    ContextEngine.build() 输出：
      [ctx.system] [provider system] [history] [provider non-system] ← 长条目
    尾部贪心截断优先保留靠后的 provider 注入，**当前用户任务可能被挤掉**；
    provider 注入体积超过预算时更极端 —— 只留 system，任务整条消失。
"""
from __future__ import annotations

import pytest

from agentkit.context.transform import BudgetTransform, SlidingWindowTransform
from agentkit.kernel.types import Message

pytestmark = pytest.mark.anyio


def current_task(messages: list[Message]) -> list[Message]:
    return [m for m in messages if m.role == "user" and m.content == "current question"]


async def test_budget_transform_preserves_current_user_task_with_provider_injection():
    """文档 §3.1.3 的场景：system + 短 history + 长 provider 注入 + 当前任务。"""
    messages = [
        Message("system", "sys"),
        Message("user", "task"),
        Message("assistant", "answer"),
        # provider 注入（长；可能是 system 也可能是 user role）
        Message("system", "provider context: " + "x" * 10_000),
        Message("user", "current question"),
    ]

    result = await BudgetTransform(max_tokens=100).apply(messages)

    assert current_task(result), "current user task was dropped"


async def test_budget_transform_preserves_current_task_under_a_tight_budget():
    """预算紧到装不下尾部贪心的候选时，当前任务仍必须活下来。"""
    messages = [
        Message("system", "sys"),
        Message("user", "old question"),
        Message("assistant", "old answer"),
        Message("user", "current question"),
    ]

    result = await BudgetTransform(max_tokens=10).apply(messages)

    assert current_task(result), "current user task was dropped"


async def test_sliding_window_preserves_current_task_with_trailing_provider_items():
    """同一 invariant 的第二个实例：尾部窗口可能被靠后的 provider 注入占满。"""
    messages = [
        Message("system", "sys"),
        Message("user", "current question"),
        Message("system", "extra-1"),
        Message("system", "extra-2"),
    ]

    result = await SlidingWindowTransform(max_messages=2).apply(messages)

    assert current_task(result), "current user task was dropped"


async def test_generous_budget_keeps_the_original_order():
    """非回归：预算充足时顺序与内容不变（invariant 不该改变正常路径）。"""
    messages = [
        Message("system", "sys"),
        Message("user", "task"),
        Message("assistant", "answer"),
        Message("user", "current question"),
    ]

    result = await BudgetTransform(max_tokens=10_000).apply(messages)

    assert result == messages
