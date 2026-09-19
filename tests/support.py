"""测试装配助手：两行拿到一个可跑的 Runtime / Agent。"""
from __future__ import annotations

from agentkit.harness.base import Harness
from agentkit.kernel.events import EventBus
from agentkit.kernel.state import RunContext
from agentkit.kernel.types import Message
from agentkit.runtime.default import ContextEngine, DefaultRuntime
from agentkit.toolbox import Toolbox


class RuntimeHarness(Harness):
    def __init__(self, model, tools=(), memory=None, providers=None,
                 events=None, context=None, history_limit=40):
        self.model = model
        self.toolbox = Toolbox(list(tools))
        self.events = events or EventBus()
        self.memory = memory
        self.context = context if context is not None else ContextEngine(
            list(providers or []), history_limit=history_limit,
        )
        self.close_count = 0

    def build_runtime(self) -> DefaultRuntime:
        return DefaultRuntime(
            model=self.model,
            toolbox=self.toolbox,
            context=self.context,
            memory=self.memory,
            events=self.events,
        )

    async def close(self) -> None:
        self.close_count += 1


def runtime_for(model, **kwargs) -> DefaultRuntime:
    return RuntimeHarness(model, **kwargs).build_runtime()


class Recorder:
    """记录事件名与 payload，用于断言事件序列。"""

    def __init__(self) -> None:
        self.seen: list[str] = []
        self.payloads: list[tuple[str, dict]] = []

    def __call__(self, event, **payload):
        self.seen.append(event)
        self.payloads.append((event, payload))

    def ctx_of(self, event: str) -> RunContext:
        for name, payload in self.payloads:
            if name == event:
                return payload["ctx"]
        raise AssertionError(f"event not emitted: {event}")


def record(events: EventBus) -> Recorder:
    rec = Recorder()
    events.on("*", rec)
    return rec


def make_ctx(task: str = "hi", **kwargs) -> RunContext:
    return RunContext(task=task, messages=[Message("user", task)], **kwargs)
