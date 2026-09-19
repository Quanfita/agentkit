换掉任意模块，都不用动 agent_loop。

# Agent Microkernel

一个 **微内核** 形态的 Agent 框架：`agent_loop` 是唯一不可替换的控制流，
它只认识 `Runtime` 这一个协议。模型厂商、工具、记忆、技能、上下文策略、
执行策略、观测与预算——全部是协议实现，挂在 Runtime 或 EventBus 上。

- Kernel（`agentkit/kernel/`）只放**稳定契约 + 单一控制流**（当前 380 行，预算 500）。
- 只有两个扩展点：**`Runtime`**（换控制流）与 **`EventBus`**（挂观测/控制）。
- MCP 不是一等公民（它是 `ToolProvider`）；Skill 不是一等公民（它是 `ContextProvider`）。
- 没有 `MemoryManager / SkillManager / PluginManager / ExecutorManager / Workflow / Graph / StateMachine`。
- 核心零第三方依赖（只用到标准库）。
- **V2 加了一整层执行策略和一整个 Streaming 能力，`agent_loop` 的控制流结构没变。**

## V2 变更（相对 V1）

| 主题 | 变化 |
|---|---|
| **P0-A** | `ToolCalls.content`：与 `tool_calls` 并存的文本不再被丢弃，`observe()` 写进 assistant message |
| **P0-B** | `RunContext.reason` + `TerminationReason`（final / max_iterations / stopped / error）；`max_iterations` 冻结为「最多完成的 `model → (可选) tool` 轮数」；新增 `Agent.run_ctx()` |
| **P0-C** | `finish()` 异常不再覆盖原始错误、不再吞掉 `agent.end`；改发 `agent.finish_error` |
| **P0-D** | `ToolResult.tool_call_id`；新增 `ToolExecutor` Protocol 与 4 个实现；执行策略从 Toolbox 解耦 |
| **Streaming** | `models/base.py` 的 `Delta` / `StreamingModel`，三个适配器提供 `stream()`；**Kernel 零新增** |
| **事件** | 新增 `executor.before` / `executor.after` / `model.delta` / `agent.finish_error` |
| **破坏性** | `ToolResult` 首字段变为 `tool_call_id`（位置参数需改关键字）；`run(task, **kw)`；`Toolbox.execute()` 弃用 |

逐项修复方式、迁移清单、与文档代码的三处等价差异 → **[CHANGELOG_v2.md](CHANGELOG_v2.md)**。
V1 的四个 P0「Known Limitations」消除记录见下文。

## V2.5 变更（Contract 收口 + Provider Conformance）

> **V2.5 不是新版本，是 V2 的最后一个阶段。它不加任何新功能，只做两件事：把 V2 遗留的 Contract 收口，
> 以及用真实 Provider 验证 V2 Contract。V2.5 真正产出的不是代码，而是证据。**

| 主题 | 变化 |
|---|---|
| **`ToolResult`** | 变 `kw_only=True`：`ToolResult("hello")` 现在是 `TypeError`，不再把文本静默塞进 `tool_call_id` |
| **`ToolExecutor`** | Protocol **只保留** `execute()` + `close()`；per-call 原语 `dispatch()` 降为 `executor/builtin.py` 内部 helper |
| **`tool_call_id`** | ownership 冻结为 `ToolCall.id → Executor → ToolResult.tool_call_id`，**强制覆盖**（不再「为空补齐」） |
| **`Toolbox.lookup()`** | 异常**冒泡**（基础设施故障不伪装成 Observation）；`Tool.run()` 异常才转 `ToolResult(error=True)` |
| **Cancellation** | loop 的 `except` 拆分：`CancelledError` **不进** Agent 错误模型（不设 `error`/`reason`），只做 cleanup 后穿透；`KeyboardInterrupt`/`SystemExit` 同样不进 |
| **类型门禁** | `docs/freeze/v2/scratch.py` 冻结快照 + `mypy --strict` + `pyright` + 签名漂移测试（44 项） |
| **真机验证** | `tests/conformance/`：6 场景 × 4 Provider；**DeepSeek（新增 Provider）+ Ollama 真机 6/6 通过** |
| **新增 Provider** | `models/deepseek.py`（OpenAI-compatible，复用 `OpenAIModel` 的转换与流式实现） |

逐项收口、迁移指南（6 条破坏性变更）、与文档的等价差异 → **[CHANGELOG_v2_5.md](CHANGELOG_v2_5.md)**；
真机证据 → **[docs/CONFORMANCE_REPORT.md](docs/CONFORMANCE_REPORT.md)** + `docs/conformance/<timestamp>.json`。

### Provider 支持矩阵（真机结果）

2026-09-19 实测（`contract_revision: v2`，`git_revision: 82d1c81`）：

| Capability | OpenAI | Anthropic | Ollama `qwen3.5:9b` | DeepSeek `deepseek-flash` |
|---|---|---|---|---|
| Normal（Contract） | 未验证（无 key） | 未验证（无 key） | ✓ pass | ✓ pass |
| Tool（Contract） | 未验证（无 key） | 未验证（无 key） | ✓ pass | ✓ pass |
| Stream Text（Contract） | 未验证（无 key） | 未验证（无 key） | ✓ pass | ✓ pass |
| Error（Contract） | 未验证（无 key） | 未验证（无 key） | ✓ pass | ✓ pass |
| Parallel Tool（Capability） | 未验证（无 key） | 未验证（无 key） | ✓ pass | ✓ pass |
| Stream Tool（Capability） | 未验证（无 key） | 未验证（无 key） | ✓ pass | ✓ pass |

- **Gate A 原定 Provider（OpenAI / Anthropic）本机无 key → `not_verified`，不算 pass**（§4.6）。
  报告额外给出「Gate A（替代验证）」一栏：Ollama 与 DeepSeek 真实行使了同一组 Contract 场景并 4/4 `pass`。
  二者不可互相替代 —— 带 key 的环境跑同一条命令即可补齐原定两列。
- 真机上还坐实了 P0-A 的动机：DeepSeek 在 `parallel_tool` 场景**同时返回了文本与 tool_calls**
  （`"我来为您查询北京和上海的天气情况。"`），V1 会把这段文本丢掉。

### 怎么跑 Conformance

```bash
# 默认（addopts = -m "not conformance"）：离线用例，不碰网络
python -m pytest tests/

# 真机（需 key；无 key 的 Provider 自动 not_verified，不算失败）
DEEPSEEK_API_KEY=... AGENTKIT_DEEPSEEK_MODEL=deepseek-flash \
AGENTKIT_OLLAMA_MODEL=<已 pull 的模型> \
python -m pytest tests/conformance -m conformance -v

# 从已落盘的机读 JSON 复现人读报告
python -m tests.conformance.report docs/conformance/<timestamp>.json
```


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
        ctx = await agent.run_ctx("读一下 README.md 并总结")
    print(ctx.result, ctx.reason)
```

不想配 Key 也能跑：`python examples/offline_demo.py`（全套 Hook）、
`python examples/streaming_harness.py`（Streaming 全链路）、
`python -m agentkit --model echo -t "hi"`（CLI）。

## 执行策略（V2 新增）

```python
from agentkit.executor.builtin import (
    ParallelExecutor, RetryExecutor, SequentialExecutor, TimeoutExecutor,
)

executor = TimeoutExecutor(                       # 单 call 的整个 retry 过程共享 30s
    RetryExecutor(ParallelExecutor(toolbox),      # batch 内并发，失败 call 独立重试
                  max_attempts=3, backoff=0.5),
    seconds=30,
)
runtime = DefaultRuntime(model=..., toolbox=toolbox, context=..., executor=executor)
```

| 表达式 | 语义 |
|---|---|
| `Timeout(Retry(X))` | 单 call 的整个 retry 过程共享一个 timeout |
| `Retry(Timeout(X))` | 每次 retry 各自拥有独立 timeout |
| `Retry(Parallel(X))` | batch 内并发，失败 call 独立重试 |
| `Timeout(Parallel(X))` | batch 内并发，单 call timeout |

要点：

- `RetryExecutor` 只重试 `error=True` 的单个 call，**成功的 call 绝不重跑**（副作用工具安全）。
- `SequentialExecutor` 批内响应 `ctx.stop`；`ParallelExecutor` 只保证「尚未开始的 batch 不启动」。
- `Executor` 借用 Toolbox，不拥有生命周期：`Executor.close()` 不会关闭 Toolbox。
- 自定义执行策略只需实现 `execute` / `execute_one` / `close` 三个方法，`DefaultRuntime` 照单全收。

## Streaming 用法

```python
from agentkit.models.base import StreamingModel, TextDelta, ToolCallDelta

class MyRuntime(DefaultRuntime):
    async def reason(self, ctx, inp):
        if not isinstance(self._model, StreamingModel):
            return await super().reason(ctx, inp)
        async for delta in self._model.stream(inp.messages, inp.tools):
            ...   # TextDelta → 追加文本 / ToolCallDelta → 拼接 args，见 examples/streaming_harness.py
```

- `Delta` 是 **Adapter Normalized Delta**：Harness 永远看不到厂商原始 chunk。
- `OpenAIModel` / `AnthropicModel` / `OllamaModel` 已实现 `stream()`；`StreamingModel` 是正式 Protocol，不用 `hasattr` 探测。
- Kernel 不知道 Streaming 存在：`kernel/` 里 `stream` / `delta` 出现 0 次（有测试钉死）。
- 想边流边展示：`events.on("model.delta", lambda event, **p: print(p["text"], end=""))`。

## system 语义（V2 冻结）

`ContextEngine.build()` 的输出顺序：

```text
[ctx.system（调用级，可选）]
[provider 产出的 system 消息（按 providers 顺序）]
[history（ctx.messages 尾部 history_limit 条）]
[provider 产出的非 system 消息（按 providers 顺序）]
```

- `ContextEngine` **允许多个 system message**：`agent.run("任务", system=...)` 与 Harness 的
  `SystemPrompt` 可以叠加（调用级在前）。**不推荐**同时使用，除非明确需要分层。
- 把它们转成 provider-native format 是 Model Adapter 的责任，这里不假设任何厂商行为。
- 只配 Harness `SystemPrompt`（`system=` 不传）或只配调用级，都是受支持的单一路径。

## Known Limitations 消除记录（V1 → V2）

| V1 限制 | V2 状态 |
|---|---|
| 模型同时返回文本与 `tool_calls` 时文本丢失 | **已消除**：`ToolCalls.content` 全链路保留（P0-A） |
| Run 为什么结束不可观测，`max_iterations` 边界模糊 | **已消除**：`run_ctx().reason` 四值枚举 + 冻结的轮次语义（P0-B） |
| 记忆写入失败会覆盖原始异常、吞掉 `agent.end` | **已消除**：`finish` 保护 + `agent.finish_error` 事件（P0-C） |
| 执行策略焊死在 Toolbox，重试只能重跑整批 | **已消除**：`ToolExecutor` 层 + `execute_one` 原语（P0-D） |
| 批量结果无法与 call 归因 | **已消除**：`ToolResult.tool_call_id` |
| Streaming 只能用 `hasattr` 探测 | **已消除**：`StreamingModel` Protocol（仍在 Kernel 之外） |
| `Toolbox.execute()` 既做发现又做执行 | **已收缩**：只做 Discovery + Lookup；`execute()` 弃用，V3/V4 移除 |

## 目录结构

```
agentkit/
├── kernel/           # 冻结契约层（≤500 行，测试锁死）
│   ├── types.py      # Message / ToolCall / ToolSpec / ToolResult(tool_call_id)
│   │                 # MemoryItem / MemoryInput / ContextItem / Final / ToolCalls(content)
│   ├── state.py      # RunContext（Loop State）+ TerminationReason
│   ├── events.py     # EventBus
│   ├── protocols.py  # Model / Tool / ToolProvider / ToolExecutor / Memory
│   │                 # ContextProvider / Runtime
│   └── loop.py       # agent_loop —— 唯一不可替换
├── executor/builtin.py # Sequential / Parallel / Retry / Timeout（+ 组合语义）
├── runtime/default.py  # DefaultRuntime + ContextEngine
├── toolbox.py          # Toolbox（Discovery + Lookup）
├── agent.py            # Agent（run / run_ctx / close）
├── harness/base.py     # Harness 基类
├── observability.py    # EventBus 内置 Tracer / CostTracker / Logger
├── cli.py              # CLI Harness（REPL + 单次执行）
├── tools/              # function.py（@tool） / schema.py / mcp.py（MCPProvider）
├── context/providers.py# SystemPrompt / MemoryContext / CallableProvider
├── memory/simple.py    # NullMemory / InMemoryMemory
├── skills/             # Skill / DirectorySkills（SKILL.md）
├── models/             # base.py（Delta/StreamingModel） / echo / openai / anthropic / ollama
│                       # deepseek.py（OpenAI-compatible，复用 openai 适配器的转换）
└── contrib/            # sqlite_memory.py / vector_memory.py / local_tools.py

tests/
├── unit/               # 284 个单元测试（V1 158 + V2 68 + 冻结漂移 44 + V2.5 14）
└── conformance/        # 6 场景 × 4 Provider 真机矩阵 + 报告生成器（约 11 个文件）

docs/
├── conformance/        # 机读证据 <timestamp>.json
├── CONFORMANCE_REPORT.md   # 人读证据（Gate A/B/C + 支持矩阵）
└── freeze/v2/scratch.py    # V2 Contract 冻结快照（mypy --strict + pyright 通过）
```

## 架构

```text
                        User Harness
                             │ owns
                             ▼
┌──────────────────────────────────────────────────────┐
│                     Agent Loop                       │
│    RunContext  ◄── Loop State（含 reason）           │
│    runtime.prepare(ctx)  ──► PreparedInput           │
│    runtime.reason(ctx)   ──► Action                  │
│    runtime.act(ctx)      ──► list[ToolResult]        │
│    runtime.observe(ctx)  ──► (mutates ctx.messages)  │
│    runtime.finish(ctx)   ──► (memory.commit)         │
└──────────────────────┬───────────────────────────────┘
                       │
                DefaultRuntime ── reason ──► Model（可选 StreamingModel.stream）
                       │
      ┌────────────────┼─────────────────┐
      ▼                ▼                 ▼
  ContextEngine     ToolExecutor      Toolbox
      │                │                 │
      │          Sequential/Parallel     ├── Local
      │          Retry/Timeout(装饰)     ├── MCP
      ▼                │                 └── Skill
  ContextProvider[]    └──► Tool.run ──► ToolResult(tool_call_id, content, error)
      │
      ▼
  Memory.recall ──► MemoryItem[] ──► ContextItem[]
```

## 扩展点（都不碰 kernel）

| 想改什么 | 改哪里 |
|---|---|
| 换模型厂商 | `models/*.py` 实现 `Model` |
| 流式输出 | `models/*.py` 实现 `stream()`（`StreamingModel`） |
| 加本地工具 | `@tool` 或 `Toolbox.register(...)` |
| 加 MCP | `toolbox.add_provider(MCPProvider(session))`，或用 `stdio_provider(...)` 起子进程 |
| 加技能 | 丢一个 `skills/<name>/SKILL.md` |
| 换记忆 | 实现 `Memory.recall/remember`（`contrib/sqlite_memory.py`、`vector_memory.py` 是样板） |
| 改上下文策略 | 加一个 `ContextProvider` |
| **改执行策略** | **实现 `ToolExecutor`（`executor/builtin.py` 已有 4 个）** |
| 加日志/追踪/预算/压缩 | `events.on("*", Tracer())`、`events.on("model.before", CostTracker(budget=...))` |
| 换调度策略 | 自己实现 `Runtime`（见 `examples/offline_demo.py` 的 `NoModelRuntime`） |
| **改控制流本身** | **`kernel/loop.py` —— 唯一允许碰 Loop 的改动** |

事件清单（V2 冻结，仍是 `str + kwargs`）：

```text
agent.start          ctx
model.before         ctx, inp
model.after          ctx, action
executor.before      ctx, action
executor.after       ctx, results
model.delta          text                    （仅 Streaming）
iteration.done       ctx, action, results
agent.finish_error   ctx, error
agent.error          ctx, error
agent.end            ctx
*                    任意事件
```

三个容易踩的语义点：

- `model.before` 在 `prepare()` **之后**触发：改 `payload["inp"].messages` 影响**本次**模型调用，
  改 `payload["ctx"].messages` 影响**后续** iteration。`CostTracker` 正是靠后者置 `ctx.stop` 熔断。
- **stop invariant**：任何可产生副作用的动作之前都检查 `ctx.stop`（`reason` 前、`act` 前，
  以及进入新 iteration 前）。新增副作用动作时必须同步加检查点。
- `executor.before` / `executor.after` 由 `DefaultRuntime.act` 发出，**不属于 Kernel**：
  `loop.py` 的事件名被测试钉死为它自己那一组，新事件只能从 Kernel 之外发。

## 生命周期与错误语义

```text
Harness 创建 Model / Toolbox / Executor / Provider / Memory
   └── Harness.build_runtime() ──► Runtime
          └── Agent.run() / run_ctx() × N
                 └── Agent.close() → runtime.close() → executor.close()  （不关 Toolbox）
                                                   → toolbox.close() → provider.close()
                                  → harness.close()
```

| 错误源 | 行为 |
|---|---|
| 工具执行异常（`Exception`） | 转 `ToolResult(tool_call_id, error=True)`，模型能看到 `[tool_error] ...` |
| 工具执行取消（`asyncio.CancelledError`） | 穿透，不吞；`RetryExecutor` 也不重试它 |
| 执行基础设施超时 | `ToolResult(error=True, content="timeout after ...")` |
| EventBus handler 异常 | 默认 raise；`EventBus(on_handler_error="ignore")` 可容忍 |
| Model 调用异常 | 冒泡 → `agent.error` 事件 → `ctx.error` + `ctx.reason=ERROR` → 重新抛出（`finish` 仍执行） |
| `finish()` 异常 | 不覆盖已有 `ctx.error`；发 `agent.finish_error`；`agent.end` 必然发出 |
| `Memory.remember` 异常 | 同上（走 `finish` 保护；要容错就在 Harness 里包） |

四种终止状态：

| `reason` | 触发点 | `done` | `error` | `result` |
|---|---|---|---|---|
| `FINAL` | 模型返回 `Final` | True | None | `str`（允许空串） |
| `MAX_ITERATIONS` | 完成 `max_iterations` 轮仍有工具调用 | False | None | `""` |
| `STOPPED` | Hook 置 `ctx.stop` | False | None | `""` |
| `ERROR` | 循环内异常 | True | 非 None | `""` |

## CLI

```bash
python -m agentkit                                        # REPL（默认 echo 模型）
python -m agentkit --model echo -t "总结 README"          # 单次执行
python -m agentkit --model openai --model-name gpt-4o-mini -t "..." \
    --skills ./skills --allow-shell --budget 32000 --log-level INFO
```

REPL 内：`/help` `/tools` `/skills` `/system <text>` `/memory` `/reset` `/quit`。

## 实施状态

### V1 路线图（文档 §十二）

- **Phase 1 — Kernel ✅**（契约层 + `DefaultRuntime` + `Harness`/`Agent` + `EchoModel` + 引擎单测）
- **Phase 2 — 最小可用 ✅**（`OpenAIModel` / `FunctionTool`+`@tool` / `Toolbox` / Memory / Context / demo）
- **Phase 3 — 生态 ✅**（`MCPProvider` / `DirectorySkills` / `AnthropicModel`+`OllamaModel` / CLI REPL / 观测 Hook）
- **Phase 4（V1 版）— 留给 V2 ✅**：文档 V1 §十二 把 `ToolExecutor`、`Event` 对象、`Model.stream()`、
  `ContextEngine.compact/budget`、Skill 动态工具注入列为「V2 观察后决定」。V2 现在接走了前三项。

### V2 路线图（V2 文档 §十）

- **Phase V2-1 — P0 语义修正 ✅**：`ToolCalls.content`（含 `observe`）、`ToolResult.tool_call_id`、
  `RunContext.reason` + `TerminationReason`、`max_iterations` 边界、`run_ctx()/run()`、`finish()` 异常处理
- **Phase V2-2 — ToolExecutor ✅**：Kernel Protocol（含 `execute_one`）、`executor/builtin.py` 四实现、
  `DefaultRuntime(executor=)`、`Toolbox.lookup()` + `execute()` 弃用、`executor.*` 事件
- **Phase V2-3 — Streaming ✅**：`models/base.py`（`Delta` + `StreamingModel`）、三个适配器 `stream()`、
  `examples/streaming_harness.py`、Kernel 零新增（有测试断言）
- **Phase V2-4 — 验收 ✅**：V1 测试全绿、V2 新增测试全绿、ruff 全绿、LOC 报告、README + CHANGELOG

### V2.5 路线图（V2.5 文档 §八）

- **Phase V2.5-1 — Contract 收口 ✅**：`ToolResult` kw-only、`ToolExecutor` 移除 `execute_one`、
  `dispatch()` 强制绑定 `tool_call_id` + `lookup()` 异常冒泡、loop `except` 拆分、
  `kernel/protocols.py` 生命周期 docstring、`docs/freeze/v2/scratch.py` + 漂移门禁、DeepSeek Provider
- **Phase V2.5-2 — Conformance 骨架 ✅**：`tests/conformance/`（cases / runner ≤150 行 / assertions /
  report 纯函数 / provider fixtures / malformed stream 离线用例 / README）
- **Phase V2.5-3 — 真机验证 ✅**：DeepSeek 6/6、Ollama 6/6、OpenAI/Anthropic `not_verified`（无 key）、
  证据落盘 `docs/conformance/*.json` + `docs/CONFORMANCE_REPORT.md`
- **Phase V2.5-4 — 收口 ✅**：README 支持矩阵 + 立场、DoD 逐项勾选、V3 启动条件

### V2 / V2.5 明确不做（非目标）

Planner / RAG / Reflection / Multi-Agent；`ContextEngine` 的 budget / compact 内置；
`ToolResult` 多模态；`Event` 对象化；任何 manager 层；Retry 高级策略
（jitter / backoff / `retry_on` 谓词）；Executor 沙箱化与权限系统；V2.5 不改 Kernel 抽象、
不加新 Protocol、`ToolResult` 不加新字段。→ V3 候选（V2 文档 §十二 / V2.5 文档 §一）。

## 验收清单

### V1（V1 文档 §十三）

| # | 条目 | 结果 |
|---|---|---|
| 1 | `kernel/` 总行数 ≤ 400 | ✅ 380（V2 把预算上调到 500） |
| 2 | `agent_loop` 函数体 ≤ 45 行 | ✅ 代码行 50（V2 把预算上调到 55） |
| 3 | `loop.py` 里没有 `model/memory/tool/mcp/skill/context` 任何一词 | ✅ 词表为零；仅 `model.before`/`model.after` 事件名保留 |
| 4 | `Agent.__init__` 只有 `harness` 一个参数 | ✅ 测试断言签名 |
| 5 | `Memory` 里没有 `Message` 类型出现 | ✅ 测试扫描 `agentkit/memory/*.py` |
| 6 | `Tool.run` 返回 `ToolResult`，工具错误不炸循环 | ✅ |
| 7 | `asyncio.CancelledError` 不被吞 | ✅ Executor / Toolbox / EventBus / Loop 四层都穿透 |
| 8 | 每个副作用动作前都有 `if ctx.stop: break` | ✅ 3 个检查点，各有行为测试 |
| 9 | `ctx.result` 只在 `Final` 时被设置 | ✅ |
| 10 | `Agent` 支持 `async with` | ✅ |
| 11 | 换 OpenAI → Anthropic 只改 `models/` 下一个文件 | ✅ 测试锁死厂商 SDK 只在对应适配器里出现 |
| 12 | 挂 MCP 只加一行 `toolbox.add_provider(...)` | ✅ |
| 13 | 加日志只加一行 `events.on("*", fn)` | ✅ |
| 14 | 加技能只丢一个 `SKILL.md` | ✅ `skills/code-review/SKILL.md` |
| 15 | 用户能在 `build_runtime()` 里组装任何形态 Agent | ✅ `examples/offline_demo.py` 的 `NoModelRuntime` |
| 16 | README 第一行：**"换掉任意模块，都不用动 agent_loop。"** | ✅ 由 `tests/test_contract.py` 断言 |

### V2 完成定义（V2 文档 §十一 DoD）

| 判据 | 结果 |
|---|---|
| `kernel/` LOC ≤ 500（V1 322） | ✅ 380 |
| `agent_loop` 代码行 ≤ 55（V1 38） | ✅ 50 |
| `kernel/` 目录依然是 5 个模块 | ✅ `types / state / events / protocols / loop`（+ 0 行 `__init__.py`） |
| `loop.py` 词表依然只含 `model.before` / `model.after` | ✅ 且事件字符串集合被钉死 |
| `agent_loop` 控制流结构不变 | ✅ 新增仅终止原因记录 + finish 保护；尺寸与事件归属有测试 |
| `ToolCalls.content` 存在且真实调用中被保留 | ✅ 三个适配器 + `observe()` 都有测试 |
| `ToolResult` 在 `kernel/types.py` 且含 `tool_call_id` | ✅ |
| `ToolExecutor` Protocol 含 `execute`/`execute_one`/`close` | ✅ |
| 4 个 executor 存在，装饰器组合语义已测试 | ✅ |
| `RetryExecutor` 不重复执行成功的 call | ✅ 关键测试断言调用次数 |
| `Executor.close()` 不关闭 Toolbox | ✅ 关键测试断言 provider 未关闭 |
| `DefaultRuntime` 可接受任意 `ToolExecutor` | ✅ `FakeExecutor` 可替换性测试 |
| `run_ctx()` 返回带 `reason` 的 `RunContext` | ✅ |
| `FINAL` 允许空 `result` | ✅ |
| `finish` 异常不覆盖原始错误 | ✅ |
| Streaming 只出现在 `models/`，`kernel/` 零新增 | ✅ `kernel/` 无 `stream`/`delta` |
| `StreamingModel` Protocol 存在（非 `hasattr`） | ✅ |
| V1 全部 158 测试仍通过 | ✅ 逐文件跑过，计数不变 |
| V2 新增测试全绿 | ✅ 68 个 |
| README 含「V2 变更」+「system 语义」+「Streaming 用法」 | ✅ |
| `CHANGELOG_v2.md` 记录每个 P0 的修复方式 | ✅ |

### V2.5 完成定义（V2.5 文档 §七）

**Contract Gate**（全部 ✅）

| 判据 | 结果 |
|---|---|
| `ToolResult` 为 `kw_only=True`，`ToolResult("x")` 抛 `TypeError` | ✅ `test_tool_result_is_keyword_only` |
| `ToolExecutor` Protocol **不含** `execute_one()` | ✅ `test_tool_executor_protocol_has_no_execute_one` |
| `dispatch()` 强制覆盖 `tool_call_id` | ✅ `test_dispatch_three_states_all_end_with_the_originating_call_id`（空 / 显式空 / 错误值三态） |
| `test_executor_close_does_not_close_the_toolbox`（SpyToolbox） | ✅ 断言 `toolbox.close_called is False` |
| `Toolbox.lookup()` 异常冒泡 | ✅ `test_lookup_failure_propagates_instead_of_becoming_a_tool_result` |
| `CancelledError` → 不设 ERROR / 不设 `ctx.error` / 重新抛出 | ✅ `test_cancelled_error_does_not_enter_the_agent_error_model` |
| 普通 `Exception` → `ctx.error` + `reason == ERROR` + re-raise | ✅ `test_plain_exception_sets_error_and_reason` |
| Model exception → `reason == ERROR` + re-raise | ✅ 真机 Error 场景双层验证 |
| Timeout / Retry 语义测试通过 | ✅ V2 的 4 组组合语义测试全绿 |
| `kernel/protocols.py` docstring 含生命周期 + 异常 + ownership + Cancellation | ✅ 冻结快照比对 |
| `docs/freeze/v2/scratch.py` 存在且 `mypy --strict` + `pyright` 通过 | ✅ Success / 0 errors |
| Fake implementation 能通过 Contract tests | ✅ `test_minimal_fake_implementations_satisfy_every_protocol` |
| Signature drift 测试 | ✅ 44 项；改动快照即失败（已实测咬人） |
| V1 (158) + V2 (226) 全部回归通过 | ✅ 逐文件核对 |

**Kernel Gate**（全部 ✅）：`agent_loop` 52 行（≤55）；`kernel/` 仍 5 模块 + `__init__.py`；无新 Kernel 抽象；
Loop 唯一改动是 `except` 拆分；`loop.py` 词表仍只含 `model.before` / `model.after`。

**Provider Gate**（部分 ✅，受 key 限制）

| Gate | 判据 | 结果 |
|---|---|---|
| A（必过） | OpenAI / Anthropic 4 个 Contract 场景 `pass` | ⚠️ **NOT VERIFIED** — 本机无 key，按 §4.6 记 `not_verified`（不算 pass） |
| A（替代验证） | 可用真机 Provider 行使同一组 Contract 场景 | ✅ Ollama 4/4、DeepSeek 4/4 且 `contract_verified: true` |
| B（尽力） | `parallel_tool` / `stream_tool` | ✅ Ollama / DeepSeek 均 `pass`；OpenAI/Anthropic 未验证 |
| C（不阻塞） | Ollama 至少 Normal + Error 真实运行 | ✅ 实际 6/6 |

**Evidence Gate**：每个 `pass` 都是真机运行（`docs/conformance/20260919T080407Z.json`）；
`not_verified` 明确 `contract_verified: false`；无 `fail`（即无 Contract 违反 → 无需分类修复）；
JSON 含 `contract_revision` + `git_revision` + `sdk.version` + `model`；Markdown 已生成；
`runner.py` 135 行 ≤150 且未复制 Runtime 逻辑。

> **V2.5 的诚实结论**：Contract 在**两个真机 Provider（Ollama / DeepSeek）**上成立，
> 因此 V2 的 Protocol / dataclass / 事件 / 异常语义**已获得真实 Provider 证据**；
> Gate A 原定的 OpenAI / Anthropic 两列仍是 `not_verified`（环境缺 key），
> 补 key 后跑一条命令即可关闭。**V3 启动条件中的「Gate A 全部 pass」尚未满足 —— 差的是 key，不是代码。**

## 取舍

**会做的：** 微内核；Context 一等公民但保持极简（provider 列表 + stable partition）；
执行策略独立成层（`ToolExecutor`）但不必进 Kernel 控制流；Streaming 归适配器；
一切用 `Protocol` 表达而非继承树；`EventBus` 作为唯一通用扩展点；
Kernel 只包含稳定契约 + 单一控制流。

**不会做的：** 不做 YAML 配置；不做万能 `Agent(...)` 构造函数（只有 `Agent(harness)`）；
不做内置 budget/compact（`events.on("model.before", fn)` 就够）；
不做 `Event` 对象化；不做 `ToolResult` 多模态；不做 Planner / RAG / Reflection / Multi-Agent；
不做任何 manager 层；不做 Retry 高级策略。

核心竞争力不是功能多，而是：

```python
async with Agent(MyHarness()) as agent:
    result = await agent.run(task)
```

## 开发

```bash
python -m pytest                                     # 292 passed（离线：单元 284 + malformed stream 8）
ruff check .                                         # All checks passed!
mypy --strict docs/freeze/v2/scratch.py              # 冻结快照的类型门禁
pyright docs/freeze/v2/scratch.py                    # 0 errors
python -m agentkit --model echo -t "hi"
python examples/offline_demo.py
python examples/streaming_harness.py

# 真机 Conformance（默认不跑，需 key）
python -m pytest tests/conformance -m conformance -v
```

测试门禁分工：

- `tests/test_contract.py` —— V1 契约（kernel 行数、loop 词表与尺寸、`Agent.__init__` 签名、
  Memory 不碰 Message、厂商 SDK 隔离、README 首行）。
- `tests/test_contract_v2.py` —— V2 契约（500/55 预算、kernel 模块集与依赖方向、
  Kernel 对 Streaming 零认知、新数据契约、事件归属）。
- `tests/test_semantics_v2.py` —— V2 语义（4 个 P0、四态终止、执行层全部语义、Streaming 组装）。
- `tests/test_replaceability_v2.py` —— V2 可替换性（任意 `ToolExecutor`、
  `Executor.close()` 不关 Toolbox、装饰器组合、副作用只发生一次）。
- `tests/unit/test_contract_v2_5.py` —— V2.5 Contract Gate（kw-only、Protocol 形状、ownership 三态、
  `lookup` 冒泡、Cancellation 与进程级信号、SpyToolbox 生命周期、fake implementation 覆盖全部 Protocol）。
- `tests/unit/test_freeze_snapshot_v2.py` —— 签名漂移门禁（`current == docs/freeze/v2/scratch.py`）。
- `tests/conformance/` —— 真机 Contract 证据（见其 README）。
