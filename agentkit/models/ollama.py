"""Ollama 适配器：走本地 /api/chat，只依赖 httpx。"""
from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Any

from ..kernel.types import Final, Message, ToolCall, ToolCalls, ToolSpec
from .base import Delta, TextDelta, ToolCallDelta


def _to_ollama(m: Message) -> dict:
    if m.role == "tool":
        return {"role": "tool", "content": m.content, "tool_call_id": m.tool_call_id}
    if m.role == "assistant" and m.tool_calls:
        return {
            "role": "assistant",
            "content": m.content,
            "tool_calls": [
                {"function": {"name": tc.name, "arguments": tc.arguments}}
                for tc in m.tool_calls
            ],
        }
    return {"role": m.role, "content": m.content}


def _to_ollama_tool(spec) -> dict:
    return {
        "type": "function",
        "function": {
            "name": spec.name,
            "description": spec.description,
            "parameters": spec.parameters,
        },
    }


def _arguments(raw: Any) -> dict:
    if isinstance(raw, str):
        try:
            raw = json.loads(raw or "{}")
        except json.JSONDecodeError:
            return {}
    return dict(raw or {})


class OllamaModel:
    def __init__(
        self,
        model: str,
        host: str = "http://localhost:11434",
        client=None,
        timeout: float = 120.0,
        **options: Any,
    ) -> None:
        self.model = model
        self.host = host.rstrip("/")
        self.options = options
        self.timeout = timeout
        self._owns_client = client is None
        self._client = client

    def _get_client(self):
        if self._client is None:
            import httpx
            self._client = httpx.AsyncClient(base_url=self.host, timeout=self.timeout)
        return self._client

    def _payload(self, messages, tools, stream: bool) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": [_to_ollama(m) for m in messages],
            "stream": stream,
        }
        if tools:
            payload["tools"] = [_to_ollama_tool(t) for t in tools]
        if self.options:
            payload["options"] = dict(self.options)
        return payload

    async def generate(self, messages, tools):
        resp = await self._get_client().post(
            "/api/chat", json=self._payload(messages, tools, stream=False),
        )
        resp.raise_for_status()
        msg = resp.json().get("message") or {}
        calls = msg.get("tool_calls") or []
        if calls:
            return ToolCalls(
                calls=[
                    ToolCall(
                        id=c.get("id") or f"call_{i}",
                        name=c["function"]["name"],
                        arguments=_arguments(c["function"].get("arguments")),
                    )
                    for i, c in enumerate(calls)
                ],
                # P0-A：与 tool_calls 同时出现的文本必须保留
                content=msg.get("content") or "",
            )
        return Final(msg.get("content") or "")

    async def stream(
        self, messages: list[Message], tools: list[ToolSpec],
    ) -> AsyncIterator[Delta]:
        """Ollama 的 NDJSON 流归一化成 `Delta`。

        文本是逐块而来的；工具调用是整体到达的，因此一次性给出
        完整的 `args_delta`（JSON 字符串），组装者无需特殊处理。
        """
        client = self._get_client()
        async with client.stream(
            "POST", "/api/chat", json=self._payload(messages, tools, stream=True),
        ) as resp:
            resp.raise_for_status()
            async for line in resp.aiter_lines():
                if not line.strip():
                    continue
                msg = json.loads(line).get("message") or {}
                if msg.get("content"):
                    yield TextDelta(msg["content"])
                for i, c in enumerate(msg.get("tool_calls") or []):
                    yield ToolCallDelta(
                        index=i,
                        id=c.get("id") or f"call_{i}",
                        name=c["function"]["name"],
                        args_delta=json.dumps(
                            _arguments(c["function"].get("arguments")),
                            ensure_ascii=False,
                        ),
                    )

    async def close(self) -> None:
        if self._owns_client and self._client is not None:
            await self._client.aclose()
            self._client = None
