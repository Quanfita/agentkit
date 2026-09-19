"""Anthropic 适配器：与 OpenAI 完全同构（tool_use → ToolCalls、text → Final）。

Anthropic 与 OpenAI 的两处结构差异由本文件吃掉：
1. system 不是一个 message，而是顶层 `system` 参数；
2. 同角色消息必须合并，tool_result 属于下一个 user message。
"""
from __future__ import annotations

from ..kernel.types import Final, Message, ToolCall, ToolCalls


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

    async def generate(self, messages, tools):
        system, msgs = _to_anthropic(messages)
        resp = await self.client.messages.create(
            model=self.model,
            system=system or None,
            messages=msgs,
            max_tokens=self.max_tokens,
            tools=[_to_anthropic_tool(t) for t in tools] or None,
            **self.kwargs,
        )
        calls, texts = [], []
        for block in resp.content:
            if block.type == "tool_use":
                calls.append(ToolCall(
                    id=block.id, name=block.name, arguments=dict(block.input or {}),
                ))
            elif block.type == "text":
                texts.append(block.text)
        if calls:
            return ToolCalls(calls)
        return Final("".join(texts))

    async def close(self) -> None:
        if self._owns_client:
            await self.client.close()
