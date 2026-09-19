"""history 尾部截断必须落在合法边界上（真机验证发现的缺陷）。

现象：长工具链的 run 超过 `history_limit` 后，`ctx.messages[-N:]` 会把
`assistant(tool_calls)` 与它随后的 `tool` 结果**从中间切开**，报文以孤儿 `tool`
消息开头。OpenAI / DeepSeek 直接 400：

    Messages with role 'tool' must be a response to a preceding message with 'tool_calls'

这个缺陷从 V1 就在（`history_limit` 默认 40），只在长工具链下暴露 —— 仿真客户端
与短用例都抓不到，是真机 Conformance 纪律的产出。
"""
from __future__ import annotations

import pytest
from support import make_ctx

from agentkit.context.engine import ContextEngine
from agentkit.kernel.types import Message, ToolCall


def _tool_batch(index: int) -> list[Message]:
    call = ToolCall(f"c{index}", "probe", {"i": index})
    return [
        Message("assistant", "", tool_calls=[call]),
        Message("tool", f"result-{index}", tool_call_id=call.id),
    ]


def _assert_pairing_is_valid(messages: list[Message]) -> None:
    """每条 `tool` 消息都必须能对应到紧邻的、声明了同 id 的 assistant(tool_calls)。"""
    open_ids: set[str] = set()
    for message in messages:
        if message.role == "tool":
            assert message.tool_call_id in open_ids, (
                f"孤儿 tool 消息（tool_call_id={message.tool_call_id}）会被 provider 拒绝"
            )
        else:
            open_ids = {c.id for c in message.tool_calls}
    if messages and messages[0].role == "tool":
        raise AssertionError("报文以 tool 消息开头")


@pytest.mark.anyio
async def test_history_truncation_never_orphans_tool_messages():
    ctx = make_ctx("开始")
    ctx.messages = [Message("user", "开始")]
    for i in range(25):
        ctx.messages += _tool_batch(i)

    engine = ContextEngine(history_limit=40)
    built = await engine.build(ctx)

    _assert_pairing_is_valid(built)
    assert len(built) == 40                       # 截断仍然生效
    assert built[0].role == "assistant"           # 开头是完整的 assistant(tool_calls)
    assert built[1].role == "tool"
    assert built[-1].tool_call_id == "c24"        # 尾部保留


@pytest.mark.anyio
async def test_short_history_is_untouched():
    ctx = make_ctx("开始")
    ctx.messages = [Message("user", "开始"), *_tool_batch(0)]

    built = await ContextEngine(history_limit=40).build(ctx)

    _assert_pairing_is_valid(built)
    assert [m.role for m in built] == ["user", "assistant", "tool"]


@pytest.mark.anyio
async def test_truncation_that_lands_exactly_on_a_boundary_keeps_the_batch():
    ctx = make_ctx("开始")
    ctx.messages = [Message("user", "开始")] + _tool_batch(0) + _tool_batch(1)

    built = await ContextEngine(history_limit=3).build(ctx)

    # 窗口是 [t0, a1, t1]：删掉孤儿 t0，留下完整的第二批
    _assert_pairing_is_valid(built)
    assert [m.role for m in built] == ["assistant", "tool"]
    assert built[-1].tool_call_id == "c1"


@pytest.mark.anyio
async def test_all_orphaned_prefix_is_dropped_not_left_empty():
    """极端情形：窗口内全是孤儿 tool 消息 → 宁可空，也不发非法报文。"""
    ctx = make_ctx("开始")
    ctx.messages = [Message("user", "开始"), *_tool_batch(0)]

    built = await ContextEngine(history_limit=1).build(ctx)   # 窗口 = [t0]

    assert built == []                            # 宁可空，也不发非法报文
    _assert_pairing_is_valid(built)
