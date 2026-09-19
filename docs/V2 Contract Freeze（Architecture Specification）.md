# V2 Contract Freeze（Architecture Specification）v2

> **V2 的第一目标不是加功能，而是证明"加入执行策略、上下文策略和流式能力后，Kernel 依然不会膨胀"。**
>
> 本文件是 V2 的编码前冻结。所有 P0 项在此文件中的定义即为最终定义，编码阶段不得偏离。

---

## 〇、V2 目标与非目标

### 目标
1. 修复 V1 的 4 个 P0 语义缺陷（A/B/C/D）
2. 引入 `ToolExecutor`，把"执行策略"从 Kernel 和 Toolbox 中解耦
3. 让 Streaming 在 Adapter 层可用，**但不进入 Kernel**
4. 证明 Kernel 可扩张且不膨胀

### 非目标（明确不做）
- Planner / RAG / Reflection / Multi-Agent
- `ContextEngine` 的 budget / compact 内置
- `ToolResult` 的多模态（`content` 仍是 `str`）
- `Event` 对象化（仍是 `str + kwargs`）
- `MemoryManager` / `SkillManager` / `PluginManager` / `ExecutorManager`
- Retry 的高级策略（jitter / exponential backoff / retry_on 谓词 / predicate）

---

## 一、Kernel 的变更清单

**V2 允许 Kernel 增加 Tool Execution Protocol 所必需的数据契约，但禁止新增 Kernel 模块、执行实现或能力逻辑。**

### 1.1 `ToolCalls.content`（P0-A 修复）

```python
# kernel/types.py
@dataclass(slots=True)
class ToolCalls:
    calls: list[ToolCall]
    content: str = ""
```

**理由**：OpenAI / Anthropic 的响应可以同时包含 `content` 和 `tool_calls`。V1 丢弃 `content` 是语义缺陷，每次都发生。

### 1.2 `ToolResult` 位于 Kernel Types

```python
# kernel/types.py
@dataclass(slots=True)
class ToolResult:
    tool_call_id: str = ""        # 新增：显式绑定
    content: str = ""
    error: bool = False
    metadata: dict[str, Any] = field(default_factory=dict)
```

**理由**：`ToolResult` 是 **Agent Tool Protocol 的数据契约**，不是 Executor 的实现细节。它必须与 `ToolCall` / `ToolCalls` 同层。否则 `ToolExecutor` Protocol 无法在 `kernel/protocols.py` 里被定义。

**契约**：`ToolExecutor.execute()` 返回的 `list[ToolResult]` 与输入的 `action.calls` **顺序一一对应**。`tool_call_id` 是冗余信息，供 debug / logging / 乱序重排使用。

### 1.3 `ToolExecutor` Protocol

```python
# kernel/protocols.py
@runtime_checkable
class ToolExecutor(Protocol):
    async def execute(
        self, ctx: RunContext, action: ToolCalls,
    ) -> list[ToolResult]: ...

    async def execute_one(
        self, ctx: RunContext, call: ToolCall,
    ) -> ToolResult: ...

    async def close(self) -> None: ...
```

**理由**：`execute_one` 是 Retry / Timeout / Permission / Sandbox 等装饰器的必要原语。**没有它，Retry 只能重新跑整个 batch，会重复执行已经成功的 Tool**——对 `write_file` / `send_email` 等副作用工具是灾难。

### 1.4 `RunContext.reason` + `TerminationReason`

```python
# kernel/state.py
class TerminationReason(str, Enum):
    FINAL = "final"
    MAX_ITERATIONS = "max_iterations"
    STOPPED = "stopped"
    ERROR = "error"


@dataclass
class RunContext:
    ...
    reason: TerminationReason | None = None
```

### 1.5 `max_iterations` 的冻结语义

> **`max_iterations` 表示最多允许完成多少轮 `model → (可选) tool execution`。**

- 每轮以 `model` 调用开始
- 如果 `model` 返回 `Final` → 该轮以 `FINAL` 结束，**不消耗下一轮的额度**
- 如果 `model` 返回 `ToolCalls` → 执行工具，`step += 1`
- 当 `step >= max_iterations` 且尚未 `Final` → `MAX_ITERATIONS`

**优先级**：最后一次 `model` 调用返回 `Final` 时，即使 `step` 已达上限，仍判定为 `FINAL`。

### 1.6 Loop 的 `finally` 异常保护（P0-C）

```python
finally:
    try:
        await runtime.finish(ctx)
    except Exception as e:
        if ctx.error is None:
            ctx.error = e
        await runtime.events.emit("agent.finish_error", ctx=ctx, error=e)
    finally:
        await runtime.events.emit("agent.end", ctx=ctx)
```

### 1.7 Loop 的 `reason` 赋值点

`agent_loop` 在 4 个退出点分别设置 `ctx.reason`：

```python
if isinstance(action, Final):
    ...
    ctx.reason = TerminationReason.FINAL
    break

if ctx.stop:              # 检查点 1/2/3 任一触发
    ctx.reason = TerminationReason.STOPPED
    break

# while 条件自然退出
ctx.reason = TerminationReason.MAX_ITERATIONS   # 循环结束后、进入 finally 前

# except 分支
ctx.reason = TerminationReason.ERROR
```

**Loop 控制流结构不变**。新增逻辑仅限于：终止原因记录（4 处赋值）+ `finish` 异常保护（3 行）。

---

## 二、Kernel 不变量（V2 冻结）

| 不变量 | V1 阈值 | V2 阈值 |
|---|---|---|
| `kernel/` 总行数 | ≤ 400 | **≤ 500** |
| `agent_loop` 代码行 | ≤ 45 | **≤ 55** |
| `loop.py` 能力词表 | 只允许 `model.before` / `model.after` | **不变** |
| `kernel/` 依赖方向 | 不 import `runtime/` / `contrib/` / `tools/` / `memory/` / `executor/` | **不变** |
| `Memory` 不 import `Message` | 强约束 | **不变** |
| `kernel/` 目录树 | 5 个文件 | **不变** |

**V2 完成后，`kernel/` 目录树必须仍是**：

```
kernel/
├── types.py
├── state.py
├── events.py
├── protocols.py
└── loop.py
```

**不允许出现** `kernel/executor.py` / `kernel/context_engine.py` / `kernel/stream.py` / `kernel/planner.py`。

---

## 三、执行层设计

### 3.1 职责边界（三条线，永不交叉）

```text
Toolbox      = "有哪些工具？"       （Discovery + Lookup）
ToolExecutor = "怎么执行工具？"     （Execution Policy）
Tool         = "工具具体做什么？"   （Capability）
```

### 3.2 `ToolExecutor` Protocol（已冻结于 §1.3）

**关键契约**：
- `execute(action)`：批量执行，返回与 `action.calls` **顺序一一对应**的 `list[ToolResult]`
- `execute_one(call)`：单工具执行原语
- `close()`：只关闭 Executor 自己拥有的资源，**不得关闭 Toolbox**

### 3.3 基础实现

```python
# executor/builtin.py

class SequentialExecutor:
    def __init__(self, toolbox):
        self.toolbox = toolbox          # borrow，不拥有

    async def execute(self, ctx, action):
        results = []
        for call in action.calls:
            if ctx.stop:
                break
            results.append(await self.execute_one(ctx, call))
        return results

    async def execute_one(self, ctx, call):
        return await self._dispatch(call)

    async def _dispatch(self, call):
        tool = await self.toolbox.lookup(call.name)
        if tool is None:
            return ToolResult(call.id, f"unknown tool: {call.name}", error=True)
        try:
            return await tool.run(call.arguments)
        except asyncio.CancelledError:
            raise                       # 取消穿透
        except Exception as e:
            return ToolResult(call.id, f"{type(e).__name__}: {e}", error=True)

    async def close(self):
        pass


class ParallelExecutor:
    def __init__(self, toolbox):
        self.toolbox = toolbox

    async def execute(self, ctx, action):
        if ctx.stop:
            return []
        return await asyncio.gather(
            *(self.execute_one(ctx, c) for c in action.calls)
        )

    async def execute_one(self, ctx, call):
        return await SequentialExecutor(self.toolbox)._dispatch(call)

    async def close(self):
        pass
```

### 3.4 装饰器实现

```python
class RetryExecutor:
    """per-call retry。max_attempts 表示总执行次数（含首次）。"""

    def __init__(self, inner, max_attempts=3, backoff=0.5):
        self.inner = inner
        self.max_attempts = max_attempts
        self.backoff = backoff

    async def execute(self, ctx, action):
        results = []
        for call in action.calls:
            if ctx.stop:
                break
            results.append(await self.execute_one(ctx, call))
        return results

    async def execute_one(self, ctx, call):
        last = None
        for attempt in range(1, self.max_attempts + 1):
            last = await self.inner.execute_one(ctx, call)
            if not last.error:
                return last
            if attempt < self.max_attempts:
                await asyncio.sleep(self.backoff * attempt)
        return last

    async def close(self):
        await self.inner.close()


class TimeoutExecutor:
    """per-call timeout（默认）。"""

    def __init__(self, inner, seconds=30):
        self.inner = inner
        self.seconds = seconds

    async def execute(self, ctx, action):
        results = []
        for call in action.calls:
            if ctx.stop:
                break
            results.append(await self.execute_one(ctx, call))
        return results

    async def execute_one(self, ctx, call):
        try:
            return await asyncio.wait_for(
                self.inner.execute_one(ctx, call),
                timeout=self.seconds,
            )
        except asyncio.TimeoutError:
            return ToolResult(call.id, f"timeout after {self.seconds}s", error=True)

    async def close(self):
        await self.inner.close()
```

**`max_attempts` 语义**：`max_attempts=3` = 首次 + 2 次 retry。

**`TimeoutExecutor` 默认 per-call**：整个 retry budget 的 timeout 由装饰器组合自然决定（见 §3.5）。

### 3.5 装饰器组合语义（冻结）

所有装饰器**重写 `execute_one`**，`execute` 默认由装饰器自身串行遍历。因此：

| 表达式 | 语义 |
|---|---|
| `Timeout(Retry(X))` | 单 call 的**整个 retry 过程**共享一个 timeout |
| `Retry(Timeout(X))` | 每次 retry **各自拥有独立** timeout |
| `Retry(Parallel(X))` | batch 内并发执行，失败 call 独立重试 |
| `Timeout(Parallel(X))` | batch 内并发执行，单 call timeout（默认） |

**这个语义由"装饰器重写 execute_one"这一条自然导出**，不需要额外机制。

### 3.6 异常模型（冻结）

| 来源 | 行为 |
|---|---|
| Tool 内部异常 | 被 Executor 捕获 → `ToolResult(error=True)` |
| Tool 内部 `asyncio.CancelledError` | **穿透，不捕获** |
| Executor 基础设施超时 | 转 `ToolResult(error=True, content="timeout ...")` |
| Executor 配置错误 / 编程错误 | **抛异常** |
| Executor 内部 `asyncio.CancelledError` | **穿透** |

**规则**：**工具执行失败 → `ToolResult(error=True)`；执行基础设施失败 → 异常。**

`RetryExecutor` 只对 `result.error is True` 重试，**不会**对 `CancelledError` / `TimeoutError`（异常形态）重试。

### 3.7 Executor 与 Toolbox 的生命周期所有权（冻结）

> **Toolbox 的生命周期归 Runtime；Executor 不拥有 Toolbox。**

```python
class DefaultRuntime:
    def __init__(self, model, toolbox, context,
                 executor=None, memory=None, events=None):
        self._toolbox = toolbox
        self._executor = executor or ParallelExecutor(toolbox)
        ...

    async def close(self):
        await self._executor.close()
        await self._toolbox.close()
```

- `Executor.close()` **只关闭 Executor 自己持有的资源**（HTTP client / sandbox / 子进程池）
- `Executor.close()` **不得关闭 Toolbox**
- `DefaultRuntime.close()` 独立关闭两者

**这是 Executor 作者的契约，写进 docstring。**

### 3.8 ParallelExecutor 的 `ctx.stop` 语义（冻结）

> `ctx.stop` 对 `ParallelExecutor` **只保证"尚未开始的 batch 不启动"**；不保证已经进入并发执行的 ToolCall 被取消。

- 批量前检查一次：`if ctx.stop: return []`
- 批内某个 call 触发 `ctx.stop` 不影响同批其他 call
- 需要在批内响应 `stop` 的场景，使用 `SequentialExecutor`

### 3.9 `DefaultRuntime.act` 的接线

```python
async def act(self, ctx, action: ToolCalls):
    if ctx.stop:
        return []
    await self.events.emit("executor.before", ctx=ctx, action=action)
    results = await self._executor.execute(ctx, action)
    await self.events.emit("executor.after", ctx=ctx, results=results)
    return results
```

### 3.10 `Toolbox` 的职责收缩（但不破坏 V1）

**新增**：
- `lookup(name) -> Tool | None` ← 供 executor 使用

**保留但标记 deprecated**：
- `execute(action)` —— 内部委托 `ParallelExecutor`。V1 用户零改动。

**契约**：`Toolbox.execute()` 将在 V3 或 V4 移除。

---

## 四、Streaming 分层设计（不进 Kernel）

### 4.1 Delta 类型（`models/base.py`，Kernel 外）

```python
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
```

**契约**：`Delta` 是 **Adapter Normalized Delta**。Provider SDK 的原始 delta 必须由 `Model.stream()` 归一化成这两种之一。**Harness 永远看不到 provider-specific delta。**

### 4.2 `StreamingModel` Protocol（`models/base.py`，Kernel 外）

```python
@runtime_checkable
class StreamingModel(Protocol):
    async def stream(
        self, messages: list[Message], tools: list[ToolSpec],
    ) -> AsyncIterator[Delta]: ...
```

**理由**：避免 `hasattr(model, "stream")` 形成隐式契约。Kernel 不知道 Streaming 存在，但 Streaming 能力本身拥有正式 Protocol。

### 4.3 `StreamingRuntime` 示范（`examples/`，非框架代码）

```python
class StreamingRuntime(DefaultRuntime):
    async def reason(self, ctx, inp):
        if not isinstance(self._model, StreamingModel):
            return await super().reason(ctx, inp)

        text_parts: list[str] = []
        tc_buf: dict[int, dict] = {}

        async for d in self._model.stream(inp.messages, inp.tools):
            if isinstance(d, TextDelta):
                await self.events.emit("model.delta", text=d.text)
                text_parts.append(d.text)
            elif isinstance(d, ToolCallDelta):
                buf = tc_buf.setdefault(d.index,
                                        {"id": "", "name": "", "args": ""})
                buf["id"] = d.id or buf["id"]
                buf["name"] = d.name or buf["name"]
                buf["args"] += d.args_delta or ""

        if tc_buf:
            return ToolCalls(
                calls=[
                    ToolCall(id=b["id"], name=b["name"],
                             arguments=json.loads(b["args"] or "{}"))
                    for b in tc_buf.values()
                ],
                content="".join(text_parts),
            )
        return Final("".join(text_parts))
```

**Kernel 永远不知道 Streaming 存在。**

---

## 五、ContextEngine 契约

### 5.1 Stable Partition（冻结）

`ContextEngine.build()` 的输出顺序：

```text
[ctx.system（调用级，可选）]
[provider 产出的 system 消息（按 providers 顺序）]
[history（ctx.messages 尾部 N 条）]
[provider 产出的非 system 消息（按 providers 顺序）]
```

**规则**：provider 输出会被 **stable partition**：
- `role == "system"` 的 items 归入 system 分区
- 其他 role 的 items 归入 non-system 分区
- 每个分区内部保持 provider 注册顺序

### 5.2 `system` 与 Harness `SystemPrompt` 的语义

**契约**（不依赖厂商行为）：

> `ContextEngine` **允许多个 system message**。Model Adapter 负责把它们转换成 provider-native format。

- 只配 Harness `SystemPrompt`：`Agent.run(system=...)` 不传
- 只配调用级：Harness 不配 `SystemPrompt`
- 都配：会叠加，顺序见 §5.1。**不推荐**，除非明确需要分层

**不写厂商特定行为到 Contract**（例如"OpenAI 会自动拼接"）。Adapter 自己保证符合各自 provider 契约。

---

## 六、Agent API 扩展

```python
class Agent:
    async def run(self, task: str, **kw) -> str:
        return (await self.run_ctx(task, **kw)).result

    async def run_ctx(self, task: str, **kw) -> RunContext:
        ctx = RunContext(task=task, messages=[Message("user", task)], **kw)
        await agent_loop(self.runtime, ctx)
        return ctx
```

### 终止状态表（冻结）

| `reason` | 触发点 | `done` | `error` | `result` |
|---|---|---|---|---|
| `FINAL` | 模型返回 `Final` | True | None | **`str`（允许空串）** |
| `MAX_ITERATIONS` | `step >= max_iterations` 且无 `Final` | False | None | `""` |
| `STOPPED` | Hook 置 `ctx.stop` | False | None | `""` |
| `ERROR` | 循环内异常 | True | 非 None | `""` |

**`FINAL` 不要求 `result` 非空**。`Final("")` 是合法终止。

---

## 七、事件清单（V2 冻结）

```text
agent.start          ctx
model.before         ctx, inp
model.after          ctx, action
executor.before      ctx, action           ← 新增
executor.after       ctx, results          ← 新增
model.delta          text                  ← 新增（Streaming 用，可选）
iteration.done       ctx, action, results
agent.finish_error   ctx, error            ← 新增
agent.error          ctx, error
agent.end            ctx
*                    任意
```

**事件名依然不允许出现在 `loop.py` 里**（除 `model.before` / `model.after`）。新事件由 `DefaultRuntime` / `ToolExecutor` / `StreamingRuntime` 发出，不属于 Kernel。

---

## 八、Kernel 之外的改动清单

| 文件 | 改动 |
|---|---|
| `models/base.py` | **新增** `TextDelta` / `ToolCallDelta` / `Delta` / `StreamingModel` |
| `models/openai.py` | `ToolCalls(..., content=...)`；新增 `stream()` |
| `models/anthropic.py` | 同上 |
| `models/ollama.py` | 同上 |
| `runtime/default.py` | 加 `executor=` 参数；`act` 委托 executor；`observe` 使用 `action.content` |
| `toolbox.py` | 加 `lookup()`；`execute()` 标 deprecated |
| `agent.py` | 加 `run_ctx()` |
| `executor/__init__.py` | **新增** 模块 |
| `executor/builtin.py` | **新增** 4 个实现 |
| `examples/streaming_harness.py` | **新增** Streaming 示范 |
| `README.md` | 加 "V2 变更" + "system 语义" + "Streaming 用法" + "Known Limitations 消除记录" |
| `CHANGELOG_v2.md` | **新增** 记录每个 P0 的修复方式 |

---

## 九、测试门禁

### 9.1 契约测试（`tests/test_contract_v2.py`）

- [ ] `kernel/` 总行数 ≤ 500
- [ ] `agent_loop` 代码行 ≤ 55
- [ ] `loop.py` 只含 `model.before` / `model.after` 两个事件名字符串
- [ ] `kernel/` 不含 `executor.py` / `context_engine.py` / `stream.py` / `planner.py`
- [ ] `kernel/` 不 import `runtime/` / `contrib/` / `tools/` / `memory/` / `executor/`
- [ ] `ToolCalls` 有 `content: str = ""` 字段
- [ ] `ToolResult` 有 `tool_call_id: str = ""` 字段
- [ ] `ToolExecutor` Protocol 存在且含 `execute` / `execute_one` / `close`
- [ ] `RunContext` 有 `reason: TerminationReason | None`
- [ ] `TerminationReason` 是 4 值枚举

### 9.2 语义测试（`tests/test_semantics_v2.py`）

- [ ] `ToolCalls.content` 在 OpenAI 适配器中被保留
- [ ] `DefaultRuntime.observe` 把 `action.content` 写入 assistant message
- [ ] **`RetryExecutor` 只对失败的 call 重试，不重跑成功的 call**（关键）
- [ ] **`TimeoutExecutor` 默认 per-call 语义**（关键）
- [ ] **`Timeout(Retry(X))` 与 `Retry(Timeout(X))` 语义不同**（关键）
- [ ] `SequentialExecutor` / `ParallelExecutor` 行为一致
- [ ] `executor.before` / `executor.after` 事件被触发
- [ ] `run_ctx()` 返回 `RunContext`
- [ ] 4 种终止原因都被正确设置
- [ ] `finish` 异常不覆盖原始 `ctx.error`
- [ ] `agent.finish_error` 事件被触发
- [ ] `ctx.system` 是首条 system 消息
- [ ] `max_iterations=3` 时恰好允许 3 轮 model → tool
- [ ] `Final("")` 时 `reason == FINAL`
- [ ] Streaming runtime 能把 `Delta` 组装成 `Action`

### 9.3 可替换性测试（`tests/test_replaceability_v2.py`）—— 关键

- [ ] **`DefaultRuntime` 接受任意 `ToolExecutor`**（FakeExecutor），运行成功
- [ ] Executor 替换为 `Sequential` / `Parallel` / `Retry(Parallel)` / `Timeout(Retry(Parallel))`，行为符合预期
- [ ] **`Executor.close()` 不触发 `Toolbox.close()`**
- [ ] **`Executor` 借用 Toolbox，不拥有生命周期**

### 9.4 兼容性测试

- [ ] **V1 全部 158 测试仍通过**
- [ ] V1 用户代码（无 `executor=` 参数）行为不变
- [ ] `Toolbox.execute()` 仍可用（deprecated）

---

## 十、实施路线图

### Phase V2-1：P0 语义修正（1~2 天）
- [ ] `ToolCalls.content`（含 `observe` 修复）
- [ ] `ToolResult.tool_call_id`
- [ ] `RunContext.reason` + `TerminationReason`
- [ ] `max_iterations` 边界语义
- [ ] `run_ctx()` + `run()` 重构
- [ ] `finish()` 异常处理
- [ ] README "system 语义" 段落
- [ ] 对应语义测试

### Phase V2-2：ToolExecutor（3~4 天）
- [ ] `kernel/protocols.py` 加 `ToolExecutor`（含 `execute_one`）
- [ ] `executor/builtin.py` 4 个实现（含装饰器组合）
- [ ] `DefaultRuntime` 加 `executor=` 参数
- [ ] `Toolbox.lookup()` + `execute()` deprecated
- [ ] `executor.before` / `executor.after` 事件
- [ ] 契约测试 + 可替换性测试 + 装饰器组合测试

### Phase V2-3：Streaming（2~3 天）
- [ ] `models/base.py` 加 `Delta` 类型 + `StreamingModel` Protocol
- [ ] `OpenAIModel.stream()`
- [ ] `AnthropicModel.stream()`
- [ ] `examples/streaming_harness.py`
- [ ] **Kernel 不动测试**（断言 `loop.py` 未变）

### Phase V2-4：验收（1 天）
- [ ] V1 全部测试通过
- [ ] V2 新增测试全绿
- [ ] ruff 全绿
- [ ] `kernel/` LOC 报告
- [ ] README + CHANGELOG 更新

---

## 十一、V2 完成定义（Definition of Done）

V2 通过的判据是下列每一条都是 yes：

- [ ] `kernel/` LOC ≤ 500（V1 是 322）
- [ ] `agent_loop` 代码行 ≤ 55（V1 是 38）
- [ ] `kernel/` 目录依然是 5 个文件
- [ ] `loop.py` 词表依然只含 `model.before` / `model.after`
- [ ] **`agent_loop` 的控制流结构不变**（新增逻辑仅限终止原因记录 + `finish` 异常保护）
- [ ] `ToolCalls.content` 存在且在真实调用中被保留
- [ ] `ToolResult` 位于 `kernel/types.py` 且含 `tool_call_id`
- [ ] `ToolExecutor` Protocol 含 `execute` / `execute_one` / `close`
- [ ] 4 个 executor 实现存在，装饰器组合语义已测试
- [ ] **`RetryExecutor` 不重复执行成功的 call**
- [ ] **`Executor.close()` 不关闭 Toolbox**
- [ ] **`DefaultRuntime` 可接受任意 `ToolExecutor`**（可替换性测试通过）
- [ ] `run_ctx()` 返回带 `reason` 的 `RunContext`
- [ ] `FINAL` 允许空 `result`
- [ ] `finish` 异常不覆盖原始错误
- [ ] Streaming 只出现在 `models/`，`kernel/` 里零新增
- [ ] `StreamingModel` Protocol 存在（非 `hasattr`）
- [ ] V1 全部 158 测试仍通过
- [ ] V2 新增测试全绿
- [ ] README 含 "V2 变更" + "system 语义" + "Streaming 用法"
- [ ] `CHANGELOG_v2.md` 记录每个 P0 的修复方式

**如果以上任何一条不满足，V2 不算完成。**

---

## 十二、V3 候选（不在 V2 讨论）

只有在 V2 用真实场景跑过一段时间后，才考虑以下条目：

- `Event` 对象化（`type` + `data`）
- `ToolResult` 多模态（`content: Any` + artifact 元数据）
- `ContextEngine` 的 `compact` / `budget` 一等支持
- Skill 动态工具注入
- Retry 高级策略（jitter / backoff / `retry_on` 谓词）
- `MemoryManager` / `PluginManager`（如确有需求）
- Executor 的沙箱化 / 权限系统
- `Toolbox.execute()` 移除

**每一项进入 V3 都必须先满足**：
1. V2 有真实使用案例证明其必要
2. Kernel 增量 ≤ 50 行
3. 不引入新的 manager 层

---

## 十三、V2 的立场（写进 README 与 CHANGELOG）

> **V2 不是"V1 + 一堆新功能"，而是"用最小代价消化 V1 的真实缺陷，同时证明 Kernel 能扩张而不膨胀"。**

V2 完成后应该能说：

- V1 承诺"换掉任意模块不用动 loop" → V2 依然守住
- V2 **新增了一整层执行策略（ToolExecutor）** 和 **一整个 Streaming 能力**，`agent_loop` **控制流结构不变**，仅多了终止原因记录和 `finish` 异常保护
- V2 **新增了 4 个 P0 语义修复**，`kernel/` 目录树和依赖方向不变

**这就是 V2 的价值。**