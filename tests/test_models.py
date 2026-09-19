"""Model 适配器：Kernel 契约 ⇄ 各厂商 SDK（用假客户端，离线验证）。"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from agentkit.kernel.protocols import Model
from agentkit.kernel.types import Final, Message, ToolCall, ToolCalls, ToolSpec
from agentkit.models.anthropic import AnthropicModel, _to_anthropic
from agentkit.models.echo import EchoModel, ScriptedModel
from agentkit.models.ollama import OllamaModel
from agentkit.models.openai import OpenAIModel, _to_openai

# ── OpenAI ─────────────────────────────────────────────


class FakeCompletions:
    def __init__(self, message):
        self.message = message
        self.calls: list[dict] = []

    async def create(self, **kwargs):
        self.calls.append(kwargs)
        return SimpleNamespace(choices=[SimpleNamespace(message=self.message)])


class FakeOpenAI:
    def __init__(self, message):
        self.chat = SimpleNamespace(completions=FakeCompletions(message))
        self.close_count = 0

    async def close(self):
        self.close_count += 1


def openai_message(content=None, tool_calls=None):
    return SimpleNamespace(content=content, tool_calls=tool_calls)


def test_openai_message_conversion_covers_all_three_shapes():
    tool_call = ToolCall("c1", "read_file", {"path": "a.txt"})
    assert _to_openai(Message("system", "s")) == {"role": "system", "content": "s"}
    assert _to_openai(Message("tool", "out", tool_call_id="c1")) == {
        "role": "tool", "content": "out", "tool_call_id": "c1",
    }
    converted = _to_openai(Message("assistant", "", tool_calls=[tool_call]))
    assert converted["role"] == "assistant" and converted["content"] is None
    assert converted["tool_calls"][0]["function"] == {
        "name": "read_file", "arguments": '{"path": "a.txt"}',
    }


@pytest.mark.anyio
async def test_openai_generate_returns_final_and_passes_tools_none_when_empty():
    client = FakeOpenAI(openai_message(content="你好"))
    model = OpenAIModel("gpt-x", client=client, temperature=0.2)

    action = await model.generate([Message("user", "hi")], [])

    assert action == Final("你好")
    call = client.chat.completions.calls[0]
    assert call["model"] == "gpt-x" and call["temperature"] == 0.2
    assert call["tools"] is None
    assert call["messages"] == [{"role": "user", "content": "hi"}]


@pytest.mark.anyio
async def test_openai_generate_parses_tool_calls():
    raw = SimpleNamespace(
        id="c9", function=SimpleNamespace(name="add", arguments='{"a": 1, "b": 2}'),
    )
    client = FakeOpenAI(openai_message(content=None, tool_calls=[raw]))
    model = OpenAIModel("gpt-x", client=client)
    spec = ToolSpec("add", "两数相加", {"type": "object", "properties": {}})

    action = await model.generate([Message("user", "1+2")], [spec])

    assert action == ToolCalls([ToolCall("c9", "add", {"a": 1, "b": 2})])
    assert client.chat.completions.calls[0]["tools"] == [{
        "type": "function",
        "function": {"name": "add", "description": "两数相加",
                     "parameters": spec.parameters},
    }]


@pytest.mark.anyio
async def test_openai_close_only_closes_its_own_client(monkeypatch):
    injected = FakeOpenAI(openai_message(content="x"))
    await OpenAIModel("gpt-x", client=injected).close()
    assert injected.close_count == 0        # 注入的客户端归调用方所有

    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")   # 构造自建客户端不需要网络
    owned = OpenAIModel("gpt-x")
    assert owned._owns_client is True
    await owned.close()
    await owned.close()                     # 关闭是幂等的


# ── Anthropic ──────────────────────────────────────────


class FakeAnthropicMessages:
    def __init__(self, blocks):
        self.blocks = blocks
        self.calls: list[dict] = []

    async def create(self, **kwargs):
        self.calls.append(kwargs)
        return SimpleNamespace(content=self.blocks)


class FakeAnthropic:
    def __init__(self, blocks):
        self.messages = FakeAnthropicMessages(blocks)
        self.close_count = 0

    async def close(self):
        self.close_count += 1


def test_anthropic_pulls_system_out_and_merges_tool_results_into_user():
    messages = [
        Message("system", "规则A"),
        Message("system", "规则B"),
        Message("user", "任务"),
        Message("assistant", "", tool_calls=[ToolCall("t1", "read", {"path": "a"})]),
        Message("tool", "文件内容", tool_call_id="t1"),
        Message("tool", "第二个结果", tool_call_id="t2"),
        Message("assistant", "", tool_calls=[]),
    ]
    system, converted = _to_anthropic(messages)

    assert system == "规则A\n\n规则B"
    assert [m["role"] for m in converted] == ["user", "assistant", "user"]
    assert converted[1]["content"] == [
        {"type": "tool_use", "id": "t1", "name": "read", "input": {"path": "a"}},
    ]
    assert converted[2]["content"] == [
        {"type": "tool_result", "tool_use_id": "t1", "content": "文件内容"},
        {"type": "tool_result", "tool_use_id": "t2", "content": "第二个结果"},
    ]


@pytest.mark.anyio
async def test_anthropic_generate_returns_final_with_max_tokens():
    client = FakeAnthropic([SimpleNamespace(type="text", text="答案是 42")])
    model = AnthropicModel("claude-x", client=client, max_tokens=256)
    spec = ToolSpec("add", "两数相加", {"type": "object", "properties": {}})

    action = await model.generate([Message("system", "S"), Message("user", "1+2")], [spec])

    assert action == Final("答案是 42")
    call = client.messages.calls[0]
    assert call["system"] == "S" and call["max_tokens"] == 256
    assert call["messages"] == [{"role": "user", "content": [{"type": "text", "text": "1+2"}]}]
    assert call["tools"] == [{"name": "add", "description": "两数相加",
                              "input_schema": spec.parameters}]


@pytest.mark.anyio
async def test_anthropic_generate_parses_tool_use_blocks():
    client = FakeAnthropic([
        SimpleNamespace(type="text", text="我来调用工具"),
        SimpleNamespace(type="tool_use", id="tu1", name="add", input={"a": 1, "b": 2}),
    ])
    model = AnthropicModel("claude-x", client=client)
    action = await model.generate([Message("user", "1+2")], [])
    assert action == ToolCalls([ToolCall("tu1", "add", {"a": 1, "b": 2})])
    assert client.messages.calls[0]["tools"] is None
    assert client.messages.calls[0]["system"] is None


@pytest.mark.anyio
async def test_anthropic_close_only_closes_its_own_client():
    injected = FakeAnthropic([SimpleNamespace(type="text", text="x")])
    await AnthropicModel("claude-x", client=injected).close()
    assert injected.close_count == 0


# ── Ollama ─────────────────────────────────────────────


class FakeResponse:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self._payload


class FakeHTTP:
    def __init__(self, payload):
        self.payload = payload
        self.requests: list[tuple[str, dict]] = []
        self.close_count = 0

    async def post(self, url, json):
        self.requests.append((url, json))
        return FakeResponse(self.payload)

    async def aclose(self):
        self.close_count += 1


@pytest.mark.anyio
async def test_ollama_generate_sends_expected_payload():
    client = FakeHTTP({"message": {"content": "本地回答"}})
    model = OllamaModel("qwen3:8b", client=client, temperature=0.5)
    spec = ToolSpec("add", "两数相加", {"type": "object", "properties": {}})

    action = await model.generate([Message("user", "hi")], [spec])

    assert action == Final("本地回答")
    url, payload = client.requests[0]
    assert url == "/api/chat"
    assert payload["model"] == "qwen3:8b" and payload["stream"] is False
    assert payload["messages"] == [{"role": "user", "content": "hi"}]
    assert payload["options"] == {"temperature": 0.5}
    assert payload["tools"][0]["function"]["name"] == "add"


@pytest.mark.anyio
async def test_ollama_parses_tool_calls_with_dict_or_json_arguments():
    client = FakeHTTP({"message": {"tool_calls": [
        {"function": {"name": "add", "arguments": {"a": 1}}},
        {"function": {"name": "sub", "arguments": '{"a": 2}'}},
        {"function": {"name": "broken", "arguments": "not json"}},
    ]}})
    model = OllamaModel("qwen3:8b", client=client)

    action = await model.generate([Message("user", "x")], [])

    assert action == ToolCalls([
        ToolCall("call_0", "add", {"a": 1}),
        ToolCall("call_1", "sub", {"a": 2}),
        ToolCall("call_2", "broken", {}),
    ])


@pytest.mark.anyio
async def test_ollama_close_only_closes_its_own_client():
    injected = FakeHTTP({"message": {"content": "x"}})
    await OllamaModel("qwen3:8b", client=injected).close()
    assert injected.close_count == 0
    owned = OllamaModel("qwen3:8b")
    owned_client = FakeHTTP({"message": {"content": "x"}})
    owned._client = owned_client
    await owned.close()
    assert owned_client.close_count == 1


# ── 离线模型 ────────────────────────────────────────────


@pytest.mark.anyio
async def test_echo_model_returns_final_and_records_input():
    model = EchoModel("说：")
    assert isinstance(model, Model)
    action = await model.generate([Message("user", "你好")], [ToolSpec("t")])
    assert action == Final("说：你好")
    assert model.calls[0][1] == [ToolSpec("t")]


@pytest.mark.anyio
async def test_scripted_model_repeats_last_action_and_rejects_empty_script():
    with pytest.raises(ValueError):
        ScriptedModel([])
    model = ScriptedModel([ToolCalls([ToolCall("1", "t")]), Final("done")])
    assert await model.generate([], []) == ToolCalls([ToolCall("1", "t")])
    assert await model.generate([], []) == Final("done")
    assert await model.generate([], []) == Final("done")
