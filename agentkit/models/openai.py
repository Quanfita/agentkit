"""OpenAI 适配器：Kernel Message ⇄ OpenAI Chat Completions。"""
from __future__ import annotations

import json

from ..kernel.types import Final, Message, ToolCall, ToolCalls


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
    def __init__(self, model: str, client=None, **kwargs):
        self.model = model
        self.kwargs = kwargs
        self._owns_client = client is None
        if client is None:
            from openai import AsyncOpenAI
            client = AsyncOpenAI()
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
            return ToolCalls([
                ToolCall(
                    id=tc.id,
                    name=tc.function.name,
                    arguments=json.loads(tc.function.arguments or "{}"),
                )
                for tc in msg.tool_calls
            ])
        return Final(msg.content or "")

    async def close(self) -> None:
        if self._owns_client:
            await self.client.close()
