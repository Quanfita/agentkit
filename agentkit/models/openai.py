"""OpenAI 适配器：Kernel Message ⇄ OpenAI Chat Completions（含流式归一化）。"""
from __future__ import annotations

import json
from collections.abc import AsyncIterator

from ..kernel.types import Final, Message, ToolCall, ToolCalls, ToolSpec
from .base import Delta, TextDelta, ToolCallDelta


def _to_openai(m: Message) -> dict:
    if m.role == "tool":
        return {"role": "tool", "content": m.content, "tool_call_id": m.tool_call_id}
    if m.role == "assistant" and m.tool_calls:
        return {
            "role": "assistant",
            "content": m.content or None,
            "tool_calls": [
                {
                    "id": tc.id,
                    "type": "function",
                    "function": {
                        "name": tc.name,
                        "arguments": json.dumps(tc.arguments, ensure_ascii=False),
                    },
                }
                for tc in m.tool_calls
            ],
        }
    return {"role": m.role, "content": m.content}


def _to_openai_tool(spec) -> dict:
    return {
        "type": "function",
        "function": {
            "name": spec.name,
            "description": spec.description,
            "parameters": spec.parameters,
        },
    }


class OpenAIModel:
    """OpenAI normalization path：canonical Message/ToolSpec ⇄ OpenAI wire format。

    `client` 注入时归调用方所有（不会被 `close()`）；`base_url` / `api_key` 只用于
    「自建 OpenAI-compatible 客户端」的场景，让同一条 path 能被不同服务端预设复用
    （`models/deepseek.py` 就是这么用的）。normalization 代码只有这一份。
    """

    def __init__(
        self,
        model: str,
        client=None,
        *,
        base_url: str | None = None,
        api_key: str | None = None,
        **kwargs,
    ):
        self.model = model
        self.kwargs = kwargs
        self._owns_client = client is None
        if client is None:
            from openai import AsyncOpenAI
            client = AsyncOpenAI(api_key=api_key, base_url=base_url)
        self.client = client

    async def generate(self, messages, tools):
        resp = await self.client.chat.completions.create(
            model=self.model,
            messages=[_to_openai(m) for m in messages],
            tools=[_to_openai_tool(t) for t in tools] or None,
            **self.kwargs,
        )
        msg = resp.choices[0].message
        if msg.tool_calls:
            return ToolCalls(
                calls=[
                    ToolCall(
                        id=tc.id,
                        name=tc.function.name,
                        arguments=json.loads(tc.function.arguments or "{}"),
                    )
                    for tc in msg.tool_calls
                ],
                # P0-A：与 tool_calls 同时出现的文本必须保留
                content=msg.content or "",
            )
        return Final(msg.content or "")

    async def stream(
        self, messages: list[Message], tools: list[ToolSpec],
    ) -> AsyncIterator[Delta]:
        """把 OpenAI chunk 归一化成 `TextDelta` / `ToolCallDelta`。"""
        chunks = await self.client.chat.completions.create(
            model=self.model,
            messages=[_to_openai(m) for m in messages],
            tools=[_to_openai_tool(t) for t in tools] or None,
            stream=True,
            **self.kwargs,
        )
        async for chunk in chunks:
            choices = getattr(chunk, "choices", None) or ()
            if not choices:
                continue
            delta = choices[0].delta
            text = getattr(delta, "content", None)
            if text:
                yield TextDelta(text)
            for tc in getattr(delta, "tool_calls", None) or ():
                fn = getattr(tc, "function", None)
                yield ToolCallDelta(
                    index=getattr(tc, "index", 0),
                    id=getattr(tc, "id", None),
                    name=getattr(fn, "name", None),
                    args_delta=getattr(fn, "arguments", None),
                )

    async def close(self) -> None:
        if self._owns_client:
            await self.client.close()
