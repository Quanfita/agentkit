换掉任意模块，都不用动 agent_loop。

# Agent Microkernel V1

一个 **微内核** 形态的 Agent 框架：`agent_loop` 是唯一不可替换的控制流，
它只认识 `Runtime` 这一个协议。模型厂商、工具、记忆、技能、上下文策略、
观测与预算——全部是协议实现，挂在 Runtime 或 EventBus 上。

- Kernel（`agentkit/kernel/`）只放**稳定契约 + 单一控制流**，总量 < 400 行。
- 只有两个扩展点：**`Runtime`**（换控制流）与 **`EventBus`**（挂观测/控制）。
- MCP 不是一等公民（它是 `ToolProvider`）；Skill 不是一等公民（它是 `ContextProvider`）。
- 没有 `MemoryManager / SkillManager / PluginManager / Workflow / Graph / StateMachine`。
- 核心零第三方依赖（只用到标准库）。

## 安装

```bash
pip install -e .                 # 核心，零依赖
pip install -e ".[openai]"       # 需要 OpenAI 时
pip install -e ".[anthropic,mcp,ollama]"   # 按需
pip install -e ".[dev]"          # 开发：pytest + anyio + ruff
```

## 30 秒上手

```python
import asyncio
from agentkit.agent import Agent
from agentkit.harness.base import Harness
from agentkit.runtime.default import ContextEngine, DefaultRuntime
from agentkit.context.providers import MemoryContext, SystemPrompt
from agentkit.contrib.local_tools import make_local_tools
from agentkit.toolbox import Toolbox
from agentkit.memory.simple import InMemoryMemory
from agentkit.models.openai import OpenAIModel


class CodingHarness(Harness):
    def __init__(self):
        self.memory = InMemoryMemory()
        self.toolbox = Toolbox(make_local_tools(".", allow_shell=False))
        self.model = OpenAIModel("gpt-4o-mini")
        self.context = ContextEngine([
            SystemPrompt("You are a careful coding agent."),
            MemoryContext(self.memory),
        ])

    def build_runtime(self):
        return DefaultRuntime(
            model=self.model, toolbox=self.toolbox,
            context=self.context, memory=self.memory,
        )

    async def close(self):
        await self.toolbox.close()
        await self.model.close()


async def main():
    async with Agent(CodingHarness()) as agent:
        print(await agent.run("读一下 README.md 并总结"))
```

不想配 Key 也能跑：`python examples/offline_demo.py`（EchoModel/ScriptedModel + 全套 Hook），
`python -m agentkit --model echo -t "hi"`（CLI）。

## 目录结构

```
agentkit/
├── kernel/           # 冻结契约层（≤400 行，测试锁死）
│   ├── types.py      # Message / ToolCall / ToolSpec / ToolResult
│   │                 # MemoryItem / MemoryInput / ContextItem / Final / ToolCalls
│   ├── state.py      # RunContext（Loop State，不含任何能力引用）
│   ├── events.py     # EventBus
│   ├── protocols.py  # Model / Tool / ToolProvider / Memory / ContextProvider / Runtime
│   └── loop.py       # agent_loop —— 唯一不可替换
├── runtime/default.py  # DefaultRuntime + ContextEngine
├── toolbox.py          # Toolbox（Discovery + Lookup，asyncio.gather 并发）
├── agent.py            # Agent（构造函数只有 harness）
├── harness/base.py     # Harness 基类
├── observability.py    # EventBus 内置 Tracer / CostTracker / Logger
├── cli.py              # CLI Harness（REPL + 单次执行）
├── tools/              # function.py（@tool） / schema.py / mcp.py（MCPProvider）
├── context/providers.py# SystemPrompt / MemoryContext / CallableProvider
├── memory/simple.py    # NullMemory / InMemoryMemory
├── skills/             # Skill / DirectorySkills（SKILL.md）
├── models/             # echo.py / openai.py / anthropic.py / ollama.py
└── contrib/            # sqlite_memory.py / vector_memory.py / local_tools.py
```

## 架构

```text
                        User Harness
                             │ owns
                             ▼
┌──────────────────────────────────────────────────────┐
│                     Agent Loop                       │
│    RunContext  ◄── Loop State                        │
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
    Model        ContextEngine        Toolbox
                       │                 │
                       │        ┌────────┼────────┐
                       │        ▼        ▼        ▼
                       │      Local     MCP     Skill
                       ▼
              ContextProvider[]
                       │
                       ▼
                 Memory.recall ──► MemoryItem[] ──► ContextItem[]
```

## 七个扩展动作（都不碰 kernel）

| 想改什么 | 改哪里 |
|---|---|
| 换模型厂商 | `models/*.py` 实现 `Model` |
| 加本地工具 | `@tool` 或 `Toolbox.register(...)` |
| 加 MCP | `toolbox.add_provider(MCPProvider(session))`，或用 `stdio_provider(...)` 起子进程 |
| 加技能 | 丢一个 `skills/<name>/SKILL.md` |
| 换记忆 | 实现 `Memory.recall/remember`（`contrib/sqlite_memory.py`、`vector_memory.py` 是样板） |
| 改上下文策略 | 加一个 `ContextProvider` |
| 加日志/追踪/预算/压缩 | `events.on("*", Tracer())`、`events.on("model.before", CostTracker(budget=...))` |
| 换调度策略 | 自己实现 `Runtime`（见 `examples/offline_demo.py` 的 `NoModelRuntime`） |
| **改控制流本身** | **`kernel/loop.py` —— 唯一允许碰 Loop 的改动** |

事件清单（字符串事件，V1 不做 Event 对象）：

```text
agent.start       ctx
model.before      ctx, inp
model.after       ctx, action
iteration.done    ctx, action, results
agent.error       ctx, error
agent.end         ctx
*                 任意事件
```

两个容易踩的语义点：

- `model.before` 在 `prepare()` **之后**触发：改 `payload["inp"].messages` 影响**本次**模型调用，
  改 `payload["ctx"].messages` 影响**后续** iteration。`CostTracker` 正是靠后者置 `ctx.stop` 熔断。
- **stop invariant**：任何可产生副作用的动作之前都检查 `ctx.stop`（`reason` 前、`act` 前，
  以及进入新 iteration 前）。新增副作用动作时必须同步加检查点。

## 生命周期与错误语义

```text
Harness 创建 Model / Toolbox / Provider / Memory
   └── Harness.build_runtime() ──► Runtime
          └── Agent.run() × N
                 └── Agent.close() → runtime.close() → toolbox.close() → provider.close()
                                  → harness.close()
```

| 错误源 | 行为 |
|---|---|
| 工具执行异常（`Exception`） | 转 `ToolResult(error=True)`，模型能看到 `[tool_error] ...` |
| 工具执行取消（`asyncio.CancelledError`） | 穿透，不吞 |
| EventBus handler 异常 | 默认 raise；`EventBus(on_handler_error="ignore")` 可容忍 |
| Model 调用异常 | 冒泡 → `agent.error` 事件 → `ctx.error` 记录 → 重新抛出（`finish` 仍执行） |
| Memory.remember 异常 | 冒泡（要容错就在 Harness 里包） |

## CLI

```bash
python -m agentkit                                        # REPL（默认 echo 模型）
python -m agentkit --model echo -t "总结 README"          # 单次执行
python -m agentkit --model openai --model-name gpt-4o-mini -t "..." \
    --skills ./skills --allow-shell --budget 32000 --log-level INFO
```

REPL 内：`/help` `/tools` `/skills` `/system <text>` `/memory` `/reset` `/quit`。

## 实施状态（文档 §十二 路线图）

### Phase 1 — Kernel ✅

- [x] `kernel/types.py` / `state.py` / `events.py` / `protocols.py` / `loop.py`
- [x] `DefaultRuntime` + `ContextEngine`
- [x] `Harness` / `Agent`
- [x] `EchoModel`（返回 `Final`）
- [x] 单测：0 工具 / 1 工具 / 工具报错 / 达到 `max_iterations` / stop 检查点

### Phase 2 — 最小可用 ✅

- [x] `OpenAIModel`
- [x] `FunctionTool` + `@tool` + `schema_from_signature`
- [x] `Toolbox`（并发 + `ToolResult` 语义 + `refresh`）
- [x] `NullMemory` / `InMemoryMemory`
- [x] `SystemPrompt` / `MemoryContext`
- [x] `CodingHarness` demo（`examples/coding_harness.py`）

### Phase 3 — 生态 ✅

- [x] `MCPProvider`（+ `stdio_provider`，测试里跑真实 stdio MCP server 端到端）
- [x] `DirectorySkills`
- [x] `AnthropicModel` / `OllamaModel`
- [x] CLI harness（REPL）
- [x] `EventBus` 内置 tracer / cost / logger（`agentkit/observability.py`）

### Phase 4 — V2 ⏸ 未实施（按设计门禁）

文档 §十二 把 Phase 4 标为「V2（观察后决定）」，§十一 又明确把这几项写进
**「V1 明确不做」**，验收清单里也写着「不做 `ToolExecutor`」。因此 V1 收口于此：
`ToolExecutor` Protocol、`Event` 对象、`Model.stream()`、`ContextEngine` 的
`budget/compact`、Streaming 事件、Skill 动态工具注入 **全部留到 V2**，
届时新增它们不需要改 `agent_loop`（`ToolExecutor` 会作为 V2 唯一的新 Protocol）。

## 验收清单（文档 §十三）

| # | 条目 | 结果 |
|---|---|---|
| 1 | `kernel/` 总行数 ≤ 400 | ✅ 322 行 |
| 2 | `agent_loop` 函数体 ≤ 45 行 | ✅ 语句 31 / 代码行 38（物理行 53 含空行与注释） |
| 3 | `loop.py` 里没有 `model/memory/tool/mcp/skill/context` 任何一词 | ✅ 词表（标识符/导入）为零；仅事件名字符串 `model.before`/`model.after` 保留，见下注 |
| 4 | `Agent.__init__` 只有 `harness` 一个参数 | ✅ 测试断言签名 |
| 5 | `Memory` 里没有 `Message` 类型出现 | ✅ 测试扫描 `agentkit/memory/*.py` |
| 6 | `Tool.run` 返回 `ToolResult`，工具错误不炸循环 | ✅ 异常 → `error=True`，模型看到 `[tool_error]` |
| 7 | `asyncio.CancelledError` 不被吞 | ✅ Toolbox / EventBus / Loop 三层都穿透 |
| 8 | 每个副作用动作前都有 `if ctx.stop: break` | ✅ 3 个检查点，各有行为测试 |
| 9 | `ctx.result` 只在 `Final` 时被设置 | ✅ `max_iterations` 耗尽后 `result == ""` |
| 10 | `Agent` 支持 `async with` | ✅ |
| 11 | 换 OpenAI → Anthropic 只改 `models/` 下一个文件 | ✅ 测试锁死厂商 SDK 只在对应适配器里出现 |
| 12 | 挂 MCP 只加一行 `toolbox.add_provider(...)` | ✅ |
| 13 | 加日志只加一行 `events.on("*", fn)` | ✅ `Tracer` / `Logger` / `CostTracker` |
| 14 | 加技能只丢一个 `SKILL.md` | ✅ `skills/code-review/SKILL.md` |
| 15 | 用户能在 `build_runtime()` 里组装任何形态 Agent，不需要读 `loop.py` | ✅ `examples/offline_demo.py` 的 `NoModelRuntime` |
| 16 | README 第一行：**"换掉任意模块，都不用动 agent_loop。"** | ✅ 本文件第一行（由 `tests/test_contract.py` 断言） |

> **注（条目 3）**：文档 §十三 的措辞是「`loop.py` 里没有 model/memory/tool/mcp/skill/context
> 任何一个词」，但 §十 冻结的事件清单里就有 `model.before` / `model.after` 两个事件名，
> 它们是 EventBus 的公开契约（文档 §8.2 的示例 hook 也这么挂）。
> 这里按「词表 = 标识符与导入」执行：`loop.py` 不导入、不命名任何能力，
> 只认识 `Runtime` / `RunContext` / `Action`；事件名字符串保留，并由测试
> `test_loop_only_mentions_capability_words_in_frozen_event_names` 钉死只有这两个。
>
> **注（`ctx.system`）**：`Agent.run(system=...)` 写入 `RunContext.system`，
> `ContextEngine.build()` 会把它作为第一条 system 消息插到 provider 产出的 system 之前
> （文档未明写这条路径；不写就等于该参数是死的）。`ContextEngine` 属 §十一 的
> Internal/可变 范围，V2 替换实现时请保持这一语义或显式移除该参数。

## 取舍（文档 §十四）

**会做的：** 微内核；Context 一等公民但 V1 保持极简；一切用 `Protocol` 表达而非继承树；
`EventBus` 作为唯一通用扩展点；Kernel 只包含稳定契约 + 单一控制流。

**不会做的（V1）：** 不做 YAML 配置；不做万能 `Agent(...)` 构造函数（只有 `Agent(harness)`）；
不做 `ContextEngine` 的 budget/compact（`events.on("model.before", fn)` 就够）；
不做 `ToolExecutor`；不做 Planner / RAG / Reflection / Multi-Agent；
不做 `MemoryManager / SkillManager / PluginManager`。

核心竞争力不是功能多，而是：

```python
async with Agent(MyHarness()) as agent:
    result = await agent.run(task)
```

## 开发

```bash
python -m pytest          # 全量测试（含真实 stdio MCP server 端到端）
ruff check .
python -m agentkit --model echo -t "hi"
python examples/offline_demo.py
```

`tests/test_contract.py` 是验收清单的可执行版本：kernel 行数预算、loop 词表与尺寸、
`Agent.__init__` 签名、Memory 不碰 Message、厂商 SDK 隔离、README 契约——
越权改动会在这里失败。
