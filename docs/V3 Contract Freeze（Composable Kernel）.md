# V3 Contract Freeze（Composable Kernel）v2

> **V3 的命题：能力可以增长，组合复杂度可以增长，但 Kernel 不增长。**
>
> **V3 最应该留下的资产不是 `BudgetCompactor` 或 `PermissionExecutor`，而是：**
>
> - **Architecture Firewall**
> - **Public Extension API**
> - **Composition Test Suite**

本版本采纳评审的 6 项必改/强烈建议项，重写 V3 Contract Freeze。

---

## 〇、V3 的定位

```text
V1   ──►  Architecture Proof    Kernel 可以成立
V2   ──►  Evolution Proof       加 Executor + Streaming，Kernel 不膨胀
V2.5 ──►  Contract Proof        Contract 经真实 Provider 验证
V3   ──►  Composition Proof     多个独立能力同时组合，Kernel 不变
```

**验收标准不是功能数量，而是**：

> **扩展压力不会改变架构拓扑。**

---

## 一、V3 命题（Claim）

```text
A growing set of independently implemented capabilities
can be composed through the existing extension contracts
without modifying Kernel control flow or Kernel public ABI.

中文：
能力可以增长，组合复杂度可以增长，但 Kernel 不增长。
```

**关键**：不是 "Kernel LOC 不变"，而是 "Kernel 公共 ABI 不变"。这两者的区别贯穿全文。

---

## 二、Non-goals（明确不做）

```text
❌ ToolResult 多模态（content: Any）
❌ Event 对象化（emit(name, **kw) → emit(Event)）
❌ 任何 Manager 类（MemoryManager / PluginManager / ExecutorManager）
❌ Planner / RAG / Reflection / Multi-Agent
❌ Streaming 进入 Kernel
❌ Kernel 新增 Protocol
❌ Kernel 数据契约变更
❌ 修改 agent_loop（除非触发 Kernel Change Review 并通过）
```

**V3 期间 `kernel/` 的公共 ABI 必须与 V2.5 完全一致。**

---

## 三、Kernel ABI Freeze（Gate A 的核心）

### 3.1 ABI Freeze 的定义

从 V3 起，"Kernel 不变"的判据改为 **ABI Freeze**，不再是 LOC Freeze：

**冻结范围**：

```text
kernel.protocols：
  Model.generate / Tool.run / ToolProvider.tools+close
  Memory.recall+remember / ContextProvider.provide
  ToolExecutor.execute+close / Runtime.prepare+reason+act+observe+finish+close

kernel.types：
  Message / ToolCall / ToolCalls / ToolSpec / ToolResult
  MemoryItem / MemoryInput / ContextItem
  Final / Action

kernel.state：
  RunContext（全部字段名 + 类型）
  TerminationReason（全部枚举值）

kernel.events：
  EventBus.on / off / emit 签名
```

**冻结内容**：

- 类名
- 方法名
- 参数名 + 类型
- 返回类型
- dataclass 字段名 + 类型 + 顺序
- 生命周期语义
- 异常语义

**不冻结内容**：

- docstring 长度
- 内部实现
- 私有属性
- `__repr__` 格式

**LOC 只作为观察指标，不作为门禁**（除 `agent_loop ≤ 55`）。

### 3.2 ABI 冻结的强制机制

**`docs/freeze/v3/scratch.py`** + **`tests/test_abi_drift.py`**：

```python
# test_abi_drift.py
def test_kernel_abi_matches_freeze_snapshot():
    """V3 Kernel 公共 ABI 必须与 V2.5 freeze snapshot 一致。"""
    # 通过 inspect.signature / __dataclass_fields__ / enum 遍历
    # 与 docs/freeze/v2/scratch.py 的签名逐条比对
    ...
```

**门禁**：任何 ABI 变更（含"只是加个可选参数"）必须显式通过 `Kernel ABI Change` 流程。**V3 期间不允许**。

### 3.3 Kernel Change Review（如果必须改 ABI）

三问（同 V2.5 精神）：

```text
1. 为什么现有 extension point 无法承载？
2. 为什么必须改变 Kernel Control Flow / Data Contract？
3. 如果改变 Kernel ABI，新增的能力是否具有长期稳定的语义？
```

**三问不能答清就不准进 Kernel。** V3 期间改 ABI 等于宣告命题证伪。

---

## 四、Public Extension API（新增层）

### 4.1 为什么需要 `agentkit.api`

V2.5 的第三方实现契约是：

> 只依赖 `kernel.protocols` / `kernel.types` / `kernel.state`。

但这有一个内在矛盾：**新增的扩展 Protocol（`ContextTransform` / `SkillProvider` / `PermissionPolicy`）不属于 Kernel**。第三方实现它们的实现者无处可 import。

**V3 冻结**：引入 `agentkit.api` 作为**唯一面向第三方的公共扩展入口**。

### 4.2 目录结构

```text
agentkit/
├── kernel/                 # 内部实现，ABI 冻结
│   ├── types.py
│   ├── state.py
│   ├── events.py
│   ├── protocols.py
│   └── loop.py
│
├── api/                    # 第三方唯一入口
│   ├── __init__.py         # 重导出 kernel + 扩展 Protocol
│   ├── kernel.py           # re-export kernel.protocols / types / state
│   ├── context.py          # ContextTransform Protocol
│   ├── skill.py            # SkillProvider Protocol
│   └── executor.py         # PermissionPolicy Protocol
│
├── context/                # 实现
│   ├── engine.py
│   ├── providers.py
│   └── transform.py        # BudgetTransform / SlidingWindowTransform
│
├── skills/
│   ├── skill.py
│   ├── directory.py
│   └── mcp_backed.py
│
├── executor/
│   ├── builtin.py
│   ├── permission.py       # PermissionExecutor + AllowListPolicy
│   ├── retry.py
│   └── timeout.py
│
└── ...（runtime / tools / memory / models / harness 保持 V2.5 形态）
```

### 4.3 `agentkit.api` 的内容

**`api/kernel.py`**：

```python
"""Kernel 公共 ABI 的官方重导出。

第三方若需要 kernel 内的 Protocol / 类型，一律从 agentkit.api 导入。
直接 import agentkit.kernel.* 的代码会被 tests/test_api_boundary.py 拒绝。
"""

from agentkit.kernel.protocols import (
    ContextProvider, Memory, Model, Runtime, Tool, ToolExecutor, ToolProvider,
)
from agentkit.kernel.state import RunContext, TerminationReason
from agentkit.kernel.types import (
    Action, ContextItem, Final, MemoryInput, MemoryItem,
    Message, ToolCall, ToolCalls, ToolResult, ToolSpec,
)

__all__ = [...]
```

**`api/context.py`**：

```python
"""Context 扩展 Protocol。"""

from typing import Protocol, Sequence, runtime_checkable
from .kernel import Message


@runtime_checkable
class ContextTransform(Protocol):
    """消息序列的纯变换。

    严格语义：
      - 输入/输出都是 Message 序列
      - 不接收 RunContext（策略是纯函数）
      - 不访问外部世界（无 memory / 无 tool / 无 event）

    允许：过滤、截断、排序、去重、替换 content
    禁止：读取 ctx / 调用 LLM / 写 memory / 发 event
    """

    async def apply(self, messages: Sequence[Message]) -> list[Message]: ...
```

**`api/skill.py`**：

```python
"""Skill 扩展 Protocol。"""

from typing import Protocol, runtime_checkable
from ..skills.skill import Skill


@runtime_checkable
class SkillProvider(Protocol):
    """Skill 查询接口。

    只承担查询职责。同步 / 缓存 / 生命周期属于 Runtime startup hook，
    不属于 Provider Protocol。
    """

    async def search(self, query: str, limit: int = 3) -> list[Skill]: ...
```

**`api/executor.py`**：

```python
"""Executor 扩展 Protocol。"""

from typing import Protocol, runtime_checkable
from .kernel import RunContext, ToolCall


@runtime_checkable
class PermissionPolicy(Protocol):
    """执行前的权限判定。

    返回 True = 允许；False = 拒绝。
    拒绝行为由 PermissionExecutor 决定（见 V3 §六.4）。
    """

    async def allow(self, call: ToolCall, ctx: RunContext) -> bool: ...
```

### 4.4 `api/__init__.py`

```python
"""AgentKit 公共扩展 API。

第三方实现必须只依赖此模块。

禁止 import：
  agentkit.kernel.*（除本模块内部）
  agentkit.runtime / context / skills / executor / memory / tools / models
"""

from .context import ContextTransform
from .executor import PermissionPolicy
from .kernel import *    # noqa: F401,F403
from .skill import SkillProvider

__all__ = [
    # Kernel re-exports
    "Model", "Tool", "ToolProvider", "ToolExecutor", "Memory",
    "ContextProvider", "Runtime",
    "Message", "ToolCall", "ToolCalls", "ToolSpec", "ToolResult",
    "MemoryItem", "MemoryInput", "ContextItem",
    "Final", "Action", "RunContext", "TerminationReason",
    # Extension Protocols
    "ContextTransform", "SkillProvider", "PermissionPolicy",
]
```

### 4.5 API 边界的强制

**`tests/test_api_boundary.py`**：

```python
"""第三方实现只能 import agentkit.api。"""

FORBIDDEN_PREFIXES = (
    "agentkit.kernel.",
    "agentkit.runtime",
    "agentkit.context",
    "agentkit.skills",
    "agentkit.executor",
    "agentkit.memory",
    "agentkit.tools",
    "agentkit.models",
    "agentkit.harness",
)

def _scan(path):
    tree = ast.parse(path.read_text())
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            for prefix in FORBIDDEN_PREFIXES:
                assert not node.module.startswith(prefix), \
                    f"{path}: illegal import {node.module}"

def test_third_party_only_imports_api():
    for path in Path("tests/third_party").rglob("*.py"):
        _scan(path)
```

**这是 V3 生态命题的可执行证据。**

---

## 五、Architecture Firewall（负向 Contract）

### 5.1 五条规则（调整后）

```text
规则 1：kernel/ 里任何模块的 import 来源必须满足：
  - Python 标准库
  - agentkit.kernel.* 内部互相
  - 无第三方包

规则 2：kernel/ 里不允许 import agentkit 内任何非 kernel 模块：
  agentkit.api / agentkit.runtime / agentkit.context / agentkit.skills
  agentkit.executor / agentkit.memory / agentkit.tools / agentkit.models
  agentkit.harness / agentkit.observability / agentkit.cli / agentkit.contrib

规则 3（修订）：kernel/ 内不得定义以下命名模式的类：
  - *Manager
  - *Registry
  - *Factory
  - *Adapter（*Provider 除外，因为 ToolProvider / ContextProvider 是 Kernel 语义）
  且不得定义具体能力实现（如 BudgetCompactor / RetryExecutor 等）

  注：关键词扫描作为辅助，主判据是依赖方向（规则 1+2）。

规则 4：kernel/ 只能有 5 个文件 + __init__.py

规则 5：agent_loop <= 55 行
```

**关键调整**：从"关键词扫描"改为"依赖方向扫描为主，类名模式为辅"。避免误杀 `ModelProvider` 这样的合法命名。

### 5.2 落地

**`tests/test_architecture_firewall.py`**（用 `ast` 静态扫描）：

- `test_kernel_no_external_imports`（规则 1）
- `test_kernel_no_agentkit_non_kernel_imports`（规则 2）
- `test_kernel_no_manager_classes`（规则 3）
- `test_kernel_file_count`（规则 4）
- `test_agent_loop_line_count`（规则 5）

**V3 期间这五条测试必须始终全绿。** 任何一个变红，V3 命题证伪。

---

## 六、V3 的 6 个子能力（精确定义）

### 6.1 ContextTransform（替代原 ContextCompactor）

**Protocol**（`api/context.py`）：

```python
@runtime_checkable
class ContextTransform(Protocol):
    async def apply(self, messages: Sequence[Message]) -> list[Message]: ...
```

**关键设计**：

- **不接收 `ctx`**：策略是纯函数，不读取 Runtime 状态
- **只做 Message → Message**：不做 Message → Memory / Tool / Event
- **async 但不等待外部世界**：留 async 以允许未来微调，但当前实现不 await 任何外部资源

**实现**（`context/transform.py`）：

| 实现 | 作用 |
|---|---|
| `BudgetTransform(max_tokens, estimate=len)` | 超预算时保留 system + 尾部 N 条 |
| `SlidingWindowTransform(max_messages)` | 只保留最后 N 条 |
| `DedupeTransform()` | 相邻重复消息去重 |
| `SystemPriorityTransform()` | 保证 system 消息优先 |

**接入**（`ContextEngine`）：

```python
ContextEngine(
    providers=[...],
    transform=BudgetTransform(32_000),   # 可选
)
```

**位置**：`ContextEngine.build()` 的**最后一步**，对 provider 输出 + history 拼接后的完整 messages 应用。

**Kernel 改动**：**零**。

### 6.2 SkillProvider（去掉 refresh）

**Protocol**（`api/skill.py`）：

```python
@runtime_checkable
class SkillProvider(Protocol):
    async def search(self, query: str, limit: int = 3) -> list[Skill]: ...
```

**关键设计**：

- **只有 `search()`**
- **没有 `refresh()` / `close()`**：生命周期与同步属于 Runtime startup hook，不属于 Provider Protocol

**实现**（`skills/`）：

| 实现 | 作用 |
|---|---|
| `DirectorySkills(root)` | V2 已有，Protocol 化 |
| `MCPBackedSkills(session)` | 从 MCP server 拉 skill 列表 |

**同步策略**（V3 冻结）：

- 若需要 refresh，通过 `Runtime.startup_hook` 或 Harness 层做
- Provider 本身无状态或内部自行管理缓存
- **不污染 Protocol**

**Kernel 改动**：**零**。

### 6.3 PermissionPolicy + PermissionExecutor

**Protocol**（`api/executor.py`）：

```python
@runtime_checkable
class PermissionPolicy(Protocol):
    async def allow(self, call: ToolCall, ctx: RunContext) -> bool: ...
```

**关键设计**：

- Policy 通过**构造注入**，不通过 Executor 读 ctx 里的隐式状态
- 读 `ctx` 是通过**显式参数**，不是通过环境变量 / 单例

**实现**（`executor/permission.py`）：

| 实现 | 作用 |
|---|---|
| `AllowListPolicy(names)` | 只允许指定工具 |
| `DenyListPolicy(names)` | 拒绝指定工具 |
| `InteractivePolicy(prompt_fn)` | 询问用户（CLI 场景） |

**`PermissionExecutor` 的异常语义（V3 冻结）**：

```text
权限拒绝 = Executor policy failure，不是 tool failure。

返回：
  ToolResult(
      tool_call_id=call.id,
      content=f"[blocked by policy] {call.name}",
      error=True,                    ← 从模型视角是"不能做"
      metadata={"blocked": True, "reason": "permission"},
  )
```

**理由**：

- 从**模型视角**：这是一次失败的调用，模型应看到 `error=True` 并尝试其他方式
- 从**人类视角**：这不是工具崩溃，是策略拦截——通过 `metadata.blocked=True` 区分
- **V2.5 契约不变**：Executor 返回 ToolResult，不引入新的异常类型
- **V4 再考虑**：是否引入 `ExecutorDecision` 更精细的表达

**Kernel 改动**：**零**。

### 6.4 Executor 组合（沿用 V2.5 装饰器）

```python
executor = TimeoutExecutor(
    RetryExecutor(
        PermissionExecutor(
            ParallelExecutor(toolbox),
            policy=AllowListPolicy({"read_file", "search"}),
        ),
        max_attempts=3,
    ),
    seconds=30,
)
```

**组合语义**（V2.5 已冻结）：装饰器叠加，per-call timeout 默认。

**Kernel 改动**：**零**。

---

## 七、四道 Gate

### Gate A — Kernel ABI Stability

- [ ] **Kernel 公共 ABI 与 `docs/freeze/v2/scratch.py` 完全一致**（ABI drift 测试全绿）
- [ ] `agent_loop <= 55` 行
- [ ] Architecture Firewall 五条规则全绿
- [ ] `kernel/` 目录 5 文件 + `__init__.py`

**Gate A 不通过，V3 未开始。**

### Gate B — Composition（三级递进）

**关键调整**：从"一次性全组合"改为"三级递进"。失败时能定位。

#### B1 — Single Capability

每个新能力**独立与 Kernel 组合**：

- [ ] `PermissionExecutor + Toolbox`
- [ ] `BudgetTransform + ContextEngine`
- [ ] `DirectorySkills + ContextProvider`

**验证**：每个能力单独工作时，Kernel 零改动。

#### B2 — Multi Capability（能力间不耦合）

新能力**互相组合**：

- [ ] `Retry(Permission(Parallel(toolbox)))`
- [ ] `BudgetTransform ∘ SkillProvider`（Context 管线里同时使用）
- [ ] `Permission + BudgetTransform`（执行与上下文同时约束）

**验证**：能力之间无隐式耦合，任意替换其中一个不影响其他。

#### B3 — Full Stack（全组合）

全部能力同时工作：

```text
Runtime
  ├── Model       (OpenAI path + Ollama path + Anthropic path)
  ├── ContextEngine
  │     ├── providers: SystemPrompt + MemoryContext + SkillContext
  │     └── transform: BudgetTransform(32_000)
  ├── Toolbox
  │     ├── LocalTools
  │     ├── MCPProvider
  │     └── MCPBackedSkills
  ├── Memory
  └── Executor
        └── Timeout(Retry(Permission(Parallel(toolbox))))
```

**验证**：

- [ ] 组合测试通过
- [ ] **组合测试过程中 `kernel/` 零改动**（ABI drift 测试始终绿）
- [ ] **每个能力都能被独立观察到**（通过 EventBus 的 trace 事件）
- [ ] **单独替换任意一个能力不需要改其他能力**

### Gate C — Ecosystem（第三方实现）

**关键调整**：第三方只 import `agentkit.api`，不 import `agentkit.kernel.*`。

在 `tests/third_party/` 里实现：

```text
third_party/
├── __init__.py
├── executor.py          # FakeThirdPartyExecutor
├── context.py           # FakeThirdPartyTransform
├── skill.py             # FakeThirdPartySkillProvider
├── permission.py        # FakeThirdPartyPermissionPolicy
└── README.md            # 说明"我们只依赖 agentkit.api"
```

**约束**：

- 每个模块**只能 `from agentkit.api import ...`**
- `tests/test_api_boundary.py` 强制
- 每个 fake 实现在 Gate B 组合测试里至少使用一次

**验收**：

- [ ] 所有 third-party 实现能正常工作
- [ ] `test_api_boundary.py` 全绿
- [ ] 每个实现都在 B1/B2/B3 组合测试里出现过

**这是 V3 最有生态价值的产出。**

### Gate D — Regression / Conformance

- [ ] V1 (158) + V2 (226) + V2.5 (292) 全部通过
- [ ] ruff / mypy --strict / pyright 全绿
- [ ] Contract drift 门禁全绿
- [ ] Conformance 真机（DeepSeek + Ollama）继续通过
- [ ] **V3 不得因为增加能力而降低 V2.5 的验证强度**

---

## 八、DoD（Definition of Done）

### Gate A
- [ ] Kernel ABI drift 测试全绿
- [ ] `agent_loop <= 55` 行
- [ ] Architecture Firewall 五条全绿

### Gate B
- [ ] B1 单能力组合通过
- [ ] B2 多能力组合通过
- [ ] B3 全栈组合通过
- [ ] 组合测试中 Kernel 零改动
- [ ] 独立替换任意能力不需要改其他

### Gate C
- [ ] 4 个 third-party 实现（executor / transform / skill / permission）
- [ ] `test_api_boundary.py` 全绿
- [ ] 每个 third-party 实现在组合测试里出现过

### Gate D
- [ ] 全部历史测试通过
- [ ] 类型门禁全绿
- [ ] Contract drift 全绿
- [ ] Conformance 真机继续通过

### Documentation
- [ ] `docs/freeze/v3/scratch.py`（含 3 个新 Protocol）
- [ ] `docs/freeze/v3/ABI.md`（Kernel ABI 快照）
- [ ] `CHANGELOG_v3.md`
- [ ] README 更新：能力组合示例 + 扩展点地图 + `agentkit.api` 用法
- [ ] `docs/EXTENSION_GUIDE.md`（第三方扩展指南）

**任何一条不满足，V3 不算完成。**

---

## 九、V3 的目录结构（最终形态）

```text
agentkit/
│
├── kernel/                    # ABI 冻结，内部
│   ├── __init__.py
│   ├── types.py
│   ├── state.py
│   ├── events.py
│   ├── protocols.py
│   └── loop.py
│
├── api/                       # 第三方唯一入口
│   ├── __init__.py
│   ├── kernel.py
│   ├── context.py
│   ├── skill.py
│   └── executor.py
│
├── runtime/
│   └── default.py
│
├── context/
│   ├── engine.py
│   ├── providers.py
│   └── transform.py           # 新
│
├── skills/
│   ├── skill.py
│   ├── directory.py
│   └── mcp_backed.py          # 新
│
├── executor/
│   ├── builtin.py
│   ├── retry.py
│   ├── timeout.py
│   └── permission.py          # 新
│
├── memory/
├── tools/
├── models/
├── harness/
├── observability/
├── cli.py
└── contrib/

docs/
├── freeze/
│   ├── v2/scratch.py
│   └── v3/
│       ├── scratch.py
│       └── ABI.md
├── CONFORMANCE_REPORT.md
├── conformance/
├── EXTENSION_GUIDE.md          # 新
└── ...

tests/
├── unit/                       # 现有 292
├── conformance/                # 现有真机
├── third_party/                # 新
│   ├── executor.py
│   ├── context.py
│   ├── skill.py
│   └── permission.py
├── test_abi_drift.py           # 新
├── test_api_boundary.py        # 新
└── test_architecture_firewall.py  # 新
```

---

## 十、实施路线图

### Phase V3-1：Contract Freeze + 骨架（1~2 天）

**顺序严格**：

1. [ ] 写 `docs/freeze/v3/scratch.py`（含 3 个新 Protocol）
2. [ ] `mypy --strict` + `pyright` 通过
3. [ ] 写 `docs/freeze/v3/ABI.md`（Kernel ABI 快照）
4. [ ] 写 `tests/test_abi_drift.py`（先跑 V2.5 应全绿）
5. [ ] 写 `tests/test_architecture_firewall.py`（先跑应全绿）
6. [ ] 写 `tests/test_api_boundary.py` + `tests/third_party/` 骨架
7. [ ] 写 `agentkit/api/__init__.py`（重导出 kernel）
8. [ ] `CHANGELOG_v3.md`

**这一步不写任何能力。只建约束。**

### Phase V3-2：三能力实现（3~4 天）

- [ ] `ContextTransform` Protocol + 4 个实现
- [ ] `SkillProvider` Protocol + `MCPBackedSkills`
- [ ] `PermissionPolicy` Protocol + `PermissionExecutor` + 3 个 Policy
- [ ] 各能力单元测试
- [ ] `ContextEngine` 接入 `transform=` 参数

**每完成一个能力，跑一次 Gate A + B1 验证。**

### Phase V3-3：组合验证（2~3 天）

- [ ] B1 单能力组合测试
- [ ] B2 多能力组合测试
- [ ] B3 全栈组合测试
- [ ] EventBus 可观测性测试
- [ ] 独立替换测试

**B3 通过前不要开始 Gate C。**

### Phase V3-4：Third-party 验证（1~2 天）

- [ ] 4 个 third-party 实现（只 import `agentkit.api`）
- [ ] `test_api_boundary.py` 全绿
- [ ] 在 B1/B2/B3 里使用它们
- [ ] `docs/EXTENSION_GUIDE.md`

### Phase V3-5：验收（1 天）

- [ ] Gate A/B/C/D 全绿
- [ ] Architecture Firewall 全绿
- [ ] 全部历史测试通过
- [ ] 文档 + 报告

**总计约 8~12 天。**

---

## 十一、V3 完成后应能宣称

> **AgentKit 的架构能够随着 Agent 能力增长而增长，而不是随着能力增长把复杂度重新吸回 Kernel。**

具体证据：

```text
1. Kernel ABI 与 V2.5 完全一致（Gate A）
2. 6 个新能力 + 4 个 Provider 分三级组合工作（Gate B）
3. 4 个第三方实现只依赖 agentkit.api（Gate C）
4. 全部历史纪律保持（Gate D）
5. Architecture Firewall 五条规则可执行（负向 Contract）
6. agentkit.api 成为唯一公共扩展入口（生态边界）
```

**如果这个实验成功，V3 的价值明显高于"再增加几个 Agent 功能"。**

---

## 十二、V3 的立场（写进 README 与 CHANGELOG）

> **V3 不是"功能版本"，是"约束版本"。**
>
> V1 建立微内核，V2 证明微内核可演化，V2.5 证明契约在真机上成立，V3 证明**微内核可以承载任意组合而自身不变**。
>
> **V3 最应该留下的资产**：
>
> - Architecture Firewall
> - Public Extension API（`agentkit.api`）
> - Composition Test Suite
>
> **这三个才是 AgentKit 从"框架"走向"平台"的分界线。**

---

## 十三、评审 6 项修改的落地对照

| # | 修改 | 落地位置 |
|---|---|---|
| P0-1 | Gate A 改 ABI Freeze | §三 |
| P0-2 | ContextCompactor → ContextTransform，去 ctx | §6.1 |
| P0-3 | SkillProvider 删除 refresh | §6.2 |
| P0-4 | PermissionExecutor 异常语义冻结 | §6.3 |
| P1-1 | Gate B 拆三级 | §七 Gate B |
| P1-2 | Kernel API 与 Extension API 分离 | §四、§九 |

**次要采纳**：

- Firewall Rule 3 改为依赖方向扫描为主 → §5.1
- Third-party 只 import `agentkit.api` → §4.5
- 独立替换测试 → Gate B
- `EXTENSION_GUIDE.md` → §十

---

## 十四、一句话

**V3 不是证明 AgentKit 有多少能力，而是证明一个稳定 Contract 可以承载未知能力。**

**下一步动作**：

1. 写 `docs/freeze/v3/scratch.py`（含 3 个新 Protocol）
2. 写 `tests/test_abi_drift.py` / `test_architecture_firewall.py` / `test_api_boundary.py`
3. 写 `agentkit/api/__init__.py`（重导出 kernel）
4. **再写三能力实现**

**不要先写 `BudgetTransform`，再补防火墙。** 先建约束，再放功能，是唯一能在 V3 结束时守住命题的顺序。