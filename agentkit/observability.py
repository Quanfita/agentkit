"""EventBus 上的内置观测 Hook：tracer / cost / logger。

全部是 `events.on("*", hook)` 一行接入的可调用对象，
不引入任何新的 Protocol，也不碰 Loop。
"""
from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

from .kernel.types import Final, ToolCalls

Printer = Callable[[str], None]


def describe(event: str, payload: dict[str, Any]) -> str:
    """把一次事件压成一行可读摘要。"""
    ctx = payload.get("ctx")
    if event == "agent.start":
        return f"task={ctx.task!r}"
    if event == "model.before":
        inp = payload.get("inp")
        return f"messages={len(inp.messages)} tools={len(inp.tools)}"
    if event == "model.after":
        action = payload.get("action")
        if isinstance(action, Final):
            return f"final chars={len(action.content)}"
        if isinstance(action, ToolCalls):
            return f"tool_calls={[c.name for c in action.calls]}"
        return type(action).__name__
    if event == "iteration.done":
        results = payload.get("results") or []
        return f"step={ctx.step} results={[('error' if r.error else 'ok') for r in results]}"
    if event == "agent.error":
        return repr(payload.get("error"))
    if event == "agent.end":
        return f"steps={ctx.step} done={ctx.done} result_chars={len(ctx.result)}"
    return ""


class Tracer:
    """events.on("*", Tracer()) —— 一行拿到全量事件追踪。"""

    def __init__(self, printer: Printer = print, include: list[str] | None = None):
        self.printer = printer
        self.include = tuple(include) if include else None
        self.seen: list[str] = []

    def __call__(self, event: str, **payload: Any) -> None:
        if self.include is not None and event not in self.include:
            return
        self.seen.append(event)
        self.printer(f"[trace] {event} {describe(event, payload)}".rstrip())


class Logger:
    """events.on("*", Logger()) —— 一行把事件接进标准 logging。"""

    def __init__(self, logger: logging.Logger | None = None, level: int = logging.INFO):
        self.logger = logger or logging.getLogger("agentkit")
        self.level = level

    def __call__(self, event: str, **payload: Any) -> None:
        self.logger.log(self.level, "%s %s", event, describe(event, payload))


class CostTracker:
    """按字符数估算 token 的粗糙账单，可选预算熔断。

    估算方式与文档 §8.2 一致：prompt 字符数 // 4。
    预算耗尽时置 `ctx.stop = True`，由 Loop 的检查点停下——
    这正是「加预算不碰 Loop」的实证。
    """

    def __init__(
        self,
        budget: int | None = None,
        chars_per_token: int = 4,
        printer: Printer | None = None,
    ) -> None:
        self.budget = budget
        self.chars_per_token = chars_per_token
        self.printer = printer
        self.calls = 0
        self.estimated_tokens = 0
        self.stopped = False

    def __call__(self, event: str, **payload: Any) -> None:
        if event != "model.before":
            return
        ctx = payload["ctx"]
        self.calls += 1
        self.estimated_tokens += sum(
            len(m.content) for m in ctx.messages
        ) // self.chars_per_token
        if self.budget is not None and self.estimated_tokens > self.budget:
            ctx.stop = True
            self.stopped = True
            if self.printer:
                self.printer(
                    f"[cost] budget={self.budget} exceeded "
                    f"(~{self.estimated_tokens} tokens after {self.calls} calls)"
                )
