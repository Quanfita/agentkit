# Contributing to AgentKit

AgentKit 的改动规则比多数项目更严，原因只有一个：**Kernel 是不可替换的控制流，
它上面盖了三层长期冻结资产（Kernel ABI / Internal Message Sequence Contract /
Provider Adapter Contract）。** 任何一次「顺手改一下」都可能证伪 V3 的命题。

先读这三份文件再动手：

| 文件 | 内容 |
|---|---|
| `docs/freeze/v3/ABI.md` | Kernel ABI 冻结清单（V3 期间不得变更） |
| `docs/contracts/message_protocol.md` | 消息序列契约 I1–I5（V3.1 起冻结） |
| `docs/INVARIANTS.md` | 不变量 → 可执行门禁索引 |

环境：

```bash
pip install -e ".[dev]"      # pytest / anyio / ruff / hypothesis
```

---

## 一、Correctness 修复：必须两段提交

**一个 correctness 修复必须拆成两次提交，且顺序固定：**

```text
Commit A：加复现测试          —— 期望 fail
Commit B：修复                —— 期望 pass，且 Kernel SHA256 不变
```

**禁止一次提交同时加测试和修复。**

### 1.1 Commit A（复现测试）

- 只在 `tests/**` 下新增 / 修改，**不动任何实现**；
- 提交信息里写下你**实际观察到的红**（命令 + 失败断言），例如：

  ```text
  test(executor): 复现 policy_denied 被重试（红）
  $ python -m pytest tests/unit/executor/test_permission_retry.py -q
  -> 1 failed: assert len(attempts) == 1  (actual: 3)
  ```

- 复现测试的判据是**行为**，不是实现：断言用户能观察到的东西
  （尝试次数、返回值、最终序列），不断言内部调用、字段拷贝、日志文本。

### 1.2 Commit B（修复）

- 只改实现；
- 提交前必须自证两条：

  ```bash
  python -m pytest tests/unit/executor/test_permission_retry.py -q   # 新用例转绿
  python -m pytest tests/unit/test_composition.py -q -k kernel_bytes # Kernel SHA256 不变
  ```

### 1.3 为什么必须两段

> 一次提交同时写测试和修复，等价于**让测试跟着实现写**。

一起写的时候，你心里已经有实现方案了，测试会不自觉地被写成「实现目前的行为」，
而不是「需求规定的行为」——它永远 pass，却什么都没证明。

先写 Commit A，是在**不知道修法**的情况下把期望固化下来，并且留下
「这条用例确实能抓住这个缺陷」的证据：红的输出就是证据。没有红过的测试，
不知道它防的是什么。

对崩溃 / 安全 / 数据损坏类缺陷，这条是硬要求；对纯文档改动不适用。

### 1.4 参考先例（V3.1 的两个修复，照抄这个形状）

```text
2758dc3  test(v3.1): 修复 ① 的复现测试（Commit A，故意保持红）
bd444af  fix(v3.1):  policy_denied 不消耗重试预算（修复 ① Commit B）

742e23b  test(v3.1): 修复 ② 的复现测试（Commit A，故意保持红）
b4af436  fix(v3.1):  当前用户任务不再被截断类 transform 丢弃（修复 ② Commit B）
```

自查：`git log --oneline` 里每个 correctness 修复都应当看到「test → fix」这一对，
且 test 提交的信息里写着当时观察到的红。

---

## 二、Changed Files 政策

权威文件：**`docs/v3_1/CHANGED_FILES_POLICY.yml`**（唯一真源；不要在 CI 配置里
另抄一份规则）。可执行门禁：`scripts/audit_changed_files.py`。

### 2.1 三级语义

| level | 含义 | CI 行为 |
|---|---|---|
| `forbidden` | 绝对禁区（当前唯一一档是 `kernel/**`） | **fail PR**（`action: fail_immediately`），不给人批准的空间 |
| `warning` | 需要人看的地方（`runtime/**` / `agent.py` / `api/**` / `harness/**`，以及其他未列出的路径 = `default`） | **需人工批准**，打印 `Changed files audit: <path> requires review` |
| `allowed` | 已登记本次变更且在预期内的路径（`tests/**` / `docs/**` / `executor/retry.py` / `context/engine.py` …） | pass |

退出码：

```text
0  = 全部 allowed
1  = 存在 forbidden（forbidden 优先于 warning）
2  = 存在 warning（需人工评审；不等于失败，但 PR 不能自动合并）
```

### 2.2 为什么 warning 区域最危险

> **架构漂移通常不发生在 `kernel/`。**
> 它发生在 `runtime/`、`agent.py`、`harness/` ——「我只是顺便调整一下」。

`kernel/` 已经被 Architecture Firewall 与 ABI Freeze 双重锁死，改它几乎不可能悄无声息。
真正会长成「第二内核」的是那些**看起来只是接线**的地方：

| 路径 | 典型漂移方式 |
|---|---|
| `agentkit/agent.py` | 把「编排逻辑」从 Runtime 搬进 Agent → 事实上的第二个控制流 |
| `agentkit/runtime/**` | 在 Runtime 里内联策略判断（预算、压缩、重试分支） |
| `agentkit/harness/**` | 在 Harness 里做本该由能力层做的事（记忆检索、工具选择） |
| `agentkit/api/**` | 给扩展 Protocol 加方法 → 静默扩大第三方契约面 |

因此这些区域**默认需要人工评审**：不是为了拦人，是为了让「顺手调整」
在 PR 里显式出现一次，而不是在半年后以「第二内核」的形式出现。

### 2.3 怎么跑

```bash
# 常见：相对某个基线（V3 封版提交）审计整个工作树
python scripts/audit_changed_files.py --base b61a739

# 指定一批路径（用于本地快速自检 / CI 传入 PR 的文件列表）
python scripts/audit_changed_files.py --paths agentkit/agent.py docs/notes.md

# 只看结论行
python scripts/audit_changed_files.py --base b61a739 | tail -1
# -> Changed files audit: <N> file(s) — allowed=<N> warning=0 forbidden=0
```

实现细节（也在该脚本的 docstring 里）：

- 变更集合 = `git diff --name-only <base>` **∪** 未跟踪文件 —— 新文件不许绕过审计；
- 匹配优先级：**精确路径优先，其次最长 glob**，都不中落 `default`；
- 政策里的路径有「仓库根相对」（`tests/**`）与「`agentkit/` 相对」（`kernel/**`、
  `agent.py`）两种书写基准，两者都作为候选参与匹配。

分类器本身的回归测试：

```bash
python -m pytest tests/test_changed_files_audit.py -q
```

---

## 三、门禁怎么跑

全部命令在仓库根目录执行。默认 `addopts = -q -m "not conformance"` ——
真机 Conformance 需要显式 `-m conformance` 覆盖。

### 3.1 Kernel ABI drift

```bash
python -m pytest tests/test_abi_drift.py -q
```

校验 `运行中的 kernel ABI == docs/freeze/v2/scratch.py == docs/freeze/v3/scratch.py`
（类 / 方法 / 参数名 / 返回类型 / dataclass 字段顺序 / 别名 / 扩展 Protocol 形状）。
**任何 ABI 变更（含「只是加个可选参数」）都会让它变红**；V3 期间不允许。

### 3.2 Architecture Firewall（五条规则）

```bash
python -m pytest tests/test_architecture_firewall.py -q
```

规则：kernel 无第三方 import / kernel 不 import 非 kernel 模块 / kernel 无
`*Manager` 等类名与能力实现 / `kernel/` 文件集合固定 / `agent_loop ≤ 55` 行。

### 3.3 Kernel 字节指纹（Kernel SHA256 不变）

```bash
python -m pytest tests/unit/test_composition.py -q -k kernel_bytes
```

`agentkit/kernel/*.py` 的内容指纹（LF 归一化后）必须与 V3 基线一致。
**这是「两段提交」里 Commit B 的第二条自证。**

### 3.4 API boundary（第三方只许 import `agentkit.api`）

```bash
python -m pytest tests/test_api_boundary.py -q
```

### 3.5 Property tests（消息序列契约 I1–I5）

```bash
python -m pytest tests/property -q                    # 完整性质套件（含 I3 closure）
python -m pytest tests/unit/test_history_pairing.py -q # I1/I2 的历史截断边界
```

**所有 `ContextTransform` 实现（内置与第三方）都必须继承
`tests/property/message_sequence.py::TransformProtocolTestBase`** —— 没有性质测试的
Transform 不得进入 `context/`。契约原文见 `docs/contracts/message_protocol.md`。

> 注意：V3.1 落地期间这套测试存在**已知红项**（当前 = `docs/INVARIANTS.md` §已登记缺口 G1：
> 内置 `Transform` 违反 I3 闭合性）。交付判据是「红项恰好等于缺口清单」，
> 不是「无脑全绿」——隐藏红项才是问题。

### 3.6 Changed Files Audit

```bash
python scripts/audit_changed_files.py --base b61a739
```

退出码语义见 §2.1。

### 3.7 真机 Conformance（Provider Adapter）

```bash
# 真机矩阵（需要 key / 本地 Ollama）
python -m pytest tests/conformance -m conformance -v

# 离线部分（fake provider，不打 marker）
python -m pytest tests/conformance/test_malformed_stream.py -q
```

无 key 不会失败：对应 Provider 全部记 `not_verified` 并 skip。
证据落盘在 `docs/conformance/<timestamp>.json` 与 `docs/CONFORMANCE_REPORT.md`。
Gate 定义见 `tests/conformance/README.md`。

### 3.8 全量离线回归

```bash
python -m pytest
```

冻结基线是 **471 passed**（V3）；V3.1 只增不减。

**唯一的例外**是 `docs/INVARIANTS.md` §已登记缺口 里写明的红项（当前只有 G1：
内置 `Transform` 的 I3 闭合性）。**允许的红项集合必须恰好等于缺口清单** ——
清单外多一条红就是回归。

---

## 四、提交前的自检清单

```text
[ ] correctness 修复：Commit A（红）与 Commit B（绿）分开
[ ] python -m pytest tests/test_abi_drift.py tests/test_architecture_firewall.py -q  → 全绿
[ ] python -m pytest tests/unit/test_composition.py -q -k kernel_bytes             → 全绿
[ ] python -m pytest  → 除 docs/INVARIANTS.md §已登记缺口 列出的红项外全绿
[ ] python scripts/audit_changed_files.py --base <V3 封版 SHA>                   → 无 forbidden
[ ] 新增红项？→ 写进 docs/INVARIANTS.md 的「已登记缺口」
[ ] 顺手改了什么吗？→ 删掉
```

**收敛原则：修复完即停。无顺手改，无预防性重构，无「如果…就…」的抽象。**
观察期只记录，不修 —— 入口是 `docs/signals/README.md`，信号照 `docs/signals/TEMPLATE.md` 写；
聚类与 V4 判定在窗口结束之后，见 `docs/clusters/README.md` 与 `docs/v4/ENTRY_CRITERIA.md`。

---

## 五、文档地图

| 文件 | 什么时候读 |
|---|---|
| `docs/freeze/v3/ABI.md` | 动 `agentkit/kernel/**` 或 `agentkit/api/**` 之前 |
| `docs/contracts/message_protocol.md` | 动 Context / Transform / 消息序列 / Executor 结果写回之前 |
| `docs/INVARIANTS.md` | 想知道「这条约束由什么保证、跑哪条命令」时 |
| `docs/v3_1/CHANGED_FILES_POLICY.yml` | PR 里出现了 `runtime/**` / `agent.py` / `harness/**` 时 |
| `docs/signals/README.md` | 观察期（只记录，不修） |
| `tests/conformance/README.md` | 动 `agentkit/models/**` 或做真机验证时 |
