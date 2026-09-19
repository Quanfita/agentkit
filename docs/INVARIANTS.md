# AgentKit 不变量索引

本文件是**不变量 → 可执行门禁**的索引。规则只有一条：

> **每一条不变量都必须指向一个能跑的命令。**
> 没有门禁的不变量，只在 §已登记缺口 里登记 —— 不许留在正文里当口号。

分三层，对应三份不同的冻结资产：

| 层 | 冻结什么 | 权威文件 | 变更流程 |
|---|---|---|---|
| Layer 1：Kernel ABI | Kernel 的公共形状与依赖方向 | `docs/freeze/v3/ABI.md` + `docs/freeze/*/scratch.py` | Kernel Change Review（V3 期间禁止） |
| Layer 2：Internal Message Sequence Contract | 消息序列的结构不变量 I1–I5 | `docs/contracts/message_protocol.md` | Contract Change Review |
| Layer 3：能力层不变量 | 单个能力的语义承诺 | 本文件各条目 | 随能力实现，见各条目 |

命令一律在仓库根目录执行（Windows PowerShell / bash 均可）。

---

## Layer 1：Kernel ABI

### L1.1 Kernel 公共 ABI 形状冻结

- **不变量**：`运行时 kernel ABI == docs/freeze/v2/scratch.py == docs/freeze/v3/scratch.py`
  （类 / 方法名 / 参数名 / 返回类型 / dataclass 字段名 + 类型 + 顺序 / 别名成员 /
  扩展 Protocol 形状）。V3 期间**不得**新增参数、字段、枚举值、方法。
- **权威文件**：`docs/freeze/v3/ABI.md`（人读清单）、`docs/freeze/v3/scratch.py`（机读快照）
- **实现位置**：`agentkit/kernel/{types,state,events,protocols,loop}.py`、`agentkit/api/*.py`
- **门禁**：

  ```bash
  python -m pytest tests/test_abi_drift.py -q
  ```

  关键用例：`test_dataclass_fields_match`、`test_protocol_methods_match`、
  `test_kernel_has_no_unsnapshotted_public_classes`（反向完整性：kernel 出现新的公共类
  而没进快照 → 红）、`test_eventbus_signatures_match_abi_doc`
  （`docs/freeze/v3/ABI.md` §1.4 的 EventBus 清单 == runtime）。

### L1.2 Kernel 字节指纹在 V3 期间不变

- **不变量**：`agentkit/kernel/*.py` 的内容 SHA256 与 V3 基线一致
  （先按 LF 归一化行尾，避免 `core.autocrlf` 误报）。
- **门禁**：

  ```bash
  python -m pytest tests/unit/test_composition.py -q -k kernel_bytes
  ```

  用例：`test_kernel_bytes_unchanged_during_v3`。
  **这条是「Correctness 修复必须两段提交」里「Kernel SHA256 不变」的可执行形式。**

### L1.3 Architecture Firewall 五条规则

- **不变量**：kernel 不得向外长出新层。规则原文见 `tests/test_architecture_firewall.py`。
- **门禁**：

  ```bash
  python -m pytest tests/test_architecture_firewall.py -q
  ```

| # | 规则 | 可执行形式（用例名） |
|---|---|---|
| 1 | kernel 的 import 只能来自 stdlib 或 kernel 内部，不得有第三方包 | `test_kernel_no_external_imports` |
| 2 | kernel 不得 import `agentkit` 内任何非 kernel 模块（依赖方向） | `test_kernel_no_agentkit_non_kernel_imports` |
| 3 | kernel 内不得定义 `*Manager` / `*Registry` / `*Factory` / `*Adapter`（`*Provider` 豁免），也不得定义具体能力实现（`*Transform` / `*Executor` / `*Policy` / `Agent` / `Harness` …） | `test_kernel_no_manager_classes` |
| 4 | `agentkit/kernel/` 只能有 5 个模块 + `__init__.py`（多一个文件就是新层） | `test_kernel_file_count` |
| 5 | `agent_loop` 代码行（非空非注释）≤ 55 | `test_agent_loop_line_count` |

规则 1 / 2 是**主判据**，规则 3 是关键词辅助（刻意改名的能力类拦不住）——
真正的结构性保证来自「kernel 无法 import 任何非 kernel 模块」。

### L1.4 生态边界：第三方实现只能 import `agentkit.api`

- **不变量**：`agentkit.api` 之外的一切路径都是内部实现；第三方扩展点实现
  （`ContextTransform` / `SkillProvider` / `PermissionPolicy` / `ToolExecutor`）
  只许依赖 `agentkit.api`，且必须能通过对应的 `runtime_checkable` Protocol `isinstance`。
- **门禁**：

  ```bash
  python -m pytest tests/test_api_boundary.py -q
  ```

  用例：`test_third_party_has_no_forbidden_imports`、
  `test_third_party_only_imports_agentkit_api`、
  `test_third_party_fakes_actually_use_the_api`（防止上面的边界证据空转）、
  `test_third_party_fakes_satisfy_extension_protocols`、
  `test_third_party_fakes_work_through_api_types_only`。

---

## Layer 2：Internal Message Sequence Contract（I1–I5）

权威定义：**`docs/contracts/message_protocol.md`**（本文件不重复 invariant 原文，
只登记「谁能跑出证据」）。

| Invariant | 一句话 | 门禁 |
|---|---|---|
| I1 Role Dependency | 序列不以 `tool` 开头；任何 `tool` 消息必须有前置 `assistant(tool_calls)` | `tests/unit/test_history_pairing.py`、`tests/property/test_transform_protocol.py` |
| I2 Call Identity Preservation | 每条 `tool` 消息的 `tool_call_id` 必须被某条前置 `assistant(tool_calls)` 声明过 | 同上 |
| I3 Transform Closure | `Valid(messages) ⇒ Valid(T(messages))`（对所有 Transform） | `tests/property/test_transform_protocol.py`（实测 26 passed，2026-09-19） |
| I4 Tool Result Ordering | 声明的全部 tool result 必须在下一个 `assistant` 之前出现（批内顺序可交换） | `tests/property/`（尚缺产生侧用例，见 §已登记缺口） |
| I5 Call ID Uniqueness | 单次 history（`ctx.messages`）内 `tool_call_id` 唯一（非 session 全局） | `tests/property/test_message_sequence.py`（检查器自检） |

### L2.1 序列合法性判定器 + 生成器

- **门禁**：

  ```bash
  python -m pytest tests/property/test_message_sequence.py -q
  ```

  文件：`tests/property/message_sequence.py` —— `valid_message_history()`（生成器）与
  `is_valid_message_sequence()`（判定器，覆盖 I1–I5，返回 `(bool, reason)`）。
- **待补**：I1–I5 是**判定器**的覆盖目标；其中 I4 的「产生侧」证据（
  `DefaultRuntime.observe()` 在批内停止时少写结果）今天没有用例，见 §已登记缺口。

### L2.2 Transform 闭合性（I3）

- **门禁**：

  ```bash
  python -m pytest tests/property/test_transform_protocol.py -q
  ```

  基类：`tests/property/message_sequence.py::TransformProtocolTestBase`。
  **所有 `ContextTransform` 实现（内置与第三方）都必须继承它。**

- **落地状态（2026-09-19 实测）**：**已落地并绿**（26 passed）。
  中间态曾按「两段提交」要求先红：两条 `I3 (transform closure) violated ... I1: sequence
  starts with a tool message` 是内置截断类 Transform 的真实违反
  （Commit A `a7689f6` 红 → Commit B `eb967cd` 绿，实现为
  `transform.py::_trim_leading_orphan_tools()`）。

### L2.3 历史截断边界（I1/I2 的回归）

- **不变量**：`ContextEngine` 的尾部截断不得产出以孤儿 `tool` 开头的报文。
- **实现位置**：`agentkit/context/engine.py::ContextEngine._history_tail()`
- **门禁**：

  ```bash
  python -m pytest tests/unit/test_history_pairing.py -q
  ```

  用例：`test_history_truncation_never_orphans_tool_messages`、
  `test_short_history_is_untouched`、
  `test_truncation_that_lands_exactly_on_a_boundary_keeps_the_batch`、
  `test_all_orphaned_prefix_is_dropped_not_left_empty`。

---

## Layer 3：能力层不变量

### L3.1 Context：当前用户任务不可被 `BudgetTransform` 丢弃

- **不变量**：`ctx.messages` 里最后一条 `user` 消息（当前用户任务）在任何
  `ContextTransform` 之后必须仍然存在。识别依据是「最后一条 user 消息」，
  不是长度、不是 role 推断。
- **注意**：这是 invariant，不是 algorithm；保尾部 / 提权 / 其他实现方式都是算法。
- **实现位置**：`agentkit/context/transform.py::_current_task_index` +
  `_kept_with_current_task`（V3.1 修复 ②），由 `BudgetTransform` 与
  `SlidingWindowTransform` 共同调用 —— 「当前任务活下来」是 invariant，
  「怎么让它活下来」是算法。
- **门禁**：

  ```bash
  python -m pytest tests/unit/context/test_budget_current_task.py -q
  ```

- **落地状态（2026-09-19 实测）**：**已落地并绿**（`tests/unit/context` 全绿）。
  中间态曾按「两段提交」要求先红（Commit A：复现测试 `current user task was dropped` × 3），
  Commit B 修复后转绿。
- **与 I3 的关系（必须知道）**：把当前任务的**下标**并回保留集合，会与 I3 相互作用，
  所以顺序不可颠倒 —— 必须**先** `_trim_leading_orphan_tools()` 清孤儿前缀，
  **再** `_kept_with_current_task()` 补当前任务；反过来的话当前任务会把孤儿
  "挡"在保留集里，清洗提前停止（G1 的修复就是这样收口的）。
  两条不变量现在都已收敛，但**必须一起看**：修好 L3.1 不代表 I3 闭合，反之亦然。

### L3.2 Executor：Retry 不重试 `policy_denied`

- **不变量**：`RetryExecutor` 只对「瞬时」执行失败重试；Policy 级拒绝
  （确定性拒绝）不消耗重试预算。
- **判断依据（冻结）**：`ToolResult.metadata["error_class"]`
  - `"policy_denied"` → 不重试（唯一冻结值）
  - 其他值 / 无值 → 按默认语义重试（保守，不破坏 V2 语义）
- **已废弃**：`metadata["blocked"]` 这类弱类型判断。
- **实现位置**：`agentkit/executor/retry.py::RetryExecutor._execute_one`；
  产生侧 `agentkit/executor/permission.py::blocked_result`
- **门禁**：

  ```bash
  python -m pytest tests/unit/executor/test_permission_retry.py -q
  ```

  用例：`test_policy_denied_does_not_consume_retry_budget`、
  `test_undefined_error_class_keeps_the_default_retry_semantics`、
  `test_missing_error_class_keeps_the_default_retry_semantics`、
  `test_permission_executor_emits_the_frozen_error_class`。
- **落地状态（2026-09-19 实测）**：**已落地并绿**（4 passed）。

### L3.3 Provider：Adapter Normalized Delta（流式增量必须归一化）

- **不变量**：Provider SDK 的原始 delta 必须由 `models/*.py` 归一化成
  `TextDelta` / `ToolCallDelta` 两类之一；Harness 永远看不到 provider-specific delta；
  且 `args_delta` 在 JSON / UTF-8 中间分片、断流、多 call 交错时仍能闭合组装。
- **实现位置**：`agentkit/models/base.py`（`Delta` 契约）、
  `agentkit/models/{openai,anthropic,ollama}.py`（各 Adapter 的归一化）
- **门禁（离线，不打真机 marker）**：

  ```bash
  python -m pytest tests/conformance/test_malformed_stream.py -q
  ```

- **真机补充证据**（需要 key / 本地服务）：

  ```bash
  python -m pytest tests/conformance -m conformance -v
  ```

---

## 已登记缺口（诚实清单）

写在这里等于承认「契约已冻结、证据还没齐」。每条都必须指出归属层与收敛条件。

| # | 缺口 | 层 | 现状（2026-09-19 实测） | 收敛条件 |
|---|---|---|---|---|
| G1 | ~~**I3 对内置 `Transform` 不成立**~~ → **已收敛（V3.1 修复 ③，Commit B `eb967cd`）**。收敛证据：`tests/property -q` → 26 passed；实现为 `transform.py::_trim_leading_orphan_tools()`（先清孤儿前缀再补当前任务）。**历史记录**（修复前）：两条可执行证据：① `python -m pytest tests/property -q` → `AssertionError: I3 (transform closure) violated by BudgetTransform: I1: sequence starts with a tool message (tool_call_id='call_0_0')`（`SlidingWindowTransform` 同）；② 手写复现：`SlidingWindowTransform(2)` 作用于合法序列 `[user, assistant(A,B), tool(A), tool(B), user]` 返回 `[tool(B), user]` | Layer 2 | 代码级 + 性质测试双重确认红 | 修裁剪边界（**只许整块裁剪**：丢弃 `assistant(tool_calls)` 时必须连同它声明的全部 tool 结果一起丢），或走 Contract Change Review 收窄 I3 |
| G2 | I4 在「批内停止」路径上已知违反：`DefaultRuntime.observe()` 用 `zip(..., strict=False)` 少写结果，`assistant(tool_calls=[A, B])` 后只写 `tool(A)`，`tool(B)` 永久缺失 | Layer 2 | 无用例；语义是 V3 §3.8 刻意保留（批内停止优先于结果完备） | 登记为观察期信号（照 `docs/signals/TEMPLATE.md` 写进 `docs/signals/NNNN.md`）；若真机复现 → V4 候选（`docs/v4/ENTRY_CRITERIA.md` + Contract Change Review） |
| G3 | ~~Layer 3 Context 的当前任务不变量无保障~~ | Layer 3 | ✅ **已收敛**：Commit B 落地（`_current_task_index` + `_kept_with_current_task`），`python -m pytest tests/unit/context -q` 全绿 | — |
| G4 | ~~`tests/property/` 报 `HealthCheck.differing_executors`，检查器自检也红~~ | Layer 2 测试基础设施 | ✅ **已收敛**：`python -m pytest tests/property/test_message_sequence.py -q` 全绿；`tests/property` 的红项已能唯一归因到 G1 | — |

> 记缺口不是认输，是让「契约已冻结、证据未齐」这件事**在仓库里有名字**。
> V3.1 的收敛判据是「红项都在本表里」，不是「无脑全绿」——隐藏红项才是问题。

---

## 一条命令跑完整层

```bash
# Layer 1
python -m pytest tests/test_abi_drift.py tests/test_architecture_firewall.py tests/test_api_boundary.py -q
python -m pytest tests/unit/test_composition.py -q -k kernel_bytes

# Layer 2（tests/property 当前含 G1 的红项：根因见上）
python -m pytest tests/unit/test_history_pairing.py -q
python -m pytest tests/property/test_message_sequence.py -q
python -m pytest tests/property/test_transform_protocol.py -q

# Layer 3
python -m pytest tests/unit/context -q
python -m pytest tests/unit/executor/test_permission_retry.py -q
python -m pytest tests/conformance/test_malformed_stream.py -q
```

真机 Conformance 与全量离线回归见 `CONTRIBUTING.md` 的「门禁怎么跑」。
