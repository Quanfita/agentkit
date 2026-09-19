"""Kernel Canonical Types —— 冻结契约层。

这里的数据结构是 Agent Loop 与外部世界之间的对话表示，
既不是 OpenAI Message，也不是 Anthropic Message：
Provider SDK 的差异全部由 Model Adapter 吃掉。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal, Union

Role = Literal["system", "user", "assistant", "tool"]


@dataclass(slots=True)
class Message:
    """Kernel Canonical Message。

    这是 Agent Loop 与外部世界的对话表示，
    不是 OpenAI Message，也不是 Anthropic Message。
    Provider SDK 的差异全部由 Model Adapter 吃掉。
    """
    role: Role
    content: str = ""
    tool_calls: list["ToolCall"] = field(default_factory=list)
    tool_call_id: str | None = None


@dataclass(slots=True)
class ToolCall:
    id: str
    name: str
    arguments: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class ToolSpec:
    """JSON Schema passthrough。不做 Parameter / Property 抽象。"""
    name: str
    description: str = ""
    parameters: dict[str, Any] = field(
        default_factory=lambda: {"type": "object", "properties": {}}
    )


@dataclass(slots=True, kw_only=True)
class ToolResult:
    """工具执行的唯一返回结构（Agent Tool Protocol 的数据契约）。

    V2 新增 `tool_call_id`；**V2.5 冻结为 kw-only**：
    `ToolResult("hello")` 会直接抛 `TypeError`，而不是把 "hello" 静默塞进
    `tool_call_id` 而留一个空的 `content`。数据类不猜调用者意图，
    也不做 `__post_init__` 修补。

    `tool_call_id` 的 ownership：ToolCall.id → Executor → 这里（强制覆盖）。

    仍是四字段：多模态 / artifact 不在 V2.5 范围内。
    """
    tool_call_id: str = ""
    content: str = ""
    error: bool = False
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class MemoryItem:
    """Memory 的原子单位。

    Memory 不直接产出 Message —— 那是 Context 层的职责。
    """
    content: str
    kind: str = "memory"
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class MemoryInput:
    """Memory.remember 的输入。

    它描述「一次 Agent Run」，而不是「一堆 Message」。
    """
    task: str
    messages: list[Message] = field(default_factory=list)
    result: str = ""


@dataclass(slots=True)
class ContextItem:
    """ContextEngine 的原子单位。

    故意不带 priority / tokens / budget —— 那是 Harness 的事。
    """
    content: str
    role: Role = "system"
    source: str = "unknown"
    kind: str = "text"


# ── 模型输出（Action） ─────────────────────────────────

@dataclass(slots=True)
class Final:
    content: str


@dataclass(slots=True)
class ToolCalls:
    """ToolCalls 可以同时携带文本。

    OpenAI / Anthropic 的响应允许 content 与 tool_calls 并存，
    V1 丢弃 content 是语义缺陷（P0-A）。
    """
    calls: list[ToolCall]
    content: str = ""


Action = Union[Final, ToolCalls]
