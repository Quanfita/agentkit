# CHANGELOG · V2.5（Contract 收口 + Provider Conformance）

> **V2.5 不是新版本，是 V2 的最后一个阶段。它不加任何新功能，只做两件事：把 V2 遗留的 Contract 收口，
> 以及用真实 Provider 验证 V2 Contract。**
>
> **V2.5 真正产出的不是代码，而是证据。**

```text
V1   ──►  Architecture Proof    Kernel 边界成立
V2   ──►  Evolution Proof       加 Executor + Streaming，Kernel 不膨胀
V2.5 ──►  Contract Proof        Contract 收口 + 真机验证   ← 本文件
V3   ──►  Capability Expansion  只有在 V2.5 通过后，才能启动
```

| 度量 | V2 | V2.5 | 门禁 |
|---|---|---|---|
| `kernel/` 总行数 | 380 | **417**（其中 docstring 120 行 → 可执行代码 297 行） | ≤500 ✅（V2.5 名义值 380，超出部分全是 §2.1/§2.6 要求的契约 docstring） |
| `agent_loop` 代码行 | 50 | **52** | ≤55 ✅（文档预计 51） |
| `kernel/` 模块 | 5 + `__init__.py` | **5 + `__init__.py`** | 不变 ✅ |
| 单元测试 | 226 | **226**（V1 158 + V2 68，逐文件核对） | 全绿 ✅ |
| Conformance 套件 | — | `tests/conformance/`（6 场景 × 4 Provider） | 见 `docs/CONFORMANCE_REPORT.md` |

---

## 一、Contract 收口（Phase V2.5-1）

| # | 项 | V2 现状 | V2.5 冻结 | 证据 |
|---|---|---|---|---|
| 1 | `ToolResult`（§2.4） | `ToolResult("hello")` 静默把 `"hello"` 塞进 `tool_call_id`，`content` 空 —— 不报错、不警告、语义错误 | `@dataclass(slots=True, kw_only=True)`；位置参数直接 `TypeError`；**不做 `__post_init__` 猜测** | `test_tool_result_is_keyword_only` |
| 2 | `ToolExecutor`（§2.1） | Protocol 含 `execute_one()`，把实现原语当公共契约 | Protocol **只有** `execute()` + `close()`；per-call 原语 `dispatch()` 降为 `builtin` 内部 helper | `test_tool_executor_protocol_shape`（断言 `not hasattr(..., "execute_one")`） |
| 3 | `tool_call_id` ownership（§2.5） | 「为空时补齐」→ 允许 `tool_call_id="wrong-id"` 穿透 | `ToolCall.id ──► Executor ──► ToolResult.tool_call_id`，**强制覆盖**（Tool 误填静默覆盖，不校验） | `test_dispatch_three_states_all_end_with_the_originating_call_id` |
| 4 | `lookup()` 异常语义（§2.2） | 契约与实现不一致 | `Toolbox.lookup()` 异常**冒泡**（基础设施故障不伪装成 Observation）；`Tool.run()` 异常才转 `ToolResult(error=True)` | `test_lookup_failure_propagates_instead_of_becoming_a_tool_result` |
| 5 | Cancellation（§2.3） | `except BaseException` 把 `CancelledError` 也记成 `ERROR`，与「取消必须穿透」冲突 | `except asyncio.CancelledError: raise` 与 `except Exception:` 两分支；取消**不设** `ctx.error`/`reason`，只保证 `finally` cleanup 跑完 | `test_cancelled_error_does_not_enter_the_agent_error_model`、`test_plain_exception_sets_error_and_reason`、`test_keyboard_interrupt_is_not_part_of_the_agent_error_model` |
| 6 | 生命周期 docstring（§2.6） | 分散在实现注释里 | `kernel/protocols.py` 顶部写死所有权树 + 关闭顺序；`ToolExecutor` docstring 写全生命周期/ownership/异常/取消四类契约 | 冻结快照比对（`docs/freeze/v2/scratch.py`） |
| 7 | 流程升级（§2.7） | Freeze 文档没经过类型系统验证（`StreamingModel.stream` 的 `async def` vs `def` 就是这么错的） | `docs/freeze/v2/scratch.py` 快照 + `mypy --strict` + `pyright` + 签名漂移测试 | `tests/unit/test_freeze_snapshot_v2.py` |
| 8 | `agent_loop` 预算立场（§2.8） | — | 55 行是硬上限、语义边界 > LOC 门禁；loop 只允许含 iteration/reason/stop check/事件/termination/finish 保护/CancelledError 穿透 | `test_loop_emits_only_its_own_frozen_event_names`、`test_agent_loop_within_the_v2_line_budget` |

**Loop 的唯一改动**是 `except` 分支拆分（§9），行数净 +2（50 → 52），仍在 55 预算内。

---

## 二、Migration guide（对 V2 用户是破坏性变更）

| # | 变更 | 影响 | 迁移 |
|---|---|---|---|
| 1 | `ToolResult` 变 kw-only | `ToolResult("x")` 现在是 `TypeError` | 全部改关键字：`ToolResult(content="x")`。仓库内构造点已全部迁移 |
| 2 | `ToolExecutor` 不再有 `execute_one()` | 自定义 Executor 若实现了它，属于多余方法；若**只想**实现它，现在必须实现 `execute()` | 把 per-call 策略内联进 `execute()`（可复用自己的 `_execute_one`）；装饰器请用「单 call 的 `ToolCalls`」表达一次执行：`await inner.execute(ctx, ToolCalls([call]))` |
| 3 | `tool_call_id` 由「补齐」变「强制覆盖」 | 工具自报的 id 会被覆盖（**这是有意的**：Tool 不拥有 correlation identity） | 工具不要填 `tool_call_id`；需要自定义 id 请改 `metadata` |
| 4 | `CancelledError` 不再进错误模型 | 之前 `ctx.error` 会是 `CancelledError`、`reason == ERROR` | 不要依赖 `ctx.error` 判断取消；取消语义是「异常穿透 + `finally` cleanup」 |
| 5 | `KeyboardInterrupt` / `SystemExit` / `GeneratorExit` 不再被 Agent 捕获 | 之前会被 `except BaseException` 记进 `ctx.error` | 无（这是修正：进程级信号不属于 Agent 语义） |
| 6 | `Toolbox.execute()` 仍然 deprecated | 不变 | 新代码用 `ToolExecutor` |

---

## 三、新增 DeepSeek Provider（本次环境下的真机 Provider）

V2.5 文档的 Gate A 指定 OpenAI + Anthropic，但本机**没有这两个 key**。按用户要求新增
`agentkit/models/deepseek.py` 作为真机 Provider：DeepSeek 的 wire format 与 OpenAI 同构
（含 tool calling 与流式），因此 `DeepSeekModel` **继承 `OpenAIModel`**，只覆盖「客户端构造 + 默认值」，
消息/工具/流的转换复用同一份实现 —— 不产生第二套映射逻辑（这也让厂商隔离门禁保持成立：
`openai` SDK 只允许出现在 `models/openai.py` 与 `models/deepseek.py`）。

```bash
export DEEPSEEK_API_KEY=sk-...            # 必需
export AGENTKIT_DEEPSEEK_MODEL=deepseek-flash   # 可选，默认 deepseek-chat
python -m agentkit --model deepseek --model-name deepseek-flash -t "北京天气怎么样"
```

**Gate A 的诚实标注**：本机无 OpenAI / Anthropic key，两者在证据里一律是
`not_verified`（`contract_verified: false`，§4.6），**不算 pass**。DeepSeek 承担本次的真机 Contract 验证；
带 key 的环境用同一条命令即可补齐 Gate A 原定两列：

```bash
OPENAI_API_KEY=... ANTHROPIC_API_KEY=... python -m pytest tests/conformance -m conformance -v
```

---

## 四、与文档的等价差异与偏离（显式登记）

1. **per-call 原语改用公有 API 表达**：`execute_one_of(executor, ctx, call)` =
   `await executor.execute(ctx, ToolCalls([call]))` 取第一个结果。文档只说「实现可自由选择内部是否使用
   per-call 原语」，所以这是合规实现；额外好处是**任何第三方 `ToolExecutor` 都能被装饰器包裹**
   （装饰器不再依赖某个私有方法名），组合语义（§3.5）逐条保持。
2. **`agent_loop` 52 行**（文档 §9 预计 51，预算 55）。
3. **`kernel/` 417 行 > 文档名义值 380**：差额全部是 docstring（120 行），即 §2.1/§2.6 明确要求的
   契约文本；**可执行代码 297 行**，没有新增任何 Kernel 抽象、模块或依赖方向变化。
4. **Gate A 的 Provider 替换**（见 §三）：DeepSeek 真机 + OpenAI/Anthropic `not_verified`。
5. **新增 `models/deepseek.py`**：V1 的「厂商 SDK 隔离」契约测试白名单同步加入该文件（厂商 SDK 仍然只出现在
   `models/` 内、绝不出现在 `kernel/`）。

---

## 五、回归与验证

```bash
python -m pytest tests/unit                 # 226 passed（V1 158 + V2 68）
python -m pytest tests/conformance -m conformance -v   # 真机 Conformance（默认套件不含）
ruff check .                                # All checks passed!
mypy --strict docs/freeze/v2/scratch.py     # Success
pyright docs/freeze/v2/scratch.py           # 0 errors
```

V1 / V2 的 226 个测试**一个没删、一个没弱化**：只有 V2.5 明确的契约变更处做了对应更新
（`ToolExecutor` 无 `execute_one`、`tool_call_id` 强制覆盖、`CancelledError` 语义、loop 行数门禁、
DeepSeek 白名单）；其余全部原样通过。

真机证据见 `docs/CONFORMANCE_REPORT.md` 与 `docs/conformance/<timestamp>.json`。
