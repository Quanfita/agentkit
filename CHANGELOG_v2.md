# CHANGELOG · V2

> **V2 不是「V1 + 一堆新功能」，而是「用最小代价消化 V1 的真实缺陷，同时证明 Kernel 能扩张而不膨胀」。**

V2 新增了一整层执行策略（`ToolExecutor`）和一整个 Streaming 能力，
而 `agent_loop` **控制流结构不变**，只多了「终止原因记录」与「finish 异常保护」两处逻辑。

| 度量 | V1 | V2 | V2 预算 |
|---|---|---|---|
| `kernel/` 总行数 | 322 | **380** | ≤ 500 |
| `agent_loop` 代码行 | 38 | **50** | ≤ 55 |
| `kernel/` 模块 | 5 + `__init__.py` | **5 + `__init__.py`** | 不变 |
| `loop.py` 能力词表 | `model.before` / `model.after` | **不变** | 不变 |
| 测试 | 158 通过 | **226 通过**（V1 158 全绿 + V2 68） | — |

---

## 一、四个 P0 修复

> 文档 §1.1 / §1.6 显式标注了 **P0-A** 与 **P0-C**；**P0-B / P0-D** 是本文件按
> Phase V2-1 的修复清单（`reason` / `max_iterations` / `run_ctx` 与
> `tool_call_id` / 执行策略解耦）归纳的编号，含义与原缺陷一一对应。

### P0-A：`ToolCalls` 丢弃 `content`

| | |
|---|---|
| **现象** | OpenAI / Anthropic 的响应允许文本与 `tool_calls` 并存。V1 的 `ToolCalls` 只有 `calls`，`observe()` 也把 assistant 消息的 `content` 固定写成 `""` —— 模型的解释性文本被永久丢弃，**每次调用都发生**。 |
| **修复** | `ToolCalls.content: str = ""`（`kernel/types.py`）；`observe()` 把 `action.content` 写进 assistant message（`runtime/default.py`）；三个适配器都带出 `content`（`models/openai.py`、`models/anthropic.py`、`models/ollama.py`）。 |
| **证据** | `test_openai_adapter_keeps_content_next_to_tool_calls`、`test_ollama_adapter_keeps_content_next_to_tool_calls`、`test_observe_writes_action_content_into_the_assistant_message`、`test_anthropic_generate_parses_tool_use_blocks` |

### P0-B：终止语义不可观测 + `max_iterations` 边界模糊

| | |
|---|---|
| **现象** | V1 的 `RunContext` 只有 `done/result`：调用方无法区分「模型给了 Final」「额度耗尽」「Hook 熔断」「异常」；`max_iterations` 也没有冻结定义（到底算不算最后一轮？）。上层想重试/降级只能猜。 |
| **修复** | `TerminationReason`(4 值) + `RunContext.reason`（`kernel/state.py`）；`agent_loop` 在 4 个退出点赋值（`kernel/loop.py`）；`max_iterations` 冻结为「最多完成的 `model → (可选) tool` 轮数」，最后一次 model 返回 `Final` 时 `FINAL` 优先于 `MAX_ITERATIONS`；`Agent.run_ctx()` 让调用方拿到整个 `RunContext`（`agent.py`）。 |
| **证据** | `test_run_ctx_returns_a_run_context`、`test_reason_final_allows_empty_result`、`test_reason_max_iterations_after_exactly_n_rounds`、`test_final_on_the_last_allowed_round_wins_over_max_iterations`、`test_reason_stopped_when_a_hook_sets_stop`、`test_error_reason_is_recorded_on_the_context` |

### P0-C：`finish()` 异常破坏收尾

| | |
|---|---|
| **现象** | V1 的 `finally: await runtime.finish(ctx)` 没有任何保护：记忆写入失败会**覆盖**循环里的原始异常，并让 `agent.end` 永不发出 —— 观测链断在最后一步。 |
| **修复** | `finally` 加保护（`kernel/loop.py` §1.6）：finish 失败只在 `ctx.error is None` 时写入，并发 `agent.finish_error` 事件；`agent.end` 由内层 `finally` 保证一定发出。 |
| **证据** | `test_finish_error_is_recorded_and_announced`、`test_finish_error_does_not_overwrite_the_original_error` |

### P0-D：结果无法归因 + 执行策略焊死在 Toolbox

| | |
|---|---|
| **现象** | ① `ToolResult` 没有 `tool_call_id`，批量结果与 call 只能靠位置对齐，乱序日志/重排无从归因；② 执行策略（并发）写死在 `Toolbox.execute()` 里，Retry/Timeout 只能**重跑整批**——对 `write_file` / `send_email` 这类副作用工具是灾难。 |
| **修复** | `ToolResult.tool_call_id`（`kernel/types.py`）；`ToolExecutor` Protocol（`execute` / `execute_one` / `close`，`kernel/protocols.py`）；`executor/builtin.py` 四个实现 + 装饰器组合；`DefaultRuntime(executor=...)` 接受任意执行器（`runtime/default.py`）；`Toolbox` 收缩为 Discovery + Lookup，`execute()` 标记 deprecated（`toolbox.py`）。 |
| **证据** | `test_dispatch_binds_tool_call_id`、`test_retry_executor_only_retries_the_failed_call`、`test_retry_keeps_side_effecting_calls_single_shot_across_executors`、`test_timeout_retry_and_retry_timeout_have_different_semantics`、`test_runtime_accepts_an_arbitrary_tool_executor`、`test_executor_close_does_not_close_the_toolbox` |

---

## 二、新增能力

### 2.1 执行层 `agentkit/executor/`

```
Toolbox      = "有哪些工具？"       （Discovery + Lookup）
ToolExecutor = "怎么执行工具？"     （Execution Policy）
Tool         = "工具具体做什么？"   （Capability）
```

| 实现 | 语义 |
|---|---|
| `SequentialExecutor` | 串行；批内按 call 粒度响应 `ctx.stop`（未开始的 call 不执行） |
| `ParallelExecutor` | 默认；`asyncio.gather` 并发；`ctx.stop` 只保证「尚未开始的 batch 不启动」 |
| `RetryExecutor(inner, max_attempts=3, backoff=0.5)` | per-call 重试；`max_attempts` 是**总次数**（含首次）；只重试 `error=True` 的结果 |
| `TimeoutExecutor(inner, seconds=30)` | per-call 超时；超时转 `ToolResult(error=True)` |

组合语义由「装饰器重写 `execute_one`」自然导出：

| 表达式 | 语义 |
|---|---|
| `Timeout(Retry(X))` | 单 call 的整个 retry 过程共享一个 timeout |
| `Retry(Timeout(X))` | 每次 retry 各自拥有独立 timeout |
| `Retry(Parallel(X))` | batch 内并发，失败 call 独立重试 |
| `Timeout(Parallel(X))` | batch 内并发，单 call timeout |

**异常模型**：工具失败 → `ToolResult(error=True)`；执行基础设施失败 → 异常；
`asyncio.CancelledError` 一律穿透（`RetryExecutor` 也不会重试它）。

**生命周期**：`Executor` 借用 Toolbox，不拥有它；`Executor.close()` 只关自己的资源，
`DefaultRuntime.close()` 分别关闭 executor 与 toolbox（`test_executor_close_does_not_close_the_toolbox`）。

### 2.2 Streaming 分层（Kernel 零新增）

- `models/base.py`：`TextDelta` / `ToolCallDelta` / `Delta` / `StreamingModel` Protocol（不是 `hasattr` 隐式契约）。
- `OpenAIModel.stream()` / `AnthropicModel.stream()` / `OllamaModel.stream()` 把厂商原始 chunk 归一化成 `Delta`。
- `examples/streaming_harness.py`：`StreamingRuntime` 在 `reason()` 里把 Delta 组装成 `Action`，并发 `model.delta` 事件。
- `kernel/` 里 `stream` / `delta` 出现次数为 **0**（`test_kernel_has_zero_knowledge_of_streaming`）。

### 2.3 事件（§7 冻结）

新增 `executor.before` / `executor.after`（`DefaultRuntime.act`）、`model.delta`（StreamingRuntime）、
`agent.finish_error`（loop）。**新事件全部由 Kernel 之外发出**，
`loop.py` 的事件名字符串集合被测试钉死为它自己那一组。

---

## 三、破坏性变更与迁移

| # | 变更 | 迁移方式 |
|---|---|---|
| 1 | `ToolResult` 字段顺序：`tool_call_id` 成为首字段 | 位置参数改关键字。仓库内 8 处构造点与 6 处测试断言已全部迁移；`ToolResult("x")` 现在会写进 `tool_call_id` |
| 2 | `Agent.run(task, **kw)`（原关键字参数固定为 `max_iterations` / `system`） | 旧调用 `run(task, max_iterations=..., system=...)` **零改动**；默认值改由 `RunContext`（16 / `""`）提供 |
| 3 | `Toolbox.execute()` 弃用（内部委托 `ParallelExecutor`） | 仍可用；新代码把 `ToolExecutor` 交给 `DefaultRuntime`。V3/V4 才移除 |
| 4 | `finish()` 异常不再向外冒泡 | 改为写入 `ctx.error` + `agent.finish_error` 事件（P0-C 语义）；需要感知的 Harness 挂事件 |
| 5 | `Toolbox._run()` 私有方法移除 | 逻辑上移到 `executor.builtin.dispatch()`；公开 API 无变化 |

---

## 四、与文档代码的等价差异（实现注记）

三处**行为一致**的偏差，写在这里以免评审时对不上：

1. **`dispatch()` / `serial_execute()` 提取为模块级函数。** 文档把「lookup + 异常映射」写在
   `SequentialExecutor._dispatch`，并把串行遍历抄了三份（Sequential / Retry / Timeout）。
   提取后：异常映射单一来源（「Sequential 与 Parallel 行为一致」从约定变成结构保证），
   也避免为每次调用构造一次临时 `SequentialExecutor`。
2. **`dispatch()` 会为成功结果补齐 `tool_call_id`。** 文档只在 unknown-tool 分支写入 id；
   那样新字段在成功路径永远是空的，而 §1.2 明确它是给 debug / logging / 乱序重排用的。
   工具自己返回了 id 时以工具为准（`test_dispatch_keeps_a_tool_supplied_tool_call_id`）。
3. **`StreamingModel.stream` 声明为 `def ... -> AsyncIterator[Delta]`**（文档写 `async def`）。
   实现是 async generator —— §4.3 的 `async for d in model.stream(...)` 正是这个语义；
   写成 `async def ... -> AsyncIterator` 会类型学上暗示「先 await 再迭代」，与用法矛盾。

另：`kernel/` 目录里还有一个 0 行的 `__init__.py`（包标记）。§2 说的「5 个文件」指模块文件，
目录树与依赖方向未变。

---

## 五、V2 的非目标（明确没做）

Planner / RAG / Reflection / Multi-Agent；`ContextEngine` 的 budget / compact 内置；
`ToolResult` 多模态；`Event` 对象化；任何 manager 层；Retry 的高级策略
（jitter / exponential backoff / `retry_on` 谓词）；Executor 沙箱化与权限系统。
这些连同「`Toolbox.execute()` 移除」一起进入 V3 候选（`docs/V2 Contract Freeze...md` §12）。

---

## 六、验证

```bash
python -m pytest                 # 226 passed（V1 158 + V2 68）
ruff check .                     # All checks passed!
python examples/streaming_harness.py     # Delta → Action → Tool 全链路
python -m agentkit --model echo -t "hi"
```

V1 的 13 个测试文件一个没删、一个没弱化：只有字段用法（`ToolResult` 关键字参数）、
事件序列（多出 `executor.*`）、契约阈值（§2 的 500 / 55）与 `Agent.run` 签名按 V2 冻结更新。
