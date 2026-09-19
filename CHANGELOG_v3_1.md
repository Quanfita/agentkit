# V3.1 正式关闭（2026-09-19）

## 状态

- Kernel ABI: Frozen ✅
- Extension ABI: Stable ✅
- Message Contract: Frozen ✅
- Runtime Behavior: Observing ⏳
- V4 Claim: 空白（正确）

## 观察期

- 启动：2026-09-19
- 上限：2026-10-17
- 退出条件：见 §3.2.1

## 收口后的纪律

1. 观察期只记录，不修，不设计
2. 禁止隐形设计文档
3. 分类保守（Kernel 分类需三条件）
4. V4 Entry 需五项全满足

---

## V3.1（Maintenance Release）

> **V3.1 = 修复过去的正确性 + 建立第二层 ABI。**
>
> **Message Protocol Contract 会成为继 Kernel ABI 之后第二个长期冻结资产。它必须比普通测试更谨慎。**
>
> 观察期 = 只记录不分析，让真实使用产生 V4 命题；V4 Entry = 从信号里长，不从候选里选。

| 度量 | V3 | V3.1 | 门禁 |
|---|---|---|---|
| Kernel 6 文件 SHA256 | — | **与 V3 逐一相同** | Changed Files Audit `forbidden=0` ✅ |
| Kernel ABI | frozen | **frozen** | `test_abi_drift` 101 + `test_freeze_snapshot_v2` 44 全绿 ✅ |
| `agent_loop` | 52 行 | **52 行** | ≤55 ✅ |
| Architecture Firewall | 五条全绿 | **五条全绿** | ✅ |
| 测试 | 471 | **541 passed** | 全绿 ✅ |
| 真机 Conformance | DeepSeek + Ollama 各 6/6 | **同前（未降强度）** | ✅ |

---

## 一、修复（全部按「两段提交」纪律：Commit A 红 → Commit B 绿）

| # | 修复 | Commit A（红） | Commit B（绿） | 证据 |
|---|---|---|---|---|
| ① | **Permission × Retry**：`policy_denied` 不消耗重试预算 | `2758dc3` | `bd444af` | Commit A 复现 `assert 3 == 1`；修复后 attempts=1；**真机**：DeepSeek 请求删除文件 → 策略只被咨询 **1 次**（原 3 次），模型正确报告「被策略拦截」 |
| ② | **Budget × Current Task**：当前用户任务不被丢弃 | `742e23b` | `b4af436` | 3 条复现测试转绿（provider 注入吃满预算 / 紧预算 / 尾部窗口被注入占满） |
| ③ | **Transform I3 closure**：不输出孤儿 `tool` 前缀（性质测试发现） | `a7689f6` | `eb967cd` | `tests/property` 从「2 failed」到 **26 passed** |

**修复 ① 的冻结字段（M2）**：`ToolResult.metadata["error_class"]`，当前只冻结一个值
`"policy_denied"`（其他值/无值 → 默认重试语义，保守不破坏 V2）；V3 的弱类型
`metadata.blocked` 已废弃。`PermissionExecutor` 输出同时带 `policy`（额外信息，不参与判断）。

**修复 ② 的 invariant**：当前用户任务（`ctx.messages` 里最后一条 user 消息）在任何
transform 后必须存在 —— 这是 invariant，不是 algorithm。实现落在
`context/transform.py::_current_task_index()` / `_kept_with_current_task()`，
**同时覆盖 `SlidingWindowTransform`**（它是同一 invariant 的第二个实例：`build()` 的输出
是 system → history → provider 非 system，尾部窗口会被靠后的 provider 注入占满）。

**修复 ③ 的根因**：截断类实现只按条数/预算取连续后缀，不认 `assistant(tool_calls)` +
其 tool 结果的**批次边界** → 窗口开头留下失去宿主的孤儿 `tool` 消息（连带违反 I2）。
实现为 `transform.py::_trim_leading_orphan_tools()`，**必须**先清孤儿前缀再补当前任务
（顺序颠倒会让当前任务把孤儿"挡"在保留集里）。

**被 invariant 取代的 V3 断言（显式登记，不是"改测试迁就实现"）**：

- `SlidingWindowTransform(0)`：`[]` → `[当前任务]`
- `BudgetTransform` 超预算：只留 system → system + 当前任务
- `tests/unit/test_composition.py` 的 B1 紧预算用例同步

---

## 二、新增契约：Internal Message Sequence Contract（第二层 ABI）

`docs/contracts/message_protocol.md` —— 定位是**内部**消息生命周期契约：

```text
Kernel Message Types           （kernel/types.py，ABI Freeze）
      ↓
Internal Message Sequence Contract   （本文档，Contract Freeze ← V3.1 起）
      ↓
Provider Adapter               （models/*.py，Provider-specific）
      ↓
External Provider Protocol     （OpenAI / Anthropic / ...，各自独立）
```

**关键约束**：AgentKit 只冻结**内部序列契约**。Provider 语义差异（system 位置、
empty content、parallel 语义等）全部由 Adapter 承担；**本契约不承诺跨 Provider 一致性**。
理由：一旦写成"跨 Provider 契约"，未来若发现某 Provider 与 OpenAI 语义不同，就得改 Contract
—— 那不是 Contract 的失败，是 Contract 定位错了。

五条 invariant（I1 Role Dependency / I2 Call Identity Preservation / I3 Transform Closure /
I4 Tool Result Ordering / I5 Call ID Uniqueness）与**逐条实现保障对照**见
`docs/contracts/message_protocol.md` §五；不变量索引见 `docs/INVARIANTS.md`。

**为什么只有五条**：契约不是越多越好，每多一条就多一个测试、多一次维护；这五条覆盖了
V1→V3 真机上暴露的所有序列问题。新增 invariant 的三个前置条件：真实信号支撑 / 能写成
property test / 走独立的 Contract Change Review。

---

## 三、新增测试基础设施（I3 的可执行验证）

`tests/property/`：

- `message_sequence.py`：`valid_message_history()`（hypothesis 策略，只产合法序列）+
  `is_valid_message_sequence()`（I1/I2/I4/I5 全查，I4 区分「缺失」与「迟到」两种非法形态）+
  `TransformProtocolTestBase`（I3 的测试基类）
- `test_message_sequence.py`：**判定器自检** —— 11 个非法序列必须被拒、8 个合法序列必须被接受、
  生成器只产合法序列（防止"检查器/生成器本身写错"得出假绿）
- `test_transform_protocol.py`：四个内置 Transform 各一个子类

**所有 `ContextTransform` 实现（内置与第三方）都必须继承 `TransformProtocolTestBase`** ——
这条写进了契约，未来 MemoryCompactor / RAGInjector / ConversationSummarizer 同样受管。

---

## 四、新增门禁：Changed Files Audit（M5）

- `docs/v3_1/CHANGED_FILES_POLICY.yml`（唯一真源）+ `scripts/audit_changed_files.py`
- 三级语义：`forbidden`（kernel/**）→ exit 1 立即失败；`warning`（agent.py / runtime/** /
  api/** / harness/**）→ exit 2 需人工批准；`allowed` → exit 0
- 变更来源 = `git diff --name-only <base>` ∪ 未跟踪新文件（否则新文件绕过审计）
- **本轮自检**：`--base b61a739` → `29 file(s) — allowed=29 warning=0 forbidden=0`，exit 0
- 反向自检：`--paths kernel/loop.py` → exit 1；`--paths agentkit/api/kernel.py` → exit 2

为什么需要它：**架构漂移通常不发生在 `kernel/`**。它发生在 `runtime/`、`agent.py`、
`harness/` ——"我只是顺便调整一下"。所以最容易变成"第二内核"的区域是 warning。

---

## 五、Kernel / 回归

- **Kernel 6 文件 SHA256 与 V3 逐一相同**（`forbidden=0`，六道门禁全绿）
- ABI 不变：`test_abi_drift.py`（101）+ `unit/test_freeze_snapshot_v2.py`（44）
- Architecture Firewall 五条全绿；`agent_loop` 52 行（≤55）
- 离线测试 **541 passed**（V3 的 471 + 修复① 4 + 修复② 4 + 审计 36 + property 26）
- 真机 Conformance 未降强度：DeepSeek `deepseek-flash` 6/6 + Ollama `ornith:9b` 6/6
  （`docs/conformance/20260919T130851Z.json`）

---

## 六、遗留（观察项）

| 项 | 状态 |
|---|---|
| **I4 与 V3 §3.8 的张力**：`DefaultRuntime.observe()` 批内停止时用 `zip(..., strict=False)` 少写结果 —— `assistant(tool_calls=[A,B])` 后只写 `tool(A)`，`tool(B)` 永久缺失 | **观察项**：该行为是 V3 §3.8 刻意冻结的（批内停止优先于结果完备），V3.1 不就地改语义；登记在 `docs/INVARIANTS.md` 缺口 G2，若真机复现 → V4 候选 |
| infra vs Contract failure 区分 | V4 候选（瞬时故障会被 Conformance 记为 `fail`，与 Contract 违反不可区分） |
| `InteractivePolicy` 的 `'n'` 陷阱 | 已在 docstring 说明；**本轮明确不改**（观察期不做任何形式的"顺手修"） |
| I5 无门禁 | `ToolCall.id` 由 Provider 返回、Adapter 归一化，Kernel 不校验单次 history 内的唯一性 |
| I2 消费侧无运行时守卫 | 产生侧（`observe` + Executor 强制绑定）已保障 |

---

## 七、V4 候选（从 V3 继承）

- Retry 语义精细化（`retry_on` 谓词 或 `ExecutorDecision`）
- Infra 故障与 Contract 违反的可区分

---

## 八、观察期（V3.1 的另一半）

| | |
|---|---|
| 启动 | **2026-09-19**（V3.1 合并后立即） |
| 4 周上限 | **2026-10-17** |
| 退出条件 | A：≥3 个相同结构问题；B：≥5 个 S1/S2；C：需要新增 Contract 的候选问题且 ≥2 个独立信号支撑 |
| 只做 | 记录信号到 `docs/signals/NNNN.md`（照模板） |
| 不做 | 不修 / 不重构 / 不抽象 / 不预防性设计 / 不分类 / 不趋势判断 |

脚手架已就位：`docs/signals/{README,TEMPLATE}.md`、`docs/clusters/{README,TEMPLATE}.md`、
`docs/v4/{ENTRY_CRITERIA,PROPOSAL_TEMPLATE}.md`（后者含 M6 的 Why now 与 Rejected Alternatives）。

**当前状态**：窗口开启且尚未达到退出条件 → 阶段 4（聚类 + V4 判定）**未触发**，
产出模板而非结论。

---

## 收口审查补充（2026-09-19）

收口审查提出三项修正，全部接受并落地（**不再扩展，不再设计**）：

### 1. Signal 0002 重新分类

`docs/signals/0002.md` 的 `Architectural implication` 改为保守分类：

- 只勾 `Context / Executor / Provider`；
- Kernel 列为「候选（**暂不勾选**，待更多信号）」，条件是：≥2 个独立 Provider 同类信号
  **且** 该字段需跨 turn 保留 **且** 该字段需参与 Contract；
- 在三条达成前不升级分类。

理由：Kernel 分类会直接触发 V4 Entry 的候选池，误标会让 V4 Entry 失去意义。

### 2. 禁止隐形设计文档（上一轮的真实漏洞）

`docs/signals/README.md` 新增两条纪律：

- **分类纪律**：越接近 Kernel 的问题越不能轻易标 Kernel；三条不满足时落在最保守的一层；
- **禁止隐形设计文档**：含目录白名单（允许 `docs/signals/`、
  `docs/signals/NNNN.md`、`docs/clusters/README.md`；禁止
  `docs/proposals/`、`docs/designs/`、`docs/rfc/`、`docs/architecture-notes/`、
  `docs/ideas/`、`docs/v4/*.md` 除 `ENTRY_CRITERIA.md` 与 `PROPOSAL_TEMPLATE.md`）。

上一轮只限制了「不写代码」，没有禁止「把设计冲动写进文档」—— 这是两个不同的东西。
判断标准：写下这句话时问自己 —— 这是「发生了什么」还是「应该发生什么」？
理由：**观察 ≠ 孵化**；写作冲动 → 文档 → 讨论 → 设计 → V4 提前启动。

### 3. V4 Entry Criteria 增加第 5 条 Persistence

`docs/v4/ENTRY_CRITERIA.md` 由「全部四项」改为「**全部五项**全满足」，第 5 条：

- 该问题必须表现为以下至少一项：a) 随能力增长重复出现；b) 阻碍组合能力增长；
  c) 导致 Contract 不可维护；
- 单点 bug 不进入 V4；
- 判断标准：「修一次就完了」→ V3.x；「每次加能力都要面对」→ V4 候选。

理由：Bug → V3.x patch；Pattern → V4。Persistence 是区分「噪声」与「信号」的关键过滤器。

**同步改动**：`docs/clusters/README.md` 的升级判据由四项改为五项；
`docs/clusters/TEMPLATE.md` 折叠进 `README.md`（`docs/clusters/` 只留 `README.md`）。

### 对齐说明

- 第 1–4 条正文**保持原样**（遵守「只加第 5 条，不改前 4 条」），仅标题计数
  「全部四项」→「全部五项」；文件末尾新增该条正文与判据行，未改动既有的
  「与三层防线的分工」段。
- 为使本文件只有一个一级标题，原 `# V3.1（Maintenance Release）` 降为二级标题；
  关闭声明块按原文逐字置于顶部。

