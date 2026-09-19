# Internal Message Sequence Contract（内部消息序列契约）

> **Message Protocol Contract 会成为继 Kernel ABI 之后第二个长期冻结资产。**
> **它必须比普通测试更谨慎。**
>
> —— V3.1 审查结论，本文件的立场声明。

- **冻结版本**：V3.1
- **冻结日期**：2026-09-19
- **契约范围**：`list[Message]` 序列的**结构合法性**（角色依赖 / 调用身份 / 变换闭合 / 结果顺序 / id 唯一）
- **不覆盖**：单条 `Message` 的字段形状（那属于 Kernel ABI）、Provider wire format（那属于 Provider Adapter）

---

## 一、定位

这是 AgentKit **内部**消息生命周期契约。它约束的是「一条消息序列在 AgentKit 内部流动时，
必须始终满足什么」，不是「某个厂商的 API 要求什么」。

### 1.1 层次关系

```text
┌─────────────────────────────────────────────┐
│  Kernel Message Types                       │   agentkit/kernel/types.py
│  Message / ToolCall / ToolCalls / ToolResult│   ABI Freeze（docs/freeze/v3/ABI.md §1.2）
└───────────────────┬─────────────────────────┘
                    │  形状：字段名 / 类型 / 顺序 / 默认值
                    ▼
┌─────────────────────────────────────────────┐
│  Internal Message Sequence Contract         │   docs/contracts/message_protocol.md
│  I1 / I2 / I3 / I4 / I5                     │   Contract Freeze（V3.1 起）
└───────────────────┬─────────────────────────┘
                    │  语义：一组 Message 摆在一起时的不变量
                    ▼
┌─────────────────────────────────────────────┐
│  Provider Adapter                           │   agentkit/models/*.py
│  把内部序列翻译成 provider-specific 报文     │   每 Provider 独立
└───────────────────┬─────────────────────────┘
                    │
                    ▼
┌─────────────────────────────────────────────┐
│  External Provider Protocol                 │   OpenAI / Anthropic / Ollama / DeepSeek
│  各自有各自语义                              │   各自独立
└─────────────────────────────────────────────┘
```

四层各自冻结自己的东西，**不得互相代偿**：

| 层 | 冻结什么 | 谁承担 | 门禁 |
|---|---|---|---|
| Kernel Message Types | 单条消息的**形状** | Kernel | `tests/test_abi_drift.py` |
| **本契约** | 序列的**结构不变量** I1–I5 | 所有产生 / 变换序列的代码 | `tests/property/`、`tests/unit/test_history_pairing.py` |
| Provider Adapter | 内部序列 → wire format 的**映射** | `agentkit/models/*.py` | `tests/conformance/`（真机） |
| External Provider | 厂商语义 | 厂商 | 不在 AgentKit 控制范围内 |

### 1.2 关键约束（本契约的边界）

> AgentKit 只冻结**内部序列契约**。Provider 语义差异（`system` 消息位置、
> `empty content` 是否允许、`parallel` 的语义等）全部由 Adapter 承担。
> **本契约不承诺跨 Provider 一致性。**

理由：一旦把本契约写成「跨 Provider 契约」，未来只要发现 Anthropic 对某种序列的语义与
OpenAI 不同，就得回头改本契约。**那不是本契约的失败，是本契约定位错了。**

因此：

- 本契约**只**描述内部序列（`list[Message]`）必须满足的结构不变量；
- Adapter 有义务把满足 I1–I5 的序列翻译成各厂商能接受的报文；
- 若某 Provider 在 Adapter 层有额外要求（例如「某厂商不接受多个 system 消息」），
  那是 **Provider Adapter Contract** 的一部分，写在 `agentkit/models/*.py`，**不写进本契约**。

---

## 二、版本策略

**与 Kernel ABI 一样：一旦冻结就是冻结的。**

```text
本契约（V3.1 冻结）：
  - 冻结后用「增补」而不是「改写」的方式演进；
  - 已发布的 invariant 的语义（尤其范围限定）不得被重新解释；
  - 任何变更（新增 / 修改 / 删除 invariant）都必须走独立的
    Contract Change Review，而不是随某个 feature 一起合并。
```

### 2.1 新增 invariant 的三个前置条件

任何新增 invariant，三条**同时**满足才允许进入 Contract Change Review：

1. **必须有真实信号支撑** —— 来自 `docs/signals/NNNN.md` 的真机记录
   （照 `docs/signals/TEMPLATE.md`），不是理论推演
   （对齐 `docs/v4/ENTRY_CRITERIA.md` 第 1 条）；
2. **必须能写成 property test** —— 无法自动验证的不变量是口号，不是契约
   （对齐本文件 §四的验证方式）；
3. **必须走独立的 Contract Change Review** —— 不能塞进一个功能性 PR。

### 2.2 为什么只有五条

> 契约不是越多越好。每多一条，就要多一个测试，多一次维护。
> **五条覆盖了 V1 → V3 真机上暴露的所有序列问题。**

已经暴露但**刻意不写成 invariant** 的：

| 现象 | 为什么不写成 invariant |
|---|---|
| 某 Provider 不接受连续两条 `system` | Provider 语义差异 → Adapter 承担 |
| 某 Provider 不接受 `content == ""` 的 assistant 消息 | Provider 语义差异 → Adapter 承担 |
| 历史长度 / 预算 | 算法（`ContextTransform` 的策略），不是结构不变量 |
| 消息内容质量 / 工具结果格式 | 不在「序列结构」的定义域内 |

---

## 三、Invariants（I1–I5）

约定：

```text
Valid(messages)  ⇔  messages 同时满足 I1、I2、I4、I5
                     （I3 是「Valid 在 Transform 下封闭」的元性质）
Transform T      ⇔  agentkit.api.ContextTransform
                    async apply(messages: Sequence[Message]) -> list[Message]
```

### I1. Role Dependency

```text
sequence 不以 tool 消息开头。
任何 tool 消息必须有前置 assistant(tool_calls)。
```

即：`messages[0].role != "tool"`；且对任意 `i`，若 `messages[i].role == "tool"`，
则存在 `j < i`，`messages[j].role == "assistant"` 且 `messages[j].tool_calls` 非空。

（「这个宿主必须是**最近**的一条 assistant」由 I2 + I4 共同表述：I2 管 id 归属，
I4 管「在下一个 assistant 之前全部出现」。I1 只管「有没有宿主」。）

**为什么**：`tool` 消息在语义上是「对某个 assistant 声明的调用结果的回答」，
没有宿主就不具备可解释性。OpenAI / DeepSeek 直接 400：

```text
Messages with role 'tool' must be a response to a preceding message with 'tool_calls'
```

### I2. Call Identity Preservation

```text
∀ tool 消息 m：
    ∃ assistant 消息 a，其 tool_calls 包含 m.tool_call_id。
```

即：任何 `tool` 消息的 `tool_call_id` 必须**精确匹配**某条前置
`assistant(tool_calls)` 中声明的 `ToolCall.id`；不得指向未声明的 id、不得为空。
反过来不要求：assistant 声明了调用却没有对应结果是 I4 的管辖范围（那边是**非法**）。

**为什么**：工具结果只按 id 归属；id 丢失或不匹配 → 结果无法回到发起它的调用，
Provider 侧表现为「未知 tool_call_id」错误。

### I3. Transform Closure

```text
∀ Transform T，若 Valid(messages)：
    Valid(T(messages))
（所有 Transform 必须满足）
```

即：合法序列经过任何 `ContextTransform` 之后**仍然合法**。
`Transform` 只被允许做「保持结构闭合的裁剪」：

- 可以丢弃整块 `assistant(tool_calls) + 其全部 tool 结果`；
- 不可以丢弃其中一半（丢 `assistant` 留 `tool`，或丢部分 `tool` 留其余）；
- 不可以只留尾部窗口而不修复边界。

**为什么**：Transform 是 Context 层唯一的「序列改写点」。I1/I2/I4/I5 在进入
Transform 之前成立是**产生侧**的责任；Transform 之后仍然成立是**变换侧**的责任。
没有 I3，任何一次压缩都可能把合法输入变成非法报文。

**注意**：I3 是**元性质**，它的验证方式是「对所有 Transform 实现跑性质测试」
（本文件 §四），而不是在某一段实现里断言。

### I4. Tool Result Ordering

```text
若 assistant 消息声明 tool_calls = [A, B, ...]：
    在下一个 assistant 消息出现之前，
    所有 A、B、... 的 tool result 必须全部出现。
```

**合法**（声明顺序）：

```text
assistant(tool_calls=[A, B])
tool(A)
tool(B)
assistant(next)
```

**合法（顺序可交换）**：

```text
assistant(tool_calls=[A, B])
tool(B)
tool(A)
assistant(next)
```

B 在 A 之前出现**不算违反**：本 invariant 冻结的是「全部出现且不晚于下一个 assistant」，
不冻结批内顺序（顺序由 Executor 决定：`ParallelExecutor` 归位、`SequentialExecutor` 按声明序）。

**非法**（B 缺失 + 迟到）：

```text
assistant(tool_calls=[A, B])
tool(A)
assistant(next)          ← B 缺失
tool(B)                  ← 迟到（已经跨过了下一个 assistant）
```

**非法**（全部缺失）：

```text
assistant(tool_calls=[A, B])
assistant(next)          ← 都没有
```

**为什么**：Provider 把「assistant 声明的调用」与「随后的结果」作为一个事务处理。
一个调用没有结果就进入下一轮，等于**悬挂事务**；迟到的结果又会以 I2 的形式
指向一条已经不属于当前窗口的声明。

### I5. Call ID Uniqueness

```text
在一次 history 内，所有 tool_call_id 唯一。

范围限定：单次 history（ctx.messages）。
不是 session 全局。
```

**为什么范围只到单次 history**：

> `history` 可能被截断。截断后，不同轮的 id 可以重复。

Kernel 不生成、不校验 call id（id 由 Provider 返回，Adapter 归一化），
因此不存在「全局 id 注册中心」这种保障；把范围写成 session 全局会立刻与
「history 可以截断」这一已冻结事实冲突（`ContextEngine._history_tail()` 的
存在本身就是截断合法的证据）。

---

## 四、验证

```text
tests/property/                    —— property-based（hypothesis）
  ├── message_sequence.py          —— Valid() 的判定器 + valid_message_history() 生成器
  │                                   + TransformProtocolTestBase（I3 的测试基类）
  ├── test_message_sequence.py     —— 判定器 / 生成器自检（手写合法与非法序列）
  └── test_transform_protocol.py   —— 每个内置 Transform 一个子类（I3 closure）

tests/unit/test_history_pairing.py —— 产生侧的历史截断边界（I1/I2 的回归测试）
```

**规则：所有 `ContextTransform` 实现（内置与第三方）都必须继承
`TransformProtocolTestBase`，由它携带 I3 的性质测试。**

```python
from tests.property.message_sequence import TransformProtocolTestBase


class TestBudgetTransform(TransformProtocolTestBase):
    def make_transform(self):
        return BudgetTransform(max_tokens=100)
```

未来任何新的 Context 类能力（`MemoryCompactor` / `RAGInjector` /
`ConversationSummarizer`）同样必须继承该基类 —— **没有性质测试的 Transform
不得进入 `context/`**。

运行：

```bash
python -m pytest tests/property -q                       # I3 closure 全量
python -m pytest tests/property/test_message_sequence.py -q   # 判定器 / 生成器自检
```

**当前状态（2026-09-19 实测）**：`tests/property` **全绿**（26 passed）。
其中 I3 的两条用例曾经是红的 —— 那是内置 `Transform` 裁剪边界的**真实违反**，
按 V3.1「两段提交」纪律修复（Commit A `a7689f6` 红 → Commit B `eb967cd` 绿），
过程与证据见 §5.4。

---

## 五、与已有实现的关系

本节是**诚实清单**：逐条说明 I1–I5 今天由哪段代码保障、哪条由测试保障、
以及**哪些目前没有保障**。没有保障的必须写明，不得粉饰。

| Invariant | 今天的实现保障 | 门禁 / 测试 | 结论 |
|---|---|---|---|
| I1 | 两处 seam 都已覆盖：`ContextEngine._history_tail()`（history 截断）与 `context/transform.py::_trim_leading_orphan_tools()`（Transform 裁剪） | `tests/unit/test_history_pairing.py`（4 passed）+ `tests/property`（26 passed） | **部分保障**（产生侧的批内停止仍会少写结果，属 I4 管辖） |
| I2 | 产生侧：`DefaultRuntime.observe()` 以 `tool_call_id=call.id` 写回结果；Executor 强制 `ToolResult.tool_call_id == 输入 call.id`（ABI §1.1） | `tests/unit/test_history_pairing.py`、`tests/test_abi_drift.py` | **部分保障**（消费侧无运行时守卫） |
| I3 | **已收敛（V3.1 修复 ③）**：`BudgetTransform` / `SlidingWindowTransform` 先清孤儿前缀（`_trim_leading_orphan_tools()`）再补当前任务，顺序不可颠倒 | `tests/property/test_transform_protocol.py`（实测 26 passed） | **有保障**（对四个内置实现；新实现必须继承 `TransformProtocolTestBase`） |
| I4 | **无保障**，且有一处**已知违反**（见 §5.2） | 无（产生侧无用例） | **无保障** |
| I5 | **无保障**：`ToolCall.id` 由 Adapter 从 Provider 响应归一化（`agentkit/models/*.py`），Kernel 不校验唯一性 | 无 | **无保障** |

### 5.1 I1 —— `ContextEngine._history_tail()` 如何服务本契约

```text
ContextEngine.build(ctx)
  → system 消息（ctx.system + provider system）
  → history = ctx.messages[-history_limit:]      ← 截断发生在这里
  → provider 非 system 消息
  → transform.apply(messages)（可选）
```

裸的 `ctx.messages[-N:]` 会把 `assistant(tool_calls)` 与它随后的 `tool` 结果
**从中间切开**，报文以孤儿 `tool` 开头 —— 这正是 I1 禁止的形状，真机上
OpenAI / DeepSeek 直接 400。`_history_tail()` 的修复方式是丢掉开头连续的
孤儿 `tool` 消息（它们失去了宿主，本就无法解释）。

**它的边界**：这修的是「history 截断点」这一处。I1 在 **Transform 之后**是否成立
属于 I3 的管辖范围 —— V3.1 修复 ③ 把同一规则（清孤儿前缀）搬进了
`context/transform.py::_trim_leading_orphan_tools()`，两个截断类 Transform 都调用它，
并由 `tests/property/` 持续验证。

### 5.2 I4 —— 今天的一处**已知违反**

`DefaultRuntime.observe()`：

```python
# strict=False 是刻意的：批内 stop 会让 results 短于 calls（§3.8），
# 已开始的 call 才写回消息。
for call, res in zip(action.calls, results or (), strict=False):
    ctx.messages.append(Message("tool", text, tool_call_id=call.id))
```

`assistant(tool_calls=[A, B])` 若在 A 之后触发批内 `stop`，只会写回 `tool(A)`，
`tool(B)` **永久缺失** —— 即 I4 的第二种非法形状。

这是 V3.1 **刻意保留**的行为（批内停止优先于结果完备，属于 §3.8 的封版语义），
因此本文件不假装它已被修复：**I4 在「批内停止」这条路径上今天是已知违反**，
登记为观察期输入（`docs/signals/`），而不是就地改语义。

### 5.3 Provider 差异落在哪一层

Provider 语义差异（`system` 位置、`empty content`、`parallel` 语义等）**全部**
由 `agentkit/models/*.py` 承担，逐条对应关系：

| Provider | Adapter | 承担的差异（举例） |
|---|---|---|
| OpenAI | `agentkit/models/openai.py` | `tool` 消息 → `{"role": "tool", "tool_call_id": ...}`；`assistant.tool_calls` 的 wire 展开 |
| DeepSeek | `agentkit/models/deepseek.py` | 复用 `OpenAIModel` 的 normalization，只换 base_url / 预设 |
| Anthropic | `agentkit/models/anthropic.py` | `system` 不在 messages 里（提到顶层 `system` 参数）；`tool_result` 是 content block |
| Ollama | `agentkit/models/ollama.py` | 本地 HTTP 语义 |

`DeepSeekModel` 是 `OpenAIModel` 的别名预设（函数，不是子类），因此没有第二份
normalization —— 真机证据把它们算作**同一条 path**（见 `tests/conformance/README.md` 的 Gate A）。

**因此**：某 Provider 对某种序列的额外要求，应当在对应 Adapter 里解决；
本契约不因为 Adapter 的存在而收窄或放宽 I1–I5。

### 5.4 I3 曾经的真实违反 —— 已修（两段提交留痕）

第一次跑性质测试（`tests/property` 落地时）结果：

```text
AssertionError: I3 (transform closure) violated by BudgetTransform:
                I1: sequence starts with a tool message (tool_call_id='call_0_0')
AssertionError: I3 (transform closure) violated by SlidingWindowTransform:
                I1: sequence starts with a tool message (tool_call_id='call_0_0')
```

**这不是「测试写错了」，是契约的真实缺口**：`SlidingWindowTransform` 只按条数取尾部、
`BudgetTransform` 只按预算取连续后缀，两者都不认 `assistant(tool_calls)` +
它的 tool 结果的**批次边界**，于是窗口开头留下失去宿主的孤儿 `tool` 消息。

修复（V3.1 修复 ③，严格两段提交）：

| | |
|---|---|
| Commit A | `a7689f6` 测试基础设施落地，**保持红**（红本身就是复现） |
| Commit B | `eb967cd` 新增 `_trim_leading_orphan_tools()`，两个截断类 Transform 在补当前任务**之前**清孤儿前缀 |
| 为什么顺序不可颠倒 | 先补当前任务会被它「挡住」孤儿，导致清洗提前停止 |
| 当前状态 | `python -m pytest tests/property -q` → **26 passed**（含 11 个判定器负例） |

对照（曾容易被误判）：V3.1 修复 ② 的**当前任务保活**（`_current_task_index()` /
`_kept_with_current_task()`）**不是**那两条红的原因 —— 单独并回一条 `user` 消息
不会破坏闭合性；破坏闭合性的是主体裁剪边界本身。

---

## 六、冻结日期

```text
2026-09-19（V3.1）

Kernel Message Types ：已冻结（V2.5 基线，V3 未动）
本契约（I1–I5）      ：自 V3.1 起冻结
```

---

## 七、变更流程（占位，V3.1 不做任何变更）

```text
Contract Change Review（新增 invariant 时）：
  1. 引用 ≥2 个独立信号（docs/signals/NNNN.md）
  2. 给出 property test 形状（可自动判定）
  3. 说明为什么现有五条覆盖不了
  4. 独立提交，不与其他改动混合
```

V3.1 期间本契约**不做任何变更**；观察期只记录信号，不改本文件
（对齐《V3.1 维护 + 观察期 实施方案》§3.2.2「不修、不重构、不抽象」）。
