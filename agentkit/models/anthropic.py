"""Anthropic 适配器：与 OpenAI 完全同构（tool_use → ToolCalls、text → Final）。

Anthropic 与 OpenAI 的两处结构差异由本文件吃掉：
1. system 不是一个 message，而是顶层 `system` 参数；
2. 同角色消息必须合并，tool_result 属于下一个 user message。
流式事件也在本文件归一化成 `Delta`。
"""
from __future__ import annotations

from collections.abc import AsyncIterator

from ..kernel.types import Final, Message, ToolCall, ToolCalls, ToolSpec
from .base import Delta, TextDelta, ToolCallDelta


def _append(out: list[dict], role: str, blocks: list[dict]) -> None:
    if not blocks:
        return
    if out and out[-1]["role"] == role:
        out[-1]["content"].extend(blocks)
        return
    out.append({"role": role, "content": list(blocks)})


def _to_anthropic(messages: list[Message]) -> tuple[str, list[dict]]:
    system: list[str] = []
    out: list[dict] = []
    for m in messages:
        if m.role == "system":
            if m.content:
                system.append(m.content)
        elif m.role == "assistant":
            blocks = [{"type": "text", "text": m.content}] if m.content else []
            blocks += [
                {"type": "tool_use", "id": tc.id, "name": tc.name, "input": tc.arguments}
                for tc in m.tool_calls
            ]
            _append(out, "assistant", blocks)
        elif m.role == "tool":
            _append(out, "user", [{
                "type": "tool_result",
                "tool_use_id": m.tool_call_id or "",
                "content": m.content,
            }])
        elif m.content:
            _append(out, "user", [{"type": "text", "text": m.content}])
    return "\n\n".join(system), out


def _to_anthropic_tool(spec) -> dict:
    return {
        "name": spec.name,
        "description": spec.description,
        "input_schema": spec.parameters,
    }


class AnthropicModel:
    def __init__(self, model: str, client=None, max_tokens: int = 4096, **kwargs):
        self.model = model
        self.max_tokens = max_tokens
        self.kwargs = kwargs
        self._owns_client = client is None
        if client is None:
            from anthropic import AsyncAnthropic
            client = AsyncAnthropic()
        self.client = client

    def _request(self, messages, tools) -> dict:
        system, msgs = _to_anthropic(messages)
        return {
            "model": self.model,
            "system": system or None,
            "messages": msgs,
            "max_tokens": self.max_tokens,
            "tools": [_to_anthropic_tool(t) for t in tools] or None,
            **self.kwargs,
        }

    async def generate(self, messages, tools):
        resp = await self.client.messages.create(**self._request(messages, tools))
        calls, texts = [], []
        for block in resp.content:
            if block.type == "tool_use":
                calls.append(ToolCall(
                    id=block.id, name=block.name, arguments=dict(block.input or {}),
                ))
            elif block.type == "text":
                texts.append(block.text)
        if calls:
            # P0-A：与 tool_use 同时出现的文本必须保留
            return ToolCalls(calls=calls, content="".join(texts))
        return Final("".join(texts))

    async def stream(
        self, messages: list[Message], tools: list[ToolSpec],
    ) -> AsyncIterator[Delta]:
        """把 Anthropic 原始事件归一化成 `TextDelta` / `ToolCallDelta`。"""
        events = await self.client.messages.create(
            **self._request(messages, tools), stream=True,
        )
        async for event in events:
            etype = getattr(event, "type", None)
            if etype == "content_block_start":
                block = getattr(event, "content_block", None)
                if getattr(block, "type", None) == "tool_use":
                    yield ToolCallDelta(
                        index=event.index, id=block.id, name=block.name,
                    )
            elif etype == "content_block_delta":
                delta = event.delta
                dtype = getattr(delta, "type", None)
                if dtype == "text_delta":
                    yield TextDelta(delta.text)
                elif dtype == "input_json_delta":
                    yield ToolCallDelta(
                        index=event.index, args_delta=delta.partial_json,
                    )

    async def close(self) -> None:
        if self._owns_client:
            await self.client.close()
