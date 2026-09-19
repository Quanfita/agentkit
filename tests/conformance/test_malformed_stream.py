"""Malformed Stream 边界测试（adapter-level，用 fake provider，**不真机**）。

不属于 6 × N 真机矩阵（§3.5）：它验证的是 Adapter 在分片边界与断流下的
归一化契约，因此用 fake client 离线跑，并且**不打** `conformance` marker。

覆盖：

  1. args_delta 分片落在 JSON 中间
  2. args_delta 分片落在 UTF-8 多字节字符中间
  3. 断流（stream 提前结束）
  4. 多 call 交错（index 交错到达）

组装语义复用 `assertions.assemble_tool_calls()`，与被判定的真机场景同一套实现。
"""
from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from agentkit.kernel.types import Message, ToolSpec
from agentkit.models.anthropic import AnthropicModel
from agentkit.models.base import Delta, TextDelta, ToolCallDelta
from agentkit.models.openai import OpenAIModel

from .assertions import ContractViolation, assemble_tool_calls

MESSAGES = [Message("user", "北京天气怎么样？")]
SCHEMA = {"type": "object", "properties": {"city": {"type": "string"}}}
TOOLS = [ToolSpec("weather", "查询天气", SCHEMA)]


# ── fake provider（只提供 stream 需要的形状） ───────────


class FakeStream:
    """按顺序产出 chunk；遇到 `BaseException` 元素就抛出（模拟断流）。"""

    def __init__(self, items: list) -> None:
        self._items = list(items)

    async def _iterate(self):
        for item in self._items:
            if isinstance(item, BaseException):
                raise item
            yield item

    def __aiter__(self):
        return self._iterate()


class FakeOpenAIClient:
    def __init__(self, items: list) -> None:
        self.chat = SimpleNamespace(completions=self)
        self._items = items
        self.close_count = 0

    async def create(self, **kwargs):
        self.kwargs = kwargs
        return FakeStream(self._items)

    async def close(self) -> None:
        self.close_count += 1


class FakeAnthropicClient:
    def __init__(self, events: list) -> None:
        self.messages = self
        self._events = events
        self.close_count = 0

    async def create(self, **kwargs):
        self.kwargs = kwargs
        return FakeStream(self._events)

    async def close(self) -> None:
        self.close_count += 1


def _chunk(content=None, tool_calls=None) -> SimpleNamespace:
    delta = SimpleNamespace(content=content, tool_calls=tool_calls)
    return SimpleNamespace(choices=[SimpleNamespace(delta=delta)])


def _keepalive() -> SimpleNamespace:
    """OpenAI 会发 `choices == []` 的保活 chunk。"""
    return SimpleNamespace(choices=[])


def _call(index=0, id=None, name=None, arguments=None) -> SimpleNamespace:
    return SimpleNamespace(
        index=index, id=id, function=SimpleNamespace(name=name, arguments=arguments)
    )


def _event(event_type: str, **fields) -> SimpleNamespace:
    return SimpleNamespace(type=event_type, **fields)


async def _collect(model) -> list[Delta]:
    return [delta async for delta in model.stream(MESSAGES, TOOLS)]


def _tool_deltas(deltas: list[Delta]) -> list[ToolCallDelta]:
    return [delta for delta in deltas if isinstance(delta, ToolCallDelta)]


# ── 1. JSON 中间分片 ────────────────────────────────────


@pytest.mark.anyio
async def test_openai_args_delta_split_mid_json_still_assembles():
    parts = ['{"ci', 'ty": "北', '京"}']
    client = FakeOpenAIClient([
        _chunk(tool_calls=[_call(0, "call_1", "weather", parts[0])]),
        *[_chunk(tool_calls=[_call(0, arguments=part)]) for part in parts[1:]],
    ])

    deltas = await _collect(OpenAIModel("gpt-test", client=client))

    calls = assemble_tool_calls(_tool_deltas(deltas))
    assert [call.id for call in calls] == ["call_1"]
    assert [call.name for call in calls] == ["weather"]
    assert calls[0].arguments() == {"city": "北京"}


@pytest.mark.anyio
async def test_anthropic_input_json_delta_fragments_assemble_by_index():
    client = FakeAnthropicClient([
        _event("message_start"),
        _event(
            "content_block_start",
            index=0,
            content_block=SimpleNamespace(type="tool_use", id="toolu_1", name="weather"),
        ),
        _event(
            "content_block_delta",
            index=0,
            delta=SimpleNamespace(type="input_json_delta", partial_json='{"ci'),
        ),
        _event(
            "content_block_delta",
            index=0,
            delta=SimpleNamespace(type="input_json_delta", partial_json='ty": "北京"}'),
        ),
        _event("content_block_stop", index=0),
        _event("message_stop"),
    ])

    deltas = await _collect(AnthropicModel("claude-test", client=client))

    assert not [delta for delta in deltas if isinstance(delta, TextDelta)]
    calls = assemble_tool_calls(_tool_deltas(deltas))
    assert [call.id for call in calls] == ["toolu_1"]
    assert [call.name for call in calls] == ["weather"]
    assert calls[0].arguments() == {"city": "北京"}


# ── 2. UTF-8 中间分片 ───────────────────────────────────


@pytest.mark.anyio
async def test_openai_args_delta_split_mid_utf8_is_byte_lossless():
    """分片落在多字节字符中间时，Adapter 必须逐字透传、不做解码补偿。

    用 `surrogateescape` 表达「半个 UTF-8 序列」：这是字节边界切分在 Python
    `str` 层的真实表示。Adapter 不能吞、不能改写、不能提前解析半截 JSON；
    字节级重组必须无损。
    """
    raw = json.dumps({"city": "北京"}, ensure_ascii=False).encode("utf-8")
    cut = raw.index("北".encode()) + 1                       # 落在「北」的 UTF-8 序列中间
    left = raw[:cut].decode("utf-8", "surrogateescape")
    right = raw[cut:].decode("utf-8", "surrogateescape")
    client = FakeOpenAIClient([
        _chunk(tool_calls=[_call(0, "call_1", "weather", left)]),
        _chunk(tool_calls=[_call(0, arguments=right)]),
    ])

    deltas = await _collect(OpenAIModel("gpt-test", client=client))

    fragments = [delta.args_delta or "" for delta in _tool_deltas(deltas)]
    assert "".join(fragments) == left + right                # 逐字透传
    rebuilt = "".join(fragments).encode("utf-8", "surrogateescape")
    assert rebuilt == raw                                    # 字节级无损
    assert json.loads(rebuilt.decode("utf-8")) == {"city": "北京"}


@pytest.mark.anyio
async def test_openai_text_delta_split_mid_utf8_passes_through():
    raw = json.dumps(["北", "京"], ensure_ascii=False).encode("utf-8")
    cut = raw.index("北".encode()) + 2
    parts = [
        raw[:cut].decode("utf-8", "surrogateescape"),
        raw[cut:].decode("utf-8", "surrogateescape"),
    ]
    client = FakeOpenAIClient([_chunk(content=part) for part in parts])

    deltas = await _collect(OpenAIModel("gpt-test", client=client))

    assert all(isinstance(delta, TextDelta) for delta in deltas)
    joined = "".join(delta.text for delta in deltas)
    assert joined == parts[0] + parts[1]
    assert json.loads(joined.encode("utf-8", "surrogateescape").decode("utf-8")) == [
        "北",
        "京",
    ]


# ── 3. 断流 ─────────────────────────────────────────────


@pytest.mark.anyio
async def test_openai_stream_cut_early_propagates_and_keeps_partial_deltas():
    boom = ConnectionError("stream cut")
    client = FakeOpenAIClient([
        _chunk(content="北"),
        _chunk(tool_calls=[_call(0, "call_1", "weather", '{"ci')]),
        boom,
    ])
    deltas: list[Delta] = []

    with pytest.raises(ConnectionError):
        async for delta in OpenAIModel("gpt-test", client=client).stream(MESSAGES, TOOLS):
            deltas.append(delta)

    assert [delta.text for delta in deltas if isinstance(delta, TextDelta)] == ["北"]
    calls = assemble_tool_calls(_tool_deltas(deltas))
    assert calls[0].name == "weather"
    # 断流不产生「半成品 ToolCall」：拼装结果不可解析，不得被补齐成合法 dict
    with pytest.raises(json.JSONDecodeError):
        json.loads(calls[0].args_json)


@pytest.mark.anyio
async def test_anthropic_stream_cut_early_propagates_partial_tool_delta():
    boom = TimeoutError("sse cut")
    client = FakeAnthropicClient([
        _event(
            "content_block_start",
            index=0,
            content_block=SimpleNamespace(type="tool_use", id="toolu_1", name="weather"),
        ),
        _event(
            "content_block_delta",
            index=0,
            delta=SimpleNamespace(type="input_json_delta", partial_json='{"ci'),
        ),
        boom,
    ])
    deltas: list[Delta] = []

    with pytest.raises(TimeoutError):
        async for delta in AnthropicModel("claude-test", client=client).stream(MESSAGES, TOOLS):
            deltas.append(delta)

    calls = assemble_tool_calls(_tool_deltas(deltas))
    assert calls[0].id == "toolu_1"
    assert calls[0].args_json == '{"ci'


# ── 4. 多 call 交错 ─────────────────────────────────────


@pytest.mark.anyio
async def test_openai_interleaved_parallel_calls_assemble_by_index():
    client = FakeOpenAIClient([
        _chunk(tool_calls=[
            _call(0, "call_a", "weather", '{"ci'),
            _call(1, "call_b", "weather", '{"ci'),
        ]),
        _keepalive(),
        _chunk(tool_calls=[_call(1, arguments='ty": "上海"}')]),
        _chunk(tool_calls=[_call(0, arguments='ty": "北京"}')]),
    ])

    deltas = await _collect(OpenAIModel("gpt-test", client=client))

    calls = assemble_tool_calls(_tool_deltas(deltas))
    assert [call.index for call in calls] == [0, 1]
    assert [call.id for call in calls] == ["call_a", "call_b"]
    assert [call.arguments() for call in calls] == [{"city": "北京"}, {"city": "上海"}]


@pytest.mark.anyio
async def test_assembly_rejects_invalid_index():
    """组装是机械重建：非法 index 必须立刻暴露，而不是产出伪 call。"""
    with pytest.raises(ContractViolation):
        assemble_tool_calls([ToolCallDelta(index=-1, name="weather")])
    with pytest.raises(ContractViolation):
        assemble_tool_calls([ToolCallDelta(index="0", name="weather")])  # type: ignore[arg-type]
