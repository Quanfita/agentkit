# mypy: disallow_any_generics=False
# ruff: noqa: UP007, UP035, UP037
"""V3 Contract Freeze snapshot —— **不是 canonical source**。

canonical source 在 `agentkit/`：`kernel/types.py`、`kernel/state.py`、
`kernel/events.py`、`kernel/protocols.py`，以及 V3 新增的
`api/context.py`、`api/skill.py`、`api/executor.py`、`skills/skill.py`。
本文件逐字抄录它们的**定义形状**（dataclass 字段、Protocol 方法、类型别名、
EventBus 签名），只做两件事：

1. 让 Freeze 文档本身经受类型系统验证 —— `mypy --strict` + `pyright`。
   （V2 的教训：`StreamingModel.stream` 的 `async def` vs `def` 是文档错误，
   Freeze 文档从没跑过类型检查。）
2. 作为签名漂移基线 —— `tests/test_abi_drift.py` 比对
   `current kernel ABI == v2 snapshot == 本文件`，漂移即失败。

与 V2 的关系（V3 命题的判据）：

- 本文件的 **kernel 部分与 `docs/freeze/v2/scratch.py` 完全一致，一个字都不多**。
  `tests/test_abi_drift.py::test_v3_kernel_part_matches_v2_snapshot` 强制这一点。
- 唯一的**有意差异**是 `EventBus`：v2 快照刻意把它留成空壳（V2 明确保留把事件
  升级为 Event 对象的空间，见 v2 注释）；V3 §3.1 把 `on / off / emit` 纳入冻结范围，
  所以这里抄录真实签名 —— 但只抄签名，方法体用 `...`，实现不属于冻结内容。
- `Skill` / `ContextTransform` / `SkillProvider` / `PermissionPolicy` 属于
  **Public Extension API（V3 §四）**，不属于 Kernel ABI（V3 §二：Kernel 不新增 Protocol）。
  `Skill` 在这里出现，只是因为 `SkillProvider.search` 的签名需要它。

约束：

- 本文件**自包含**：不 import `agentkit` 任何东西（只用 stdlib + typing）。
- 定义与 `agentkit/` 当前代码逐字一致。风格规则（ruff UP007 / UP035 / UP037）在这里
  让位：文件头的 `# ruff: noqa` 是刻意的 —— `Action = Union[Final, ToolCalls]` 与
  `list["ToolCall"]` 原样保留，评审时才能和 canonical source 逐行对读，
  与 `pyproject.toml` 里 `agentkit/kernel/*.py` 的 per-file-ignores 同一个理由。
- `Skill.tools / Skill.metadata` 在 canonical `skills/skill.py` 里是裸 `list` / `dict`
  （该文件不在 mypy --strict 覆盖下）。本文件要过 `mypy --strict`，裸泛型会触发
  `disallow_any_generics`；用文件头的 `# mypy: disallow_any_generics=False` 让定义
  保持逐字一致，而不是把注解改写成 `list[Any]` 制造文档与代码的形状差异。
- 改了 `agentkit/` 的契约形状 → 同步改本文件并升 `contract_revision`，
  否则 drift 测试会失败。这是设计意图，不是误报。
"""
from __future__ import annotations

from collections.abc import Awaitable, Callable, Sequence
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


# ── kernel/events.py ───────────────────────────────────────────────────

Handler = Callable[..., Awaitable[None] | None]


class EventBus:
    """框架唯一的扩展机制。

    V3 §3.1 冻结 `on / off / emit` 的签名（v2 快照刻意不含它 —— 见 v2 注释）。
    方法体统一 `raise NotImplementedError`：冻结的是签名与生命周期语义，
    实现不在冻结范围（V3 §3.1「不冻结内容」第 2 条），本文件也不可执行。
    （`...` 方法体在 `mypy --strict` 下对非 None 返回类型会触发 `empty-body`，
    而这里不允许 `type: ignore`。）

    `__init__` / `on_handler_error` 不在 §3.1 的冻结清单内，故不抄录。
    """

    def on(self, event: str, handler: Handler) -> "EventBus":
        raise NotImplementedError("Freeze snapshot：只冻结签名，不是实现。")

    def off(self, event: str, handler: Handler) -> None:
        raise NotImplementedError("Freeze snapshot：只冻结签名，不是实现。")

    async def emit(self, event: str, **payload: Any) -> None:
        raise NotImplementedError("Freeze snapshot：只冻结签名，不是实现。")


# ── kernel/protocols.py ────────────────────────────────────────────────

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


# ── skills/skill.py ────────────────────────────────────────────────────
# 以下为 V3 新增的 **Public Extension API**，不属于 Kernel ABI（V3 §二 / §四）。

@dataclass
class Skill:
    """Skill ≠ Tool（逐字抄录 `agentkit/skills/skill.py`，见文件头注解）。"""

    name: str
    description: str = ""
    instructions: str = ""
    tools: list = field(default_factory=list)
    metadata: dict = field(default_factory=dict)


# ── api/context.py ─────────────────────────────────────────────────────

@runtime_checkable
class ContextTransform(Protocol):
    """消息序列的纯变换。

    严格语义：不接收 `RunContext`；不访问外部世界（无 memory / tool / event）。
    允许：过滤、截断、排序、去重、替换 content。
    禁止：读取 ctx / 调用 LLM / 写 memory / 发 event。
    """

    async def apply(self, messages: Sequence[Message]) -> list[Message]: ...


# ── api/skill.py ───────────────────────────────────────────────────────

@runtime_checkable
class SkillProvider(Protocol):
    """Skill 查询接口。

    只承担查询职责：没有 `refresh()` / `close()`（生命周期属于 Runtime startup hook）。
    """

    async def search(self, query: str, limit: int = 3) -> list[Skill]: ...


# ── api/executor.py ────────────────────────────────────────────────────

@runtime_checkable
class PermissionPolicy(Protocol):
    """执行前的权限判定。

    返回 `True` = 允许；`False` = 拒绝。拒绝行为由 `PermissionExecutor` 决定：
    转成一个 `error=True` 的 `ToolResult`，带 `metadata={"blocked": True, ...}`。
    Policy 通过构造注入，`ctx` 通过显式参数传入。
    """

    async def allow(self, call: ToolCall, ctx: RunContext) -> bool: ...
