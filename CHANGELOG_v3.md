# CHANGELOG · V3（Composable Kernel）

> **V3 不是"功能版本"，是"约束版本"。**
>
> V1 建立微内核，V2 证明微内核可演化，V2.5 证明契约在真机上成立，
> V3 证明**微内核可以承载任意组合而自身不变**。
>
> **V3 最应该留下的资产**：Architecture Firewall、Public Extension API（`agentkit.api`）、
> Composition Test Suite。这三个才是 AgentKit 从"框架"走向"平台"的分界线。

```text
V1   ──►  Architecture Proof    Kernel 可以成立
V2   ──►  Evolution Proof       加 Executor + Streaming，Kernel 不膨胀
V2.5 ──►  Contract Proof        Contract 经真实 Provider 验证
V3   ──►  Composition Proof     多个独立能力同时组合，Kernel 不变   ← 本文件
```

**命题**：`A growing set of independently implemented capabilities can be composed through the
existing extension contracts without modifying Kernel control flow or Kernel public ABI.`

**判据不是 LOC，而是 ABI**：`kernel/` 在 V3 期间**一个字节都没动**。

| 度量 | V2.5 | V3 | 门禁 |
|---|---|---|---|
| `kernel/` 总行数 | 417 | **417** | 仅观察指标 |
| `kernel/` 模块数 | 5 + `__init__.py` | **5 + `__init__.py`** | 不变 ✅ |
| `agent_loop` 代码行 | 52 | **52** | ≤55 ✅ |
| Kernel 公共 ABI | — | **与 V2.5 完全一致** | ABI drift 全绿 ✅ |
| Kernel 字节指纹 | — | **6 个文件逐一未变** | 组合套件门禁 ✅ |
| 离线测试 | 303 | **471**（+164 V3 + 4 缺陷回归） | 全绿 ✅ |
| 真机 Conformance | DeepSeek + Ollama 各 6/6 | **同前，未降强度** | ✅ |

---

## 一、Gate A — Kernel ABI Stability ✅

| 判据 | 结果 |
|---|---|
| Kernel 公共 ABI 与 `docs/freeze/v2/scratch.py` 一致 | ✅ `tests/test_abi_drift.py`（101 项：dataclass 字段/顺序/注解/默认值/`@dataclass` 选项 × {v2 快照, v3 快照, runtime}、7 个 Protocol 的方法集合/签名/数据成员、`Role`/`Action` 别名成员、`TerminationReason` 枚举值、EventBus.on/off/emit 签名、v3 快照 kernel 部分与 v2 逐字一致） |
| `agent_loop ≤ 55` 行 | ✅ 52 |
| Architecture Firewall 五条全绿 | ✅ `tests/test_architecture_firewall.py`（5 项，纯 `ast` 扫描） |
| `kernel/` 5 文件 + `__init__.py` | ✅ |

**Firewall 自检（负向验证，逐条注入后确认变红再还原）**：

| 注入 | 被哪条规则抓住 |
|---|---|
| `kernel/events.py`: `import httpx` | 规则 1（无第三方包） |
| `kernel/state.py`: `from agentkit.api import Model` | 规则 2（不 import 非 kernel 的 agentkit 模块） |
| `kernel/state.py`: `class FooManager` | 规则 3（`*Manager` 类名模式） |
| 新增 `kernel/extra.py` | 规则 4（文件数） |
| `agent_loop` 内插 7 行 → 59 行 | 规则 5（≤55） |

还原后 6 个 kernel 文件 sha256 与注入前逐一相同，106 项门禁复绿。

**ABI 漂移自检**：把 v3 快照里 `RunContext.task` 改成 `task_id` → 2 项立即失败（v3↔v2 与 v3↔runtime 双向抓到），还原后 106 项复绿。

---

## 二、Public Extension API（新增层）✅

`agentkit/api/` 是第三方实现的**唯一**入口：

| 模块 | 内容 |
|---|---|
| `api/kernel.py` | Kernel 公共 ABI 的官方重导出（Protocol / 类型 / RunContext / TerminationReason / EventBus / PreparedInput） |
| `api/context.py` | `ContextTransform` Protocol |
| `api/skill.py` | `SkillProvider` Protocol |
| `api/executor.py` | `PermissionPolicy` Protocol |
| `api/__init__.py` | 单一 `__all__`（28 项） |

**为什么需要它**：V2.5 的第三方契约是"只依赖 `kernel.protocols` / `types` / `state`"，
但 V3 新增的三个扩展 Protocol **不属于 Kernel** —— 实现它们的第三方无处可 import。

**规格缺口补全（3 个符号）**：§4.3 的清单缺了三个符号，缺了它们第三方**无法实现**对应 Protocol：
`Skill`（`SkillProvider.search` 的返回类型）、`PreparedInput`（`Runtime.prepare` 的返回类型）、
`EventBus`（`Runtime.events` 的类型）。已补入 `api.__all__` 并在模块 docstring 说明。

**边界强制**：`tests/test_api_boundary.py`（5 项）用 `ast` 扫描 `tests/third_party/**`，
比 §4.5 的前缀表**更严**：前缀表漏掉 `from agentkit import kernel` 这种绕法，
因此额外加了"`agentkit.*` 闭包断言"。两种绕法都实测能被抓到。

> 附带发现：`@tool` / `FunctionTool` **没有**通过 `agentkit.api` 重导出（§4.3 清单是显式的），
> 所以第三方写 Tool 是**写类**（实现 `Tool` Protocol 的 `spec` + `run`），
> 而不是用 `@tool`。`docs/EXTENSION_GUIDE.md` 已明确区分"第三方扩展代码"与"第一方装配代码"的 import 合法性。

---

## 三、三个能力（Kernel 改动 = 零）

| 能力 | Protocol（`agentkit/api/`） | 实现 | 测试 |
|---|---|---|---|
| **ContextTransform** | `async apply(messages: Sequence[Message]) -> list[Message]`（**不接收 ctx**，纯函数） | `context/transform.py`：`BudgetTransform(max_tokens, estimate=len)` / `SlidingWindowTransform` / `DedupeTransform` / `SystemPriorityTransform` | `tests/unit/test_context_transform.py`（14） |
| **SkillProvider** | `async search(query, limit=3) -> list[Skill]`（**无 `refresh` / `close`**） | `skills/directory.py`（对齐 Protocol）+ `skills/mcp_backed.py`：`MCPBackedSkills(session, limit=3)` 从 MCP 的 `skill://` 资源拉技能 | `tests/unit/test_mcp_backed_skills.py`（10） |
| **PermissionExecutor** | `async allow(call, ctx) -> bool`（Policy **构造注入**，ctx 是显式参数） | `executor/permission.py`：`PermissionExecutor(inner, policy)` + `AllowListPolicy` / `DenyListPolicy` / `InteractivePolicy` | `tests/unit/test_permission.py`（14） |

接入方式（§6.1 / §6.4）：

```python
ContextEngine(providers=[...], transform=BudgetTransform(32_000))     # transform 是最后一步

executor = TimeoutExecutor(                                            # §6.4 冻结的组合
    RetryExecutor(
        PermissionExecutor(ParallelExecutor(toolbox), policy=AllowListPolicy({"read_file"})),
        max_attempts=3,
    ),
    seconds=30,
)
```

**权限拒绝的异常语义（§6.3 冻结）**：`ToolResult(tool_call_id=call.id,
content="[blocked by policy] <name>", error=True, metadata={"blocked": True, "reason": "permission"})`
—— 从模型视角是失败调用（它会换一条路），从人类视角由 `metadata.blocked` 区分"策略拦截"与"工具崩溃"。
**没有引入新异常类型**，V2.5 契约不变。

`ContextEngine` 位置按 §九 移到 `context/engine.py`；`runtime/default.py` 保留一行**兼容重导出**
（Gate D 要求 V1/V2/V2.5 的 `from agentkit.runtime.default import ContextEngine` 继续可用）。

---

## 四、Gate B — Composition（三级递进）✅

`tests/unit/test_composition.py`（15 项）：

| 级别 | 覆盖 |
|---|---|
| **B1 单能力** | `PermissionExecutor + Toolbox`、`BudgetTransform + ContextEngine`、`DirectorySkills + ContextProvider`、第三方 Executor / Transform 各自 × Kernel |
| **B2 多能力** | `Retry(Permission(Parallel))`、`BudgetTransform ∘ SkillProvider`（同一条 context 管线）、`Permission + BudgetTransform`（执行与上下文同时约束）、第三方 Policy × 内置 Executor |
| **B3 全栈** | 完整 Runtime（Model + ContextEngine(providers + transform) + Toolbox + Memory + Executor 四层装饰）；**三条 model path 都能承载同一套装配**；每个能力**独立可观察**；**单独替换任意能力不需要改其他能力**；`kernel/` 字节指纹未变 |

「独立可观察」的实现：执行层与模型层通过 `executor.before/after`、`model.before/after`、`iteration.done`
事件；`PermissionPolicy` 的决策通过 Observation 的 `metadata.blocked` 辨认；
`ContextTransform` / `SkillProvider` **按契约它们本身不发事件**，因此通过 `model.before` 里
`inp.messages` 的组装结果观察（system 分区 → history 的顺序仍是 V2.5 冻结的）。

---

## 五、Gate C — Ecosystem（第三方实现）✅

`tests/third_party/` 4 个实现，**只 `from agentkit.api import ...`**：

| 文件 | 实现 |
|---|---|
| `executor.py` | `FakeThirdPartyExecutor`（`tool_call_id` 绑定、批内 `ctx.stop`、可注入 delay / fail_on） |
| `context.py` | `FakeThirdPartyTransform`（纯变换，不改输入） |
| `skill.py` | `FakeThirdPartySkillProvider` |
| `permission.py` | `FakeThirdPartyPermissionPolicy`（白名单 + 尊重 `ctx.stop`，演示 ctx 是显式参数） |

- `tests/test_api_boundary.py` 全绿；
- 4 个实现都在 B1/B2/B3 组合测试里**真实出现过**（不是声明）；
- `docs/EXTENSION_GUIDE.md`：10 个扩展点的契约 + 最小实现 + 装配方式 + 边界规则 + 命题证伪红线。

**边界自检**：在 `tests/third_party/executor.py` 注入 `from agentkit.kernel.types import ToolResult`
→ 2 项失败（报 `命中禁用前缀 'agentkit.kernel.'`）；另两种绕法（`from agentkit import kernel`、
`from agentkit.tools.function import tool`）也被抓到；还原后 5 项复绿。

---

## 六、Gate D — Regression / Conformance ✅

| 判据 | 结果 |
|---|---|
| 全部历史测试通过 | ✅ V1 (158) + V2 + V2.5 逐文件核对；离线总计 **471 passed** |
| `ruff` / `mypy --strict` / `pyright` | ✅ 全绿（`docs/freeze/v2|v3/scratch.py` 各跑一次） |
| Contract drift 门禁 | ✅ `tests/unit/test_freeze_snapshot_v2.py`(44) + `tests/test_abi_drift.py`(101) |
| Conformance 真机 | ✅ DeepSeek `deepseek-flash` 6/6 + Ollama `ornith:9b` 6/6（`docs/conformance/20260919T091118Z.json`） |
| 未降低 V2.5 验证强度 | ✅ 同一条命令、同一套断言、同一 Gate 结构 |

测试增量：`test_abi_drift` 101 + `test_architecture_firewall` 5 + `test_api_boundary` 5 +
`test_context_transform` 14 + `test_permission` 14 + `test_mcp_backed_skills` 10 + `test_composition` 15 = **164**，
另有 **4 项** 真机缺陷回归（见 §十）。

---

## 七、布局决定与偏差（显式登记）

1. **`ContextEngine` 移到 `context/engine.py`**（§九），`runtime/default.py` 保留一行兼容重导出 ——
   否则 V1/V2/V2.5 的既有 import 会断，违反 Gate D。
2. **`RetryExecutor` / `TimeoutExecutor` 拆到 `retry.py` / `timeout.py`**（§九），
   `builtin.py` 只留 `dispatch` / `execute_serial` / `execute_one_of` / `Sequential` / `Parallel`；
   **不保留旧路径重导出**（干净切换），两个历史测试文件仅改 import 行。
3. **`DirectorySkills.search(k=)` → `limit=`** 以对齐 `SkillProvider`；
   `tests/unit/test_skills.py` 仅一处关键字改名，断言未动。
4. **`api.__all__` 补 3 个符号**（见 §二），理由是 API 的闭合性。
5. **组合套件放在 `tests/unit/test_composition.py`**（§九 的目录树没有指定位置）：
   它复用 `tests/unit/support.py` 的装配助手，避免再造第二套 harness。
6. **修正了一处潜伏的门禁缺陷**：`tests/unit/test_freeze_snapshot_v2.py::_ast_param_lines`
   的默认值配对写成了 `[*defaults, *missing]`（应为 `[*missing, *defaults]`，默认值必须右对齐）。
   V2.5 时 v2 快照的方法都没有默认值，所以从未误报——但 V3 起协议方法开始带默认值
   （`SkillProvider.search(limit=3)`），留着就是地雷。修正后配对正确、44 项仍全绿。

---

## 八、观察到的行为与 V4 候选（如实记录，本轮不改）

1. **权限拒绝会被 `RetryExecutor` 重试**：拒绝结果是 `error=True`（§6.3 冻结的语义），
   而 `RetryExecutor` 对 `error=True` 重试 → 被拦截的 call 会被重试到 `max_attempts`。
   无副作用（工具从未执行），但语义上"确定的拒绝"不该计入重试预算。
   V4 候选：`retry_on` 谓词，或引入 `ExecutorDecision` 区分"可重试失败"与"确定性拒绝"。
2. **`BudgetTransform` 的"system + 尾部贪心"可能挤掉用户当前任务**：provider 注入的长条目
   位于 history 之后，尾部贪心会优先保留它们。V4 候选：更精确的策略（system + 末条 user + 尾部 N）。
3. **`InteractivePolicy` 的拒绝集合**：字符串按 `{'', 'n', 'no', 'false', '0'}`（strip+lower）判拒绝，
   因为 `bool('n') is True` —— 纯真值判读会把"用户答 n"当成放行。已写入 docstring。
4. 仍是 Non-goals（§二）：`ToolResult` 多模态、`Event` 对象化、任何 Manager、Planner/RAG/Reflection/Multi-Agent、
   Streaming 进 Kernel、Kernel 新 Protocol / 数据契约变更。

---

## 九、V3 完成后应能宣称

> **AgentKit 的架构能够随着 Agent 能力增长而增长，而不是随着能力增长把复杂度重新吸回 Kernel。**

```text
1. Kernel ABI 与 V2.5 完全一致                     → Gate A ✅（字节指纹 + 101 项 ABI drift）
2. 3 个新能力 + 既有能力分三级组合工作              → Gate B ✅（B1/B2/B3 共 15 项）
3. 4 个第三方实现只依赖 agentkit.api               → Gate C ✅（边界门禁 + 组合中真实使用）
4. 全部历史纪律保持                                → Gate D ✅（467 离线 + 真机 12）
5. Architecture Firewall 五条规则可执行            → ✅（含 5 类注入自检）
6. agentkit.api 成为唯一公共扩展入口               → ✅（28 项 __all__ + 边界门禁）
```

## 十、真机验证发现的缺陷（V3 的直接产出）

| | |
|---|---|
| **现象** | 长工具链的 CLI run 稳定报 `400 BadRequestError: Messages with role 'tool' must be a response to a preceding message with 'tool_calls'`（DeepSeek 拒绝该报文） |
| **定位** | 抓下失败请求的报文后可见：消息列表**以 `tool` 消息开头** —— `ContextEngine.build()` 的 `ctx.messages[-history_limit:]`（默认 40）把 `assistant(tool_calls)` 与它随后的 `tool` 结果**从中间切开**，留下失去宿主的孤儿 `tool` 消息 |
| **为什么以前没发现** | 缺陷自 V1 就在，但只在**长工具链**（>40 条消息）下触发；短用例、仿真客户端、以及"模型只调用一两次工具"的场景都碰不到 |
| **修复** | `ContextEngine._history_tail()`：尾部截断后丢掉**开头连续的孤儿 `tool` 消息**（它们本就无法解释）；纯非 kernel 改动，`history_limit` 语义不变 |
| **门禁** | `tests/unit/test_history_pairing.py`（4 项）：配对不变量、边界恰落 batch 起点、窗口内全孤儿→宁可空也不发非法报文 |
| **真机复验** | 触发大量工具调用（列举目录 + 逐个读 `agentkit/api/*.py` 并汇总）的同一条命令，修复前必炸、修复后 exit 0 正常汇总 |

> 这类缺陷只有"真实 Provider + 真实长会话"才会暴露：它既不是 Contract 违反（单元测试全绿），
> 也不是 Provider 限制（OpenAI 同样会拒绝）—— 这正是 V2.5/V3 坚持真机纪律的回报。
