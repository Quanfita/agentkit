"""EventBus —— 框架唯一的通用扩展机制。"""
from __future__ import annotations

import inspect
import traceback
from collections import defaultdict
from typing import Any, Awaitable, Callable, Literal


Handler = Callable[..., Awaitable[None] | None]
OnHandlerError = Literal["raise", "ignore"]


class EventBus:
    """框架唯一的扩展机制。

    V1 事件用 (name, **payload) 表达。未来可无痛升级为 Event 对象，
    见 roadmap「V2 事件升级」。
    """

    def __init__(self, on_handler_error: OnHandlerError = "raise") -> None:
        self._handlers: dict[str, list[Handler]] = defaultdict(list)
        self._wildcard: list[Handler] = []
        self.on_handler_error = on_handler_error

    def on(self, event: str, handler: Handler) -> "EventBus":
        (self._wildcard if event == "*" else self._handlers[event]).append(handler)
        return self

    def off(self, event: str, handler: Handler) -> None:
        bucket = self._wildcard if event == "*" else self._handlers[event]
        if handler in bucket:
            bucket.remove(handler)

    async def emit(self, event: str, **payload: Any) -> None:
        handlers = (*self._handlers.get(event, ()), *self._wildcard)
        for h in handlers:
            try:
                r = h(event, **payload)
                if inspect.isawaitable(r):
                    await r
            except Exception:
                if self.on_handler_error == "raise":
                    raise
                traceback.print_exc()
