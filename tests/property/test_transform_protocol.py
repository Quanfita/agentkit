"""四个内置 `ContextTransform` 的 I3 closure 测试（V3.1 §3.1.4）。

契约：`I3 Transform Closure`（`docs/contracts/message_protocol.md`）
—— 若 `Valid(messages)`，则 `Valid(T(messages))`，对所有 Transform 成立。

本文件**故意可能变红**：红的含义是"该 Transform 破坏了消息序列契约"，
不是"测试基础设施有问题"。检查器本身的可信度由
`tests/property/test_message_sequence.py` 的自检保证。
"""
from __future__ import annotations

from agentkit.context.transform import (
    BudgetTransform,
    DedupeTransform,
    SlidingWindowTransform,
    SystemPriorityTransform,
)

from .message_sequence import TransformProtocolTestBase, protocol_test


class TestBudgetTransform(TransformProtocolTestBase):
    """100 字符预算足以触发截断，暴露「截断切进 tool 批次」的形态。"""

    def make_transform(self) -> BudgetTransform:
        return BudgetTransform(max_tokens=100)

    test_transform_preserves_protocol = protocol_test()


class TestSlidingWindowTransform(TransformProtocolTestBase):
    """5 条窗口会切掉序列前缀，暴露孤儿 tool 消息。"""

    def make_transform(self) -> SlidingWindowTransform:
        return SlidingWindowTransform(max_messages=5)

    test_transform_preserves_protocol = protocol_test()


class TestDedupeTransform(TransformProtocolTestBase):
    def make_transform(self) -> DedupeTransform:
        return DedupeTransform()

    test_transform_preserves_protocol = protocol_test()


class TestSystemPriorityTransform(TransformProtocolTestBase):
    def make_transform(self) -> SystemPriorityTransform:
        return SystemPriorityTransform()

    test_transform_preserves_protocol = protocol_test()
