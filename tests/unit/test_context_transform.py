"""ContextTransform：4 个内置实现的行为 + 与 ContextEngine 的接入语义（V3 §六.1）。"""
from __future__ import annotations

import inspect
from collections.abc import Sequence

import pytest
from support import RuntimeHarness, make_ctx

from agentkit.api import ContextTransform
from agentkit.context.engine import ContextEngine
from agentkit.context.providers import CallableProvider, SystemPrompt
from agentkit.context.transform import (
    BudgetTransform,
    DedupeTransform,
    SlidingWindowTransform,
    SystemPriorityTransform,
)
from agentkit.kernel.types import ContextItem, Message
from agentkit.models.echo import EchoModel

ALL_TRANSFORMS = (
    BudgetTransform(10),
    SlidingWindowTransform(2),
    DedupeTransform(),
    SystemPriorityTransform(),
)


def roles_and_contents(messages: Sequence[Message]) -> list[tuple[str, str]]:
    return [(m.role, m.content) for m in messages]


class RecordingTransform:
    """记录 build() 传进来的 messages，再原样返回（证明 transform 是最后一步）。"""

    def __init__(self) -> None:
        self.seen: list[list[Message]] = []

    async def apply(self, messages: Sequence[Message]) -> list[Message]:
        self.seen.append(list(messages))
        return list(messages)


# ── Protocol 契约 ───────────────────────────────────────


def test_builtin_transforms_satisfy_the_context_transform_contract():
    for transform in ALL_TRANSFORMS:
        assert isinstance(transform, ContextTransform)
        # 冻结契约：apply(self, messages)，不接收 ctx。
        assert list(inspect.signature(type(transform).apply).parameters) == ["self", "messages"]


# ── SlidingWindowTransform ──────────────────────────────


@pytest.mark.anyio
async def test_sliding_window_keeps_the_tail_and_clears_on_non_positive():
    messages = [Message("user", str(i)) for i in range(5)]

    assert [m.content for m in await SlidingWindowTransform(2).apply(messages)] == ["3", "4"]
    assert [m.content for m in await SlidingWindowTransform(9).apply(messages)] == list("01234")
    # V3.1 invariant 取代 V3 的"清空"语义：窗口为 0 也要留下当前用户任务
    assert await SlidingWindowTransform(0).apply(messages) == [Message("user", "4")]


# ── BudgetTransform ─────────────────────────────────────


@pytest.mark.anyio
async def test_budget_transform_keeps_system_plus_the_longest_fitting_tail():
    messages = [
        Message("system", "规则"),            # 2
        Message("user", "aaaa"),             # 4 ┐
        Message("assistant", "bbbb"),        # 4 ├ 只能留下最后两条
        Message("user", "cccc"),             # 4 ┘
    ]

    built = await BudgetTransform(10).apply(messages)

    assert roles_and_contents(built) == [
        ("system", "规则"), ("assistant", "bbbb"), ("user", "cccc"),
    ]


@pytest.mark.anyio
async def test_budget_transform_never_drops_system_even_when_over_budget():
    messages = [
        Message("system", "很长的规则"),       # 5 > max_tokens
        Message("user", "aaaa"),
        Message("assistant", "bbbb"),
    ]

    # V3.1 invariant：超预算时 system 与当前用户任务都保留（V3 时只留 system）
    expected = [Message("system", "很长的规则"), Message("user", "aaaa")]
    assert await BudgetTransform(1).apply(messages) == expected
    assert await BudgetTransform(0).apply(messages) == expected


@pytest.mark.anyio
async def test_budget_transform_accepts_an_injected_estimator():
    messages = [
        Message("system", "S"),
        Message("user", "u1"),
        Message("assistant", "a1"),
        Message("user", "u2"),
    ]

    built = await BudgetTransform(3, estimate=lambda text: 1).apply(messages)

    assert roles_and_contents(built) == [
        ("system", "S"), ("assistant", "a1"), ("user", "u2"),
    ]


@pytest.mark.anyio
async def test_budget_transform_keeps_system_messages_in_their_original_order():
    messages = [
        Message("system", "A"),
        Message("user", "u"),
        Message("system", "B"),
    ]

    assert await BudgetTransform(99).apply(messages) == [
        Message("system", "A"), Message("system", "B"), Message("user", "u"),
    ]


# ── DedupeTransform ─────────────────────────────────────


@pytest.mark.anyio
async def test_dedupe_removes_only_adjacent_duplicates():
    messages = [
        Message("user", "a"),
        Message("user", "a"),
        Message("assistant", "b"),
        Message("user", "a"),
        Message("assistant", "b"),
        Message("assistant", "b"),
    ]

    assert [m.content for m in await DedupeTransform().apply(messages)] == ["a", "b", "a", "b"]


@pytest.mark.anyio
async def test_dedupe_compares_the_whole_message_and_keeps_the_first_one():
    first = Message("assistant", "", tool_calls=[], tool_call_id="c1")
    duplicate = Message("assistant", "", tool_calls=[], tool_call_id="c1")
    other_role = Message("user", "")

    deduped = await DedupeTransform().apply([first, duplicate, other_role])

    assert len(deduped) == 2
    assert deduped[0] is first
    assert deduped[1] is other_role


# ── SystemPriorityTransform ─────────────────────────────


@pytest.mark.anyio
async def test_system_priority_moves_system_messages_to_the_front_stably():
    messages = [
        Message("user", "u1"),
        Message("system", "s1"),
        Message("assistant", "a1"),
        Message("system", "s2"),
    ]

    assert await SystemPriorityTransform().apply(messages) == [
        Message("system", "s1"),
        Message("system", "s2"),
        Message("user", "u1"),
        Message("assistant", "a1"),
    ]


# ── 纯函数性：不修改调用者的序列 ─────────────────────────


def fingerprint(messages: Sequence[Message]) -> list[tuple]:
    """把序列的内容 + 身份压成可比较的快照（能发现 in-place 改动）。"""
    return [
        (id(m), m.role, m.content, tuple(m.tool_calls), m.tool_call_id) for m in messages
    ]


@pytest.mark.anyio
async def test_transforms_return_a_new_list_without_touching_the_input():
    messages = [
        Message("user", "a"),
        Message("user", "a"),
        Message("system", "s"),
        Message("assistant", "b"),
    ]
    snapshot = fingerprint(messages)

    for transform in ALL_TRANSFORMS:
        result = await transform.apply(messages)
        assert result is not messages

    assert fingerprint(messages) == snapshot


# ── 接入 ContextEngine ──────────────────────────────────


@pytest.mark.anyio
async def test_transform_receives_the_stitched_messages_as_the_last_step():
    """transform 的输入必须已经是 V2 冻结顺序的完整拼接结果。"""
    spy = RecordingTransform()
    engine = ContextEngine(
        [
            SystemPrompt("harness 规则"),
            CallableProvider(lambda ctx: [ContextItem("追加提示", role="user", source="extra")]),
        ],
        history_limit=2,
        transform=spy,
    )
    ctx = make_ctx("当前问题", system="run 级规则")
    ctx.messages = [
        Message("user", "旧1"), Message("assistant", "旧2"), Message("user", "当前问题"),
    ]

    built = await engine.build(ctx)

    assert len(spy.seen) == 1
    assert roles_and_contents(spy.seen[0]) == [
        ("system", "run 级规则"),
        ("system", "harness 规则"),
        ("assistant", "旧2"),
        ("user", "当前问题"),
        ("user", "追加提示"),
    ]
    assert built == spy.seen[0]


@pytest.mark.anyio
async def test_transform_output_is_what_build_returns():
    engine = ContextEngine(
        [SystemPrompt("harness 规则")], history_limit=1, transform=SlidingWindowTransform(1),
    )
    ctx = make_ctx("当前问题")
    ctx.messages = [Message("user", "旧"), Message("user", "当前问题")]

    assert await engine.build(ctx) == [Message("user", "当前问题")]


@pytest.mark.anyio
async def test_build_without_a_transform_keeps_the_v2_stitching():
    """没有 transform 时，build() 与 V2 的拼接结果逐字一致。"""
    engine = ContextEngine([SystemPrompt("S")], history_limit=5)
    ctx = make_ctx("t")

    assert roles_and_contents(await engine.build(ctx)) == [("system", "S"), ("user", "t")]


@pytest.mark.anyio
async def test_transform_error_bubbles_out_of_build_and_prepare():
    class Boom:
        async def apply(self, messages):
            raise RuntimeError("transform 崩了")

    engine = ContextEngine([SystemPrompt("S")], transform=Boom())
    with pytest.raises(RuntimeError, match="transform 崩了"):
        await engine.build(make_ctx())

    runtime = RuntimeHarness(EchoModel(), context=engine).build_runtime()
    with pytest.raises(RuntimeError, match="transform 崩了"):
        await runtime.prepare(make_ctx())
