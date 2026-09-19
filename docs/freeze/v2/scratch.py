# ruff: noqa: UP007, UP037
"""V2 Contract Freeze snapshot —— **不是 canonical source**。

canonical source 在 `agentkit/`：`kernel/types.py`、`kernel/state.py`、
`kernel/protocols.py`、`models/base.py`。本文件逐字抄录它们的**定义形状**
（dataclass 字段、Protocol 方法、类型别名），只做两件事：

1. 让 Freeze 文档本身经受类型系统验证 —— `mypy --strict` + `pyright`。
   （V2 的教训：`StreamingModel.stream` 的 `async def` vs `def` 是文档错误，
   Freeze 文档从没跑过类型检查。）
2. 作为签名漂移基线 —— `tests/unit/test_freeze_snapshot_v2.py` 比对
   `current signatures == freeze snapshot signatures`，漂移即失败。

约束：

- 本文件**自包含**：不 import `agentkit` 任何东西（只用 stdlib + typing）。
- 定义与 `agentkit/` 当前代码逐字一致。风格规则（ruff UP007 / UP037）在这里让位：
  文件头的 `# ruff: noqa` 是刻意的 —— `Action = Union[Final, ToolCalls]` 与
  `list["ToolCall"]` 原样保留，评审时才能和 canonical source 逐行对读，
  与 `pyproject.toml` 里 `agentkit/kernel/*.py` 的 per-file-ignores 同一个理由。
- 改了 `agentkit/` 的契约形状 → 同步改本文件并升 `contract_revision`，
  否则 drift 测试会失败。这是设计意图，不是误报。
"""
from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Literal, Protocol, Union, runtime_checkable

# ── kernel/types.py ────────────────────────────────────────────────────

Role = Literal["system", "user", "assistant", "tool"]


@dataclass(slots=True)
class Message:
    """Kernel Canonical Message：不是 OpenAI Message，也不是 Anthropic Message。"""

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
    """工具执行的唯一返回结构。

    V2.5 冻结为 **kw-only**：`ToolResult("hello")` 直接抛 `TypeError`，
    而不是把 "hello" 静默塞进 `tool_call_id`。仍是四字段。
    """

    tool_call_id: str = ""
    content: str = ""
    error: bool = False
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class MemoryItem:
    """Memory 的原子单位（Memory 不直接产出 Message）。"""

    content: str
    kind: str = "memory"
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class MemoryInput:
    """Memory.remember 的输入：描述「一次 Agent Run」。"""

    task: str
    messages: list[Message] = field(default_factory=list)
    result: str = ""


@dataclass(slots=True)
class ContextItem:
    """ContextEngine 的原子单位（故意不带 priority / tokens / budget）。"""

    content: str
    role: Role = "system"
    source: str = "unknown"
    kind: str = "text"


@dataclass(slots=True)
class Final:
    content: str


@dataclass(slots=True)
class ToolCalls:
    """ToolCalls 可以同时携带文本（content 与 calls 并存）。"""

    calls: list[ToolCall]
    content: str = ""


Action = Union[Final, ToolCalls]


# ── kernel/state.py ────────────────────────────────────────────────────

class TerminationReason(str, Enum):
    """RunContext.reason 的取值：Run 为什么结束（V2 冻结）。"""

    FINAL = "final"
    MAX_ITERATIONS = "max_iterations"
    STOPPED = "stopped"
    ERROR = "error"


@dataclass
class RunContext:
    """Loop State。

    绝不要往里面加：model / memory / toolbox / skills / mcp / workspace
    / user / session / trace / token_usage / cost。
    """

    task: str
    system: str = ""
    messages: list[Message] = field(default_factory=list)

    step: int = 0
    max_iterations: int = 16

    stop: bool = False
    done: bool = False
    result: str = ""
    last_assistant: Message | None = None
    error: BaseException | None = None
    reason: TerminationReason | None = None

    scratch: dict[str, Any] = field(default_factory=dict)


# ── kernel/protocols.py ────────────────────────────────────────────────

class EventBus:
    """**不冻结**：真实形状在 `kernel/events.py`。

    V2 明确保留把事件升级为 Event 对象的空间，所以这里只提供
    `Runtime.events` 所需的类型名，不抄任何方法，也不参与 drift 比对。
    """


@dataclass(slots=True)
class PreparedInput:
    """Loop 交给模型的一切。"""

    messages: list[Message]
    tools: list[ToolSpec]


@runtime_checkable
class Model(Protocol):
    async def generate(
        self, messages: list[Message], tools: list[ToolSpec],
    ) -> Action: ...


@runtime_checkable
class Tool(Protocol):
    spec: ToolSpec

    async def run(self, arguments: dict[str, Any]) -> ToolResult: ...


@runtime_checkable
class ToolProvider(Protocol):
    """本地工具集 / MCP session / Skill 包 —— 全部长这样。"""

    async def tools(self) -> list[Tool]: ...
    async def close(self) -> None: ...


@runtime_checkable
class ToolExecutor(Protocol):
    """Tool 执行策略协议。

    `execute()` 是唯一公共执行入口（V2.5 删掉 execute_one）。
    Toolbox 归 Runtime 拥有，Executor 只借用；`close()` 只关自身资源。
    """

    async def execute(
        self, ctx: RunContext, action: ToolCalls,
    ) -> list[ToolResult]: ...

    async def close(self) -> None: ...


@runtime_checkable
class Memory(Protocol):
    """只有两个方法（multi-store / router / dedup / summary 都是 Harness 的事）。"""

    async def recall(self, task: str) -> list[MemoryItem]: ...
    async def remember(self, run: MemoryInput) -> None: ...


@runtime_checkable
class ContextProvider(Protocol):
    async def provide(self, ctx: RunContext) -> list[ContextItem]: ...


@runtime_checkable
class Runtime(Protocol):
    """Loop 眼中世界的全部：适配器，不是容器。"""

    events: EventBus

    async def prepare(self, ctx: RunContext) -> PreparedInput: ...
    async def reason(self, ctx: RunContext, inp: PreparedInput) -> Action: ...
    async def act(self, ctx: RunContext, action: ToolCalls) -> list[ToolResult]: ...
    async def observe(
        self, ctx: RunContext, action: Action, results: list[ToolResult] | None,
    ) -> None: ...
    async def finish(self, ctx: RunContext) -> None: ...
    async def close(self) -> None: ...


# ── models/base.py ─────────────────────────────────────────────────────

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
    """实现是 async generator：调用返回 AsyncIterator，**不需要 await**。"""

    def stream(
        self, messages: list[Message], tools: list[ToolSpec],
    ) -> AsyncIterator[Delta]: ...
