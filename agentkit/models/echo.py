"""离线 Model：EchoModel 与 ScriptedModel。

两者都不依赖任何 Provider SDK，用于测试、demo 和无网络环境。
"""
from __future__ import annotations

from ..kernel.types import Action, Final, Message, ToolSpec


class EchoModel:
    """直接回显最后一条消息，永远返回 Final。

    没有任何工具调用，是 Phase 1 的最小闭环验证工具。
    """

    def __init__(self, prefix: str = "echo: ") -> None:
        self.prefix = prefix
        self.calls: list[tuple[list[Message], list[ToolSpec]]] = []

    async def generate(self, messages: list[Message], tools: list[ToolSpec]) -> Action:
        self.calls.append((list(messages), list(tools)))
        last = messages[-1].content if messages else ""
        return Final(f"{self.prefix}{last}")


class ScriptedModel:
    """按剧本吐 Action；剧本用完后重复最后一个 Action。

    用来驱动「先调工具、再给答案」这类多轮场景。
    """

    def __init__(self, actions: list[Action]) -> None:
        if not actions:
            raise ValueError("ScriptedModel needs at least one action")
        self.actions = list(actions)
        self.calls: list[tuple[list[Message], list[ToolSpec]]] = []

    async def generate(self, messages: list[Message], tools: list[ToolSpec]) -> Action:
        self.calls.append((list(messages), list(tools)))
        idx = min(len(self.calls) - 1, len(self.actions) - 1)
        return self.actions[idx]
