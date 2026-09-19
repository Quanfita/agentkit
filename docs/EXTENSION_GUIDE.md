# AgentKit 扩展指南（V3 · Composable Kernel）

> **V3 的命题：能力可以增长，组合复杂度可以增长，但 Kernel 不增长。**
>
> 这份指南回答一个问题：**站在 agentkit 外面的人，怎么写一个新能力，并且不需要
> 改 Kernel、不需要读 Kernel 源码、不需要 import Kernel 内部模块。**

读完你应该能做到三件事：

1. 找出你要替换的那个扩展点；
2. 只 import `agentkit.api` 把它实现出来；
3. 装进 Harness，跑起来，`kernel/` 一行没动。

---

## 一、边界规则（先读这条，再读别的）

```python
# ✅ 唯一合法的 agentkit 入口
from agentkit.api import Message, ToolResult, ToolSpec, ToolExecutor

# ❌ 以下全部会被 tests/test_api_boundary.py 拒绝
import agentkit.kernel.types
from agentkit.kernel.protocols import Model
from agentkit.runtime.default import DefaultRuntime
from agentkit.context.providers import SystemPrompt
from agentkit import kernel
```

**为什么**：如果每个新能力都要 import Kernel 内部模块，那每次扩展都会把
Kernel 的公共表面往外拽——「能力增长、Kernel 不变」当场失效。
`agentkit.api` 是这条边界的**唯一闸口**，它的 `__all__` 就是第三方能看到的全部世界：

```text
kernel re-exports : Model Tool ToolProvider ToolExecutor Memory ContextProvider Runtime
                    Message ToolCall ToolCalls ToolSpec ToolResult
                    MemoryItem MemoryInput ContextItem
                    Final Action RunContext TerminationReason
                    PreparedInput EventBus
data 契约          : Skill
V3 扩展 Protocol   : ContextTransform SkillProvider PermissionPolicy
```

`tests/third_party/` 里放着四个**只 import `agentkit.api`** 的第三方实现，
它们是这条规则的可执行样本；`tests/test_api_boundary.py` 用 AST 静态扫描
（不是 grep）强制它。

**适用范围**：这条边界约束的是**扩展实现**（下面第二节的 10 个 Protocol 的实现者）。
装配代码（`Harness` / `Toolbox` / `ContextEngine`）在 V3 仍直接使用第一方模块，
`Harness` 本身也还没有进 `agentkit.api`——所以本指南把装配示例单独放在第四节，
并明确标注哪些 import 属于「第一方装配」。

---

## 二、扩展点地图

| 扩展点 | Protocol | 你要 import 的 api 符号 | 替换它意味着什么 |
|---|---|---|---|
| 模型 | `Model` | `Message ToolSpec Action Final ToolCalls` | 换 Provider（OpenAI / Anthropic / Ollama / 本地） |
| 工具 | `Tool` | `ToolSpec ToolResult` | 加一个具体能力 |
| 工具来源 | `ToolProvider` | `Tool ToolSpec` | 本地工具集 / MCP session / Skill 包 |
| 执行策略 | `ToolExecutor` | `RunContext ToolCalls ToolResult` | 并行 / 重试 / 超时 / 权限 / 沙箱 |
| 上下文 | `ContextProvider` | `RunContext ContextItem` | 往 prompt 里注入什么东西 |
| 上下文变换 | `ContextTransform` | `Message` | 预算 / 截断 / 去重（**V3 新增**） |
| 记忆 | `Memory` | `MemoryItem MemoryInput` | 长期记忆的存取 |
| Skill 查询 | `SkillProvider` | `Skill` | Skill 从哪来（**V3 新增**） |
| 权限 | `PermissionPolicy` | `ToolCall RunContext` | 谁有权执行（**V3 新增**） |
| 整个循环的世界 | `Runtime` | `PreparedInput EventBus Action` | 换适配方式（很少需要） |

**Kernel 不认识任何一个具体实现。** Loop 只认识 `Runtime`；`Runtime` 只认识
Protocol。所以「装一个新能力」永远等于「换一个构造参数」，不等于「改一行 Kernel」。

---

## 三、每个扩展点的契约与最小实现

所有示例都是**完整可用的**最小实现。共同的形状约定：

- **不继承任何基类**，只要方法签名对得上（Python `Protocol`，结构类型）；
- 每个 Protocol 都带 `runtime_checkable`，所以 `isinstance(obj, Protocol)` 可以当冒烟检查
  （§5 有命令；`tests/third_party/` 的四个实现就是这样验的）；
- 示例里没有留白：凡是出现的方法都写完了实现，可以直接抄进你的代码。

### 3.1 `Model` —— 产出 `Action`

```python
from agentkit.api import Action, Final, Message, ToolSpec


class UpperCaseModel:
    """最小 Model：不调网络，把最后一条消息变成大写。"""

    async def generate(
        self, messages: list[Message], tools: list[ToolSpec],
    ) -> Action:
        text = messages[-1].content if messages else ""
        return Final(text.upper())
```

契约要点：

- 返回 `Final`（结束本轮）或 `ToolCalls`（请求执行工具）——`Action` 就是这两者的联合；
  `ToolCalls` 允许同时携带文本（`content`），模型既说话又调工具是合法输出；
- `tools` 是本次 Run 可见的工具清单（JSON Schema passthrough），可以为空；
- 这个方法是 `raise` 自由的：抛出的异常会让 loop 把 Run 判为
  `TerminationReason.ERROR` 并向上抛（重试 / 降级属于 Harness 或 Model 自己的事，
  不属于 Kernel）；
- 流式不是 Kernel 概念：`StreamingModel` / `TextDelta` 是第一方增量能力，
  不在 `agentkit.api` 里。

### 3.2 `Tool` —— 具体能力

```python
from typing import Any

from agentkit.api import ToolResult, ToolSpec


class WordCount:
    """最小 Tool：一个 spec 属性 + 一个 run()。"""

    spec = ToolSpec(
        name="word_count",
        description="统计一段文本的词数",
        parameters={
            "type": "object",
            "properties": {"text": {"type": "string"}},
            "required": ["text"],
        },
    )

    async def run(self, arguments: dict[str, Any]) -> ToolResult:
        try:
            text = str(arguments["text"])
        except KeyError:
            return ToolResult(content="missing required argument: text", error=True)
        return ToolResult(content=str(len(text.split())))
```

契约要点：

- **`Tool` 拿不到 `RunContext`。** Kernel 只把 `arguments` 交给 Tool。
  需要 ctx 的策略（权限、配额、审计）属于 `ToolExecutor`，不属于 Tool；
- Tool 内部的失败用 `ToolResult(error=True)` 表达，不要靠抛异常：
  Executor 会把 `Tool.run()` 的异常兜成 `ToolResult(error=True)`，
  但自己说清楚比被兜更好读；
- `ToolResult.tool_call_id` **不用你填**：`ToolCall.id → Executor → ToolResult.tool_call_id`
  由 Executor 强制覆盖（填错会被静默改正，所以别在 Tool 里自造 id）；
- `ToolResult` 是 kw-only（`ToolResult(content="x")`），位置参数会直接 `TypeError`；
- **注意**：`agentkit.tools.function.tool`（`@tool` 装饰器 + 签名内省）是同仓库的
  第一方便捷工具，**没有**通过 `agentkit.api` 重导出。第三方写 Tool 就是写类，
  如上例（`tests/third_party/` 里的四个实现也都不依赖它）。

### 3.3 `ToolProvider` —— 工具集

```python
from agentkit.api import Tool


class StaticTools:
    """最小 ToolProvider：一个固定的工具列表。"""

    def __init__(self, tools: list[Tool]) -> None:
        self._tools = list(tools)

    async def tools(self) -> list[Tool]:
        return list(self._tools)

    async def close(self) -> None:
        return None
```

契约要点：

- `tools()` 可能被调用多次（`Toolbox` 会缓存索引，`refresh()` 后重建），实现要幂等；
- 工具名冲突会在 `Toolbox` 索引阶段直接 `ValueError`——Provider 不负责去重；
- **生命周期**：Provider 归 `Toolbox` 拥有，`Toolbox` 归 Runtime 拥有。
  `Runtime.close()` → `executor.close()` → `toolbox.close()` → 每个
  `provider.close()`。所以 MCP session / HTTP client 这类资源在这里关；
- 长连接类 Provider（MCP）的 `close()` 必须幂等：`Toolbox.close()` 会把异常吞掉后继续，
  但别指望它替你兜第二次。

### 3.4 `ContextProvider` —— 往 prompt 里放什么

```python
from agentkit.api import ContextItem, RunContext


class TaskHint:
    """最小 ContextProvider：把 task 本身作为一条 system context 注入。"""

    async def provide(self, ctx: RunContext) -> list[ContextItem]:
        return [ContextItem(f"当前任务：{ctx.task}", source="task-hint")]
```

契约要点：

- 产出 `ContextItem`（`content` / `role` / `source` / `kind`），**不是** `Message`：
  Message 的组装是 `ContextEngine` 的事；
- 拼装顺序（V2 冻结的 stable partition）：
  `ctx.system` → provider 产出的 system 项 → history → provider 产出的非 system 项，
  provider 之间按注册顺序；
- `provide()` 抛异常会让 `prepare` 失败，整个 Run 判 `ERROR`。
  容错（超时、降级为空列表）是实现者自己的责任；
- 一个对象可以同时是多个扩展点：`agentkit.skills.directory.DirectorySkills`
  既是 `ContextProvider`（把 skill instructions 注入上下文）又是 `SkillProvider`（查询）。

### 3.5 `Memory` —— 只有两个方法

```python
from agentkit.api import MemoryInput, MemoryItem


class RecentMemory:
    """最小 Memory：只记最近 5 条结果。"""

    def __init__(self) -> None:
        self.items: list[MemoryItem] = []

    async def recall(self, task: str) -> list[MemoryItem]:
        return self.items[-5:]

    async def remember(self, run: MemoryInput) -> None:
        if run.result:
            self.items.append(MemoryItem(run.result, kind="episodic"))
```

契约要点：

- **只有 `recall` / `remember`**。multi-store / 向量库 / 去重 / 摘要 / 路由
  都是 Harness 层的事，不进 Protocol；
- Memory **不产出 Message**：`MemoryItem → ContextItem` 的转换发生在
  Context 层（`MemoryContext` 这类 provider），所以 Memory 不需要知道 prompt 长什么样；
- `remember()` 收到的是**一次 Run**（`task` / `messages` / `result`），不是一堆消息；
- `recall(task)` 的参数是 task 文本，不是 `RunContext`——Memory 看不到 Run 状态。

### 3.6 `ToolExecutor` —— 执行策略

```python
from agentkit.api import RunContext, ToolCalls, ToolExecutor, ToolResult


class LoggingExecutor:
    """最小装饰器 Executor：记录被请求的 call，然后原样交给 inner。"""

    def __init__(self, inner: ToolExecutor) -> None:
        self.inner = inner
        self.seen: list[str] = []

    async def execute(self, ctx: RunContext, action: ToolCalls) -> list[ToolResult]:
        self.seen.extend(call.id for call in action.calls)
        return await self.inner.execute(ctx, action)

    async def close(self) -> None:
        await self.inner.close()
```

契约要点（V2.5 冻结，V3 逐字继承）：

- **`tool_call_id` ownership**：返回的每个 `ToolResult.tool_call_id` 必须等于输入
  `call.id`。inner 填错了，由 Executor 负责覆盖；
- **批内 stop**：`ctx.stop` 置位后不再开始新的 call —— 所以结果**可以短于**
  `action.calls`（`DefaultRuntime.observe` 用 `zip(..., strict=False)` 对齐）；
- **异常模型**：

  | 情况 | 行为 |
  |---|---|
  | `Tool.run()` 异常 | `ToolResult(error=True)`，不冒泡 |
  | `Toolbox.lookup()` 异常 | 冒泡（基础设施故障不伪装成 Observation） |
  | Executor 自身编程错误 | 冒泡 |
  | `asyncio.CancelledError` | **穿透**，不捕获、不转 ToolResult |
  | 单 call 超时 / 重试耗尽 | `ToolResult(error=True)` |

- **生命周期**：Executor **借用** Toolbox，绝不拥有它。
  `close()` 只关自己的资源，**不得**调用 `Toolbox.close()`；
- `execute()` 是唯一公共入口。内部是否按 call 分解（`dispatch` / `execute_one_of`
  这类 helper）是实现自由，**不属于 Protocol** —— 第三方不需要也不应该 import 它们。

### 3.7 `ContextTransform` —— 纯函数式的消息变换（V3 新增）

```python
from collections.abc import Sequence

from agentkit.api import Message


class TailTransform:
    """最小 ContextTransform：只保留最后 N 条消息。"""

    def __init__(self, keep: int) -> None:
        self.keep = keep

    async def apply(self, messages: Sequence[Message]) -> list[Message]:
        return list(messages)[-self.keep:]
```

契约要点：

- **签名里没有 `RunContext`**。策略是纯函数：给定消息序列，返回新的消息序列。
  读 Runtime 状态在这里**做不到**，这是刻意的（否则 transform 会退化成第二个 context 层）；
- **不访问外部世界**：不调 LLM、不读 memory、不发 event。`async` 只是留出未来空间，
  当前实现不该 await 任何外部资源；
- 输入 `Sequence`、输出 `list`，**不得就地修改输入**（用 `dataclasses.replace`
  造新 `Message`，或直接构造新的）；
- 允许：过滤、截断、排序、去重、替换 content。禁止：读 ctx / 调模型 / 写 memory；
- 位置：`ContextEngine.build()` 的**最后一步**，作用于 providers + history 拼接完成的
  完整 messages（所以它看到的是模型真正会收到的东西）；
- 内置实现（第一方，可参考）：`BudgetTransform(max_tokens, estimate=len)` /
  `SlidingWindowTransform(max_messages)` / `DedupeTransform()` / `SystemPriorityTransform()`。

### 3.8 `SkillProvider` —— 只有查询（V3 新增）

```python
from agentkit.api import Skill


class StaticSkills:
    """最小 SkillProvider：按名字子串匹配。"""

    def __init__(self, skills: list[Skill]) -> None:
        self._skills = list(skills)

    async def search(self, query: str, limit: int = 3) -> list[Skill]:
        needle = query.lower()
        hits = [s for s in self._skills if needle in s.name.lower()]
        return hits[:limit]
```

契约要点：

- **只有 `search()`**。没有 `refresh()`，没有 `close()` —— 同步 / 缓存 / 生命周期
  属于 Runtime startup hook 或 Harness，不属于 Provider Protocol（V3 明确删掉了 `refresh`）；
- `limit` 是上界，实现可以返回更少；返回顺序由实现决定（要稳定就自己保证）；
- `Skill` 从 `agentkit.api` 拿：它的 canonical 位置在 `agentkit/skills/skill.py`，
  但第三方不需要知道这个路径——`api` 已经把它作为公共数据契约重导出了；
- 有状态缓存是允许的（Provider 可以内部管理缓存），但**别把生命周期塞进 Protocol**。

### 3.9 `PermissionPolicy` —— 执行前的判定（V3 新增）

```python
from agentkit.api import RunContext, ToolCall


class NoWritePolicy:
    """最小 PermissionPolicy：允许读，拒绝写；Run 请求 stop 后一律拒绝。"""

    async def allow(self, call: ToolCall, ctx: RunContext) -> bool:
        if ctx.stop:
            return False
        return not call.name.startswith("write_")
```

契约要点：

- 只返回 `bool`。**Policy 不构造 `ToolResult`** —— 拒绝的返回形状由
  `PermissionExecutor` 统一决定：
  `content=f"[blocked by policy] {call.name}"` / `error=True` /
  `metadata={"blocked": True, "reason": "permission"}`；
- 语义分工：从**模型**视角这是一次失败的调用（`error=True`，模型应换路），
  从**人类**视角这是策略拦截（`metadata.blocked`）。两者都不能少；
- `ctx` 是**显式参数**：策略需要 Run 状态就从参数拿，不要读环境变量 / 单例 /
  Executor 的隐式状态。Policy 通过构造注入（`PermissionExecutor(inner, policy=...)`）；
- Policy 抛异常会**冒泡**（不会被当成「拒绝」）：策略自身故障不该静默变成一次
  工具调用失败；
- 组合顺序决定语义：`Timeout(Retry(Permission(Parallel(toolbox))))` 表示
  「重试会重新过权限门禁」；把 `Permission` 放在 `Retry` 外面则只判定一次。

### 3.10 `Runtime` —— 整个循环眼中的世界

```python
from agentkit.api import (
    Action, EventBus, Final, Message, PreparedInput, RunContext, ToolCalls, ToolResult,
)


class MyRuntime:
    """最小 Runtime：5 个动作 + 1 个事件总线属性。"""

    def __init__(self, model, toolbox, context, executor) -> None:
        self._model, self._toolbox = model, toolbox
        self._context, self._executor = context, executor
        self.events = EventBus()

    async def prepare(self, ctx: RunContext) -> PreparedInput:
        return PreparedInput(
            messages=await self._context.build(ctx),
            tools=await self._toolbox.specs(),
        )

    async def reason(self, ctx: RunContext, inp: PreparedInput) -> Action:
        return await self._model.generate(inp.messages, inp.tools)

    async def act(self, ctx: RunContext, action: ToolCalls) -> list[ToolResult]:
        return await self._executor.execute(ctx, action)

    async def observe(
        self, ctx: RunContext, action: Action, results: list[ToolResult] | None,
    ) -> None:
        calls = [] if isinstance(action, Final) else action.calls
        msg = Message("assistant", action.content, tool_calls=list(calls))
        ctx.messages.append(msg)
        ctx.last_assistant = msg
        # strict=False：批内 stop 会让 results 短于 calls
        for call, res in zip(calls, results or (), strict=False):
            text = f"[tool_error] {res.content}" if res.error else res.content
            ctx.messages.append(Message("tool", text, tool_call_id=call.id))

    async def finish(self, ctx: RunContext) -> None:
        return None   # Run 收尾（例如写 memory）；异常不会覆盖循环里的原始错误

    async def close(self) -> None:
        await self._executor.close()
        await self._toolbox.close()
```

契约要点：

- `Runtime` 是**适配器，不是容器**：它不对外暴露 model / toolbox / memory / context
  任何一个字段。Loop 只能看到这 5 个方法和 `events`；
- `observe()` 必须把这一轮的 action 和 results 写回 `ctx.messages`
  （`strict=False`：批内 stop 会让 results 短于 calls）；
- `finish()` 在 `finally` 里被调用，异常不会覆盖循环里的原始错误，但会被记到
  `ctx.error` 并触发 `agent.finish_error` 事件；
- 绝大多数情况你**不需要**实现 Runtime：`DefaultRuntime` 已经把
  model / toolbox / context / executor / memory 装配好了，换能力只需要换构造参数。

---

## 四、组装：怎么把扩展装进去

装配是**第一方代码**（下面的 import 来自 agentkit 内部模块，这没有问题——
边界规则约束的是第三节那些**实现**，不是 Harness）。

```python
from agentkit.context.engine import ContextEngine
from agentkit.context.providers import MemoryContext, SystemPrompt
from agentkit.executor.builtin import ParallelExecutor
from agentkit.executor.permission import PermissionExecutor
from agentkit.executor.retry import RetryExecutor
from agentkit.executor.timeout import TimeoutExecutor
from agentkit.harness.base import Harness
from agentkit.kernel.events import EventBus
from agentkit.runtime.default import DefaultRuntime
from agentkit.toolbox import Toolbox


class MyHarness(Harness):
    def __init__(self, model, tools, policy, transform, memory=None) -> None:
        self.events = EventBus()
        self.events.on("*", lambda name, **_: print(f"[trace] {name}"))

        self.toolbox = Toolbox(tools)                     # Tool / ToolProvider
        self.context = ContextEngine(                     # ContextProvider
            providers=[SystemPrompt("You are a careful agent."), MemoryContext(memory)],
            transform=transform,                          # ContextTransform
        )
        self.executor = TimeoutExecutor(                  # 策略：装饰器叠加
            RetryExecutor(
                PermissionExecutor(ParallelExecutor(self.toolbox), policy=policy),
                max_attempts=3,
            ),
            seconds=30,
        )
        self._model, self._memory = model, memory         # Model / Memory

    def build_runtime(self) -> DefaultRuntime:
        return DefaultRuntime(
            model=self._model, toolbox=self.toolbox, context=self.context,
            executor=self.executor, memory=self._memory, events=self.events,
        )

    async def close(self) -> None:
        await self._model.close()
```

用法：

```python
from agentkit.agent import Agent

agent = Agent(MyHarness(model, tools, NoWritePolicy(), TailTransform(keep=20)))
print(await agent.run("总结 README.md"))
await agent.close()
```

三条装配规则：

1. **能力只从构造参数进**（`DefaultRuntime(model=, toolbox=, context=, executor=, memory=)`），
   没有任何「注册中心」——没有 Manager / Registry / Factory 这一层；
2. **生命周期归创建者**：Runtime 拥有 Toolbox 与 Executor，Toolbox 拥有 ToolProvider，
   `Agent.close()` → `runtime.close()` → `executor.close()` → `toolbox.close()`；
   Harness 只关自己创建的东西（model / skills / MCP session 等）；
3. **策略用装饰器叠加**，顺序即语义：

   ```text
   Timeout(Retry(X))     单 call 的整个 retry 过程共享一个 timeout
   Retry(Timeout(X))     每次 retry 各自拥有独立 timeout
   Retry(Parallel(X))    batch 内并发，失败 call 独立重试
   ```

**观察**：所有能力都通过 `EventBus` 留下 trace（`agent.start` / `model.before` /
`model.after` / `iteration.done` / `agent.end` / `agent.error` / `executor.before` /
`executor.after` / `agent.finish_error`），不需要给 Kernel 加任何钩子字段。

---

## 五、验证你的扩展

```bash
# 1) 结构类型：不继承也能过
python -c "from agentkit.api import ToolExecutor; print(isinstance(MyExecutor(), ToolExecutor))"

# 2) 边界：只 import agentkit.api
python -m pytest tests/test_api_boundary.py -q

# 3) Kernel 没被你的扩展动摇
python -m pytest tests/test_abi_drift.py tests/test_architecture_firewall.py -q
```

参考实现：`tests/third_party/` 里的四个 fake（`FakeThirdPartyExecutor` /
`FakeThirdPartyTransform` / `FakeThirdPartySkillProvider` /
`FakeThirdPartyPermissionPolicy`）就是**只依赖 `agentkit.api`** 写出来的样子，
每个都能通过对应 Protocol 的 `isinstance` 检查，并且被组合测试实际使用。

---

## 六、红线：什么情况下 V3 的命题被证伪

> **如果你的新能力必须改 Kernel 才能装进去，那 V3 的命题就不成立。**

任何 Kernel 公共 ABI 变更（**包括「只是加一个可选参数」**）都要走
Kernel Change Review 三问，而 V3 期间这三问的答案只能是「不该进 Kernel」：

```text
1. 为什么现有 extension point 无法承载？
2. 为什么必须改变 Kernel Control Flow / Data Contract？
3. 如果改变 Kernel ABI，新增的能力是否具有长期稳定的语义？
```

三条可执行证据（任何一个变红，就说明扩展压力已经渗回 Kernel）：

| 测试 | 守的是什么 |
|---|---|
| `tests/test_abi_drift.py` | Kernel 公共 ABI = V2.5 freeze snapshot |
| `tests/test_architecture_firewall.py` | kernel/ 的依赖方向、类名、文件数、`agent_loop ≤ 55` 行 |
| `tests/test_api_boundary.py` | 第三方只 import `agentkit.api` |

**优先找扩展点，不要找 Kernel 的缝。** 需要「一个 Manager 来统一管理这些能力」
是把复杂度吸回 Kernel 的前兆——组合应该发生在 Harness 的构造参数里，
而不是 Kernel 的控制流里。
