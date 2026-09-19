"""`is_valid_message_sequence` 的自检（检查器可信度基础，V3.1 §3.1.4）。

property test 的结论只有在其检查器本身正确时才有意义。这里用**手写序列**
逐条钉住 I1–I5 的判定：每条非法输入不仅要求判 `False`，还要求给出的
**原因字符串指向正确的 invariant**（判错原因等于判错）。

同时覆盖生成器：`valid_message_history()` 产出的历史必须全部通过检查器，
否则 Transform 测试会因输入不合法而误报。
"""
from __future__ import annotations

import pytest
from hypothesis import find, given

from agentkit.kernel.types import Message, ToolCall

from .message_sequence import is_valid_message_sequence, valid_message_history

# ── 用例构造小工具 ─────────────────────────────────────


def _declare(*call_ids: str) -> Message:
    """assistant 声明一批 tool_calls。"""
    return Message("assistant", "", tool_calls=[ToolCall(i, "probe", {}) for i in call_ids])


def _result(call_id: str) -> Message:
    """tool 结果消息。"""
    return Message("tool", f"result-{call_id}", tool_call_id=call_id)


VALID_CASES: list[tuple[str, list[Message]]] = [
    ("empty-history", []),
    ("single-user", [Message("user", "hi")]),
    ("batch-in-declared-order", [
        _declare("A", "B"), _result("A"), _result("B"), Message("assistant", "next"),
    ]),
    ("batch-swapped-order", [
        _declare("A", "B"), _result("B"), _result("A"), Message("assistant", "next"),
    ]),
    ("batch-at-end-of-history", [_declare("A", "B"), _result("B"), _result("A")]),
    ("two-batches", [
        _declare("A"), _result("A"), _declare("B"), _result("B"), Message("assistant", "done"),
    ]),
    ("system-first-and-injected-mid-history", [
        Message("system", "sys"), Message("user", "u"), _declare("A"),
        Message("system", "injected"), _result("A"), Message("assistant", "done"),
    ]),
    # I4 只在**下一个 assistant** 处结算批次，因此批次中间夹 message 不违规
    # （这也是 ContextEngine 拼接 provider 非 system 消息后的真实形状）。
    ("non-assistant-message-inside-batch", [
        _declare("A"), Message("user", "injected"), _result("A"), Message("assistant", "done"),
    ]),
]

INVALID_CASES: list[tuple[str, list[Message], str]] = [
    ("starts-with-tool", [_result("A")], "I1"),
    ("orphan-tool-after-plain-assistant", [
        Message("assistant", "no calls"), _result("A"),
    ], "I1/I2"),
    ("orphan-tool-after-completed-batch", [
        _declare("A"), _result("A"), Message("assistant", "x"), _result("B"),
    ], "I1/I2"),
    ("result-not-declared-by-current-batch", [_declare("A"), _result("B")], "I2"),
    ("missing-b-before-next-assistant", [
        _declare("A", "B"), _result("A"), Message("assistant", "next"),
    ], "I4: tool results missing"),
    ("no-results-at-all", [
        _declare("A", "B"), Message("assistant", "next"),
    ], "I4: tool results missing"),
    ("late-b-after-next-assistant", [
        _declare("A", "B"), _result("A"), Message("assistant", "next"), _result("B"),
    ], "I4: tool results late"),
    ("late-a-unanswered-b", [
        _declare("A", "B"), _result("B"), Message("assistant", "next"), _result("A"),
    ], "I4: tool results late"),
    ("late-and-never-delivered", [
        _declare("A", "B", "C"), _result("B"), Message("assistant", "next"), _result("A"),
    ], "never delivered: ['C']"),
    ("missing-results-at-end-of-history", [_declare("A", "B"), _result("A")],
     "I4: tool results missing"),
    ("duplicate-declared-call-id", [
        _declare("A", "A"), _result("A"),
    ], "I5: duplicate tool_call_id declared"),
    ("duplicate-result-call-id", [_declare("A"), _result("A"), _result("A")],
     "I5: duplicate tool result"),
]


@pytest.mark.parametrize(
    ("name", "messages", "expected_reason"),
    INVALID_CASES,
    ids=[case[0] for case in INVALID_CASES],
)
def test_invalid_sequences_are_rejected(name: str, messages: list[Message],
                                       expected_reason: str) -> None:
    is_valid, reason = is_valid_message_sequence(messages)
    assert is_valid is False, f"{name}: checker accepted an invalid sequence ({reason})"
    assert expected_reason in reason, f"{name}: reason {reason!r} lacks {expected_reason!r}"


@pytest.mark.parametrize(
    ("name", "messages"),
    VALID_CASES,
    ids=[case[0] for case in VALID_CASES],
)
def test_valid_sequences_are_accepted(name: str, messages: list[Message]) -> None:
    is_valid, reason = is_valid_message_sequence(messages)
    assert is_valid is True, f"{name}: checker rejected a valid sequence ({reason})"
    assert reason == "ok"


@given(valid_message_history())
def test_generator_only_produces_valid_histories(history: list[Message]) -> None:
    """生成器本身不得产出非法历史（否则 Transform 测试会误报）。"""
    is_valid, reason = is_valid_message_sequence(history)
    assert is_valid, f"generator produced an invalid history: {reason}\n{history!r}"


def test_generator_covers_tool_batches_and_system_messages() -> None:
    """防止生成器退化：没有 tool 批次的历史会让 I3 测试变成空转。

    用 `find`（"最小可满足样本"搜索）而不是 `@given`：
    「存在性」断言在 `@given` 下会被 shrink 成 `[]` 而失去意义。
    """
    find(valid_message_history(), _has_tool_batch)
    find(valid_message_history(), _has_tool_result)
    find(valid_message_history(), lambda history: any(m.role == "system" for m in history))


def _has_tool_batch(history: list[Message]) -> bool:
    return any(m.role == "assistant" and m.tool_calls for m in history)


def _has_tool_result(history: list[Message]) -> bool:
    return any(m.role == "tool" for m in history)
