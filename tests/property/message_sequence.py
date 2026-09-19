"""消息序列合法性的公共测试基础设施（V3.1 §3.1.4）。

契约：`docs/contracts/message_protocol.md`。五条 invariant 的精确定义：

```text
I1 Role Dependency              sequence 不以 tool 消息开头；
                                任何 tool 消息必须有前置 assistant(tool_calls)
I2 Call Identity Preservation   ∀tool 消息 m：∃ assistant a，
                                a.tool_calls 包含 m.tool_call_id
I3 Transform Closure            ∀Transform T，若 Valid(messages) 则 Valid(T(messages))
I4 Tool Result Ordering         若 assistant 声明 tool_calls=[A,B,...]，
                                在下一个 assistant 出现之前，
                                A/B/... 的 tool result 必须全部出现（顺序可交换）
I5 Call ID Uniqueness           单次 history 内所有 tool_call_id 唯一
```

I5 的范围是**单次 history**（`ctx.messages`），不是 session 全局：
history 可能被截断，截断后不同轮的 id 可以重复。

任何 Transform 实现都必须继承 `TransformProtocolTestBase`。
新增 invariant 前先读 `docs/contracts/message_protocol.md` 的「版本策略」：
新增 invariant 需要真实信号 + property test + Contract Change Review 三件套。
"""
from __future__ import annotations

import asyncio
from collections.abc import Callable, Sequence

from hypothesis import given, settings
from hypothesis import strategies as st

from agentkit.kernel.types import Message, ToolCall

__all__ = [
    "is_valid_message_sequence",
    "protocol_test",
    "run_transform",
    "TransformProtocolTestBase",
    "valid_message_history",
]

MAX_TURNS = 5

#: 内容字母表刻意限定为可打印 ASCII：I1–I5 只约束 role / id / 顺序，
#: 与字符集无关；限定后可保证 hypothesis 打出的反例在终端里可读、可复核。
_ALPHABET = st.characters(min_codepoint=0x20, max_codepoint=0x7E)
_TEXT = st.text(_ALPHABET, max_size=60)
_RESULT_TEXT = st.text(_ALPHABET, min_size=1, max_size=20)


@st.composite
def valid_message_history(draw: st.DrawFn) -> list[Message]:
    """生成**满足 I1–I5** 的消息序列。

    形状贴近 `ContextEngine.build()` 的真实输出：

      - 可选的前置 system 块；
      - 每轮 user + assistant；assistant 可能声明 1–3 个 tool_calls，
        随后跟齐全部 tool result（顺序合法时也可交换）；
      - 轮与轮之间可能插入 provider 注入的 system 消息。

    所有 assistant 声明的 call id 全局唯一（I5），
    每个声明都有对应的 tool 结果（I4），结果顺序可交换（I4 合法形态）。
    """
    messages: list[Message] = []

    for _ in range(draw(st.integers(min_value=0, max_value=2))):
        messages.append(Message("system", draw(_TEXT)))

    for turn in range(draw(st.integers(min_value=0, max_value=MAX_TURNS))):
        if turn and draw(st.booleans()):
            messages.append(Message("system", draw(_TEXT)))

        messages.append(Message("user", draw(_TEXT)))

        if not draw(st.booleans()):
            messages.append(Message("assistant", draw(_TEXT)))
            continue

        calls = [
            ToolCall(id=f"call_{turn}_{index}", name="probe", arguments={})
            for index in range(draw(st.integers(min_value=1, max_value=3)))
        ]
        messages.append(Message("assistant", draw(_TEXT), tool_calls=list(calls)))

        results = [
            Message("tool", draw(_RESULT_TEXT), tool_call_id=call.id) for call in calls
        ]
        if draw(st.booleans()):
            # I4 明确允许顺序交换：打乱后依然合法。
            draw(st.randoms()).shuffle(results)
        messages.extend(results)

    return messages


def _duplicates(values: Sequence[str | None]) -> list[str | None]:
    seen: set[str | None] = set()
    duplicated: list[str | None] = []
    for value in values:
        if value in seen and value not in duplicated:
            duplicated.append(value)
        seen.add(value)
    return duplicated


def _i4_reason(missing: set[str], messages: Sequence[Message], index: int) -> str:
    """区分 I4 的两种非法形态：结果「迟到」与结果「从未出现」。"""
    later = {m.tool_call_id for m in messages[index:] if m.role == "tool"}
    late = missing & later
    never = missing - later
    if late and never:
        return f"I4: tool results late: {sorted(late)}; never delivered: {sorted(never)}"
    if late:
        return f"I4: tool results late (after the next assistant): {sorted(late)}"
    return f"I4: tool results missing: {sorted(never)}"


def is_valid_message_sequence(messages: Sequence[Message]) -> tuple[bool, str]:
    """完整检查 I1–I5，返回 ``(是否合法, 原因)``。

    检查器本身也是契约的一部分：任何返回 ``False`` 的判定都必须指出违反了
    哪一条，且原因字符串带 invariant 编号（自检见
    ``tests/property/test_message_sequence.py``）。
    """
    # I1：序列不得以 tool 消息开头。
    if messages and messages[0].role == "tool":
        return False, (
            f"I1: sequence starts with a tool message "
            f"(tool_call_id={messages[0].tool_call_id!r})"
        )

    # I5：单次 history 内的 tool_call_id 唯一（声明的与作答的都算）。
    declared = [
        call.id for message in messages if message.role == "assistant"
        for call in message.tool_calls
    ]
    duplicated = _duplicates(declared)
    if duplicated:
        return False, f"I5: duplicate tool_call_id declared by assistant: {duplicated}"

    answered = [message.tool_call_id for message in messages if message.role == "tool"]
    duplicated = _duplicates(answered)
    if duplicated:
        return False, f"I5: duplicate tool result tool_call_id: {duplicated}"

    # I1 / I2 / I4：按「assistant(tool_calls) 声明的批次」逐块核对。
    open_call_ids: set[str] | None = None
    seen_ids: set[str] = set()
    for index, message in enumerate(messages):
        if message.role == "assistant":
            if open_call_ids is not None:
                missing = open_call_ids - seen_ids
                if missing:
                    return False, _i4_reason(missing, messages, index)
            open_call_ids = {call.id for call in message.tool_calls} if message.tool_calls else None
            seen_ids = set()
        elif message.role == "tool":
            if open_call_ids is None:
                return False, (
                    f"I1/I2: tool message without a preceding assistant(tool_calls) "
                    f"(tool_call_id={message.tool_call_id!r} at index {index})"
                )
            if message.tool_call_id not in open_call_ids:
                return False, (
                    f"I2: tool result {message.tool_call_id!r} is not declared by the "
                    f"current assistant(tool_calls)={sorted(open_call_ids)}"
                )
            seen_ids.add(message.tool_call_id)

    if open_call_ids is not None:
        missing = open_call_ids - seen_ids
        if missing:
            return False, _i4_reason(missing, messages, len(messages))

    return True, "ok"


def run_transform(transform: object, messages: Sequence[Message]) -> list[Message]:
    """同步包装 async ``ContextTransform.apply``（测试基类与临时脚本共用）。"""
    return asyncio.run(transform.apply(list(messages)))  # type: ignore[attr-defined]


#: `max_examples=500`：BudgetTransform 这类"截断切进 tool 批次"的形态只占
#: 生成样本的个位数百分比（实测 200 例中 5 例），默认 100 例存在漏检概率。
#: `deadline=None`：每个样例都要起一个事件循环（`asyncio.run`），
#: 200ms 的默认 deadline 在 Windows 上是噪音而非信号。
_TRANSFORM_SETTINGS = settings(max_examples=500, deadline=None)


class TransformProtocolTestBase:
    """I3（Transform Closure）的共享测试基类。

    子类实现 `make_transform()`，并用 `protocol_test()` 装上测试方法：

        class TestBudgetTransform(TransformProtocolTestBase):
            def make_transform(self): return BudgetTransform(max_tokens=100)
            test_transform_preserves_protocol = protocol_test()

    基类名不以 `Test` 开头，pytest 不会收集它本身。
    """

    def make_transform(self) -> object:
        """返回被测 Transform 实例（必须是 `agentkit.api.ContextTransform`）。"""
        raise NotImplementedError


def protocol_test() -> Callable[[TransformProtocolTestBase, list[Message]], None]:
    """构造一份 I3 closure 的 `@given` 测试方法（每个测试类调用一次）。

    `@given` / `@settings` 会**就地标记**传入的函数对象，因此同一个函数对象
    不能被多个测试类共享：共享会触发 hypothesis 的 `differing_executors`
    健康检查，而那条检查警告的是正确性问题（数据库回放不可复现），
    不能 suppress —— 只能在每次调用时构造新的函数对象。
    """

    def test_transform_preserves_protocol(
        test: TransformProtocolTestBase, history: list[Message]
    ) -> None:
        """I3：合法输入经 Transform 后必须依然合法。"""
        transform = test.make_transform()
        result = run_transform(transform, history)
        is_valid, reason = is_valid_message_sequence(result)
        assert is_valid, (
            f"I3 (transform closure) violated by {type(transform).__name__}: {reason}\n"
            f"input:  {history!r}\noutput: {result!r}"
        )

    return _TRANSFORM_SETTINGS(given(valid_message_history())(test_transform_preserves_protocol))
