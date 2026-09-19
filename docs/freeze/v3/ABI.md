# Kernel ABI 快照（V3 Contract Freeze）

> **V3 的判据不是「Kernel LOC 不变」，而是「Kernel 公共 ABI 不变」。**
>
> 本文件是 V3 §3.1 冻结范围的**人读版本**；机器版本是
> [`docs/freeze/v3/scratch.py`](scratch.py)（自包含 + `mypy --strict` + `pyright` 全绿），
> 门禁是 `tests/test_abi_drift.py`（比对 `运行中的 kernel ABI == v2 快照 == v3 快照`）。
>
> 三份东西必须同时为真：**本文件写下的清单 = scratch.py 抄录的形状 = `agentkit/kernel/` 运行时的形状。**

冻结基线是 **V2.5 快照**（`docs/freeze/v2/scratch.py`）。
V3 期间 `agentkit/kernel/` 的公共 ABI 与 V2.5 **完全一致**：不加参数、不加字段、
不加枚举值、不改返回类型、不改参数名。

---

## 一、冻结范围（逐条）

「冻结内容」= 类名 / 方法名 / 参数名 + 类型 / 返回类型 / dataclass 字段名 + 类型 + 顺序 /
生命周期语义 / 异常语义。

### 1.1 `kernel.protocols` —— 协议方法

| Protocol | 方法（名字 / 参数名 / 返回类型） |
|---|---|
| `Model` | `async generate(self, messages: list[Message], tools: list[ToolSpec]) -> Action` |
| `Tool` | 数据成员 `spec: ToolSpec`；`async run(self, arguments: dict[str, Any]) -> ToolResult` |
| `ToolProvider` | `async tools(self) -> list[Tool]`；`async close(self) -> None` |
| `ToolExecutor` | `async execute(self, ctx: RunContext, action: ToolCalls) -> list[ToolResult]`；`async close(self) -> None` |
| `Memory` | `async recall(self, task: str) -> list[MemoryItem]`；`async remember(self, run: MemoryInput) -> None` |
| `ContextProvider` | `async provide(self, ctx: RunContext) -> list[ContextItem]` |
| `Runtime` | 数据成员 `events: EventBus`；`async prepare(self, ctx: RunContext) -> PreparedInput`；`async reason(self, ctx: RunContext, inp: PreparedInput) -> Action`；`async act(self, ctx: RunContext, action: ToolCalls) -> list[ToolResult]`；`async observe(self, ctx: RunContext, action: Action, results: list[ToolResult] \| None) -> None`；`async finish(self, ctx: RunContext) -> None`；`async close(self) -> None` |

方法名的**集合**也是契约：新增方法（例如给 `Memory` 加 `forget()`、给 `Runtime` 加
`startup_hook()`、给 `ToolExecutor` 恢复 `execute_one()`）等于 ABI 变更。

生命周期 / 异常语义同样冻结（V2.5 冻结，V3 不动）：

```text
Runtime owns Model / Toolbox / ContextEngine / ToolExecutor
ToolExecutor borrows Toolbox（不关闭它），close() 只关自身资源
Runtime.close() 顺序：executor.close() → toolbox.close()
Tool.run() 内部异常 → ToolResult(error=True)，不冒泡
Toolbox.lookup() 异常 → Executor infrastructure error，冒泡
asyncio.CancelledError → 穿透，不捕获，不转 ToolResult
单 call 超时 / 重试耗尽 → 保留 ToolResult(error=True)
每个 ToolResult.tool_call_id 必须等于输入 call.id（Executor 强制覆盖）
```

`PreparedInput` 也是冻结形状：`@dataclass(slots=True)`，字段
`messages: list[Message]`、`tools: list[ToolSpec]`（无默认值，顺序固定）。

### 1.2 `kernel.types` —— dataclass 字段（名字 + 类型 + 顺序）

| dataclass | 选项 | 字段（按顺序） |
|---|---|---|
| `Message` | `slots` | `role: Role`；`content: str = ""`；`tool_calls: list[ToolCall] = field(default_factory=list)`；`tool_call_id: str \| None = None` |
| `ToolCall` | `slots` | `id: str`；`name: str`；`arguments: dict[str, Any] = field(default_factory=dict)` |
| `ToolSpec` | `slots` | `name: str`；`description: str = ""`；`parameters: dict[str, Any] = field(default_factory=lambda: {"type": "object", "properties": {}})` |
| `ToolResult` | `slots`, `kw_only` | `tool_call_id: str = ""`；`content: str = ""`；`error: bool = False`；`metadata: dict[str, Any] = field(default_factory=dict)` |
| `MemoryItem` | `slots` | `content: str`；`kind: str = "memory"`；`metadata: dict[str, Any] = field(default_factory=dict)` |
| `MemoryInput` | `slots` | `task: str`；`messages: list[Message] = field(default_factory=list)`；`result: str = ""` |
| `ContextItem` | `slots` | `content: str`；`role: Role = "system"`；`source: str = "unknown"`；`kind: str = "text"` |
| `Final` | `slots` | `content: str` |
| `ToolCalls` | `slots` | `calls: list[ToolCall]`；`content: str = ""` |

类型别名：

```text
Role   = Literal["system", "user", "assistant", "tool"]
Action = Union[Final, ToolCalls]
```

字段顺序、默认值、`slots` / `kw_only` 选项都是契约的一部分：
`ToolResult("hello")` 必须继续抛 `TypeError`（V2.5 冻结的 kw-only）。

### 1.3 `kernel.state` —— RunContext 全字段 / TerminationReason 全枚举值

`TerminationReason(str, Enum)` 全部枚举值（顺序即定义顺序）：

```text
FINAL           = "final"
MAX_ITERATIONS  = "max_iterations"
STOPPED         = "stopped"
ERROR           = "error"
```

`RunContext`（**普通 `@dataclass`，不是 `slots`**）全部字段，顺序固定：

| # | 字段 | 类型 | 默认值 |
|---|---|---|---|
| 1 | `task` | `str` | 必填 |
| 2 | `system` | `str` | `""` |
| 3 | `messages` | `list[Message]` | `field(default_factory=list)` |
| 4 | `step` | `int` | `0` |
| 5 | `max_iterations` | `int` | `16` |
| 6 | `stop` | `bool` | `False` |
| 7 | `done` | `bool` | `False` |
| 8 | `result` | `str` | `""` |
| 9 | `last_assistant` | `Message \| None` | `None` |
| 10 | `error` | `BaseException \| None` | `None` |
| 11 | `reason` | `TerminationReason \| None` | `None` |
| 12 | `scratch` | `dict[str, Any]` | `field(default_factory=dict)` |

**绝不要往 `RunContext` 里加**：model / memory / toolbox / skills / mcp / workspace /
user / session / trace / token_usage / cost —— 也不要在 V3 期间加任何别的字段。

### 1.4 `kernel.events` —— EventBus 签名

```text
def on(self, event: str, handler: Handler) -> EventBus
def off(self, event: str, handler: Handler) -> None
async def emit(self, event: str, **payload: Any) -> None
```

其中 `Handler = Callable[..., Awaitable[None] | None]`。

- v2 快照刻意**不含** EventBus 形状（V2 保留把事件升级为 Event 对象的空间），
  所以 `test_abi_drift.py` 对本节的期望值直接取自**本文件**，
  并额外比对 v3 快照里的 EventBus 抄录。
- 冻结的是这三个方法；`__init__` / `on_handler_error` / 内部实现不在 §3.1 清单内。
- V3 明确不做「Event 对象化」（§二 Non-goals）：`emit(name, **kw)` 不变。

---

## 二、不冻结内容

以下改动**不算** ABI 变更，不需要走变更流程：

```text
docstring 长度与措辞
函数体等内部实现
私有属性 / 私有方法（`_` 前缀，含 EventBus._handlers / _wildcard）
__repr__ 格式
kernel 内部的模块划分与 import 组织（只要满足 Architecture Firewall）
```

LOC 只是**观察指标**，不是门禁 —— 唯一的尺寸门禁是
`agent_loop ≤ 55` 行（`tests/test_architecture_firewall.py::test_agent_loop_line_count`）。

---

## 三、门禁机制

```text
docs/freeze/v2/scratch.py     ← V2.5 基线（kernel + models/base）
docs/freeze/v3/scratch.py     ← V3 基线（kernel 部分与 v2 逐字一致 + 3 个扩展 Protocol）
docs/freeze/v3/ABI.md         ← 本文件（人读清单，EventBus 的期望值来源）
tests/test_abi_drift.py       ← 机器门禁：runtime kernel ABI == v2 快照 == v3 快照
tests/test_architecture_firewall.py  ← 负向 Contract：5 条规则
```

任何 ABI 变更（**含「只是加个可选参数」**）都必须显式通过 `Kernel ABI Change` 流程，
而 **V3 期间不允许**。改 `agentkit/kernel/` 的契约形状 → drift 测试立刻变红。

### Kernel Change Review 三问（如果真必须改）

```text
1. 为什么现有 extension point 无法承载？
2. 为什么必须改变 Kernel Control Flow / Data Contract？
3. 如果改变 Kernel ABI，新增的能力是否具有长期稳定的语义？
```

三问不能答清就不准进 Kernel。

---

## 四、V3 期间改 ABI = 命题证伪

V3 的命题是：

> 一组**独立实现**的能力，可以通过现有 extension contract 组合起来，
> 而不修改 Kernel 控制流与 Kernel 公共 ABI。

因此 Kernel ABI 一旦在 V3 期间变化（哪怕只是加一个默认参数），
「扩展压力不会改变架构拓扑」这句话就不再成立 —— **V3 命题当场证伪**，
而不是「等 V3 结束再评估」。

新能力必须落在 `agentkit.api` 的 3 个扩展 Protocol 上
（`ContextTransform` / `SkillProvider` / `PermissionPolicy`，见 V3 §四 / §6）：

```text
ContextTransform.apply(self, messages: Sequence[Message]) -> list[Message]   # async，不接收 ctx
SkillProvider.search(self, query: str, limit: int = 3) -> list[Skill]        # async，无 refresh/close
PermissionPolicy.allow(self, call: ToolCall, ctx: RunContext) -> bool         # async
```

这三个 Protocol **不属于 Kernel ABI**，它们的定义源是
`agentkit/api/context.py`、`agentkit/api/skill.py`、`agentkit/api/executor.py`，
并被 `docs/freeze/v3/scratch.py` 逐字抄录（由 `test_abi_drift.py` 守卫）。
