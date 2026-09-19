# Agent Microkernel 实施方案 V1.1（Contract Freeze）

---

## 〇、修订对照表（相对 V1）

| # | 项 | V1 | V1.1 |
|---|---|---|---|
| 1 | Memory 返回类型 | `list[Message]` | `list[MemoryItem]`；写入用 `MemoryInput` |
| 2 | RunContext 职责 | 略含 Runtime Context | 明确定位为 **Loop State**，不含任何能力引用 |
| 3 | `stop` 检查 | 循环末尾 | **每个副作用动作之前** |
| 4 | `result` 取值 | `messages[-1].content` | Final 时设置；否则空 |
| 5 | Tool 错误 | 字符串 `[tool error]` | `ToolResult(content, error=True)` |
| 6 | 生命周期 | 无 | `Agent.close()` + `async with`；Harness owns Provider |
| 7 | Toolbox 动态性 | 只加载一次 | `refresh()` 显式刷新 |
| 8 | ToolExecutor | "以后包一下" | **不实现**，只作为文档里的唯一重要扩展点 |

---

## 一、设计原则（写代码前钉死）

1. **Loop 只认识 Runtime。** 词表里不能出现 `model / memory / tools / mcp / skill`。
2. **Runtime 是适配器，不是容器。** 只暴露 Loop 需要的 6 个方法，不暴露能力字段。
3. **只有两个扩展点：`Runtime`（换控制流）+ `EventBus`（挂观测/控制）。** 其余全是 Protocol 实现。
4. **MCP / Skill 都不是一等公民。** MCP 是 `ToolProvider`；Skill 是 `ContextProvider`（+ 可选工具）。
5. **`ContextProvider` 是一等公民，`ContextEngine` V1 保持极简**：providers 列表 + 顺序拼接，不做 budget / compact。
6. **`Agent.__init__` 只有一个参数：`harness`。** 守住这条，架构不会烂。
7. **可替换的是实现，不是数据契约。** `Message / ToolCall / ToolSpec / RunContext / MemoryItem / ToolResult / ContextItem` 是稳定契约。

---

## 二、架构总览

```text
                        User Harness
                             │
                             │ owns
                             ▼
┌──────────────────────────────────────────────────────┐
│                     Agent Loop                       │
│                                                      │
│    RunContext  ◄── Loop State                        │
│                                                      │
│    runtime.prepare(ctx)  ──► PreparedInput           │
│    runtime.reason(ctx)   ──► Action                  │
│    runtime.act(ctx)      ──► list[ToolResult]        │
│    runtime.observe(ctx)  ──► (mutates ctx.messages)  │
│    runtime.finish(ctx)   ──► (memory.commit)         │
└──────────────────────┬───────────────────────────────┘
                       │
                DefaultRuntime
                       │
      ┌────────────────┼─────────────────┐
      ▼                ▼                 ▼
    Model          ContextEngine      Toolbox
                       │                 │
                       │        ┌────────┼────────┐
                       │        ▼        ▼        ▼
                       │      Local     MCP     Skill
                       ▼
              ContextProvider[]
                       │
                       ▼
                 Memory.recall
                       │
                  MemoryItem[]
                       │
                  ContextItem[]
```

**注意：这张图里没有** `Runtime` 之外的 manager（`MemoryManager / SkillManager / PluginManager / ExecutorManager / Workflow / Graph / StateMachine`）。V1 一律不加。

---

## 三、目录结构

```
agentkit/
├── kernel/                       # 冻结契约层，≤ 400 LOC
│   ├── types.py                  # Message / ToolCall / ToolSpec / ToolResult
│   │                             # MemoryItem / MemoryInput / ContextItem
│   │                             # Final / ToolCalls / Action
│   ├── state.py                  # RunContext（Loop State）
│   ├── events.py                 # EventBus
│   ├── protocols.py              # Model / Tool / ToolProvider / Memory
│   │                             # ContextProvider / Runtime
│   └── loop.py                   # agent_loop —— 唯一不可替换
│
├── runtime/
│   └── default.py                # DefaultRuntime + ContextEngine（极简）
│
├── toolbox.py                    # Toolbox（Discovery + Lookup）
├── agent.py                      # Agent（只有 harness 一个参数）
│
├── harness/
│   └── base.py                   # Harness 基类
│
├── tools/
│   ├── function.py               # FunctionTool + @tool
│   ├── schema.py                 # schema_from_signature
│   └── mcp.py                    # MCPProvider（= ToolProvider）
│
├── context/
│   └── providers.py              # SystemPrompt / MemoryContext / Callable
│
├── memory/
│   └── simple.py                 # NullMemory / InMemoryMemory
│
├── skills/
│   ├── skill.py                  # Skill dataclass
│   └── directory.py              # DirectorySkills
│
├── models/
│   ├── openai.py
│   └── anthropic.py
│
└── contrib/                      # 用户可能新增的实现
    ├── sqlite_memory.py
    ├── vector_memory.py
    └── local_tools.py
```

---

## 四、Kernel 层（冻结，不再扩张）

### 4.1 `kernel/types.py`

```python
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


@dataclass(slots=True)
class ToolResult:
    """工具执行的唯一返回结构。

    故意只保留三个字段。多模态/artifact 留到 V2。
    """
    content: str
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
    calls: list[ToolCall]


Action = Union[Final, ToolCalls]
```

### 4.2 `kernel/state.py`

```python
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Any
from .types import Message


@dataclass
class RunContext:
    """Loop State。

    这是 Loop 的单次运行状态。它不属于 Runtime，也不属于 Harness。

    绝不要往里面加：model / memory / toolbox / skills / mcp / workspace
    / user / session / trace / token_usage / cost。
    """
    task: str
    system: str = ""
    messages: list[Message] = field(default_factory=list)

    step: int = 0
    max_iterations: int = 16

    stop: bool = False                 # Hook 可置位；Loop 在副作用前检查
    done: bool = False
    result: str = ""
    last_assistant: Message | None = None
    error: BaseException | None = None

    scratch: dict[str, Any] = field(default_factory=dict)
```

### 4.3 `kernel/events.py`

```python
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
```

> **默认 `raise`**：silent failure 比崩溃更难调试。想容忍的 Harness 显式传 `EventBus(on_handler_error="ignore")`。
>
> **`asyncio.CancelledError`** 在 Python 3.8+ 是 `BaseException` 子类，不会被 `except Exception` 捕获——符合"取消必须穿透"的语义。

### 4.4 `kernel/protocols.py`

```python
from __future__ import annotations
from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable

from .events import EventBus
from .state import RunContext
from .types import (
    Action, ContextItem, MemoryInput, MemoryItem,
    Message, ToolCalls, ToolResult, ToolSpec,
)


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
class Memory(Protocol):
    """只有两个方法。

    multi-store / router / dedup / summary 都是 Harness 层的事。
    """
    async def recall(self, task: str) -> list[MemoryItem]: ...
    async def remember(self, run: MemoryInput) -> None: ...


@runtime_checkable
class ContextProvider(Protocol):
    async def provide(self, ctx: RunContext) -> list[ContextItem]: ...


@runtime_checkable
class Runtime(Protocol):
    """Loop 眼中世界的全部。

    Runtime 是适配器，不是容器：
    它不暴露 model / toolbox / memory / context 任何一个字段。
    """
    events: EventBus

    async def prepare(self, ctx: RunContext) -> PreparedInput: ...
    async def reason(self, ctx: RunContext, inp: PreparedInput) -> Action: ...
    async def act(self, ctx: RunContext, action: ToolCalls) -> list[ToolResult]: ...
    async def observe(
        self, ctx: RunContext, action: Action, results: list[ToolResult] | None,
    ) -> None: ...
    async def finish(self, ctx: RunContext) -> None: ...
    async def close(self) -> None: ...
```

### 4.5 `kernel/loop.py` —— 唯一不可替换

```python
from __future__ import annotations
from .protocols import Runtime
from .state import RunContext
from .types import Final


async def agent_loop(runtime: Runtime, ctx: RunContext) -> RunContext:
    """30 行。只认识 Runtime。永不膨胀。

    停止检查点（stop invariant）：
      任何可产生外部副作用的动作之前，检查 ctx.stop。
      目前副作用动作有两个：model 调用、tool 批量执行。
    """
    await runtime.events.emit("agent.start", ctx=ctx)
    try:
        while not ctx.done and ctx.step < ctx.max_iterations:

            # ── 检查点 1：进入新 iteration 之前 ──
            if ctx.stop:
                break

            # prepare 无副作用，不需要 stop 检查
            inp = await runtime.prepare(ctx)
            await runtime.events.emit("model.before", ctx=ctx, inp=inp)

            # ── 检查点 2：调用模型之前 ──
            if ctx.stop:
                break

            action = await runtime.reason(ctx, inp)
            await runtime.events.emit("model.after", ctx=ctx, action=action)

            if isinstance(action, Final):
                await runtime.observe(ctx, action, None)
                ctx.result = action.content
                ctx.done = True
                break

            # ── 检查点 3：执行工具之前 ──
            # （act 内部还可能按 tool 粒度再检查一次；见 DefaultRuntime）
            if ctx.stop:
                break

            results = await runtime.act(ctx, action)
            await runtime.observe(ctx, action, results)

            ctx.step += 1
            await runtime.events.emit(
                "iteration.done", ctx=ctx, action=action, results=results,
            )

    except BaseException as e:
        ctx.error = e
        ctx.done = True
        await runtime.events.emit("agent.error", ctx=ctx, error=e)
        raise
    finally:
        await runtime.finish(ctx)
        await runtime.events.emit("agent.end", ctx=ctx)
    return ctx
```

**这就结束了 Kernel。** 包含全部 import / 空行 ≤ 400 行。

---

## 五、装配层（Runtime / Harness / Agent）

### 5.1 `runtime/default.py`

```python
from __future__ import annotations
from ..kernel.events import EventBus
from ..kernel.protocols import (
    ContextProvider, Memory, Model, PreparedInput,
)
from ..kernel.state import RunContext
from ..kernel.types import (
    Action, ContextItem, Final, MemoryInput, Message,
    ToolCalls, ToolResult,
)


class ContextEngine:
    """V1 极简 Context 装配器。

    不做 budget，不做 compact，不做 token 计数。
    唯一扩展点是 providers 列表。

    当 Harness 需要预算/压缩时，走 EventBus：
      events.on("model.before", compact_hook)  会修改 ctx.messages。
    """

    def __init__(
        self,
        providers: list[ContextProvider] | None = None,
        history_limit: int = 40,
    ) -> None:
        self.providers = list(providers or [])
        self.history_limit = history_limit

    def add(self, provider: ContextProvider) -> "ContextEngine":
        self.providers.append(provider)
        return self

    async def build(self, ctx: RunContext) -> list[Message]:
        items: list[ContextItem] = []
        for p in self.providers:
            items.extend(await p.provide(ctx))

        system = [Message("system", i.content)
                  for i in items if i.role == "system"]
        extra = [Message(i.role, i.content)
                 for i in items if i.role != "system"]
        history = ctx.messages[-self.history_limit:]
        return system + history + extra


class DefaultRuntime:
    """把 model / toolbox / context / memory 适配成 Runtime。

    注意：这些是构造参数，不是公开属性。
    Loop 看不到它们。
    """

    def __init__(
        self,
        model: Model,
        toolbox,                                  # Toolbox
        context: ContextEngine,
        memory: Memory | None = None,
        events: EventBus | None = None,
    ) -> None:
        self._model = model
        self._toolbox = toolbox
        self._context = context
        self._memory = memory
        self.events = events or EventBus()

    async def prepare(self, ctx: RunContext) -> PreparedInput:
        messages = await self._context.build(ctx)
        tools = await self._toolbox.specs()
        return PreparedInput(messages=messages, tools=tools)

    async def reason(self, ctx: RunContext, inp: PreparedInput) -> Action:
        return await self._model.generate(inp.messages, inp.tools)

    async def act(self, ctx: RunContext, action: ToolCalls) -> list[ToolResult]:
        # 二次确认 stop（Loop 已检查过一次；这里是批内检查入口）
        if ctx.stop:
            return []
        return await self._toolbox.execute(action)

    async def observe(
        self, ctx: RunContext, action: Action, results: list[ToolResult] | None,
    ) -> None:
        if isinstance(action, Final):
            msg = Message("assistant", action.content)
            ctx.messages.append(msg)
            ctx.last_assistant = msg
            return

        assistant_msg = Message("assistant", "", tool_calls=list(action.calls))
        ctx.messages.append(assistant_msg)
        ctx.last_assistant = assistant_msg

        for call, res in zip(action.calls, results or ()):
            text = f"[tool_error] {res.content}" if res.error else res.content
            ctx.messages.append(Message("tool", text, tool_call_id=call.id))

    async def finish(self, ctx: RunContext) -> None:
        if self._memory is not None:
            await self._memory.remember(MemoryInput(
                task=ctx.task,
                messages=ctx.messages,
                result=ctx.result,
            ))

    async def close(self) -> None:
        await self._toolbox.close()
```

### 5.2 `harness/base.py`

```python
from __future__ import annotations
from ..kernel.protocols import Runtime


class Harness:
    """Harness 只做两件事：

    1. build_runtime() —— 组装 Model / Toolbox / Context / Memory / Events
    2. close()         —— 释放自己持有的资源（MCP session / HTTP client...）

    Harness 拥有 Provider 的生命周期，Agent 只负责调用它。
    """

    def build_runtime(self) -> Runtime:
        raise NotImplementedError

    async def close(self) -> None:
        return None
```

### 5.3 `agent.py`

```python
from __future__ import annotations
from .harness.base import Harness
from .kernel.loop import agent_loop
from .kernel.state import RunContext
from .kernel.types import Message


class Agent:
    """构造函数只有一个参数。守住这条，架构就不会烂。"""

    def __init__(self, harness: Harness) -> None:
        self.harness = harness
        self._runtime = None

    @property
    def runtime(self):
        if self._runtime is None:
            self._runtime = self.harness.build_runtime()
        return self._runtime

    async def run(
        self,
        task: str,
        *,
        max_iterations: int = 16,
        system: str = "",
    ) -> str:
        ctx = RunContext(
            task=task,
            system=system,
            messages=[Message("user", task)],
            max_iterations=max_iterations,
        )
        await agent_loop(self.runtime, ctx)
        return ctx.result

    async def close(self) -> None:
        if self._runtime is not None:
            try:
                await self._runtime.close()
            finally:
                self._runtime = None
        await self.harness.close()

    async def __aenter__(self) -> "Agent":
        return self

    async def __aexit__(self, *exc) -> None:
        await self.close()
```

---

## 六、能力层

### 6.1 Toolbox（Discovery + Lookup）

```python
# toolbox.py
from __future__ import annotations
import asyncio
from .kernel.protocols import Tool, ToolProvider
from .kernel.types import ToolCall, ToolCalls, ToolResult, ToolSpec


class Toolbox:
    """Tool Discovery + Lookup。

    执行策略（并行/重试/权限/沙箱）留在未来的 ToolExecutor。
    V1 只提供默认 asyncio.gather 并发。
    """

    def __init__(self, tools: list[Tool] | None = None) -> None:
        self._local: dict[str, Tool] = {t.spec.name: t for t in (tools or [])}
        self._providers: list[ToolProvider] = []
        self._index: dict[str, Tool] | None = None

    def register(self, tool: Tool) -> "Toolbox":
        self._local[tool.spec.name] = tool
        self._index = None
        return self

    def add_provider(self, provider: ToolProvider) -> "Toolbox":
        self._providers.append(provider)
        self._index = None
        return self

    async def _ensure(self) -> dict[str, Tool]:
        if self._index is None:
            idx = dict(self._local)
            for p in self._providers:
                for t in await p.tools():
                    if t.spec.name in idx:
                        raise ValueError(f"duplicate tool: {t.spec.name}")
                    idx[t.spec.name] = t
            self._index = idx
        return self._index

    async def refresh(self) -> None:
        """MCP server 工具列表变化后调用。"""
        self._index = None
        await self._ensure()

    async def specs(self) -> list[ToolSpec]:
        return [t.spec for t in (await self._ensure()).values()]

    async def execute(self, action: ToolCalls) -> list[ToolResult]:
        idx = await self._ensure()
        return await asyncio.gather(*(self._run(idx, c) for c in action.calls))

    async def _run(self, idx, call: ToolCall) -> ToolResult:
        if call.name not in idx:
            return ToolResult(f"unknown tool: {call.name}", error=True)
        try:
            return await idx[call.name].run(call.arguments)
        except asyncio.CancelledError:
            # 取消必须穿透，不能吞
            raise
        except Exception as e:
            return ToolResult(f"{type(e).__name__}: {e}", error=True)

    async def close(self) -> None:
        for p in self._providers:
            try:
                await p.close()
            except Exception:
                pass
```

### 6.2 本地工具 + Schema 生成

```python
# tools/schema.py
from __future__ import annotations
import inspect
from typing import Any, Callable

_JSON_TYPE = {
    str: "string", int: "integer", float: "number",
    bool: "boolean", list: "array", dict: "object",
}
_STR_TYPE = {
    "str": "string", "int": "integer", "float": "number",
    "bool": "boolean", "list": "array", "dict": "object",
}


def _type_of(annotation: Any) -> str:
    if annotation in _JSON_TYPE:
        return _JSON_TYPE[annotation]
    if isinstance(annotation, str):
        return _STR_TYPE.get(annotation, "string")
    return "string"


def schema_from_signature(fn: Callable) -> dict[str, Any]:
    props: dict[str, Any] = {}
    required: list[str] = []
    for name, p in inspect.signature(fn).parameters.items():
        if name in ("self", "cls"):
            continue
        props[name] = {"type": _type_of(p.annotation)}
        if p.default is inspect.Parameter.empty:
            required.append(name)
    schema: dict[str, Any] = {"type": "object", "properties": props}
    if required:
        schema["required"] = required
    return schema
```

```python
# tools/function.py
from __future__ import annotations
import inspect
import json
from typing import Any
from ..kernel.types import ToolResult, ToolSpec
from .schema import schema_from_signature


class FunctionTool:
    def __init__(self, fn, *, name=None, description=None, parameters=None):
        self.fn = fn
        self.spec = ToolSpec(
            name=name or fn.__name__,
            description=description or (inspect.getdoc(fn) or "").strip(),
            parameters=parameters or schema_from_signature(fn),
        )

    async def run(self, arguments: dict[str, Any]) -> ToolResult:
        try:
            r = self.fn(**arguments)
            if inspect.isawaitable(r):
                r = await r
        except Exception as e:
            return ToolResult(f"{type(e).__name__}: {e}", error=True)

        if isinstance(r, ToolResult):
            return r
        if isinstance(r, str):
            return ToolResult(r)
        return ToolResult(json.dumps(r, ensure_ascii=False, default=str))


def tool(fn=None, *, name=None, description=None, parameters=None):
    def wrap(f):
        return FunctionTool(f, name=name, description=description, parameters=parameters)
    return wrap(fn) if fn else wrap
```

### 6.3 MCP（就是一个 ToolProvider）

```python
# tools/mcp.py
from __future__ import annotations
from ..kernel.types import ToolResult, ToolSpec


class _MCPTool:
    def __init__(self, session, defn):
        self.session = session
        self.spec = ToolSpec(
            name=defn.name,
            description=defn.description or "",
            parameters=defn.inputSchema or {"type": "object", "properties": {}},
        )

    async def run(self, arguments: dict) -> ToolResult:
        try:
            r = await self.session.call_tool(self.spec.name, arguments)
        except Exception as e:
            return ToolResult(f"{type(e).__name__}: {e}", error=True)
        text = "\n".join(c.text for c in r.content if hasattr(c, "text"))
        return ToolResult(text)


class MCPProvider:
    """MCP 只是 ToolProvider 的一个实现。Loop 永远不知道 MCP 存在。"""

    def __init__(self, session):
        self.session = session

    async def tools(self):
        resp = await self.session.list_tools()
        return [_MCPTool(self.session, t) for t in resp.tools]

    async def close(self):
        await self.session.aclose()
```

### 6.4 Memory

```python
# memory/simple.py
from __future__ import annotations
from ..kernel.types import MemoryInput, MemoryItem


class NullMemory:
    async def recall(self, task: str) -> list[MemoryItem]:
        return []

    async def remember(self, run: MemoryInput) -> None:
        return None


class InMemoryMemory:
    """最朴素的长期记忆，仅用于演示与测试。生产请替换。"""

    def __init__(self, max_items: int = 200, top_k: int = 10):
        self.items: list[MemoryItem] = []
        self.max_items = max_items
        self.top_k = top_k

    async def recall(self, task: str) -> list[MemoryItem]:
        return self.items[-self.top_k:]

    async def remember(self, run: MemoryInput) -> None:
        if not run.result:
            return
        self.items.append(MemoryItem(
            content=run.result,
            kind="episodic",
            metadata={"task": run.task},
        ))
        self.items = self.items[-self.max_items:]
```

> **关键**：Memory 里没有任何 `Message` 的影子。`MemoryItem → ContextItem → Message` 这条链由 Context 层完成。

### 6.5 Context Providers

```python
# context/providers.py
from __future__ import annotations
import inspect
from ..kernel.state import RunContext
from ..kernel.types import ContextItem


class SystemPrompt:
    def __init__(self, text: str):
        self.text = text

    async def provide(self, ctx: RunContext) -> list[ContextItem]:
        return [ContextItem(self.text, role="system", source="system")]


class MemoryContext:
    """把 MemoryItem 转成 ContextItem。

    转换在这里发生，不在 Memory 内部。
    """

    def __init__(self, memory):
        self.memory = memory

    async def provide(self, ctx: RunContext) -> list[ContextItem]:
        items = await self.memory.recall(ctx.task)
        return [
            ContextItem(i.content, role="system", source="memory", kind=i.kind)
            for i in items
        ]


class CallableProvider:
    """任意同步/异步函数都能当 ContextProvider。"""

    def __init__(self, fn):
        self.fn = fn

    async def provide(self, ctx: RunContext) -> list[ContextItem]:
        r = self.fn(ctx)
        if inspect.isawaitable(r):
            r = await r
        return r or []
```

### 6.6 Skills

```python
# skills/skill.py
from __future__ import annotations
from dataclasses import dataclass, field


@dataclass
class Skill:
    """Skill ≠ Tool。

    Skill 是一组能力定义，包含 instructions、resources 和 tools。
    V1 只实现 instructions 注入；tools 走 SkillProvider。
    """
    name: str
    description: str = ""
    instructions: str = ""
    tools: list = field(default_factory=list)
    metadata: dict = field(default_factory=dict)
```

```python
# skills/directory.py
from __future__ import annotations
from pathlib import Path
from ..kernel.state import RunContext
from ..kernel.types import ContextItem
from .skill import Skill


def _parse_frontmatter(text: str) -> tuple[dict, str]:
    if not text.startswith("---"):
        return {}, text
    _, fm, body = text.split("---", 2)
    meta = {}
    for line in fm.strip().splitlines():
        if ":" in line:
            k, v = line.split(":", 1)
            meta[k.strip()] = v.strip()
    return meta, body.strip()


class DirectorySkills:
    """V1：静态加载 skills/<name>/SKILL.md。

    - 作为 ContextProvider 注入相关技能指令
    - V1 不做动态工具注入（V2 接 tools.py）
    """

    def __init__(self, root: str, top_k: int = 3):
        self.root = Path(root)
        self.top_k = top_k
        self._cache: list[Skill] | None = None

    def _load(self) -> list[Skill]:
        out = []
        if not self.root.exists():
            return out
        for d in sorted(self.root.iterdir()):
            f = d / "SKILL.md"
            if not d.is_dir() or not f.exists():
                continue
            meta, body = _parse_frontmatter(f.read_text(encoding="utf-8"))
            out.append(Skill(
                name=meta.get("name", d.name),
                description=meta.get("description", ""),
                instructions=body,
            ))
        return out

    def _all(self) -> list[Skill]:
        if self._cache is None:
            self._cache = self._load()
        return self._cache

    async def search(self, query: str, k: int | None = None) -> list[Skill]:
        q = query.lower()
        scored = []
        for s in self._all():
            score = 2 if s.name.lower() in q else 0
            score += sum(1 for w in s.description.lower().split() if w and w in q)
            scored.append((score, s))
        scored.sort(key=lambda x: -x[0])
        return [s for _, s in scored[: (k or self.top_k)]]

    # ── ContextProvider ──
    async def provide(self, ctx: RunContext) -> list[ContextItem]:
        skills = await self.search(ctx.task)
        if not skills:
            return []
        blocks = ["# Available Skills"]
        for s in skills:
            blocks.append(f"## {s.name}\n{s.description}\n{s.instructions}".strip())
        return [ContextItem("\n\n".join(blocks), role="system", source="skills")]
```

### 6.7 Models

```python
# models/openai.py
from __future__ import annotations
import json
from ..kernel.types import Final, Message, ToolCall, ToolCalls


def _to_openai(m: Message) -> dict:
    if m.role == "tool":
        return {"role": "tool", "content": m.content, "tool_call_id": m.tool_call_id}
    if m.role == "assistant" and m.tool_calls:
        return {
            "role": "assistant",
            "content": m.content or None,
            "tool_calls": [
                {
                    "id": tc.id,
                    "type": "function",
                    "function": {
                        "name": tc.name,
                        "arguments": json.dumps(tc.arguments, ensure_ascii=False),
                    },
                }
                for tc in m.tool_calls
            ],
        }
    return {"role": m.role, "content": m.content}


def _to_openai_tool(spec) -> dict:
    return {
        "type": "function",
        "function": {
            "name": spec.name,
            "description": spec.description,
            "parameters": spec.parameters,
        },
    }


class OpenAIModel:
    def __init__(self, model: str, client=None, **kwargs):
        self.model = model
        self.kwargs = kwargs
        if client is None:
            from openai import AsyncOpenAI
            client = AsyncOpenAI()
        self.client = client

    async def generate(self, messages, tools):
        resp = await self.client.chat.completions.create(
            model=self.model,
            messages=[_to_openai(m) for m in messages],
            tools=[_to_openai_tool(t) for t in tools] or None,
            **self.kwargs,
        )
        msg = resp.choices[0].message
        if msg.tool_calls:
            return ToolCalls([
                ToolCall(
                    id=tc.id,
                    name=tc.function.name,
                    arguments=json.loads(tc.function.arguments or "{}"),
                )
                for tc in msg.tool_calls
            ])
        return Final(msg.content or "")
```

Anthropic 完全同构：`tool_use → ToolCalls`、`text → Final`。

---

## 七、生命周期与错误语义

### 7.1 生命周期

```text
Harness 创建 Model / Toolbox / Provider / Memory
        │
        ▼
Harness.build_runtime()  ──►  Runtime
        │
        ▼
Agent.run()  ×  N
        │
        ▼
Agent.close()
   ├── runtime.close()
   │      └── toolbox.close()
   │              └── provider.close()    (MCP session / HTTP client)
   └── harness.close()
```

**规则：谁创建，谁拥有；Agent.close() 沿链下调。**

### 7.2 错误语义

| 错误源 | 行为 |
|---|---|
| 工具执行异常（`Exception`） | 转 `ToolResult(error=True)`，让模型看到 |
| 工具执行取消（`asyncio.CancelledError`） | **穿透，不吞** |
| EventBus handler 异常 | 默认 raise（`on_handler_error="raise"`），可配 ignore |
| Model 调用异常 | 冒泡到 loop → `agent.error` 事件 → `ctx.error` 记录 → 重新抛出 |
| Memory.remember 异常 | 冒泡（V1 不吞；如需容错在 Harness 里包） |

### 7.3 stop 语义（Loop invariant）

> **任何可产生外部副作用的动作之前，检查 `ctx.stop`。**

副作用动作清单（V1）：
- `runtime.reason`（调用模型）
- `runtime.act`（执行工具批量）

未来新增副作用动作时，必须同步在 loop 里插入检查点。

---

## 八、使用示例

### 8.1 入门：30 行组装

```python
from agentkit.agent import Agent
from agentkit.harness.base import Harness
from agentkit.runtime.default import ContextEngine, DefaultRuntime
from agentkit.context.providers import SystemPrompt, MemoryContext
from agentkit.tools.function import tool
from agentkit.toolbox import Toolbox
from agentkit.skills.directory import DirectorySkills
from agentkit.memory.simple import InMemoryMemory
from agentkit.models.openai import OpenAIModel
from agentkit.kernel.events import EventBus


@tool
def read_file(path: str) -> str:
    """读取文件内容。"""
    return open(path).read()


class CodingHarness(Harness):
    def __init__(self):
        self.events = EventBus()
        self.events.on("*", lambda e, **_: print(f"[trace] {e}"))

        self.memory = InMemoryMemory()
        self.skills = DirectorySkills("./skills")
        self.toolbox = Toolbox([read_file])

        self.context = ContextEngine([
            SystemPrompt("You are a careful coding agent."),
            self.skills,
            MemoryContext(self.memory),
        ])

    def build_runtime(self):
        return DefaultRuntime(
            model=OpenAIModel("gpt-4o-mini"),
            toolbox=self.toolbox,
            context=self.context,
            memory=self.memory,
            events=self.events,
        )

    async def close(self):
        await self.toolbox.close()


async def main():
    async with Agent(CodingHarness()) as agent:
        print(await agent.run("读一下 README.md 并总结"))
```

### 8.2 挂 MCP + 自定义 Hook

```python
class FullHarness(Harness):
    def __init__(self):
        self.events = EventBus()
        self.toolbox = Toolbox([read_file])

        # MCP = ToolProvider，一行接入
        self.toolbox.add_provider(MCPProvider(github_session))
        self.toolbox.add_provider(MCPProvider(postgres_session))

        # 预算 Hook
        budget = {"used": 0}
        def cost(event, **payload):
            if event == "model.after" and payload.get("ctx"):
                budget["used"] += sum(
                    len(m.content) for m in payload["ctx"].messages
                ) // 4
                if budget["used"] > 32_000:
                    payload["ctx"].stop = True
        self.events.on("model.after", cost)

        # 上下文压缩 Hook
        def compact(event, **payload):
            if event == "model.before" and payload.get("ctx"):
                msgs = payload["ctx"].messages
                if len(msgs) > 40:
                    payload["ctx"].messages = msgs[-40:]
        self.events.on("model.before", compact)

        self.memory = InMemoryMemory()
        self.context = ContextEngine([
            SystemPrompt("You are a careful agent."),
            MemoryContext(self.memory),
        ])

    def build_runtime(self):
        return DefaultRuntime(
            model=OpenAIModel("gpt-4o"),
            toolbox=self.toolbox,
            context=self.context,
            memory=self.memory,
            events=self.events,
        )

    async def close(self):
        await self.toolbox.close()
```

### 8.3 极致：绕过 Harness 直接实现 Runtime

```python
class MyRuntime:
    events = EventBus()

    async def prepare(self, ctx): return PreparedInput([], [])
    async def reason(self, ctx, inp): return Final("我不需要模型。")
    async def act(self, ctx, action): return []
    async def observe(self, ctx, action, results): pass
    async def finish(self, ctx): pass
    async def close(self): pass


agent = Agent(type("H", (), {"build_runtime": lambda self: MyRuntime(),
                             "close": lambda self: None})())
```

---

## 九、扩展点地图

| 想改什么 | 改哪里 | 是否碰 Loop |
|---|---|---|
| 换模型厂商 | `models/*.py` 实现 `Model` | ❌ |
| 加本地工具 | `@tool` 或 `Toolbox.register` | ❌ |
| 加 MCP | `toolbox.add_provider(MCPProvider(...))` | ❌ |
| 加技能 | 丢 `./skills/<name>/SKILL.md` | ❌ |
| 换记忆 | 实现 `Memory.recall/remember` | ❌ |
| 改上下文策略 | 加一个 `ContextProvider` | ❌ |
| 加日志/追踪/权限 | `events.on(...)` | ❌ |
| 加预算/压缩 | `events.on("model.before", fn)` 修改 `ctx.messages` | ❌ |
| **改工具执行策略** | **`ToolExecutor`（V2 唯一新 Protocol）** | ❌ |
| 换调度策略 | 自己实现 `Runtime` | ❌ |
| **改控制流本身** | **改 `kernel/loop.py`** | ✅ **唯一** |

---

## 十、事件清单

```text
agent.start       ctx
model.before      ctx, inp
model.after       ctx, action
iteration.done    ctx, action, results
agent.error       ctx, error
agent.end         ctx
*                 任意事件
```

**V1 保持字符串事件。** V2 若需要结构化可以升级为 `Event(type, data)`——这不需要改 Loop 之外的任何地方。

---

## 十一、V1 API 稳定性分级

### Public / Stable（冻结，V2 不会破坏）

```text
kernel.types:
    Message, ToolCall, ToolSpec, ToolResult
    MemoryItem, MemoryInput, ContextItem
    Final, ToolCalls, Action

kernel.state:
    RunContext

kernel.protocols:
    Model, Tool, ToolProvider, Memory, ContextProvider, Runtime

kernel.loop:
    agent_loop

Agent.run / Agent.close / Agent.__aenter__ / __aexit__

Harness.build_runtime / Harness.close

Toolbox.register / add_provider / refresh / specs / close
```

### Internal / 可变

```text
DefaultRuntime（可以用自定义 Runtime 替换）
ContextEngine（V1 极简实现，未来可能被替换）
所有 contrib 实现（openai / mcp / skills / memory）
EventBus 的 handler 签名细节（V2 可能升级为 Event 对象）
```

### V1 明确不做

```text
ContextEngine 的 budget / compact / priority
ToolExecutor（执行策略）
Event 对象
Streaming
Skill 动态工具发现
Planner / RAG / Sandbox / Permission / Artifact
Memory pipeline / router / dedup
```

---

## 十二、实施路线图

### Phase 1 — Kernel（1~2 天）
- [ ] `kernel/types.py` / `state.py` / `events.py` / `protocols.py` / `loop.py`
- [ ] `DefaultRuntime` + `ContextEngine`
- [ ] `Harness` / `Agent`
- [ ] `EchoModel`（返回 `Final`）
- [ ] 单测：0 工具 / 1 工具 / 工具报错 / 达到 max_iterations / stop 检查点

### Phase 2 — 最小可用（3~5 天）
- [ ] `OpenAIModel`
- [ ] `FunctionTool` + `@tool` + `schema_from_signature`
- [ ] `Toolbox`（含并发 + `ToolResult` 语义 + `refresh`）
- [ ] `NullMemory` / `InMemoryMemory`
- [ ] `SystemPrompt` / `MemoryContext`
- [ ] 一个 `CodingHarness` demo

### Phase 3 — 生态（1~2 周）
- [ ] `MCPProvider`
- [ ] `DirectorySkills`
- [ ] `AnthropicModel` / `OllamaModel`
- [ ] CLI harness（REPL）
- [ ] `EventBus` 内置 tracer / cost / logger

### Phase 4 — V2（观察后决定）
- [ ] `ToolExecutor` Protocol（串行/并行/重试/权限/沙箱）
- [ ] `Event` 对象（`type` + `data`）
- [ ] `Model.stream()`（可选方法，不破坏现有实现）
- [ ] `ContextEngine` 的 `compact` / `budget`
- [ ] Streaming 事件
- [ ] Skill 动态工具注入

---

## 十三、验收清单

如果以下每一条都是 yes，就算落地：

- [ ] `kernel/` 总行数 ≤ 400
- [ ] `agent_loop` 函数体 ≤ 45 行
- [ ] `loop.py` 里没有 `model / memory / tool / mcp / skill / context` 任何一个词
- [ ] `Agent.__init__` 只有 `harness` 一个参数
- [ ] `Memory` 里没有 `Message` 类型出现
- [ ] `Tool.run` 返回 `ToolResult`，工具错误不炸循环
- [ ] `asyncio.CancelledError` 不被吞
- [ ] 每个副作用动作前都有 `if ctx.stop: break`
- [ ] `ctx.result` 只在 `Final` 时被设置（或留空）
- [ ] `Agent` 支持 `async with`
- [ ] 换 OpenAI → Anthropic 只改 `models/` 下一个文件
- [ ] 挂 MCP 只加一行 `toolbox.add_provider(...)`
- [ ] 加日志只加一行 `events.on("*", fn)`
- [ ] 加技能只丢一个 `SKILL.md`
- [ ] 用户能在 `MyHarness.build_runtime()` 里组装任何形态 Agent，不需要读 `loop.py`
- [ ] README 第一行：**"换掉任意模块，都不用动 agent_loop。"**

---

## 十四、取舍（写进 README）

**会做的：**
- 微内核：Loop 只认识 Runtime。
- Context 是一等公民，但 V1 保持极简（provider 列表 + 顺序拼接）。
- 一切通过 Python `Protocol` 表达，不用继承树。
- 唯一的通用扩展点是 `EventBus`。
- Kernel 只包含**稳定契约 + 单一控制流**。

**不会做的（V1）：**
- 不做 YAML 配置地狱 —— 组装就是写 Python。
- 不做万能 `Agent(...)` 构造函数 —— 只有 `Agent(harness)`。
- 不做 `ContextEngine` 的 budget / compact —— 用 `events.on("model.before", fn)` 就够了。
- 不做 `ToolExecutor` —— 只把它作为 V2 的**唯一新 Protocol**。
- 不做 Planner / RAG / Reflection / Multi-Agent —— 它们是 Harness 或 Runtime 的扩展。
- 不做 `MemoryManager / SkillManager / PluginManager` —— V1 一个 manager 都不要。

**这一版的核心竞争力，不是功能多，而是**：

```python
async with Agent(MyHarness()) as agent:
    result = await agent.run(task)
```
