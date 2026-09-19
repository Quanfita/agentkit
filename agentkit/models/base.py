"""Kernel 之外的 Streaming 契约。

`Delta` 是 **Adapter Normalized Delta**：Provider SDK 的原始 delta 必须由
`Model.stream()` 归一化成 `TextDelta` / `ToolCallDelta` 两种之一，
Harness 永远看不到 provider-specific delta。

Kernel 不知道 Streaming 存在 —— 这一层完全是 `models/` 的事。
"""
from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from ..kernel.types import Message, ToolSpec


@dataclass(slots=True)
class TextDelta:
    text: str


@dataclass(slots=True)
class ToolCallDelta:
    index: int
    id: str | None = None
    name: str | None = None
    args_delta: str | None = None


Delta = TextDelta | ToolCallDelta


@runtime_checkable
class StreamingModel(Protocol):
    """流式能力的正式 Protocol —— 避免 `hasattr(model, "stream")` 的隐式契约。

    实现是 async generator：调用返回 AsyncIterator，**不需要 await**。
    """

    def stream(
        self, messages: list[Message], tools: list[ToolSpec],
    ) -> AsyncIterator[Delta]: ...
