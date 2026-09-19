# `tests/third_party/` —— 生态边界证据（V3 Gate C）

这里不是 agentkit 的测试**辅助代码**，而是 agentkit 的**外部用户**。

四个模块各自扮演一个第三方作者，实现一个扩展点：

| 文件 | 实现 | 扩展点 |
|---|---|---|
| `executor.py` | `FakeThirdPartyExecutor` | `ToolExecutor` |
| `context.py` | `FakeThirdPartyTransform` | `ContextTransform` |
| `skill.py` | `FakeThirdPartySkillProvider` | `SkillProvider` |
| `permission.py` | `FakeThirdPartyPermissionPolicy` | `PermissionPolicy` |

## 我们只依赖 `agentkit.api`

每个文件的 import 都只有三处来源：

1. Python 标准库（`asyncio` / `dataclasses` / `collections.abc`）；
2. `agentkit.api`；
3. 同目录的其他文件（本目录内没有这种依赖）。

**不 import `agentkit.kernel.*`，也不 import `agentkit.runtime` / `context` /
`skills` / `executor` / `memory` / `tools` / `models` / `harness`。**

## 为什么

V3 的命题是「能力可以增长，组合复杂度可以增长，但 Kernel 不增长」。
如果第三方要扩展一个能力，必须先读 kernel 源码、import kernel 内部模块，
那么每次扩展都会**反向拉动 Kernel 的公共表面**——命题立刻失效。

所以 V3 冻结了一条可执行的边界：

> 第三方实现需要的一切（Protocol 与数据类型）都必须能从 `agentkit.api` 拿到。

`agentkit.api` 因此必须**闭合**：少了 `Skill`，`SkillProvider.search` 的返回类型
就无处可 import；少了 `PreparedInput` / `EventBus`，`Runtime` 也实现不了。
这就是本目录存在的意义——它是这条闭合性的验收样本，而不是示例代码。

## 门禁

`tests/test_api_boundary.py` 用 `ast` 静态扫描本目录（不是 grep）：

- 命中 `agentkit.kernel.` / `runtime` / `context` / `skills` / `executor` /
  `memory` / `tools` / `models` / `harness` 的 import → **失败**；
- 任何 `agentkit` 相关的 import 不落在 `agentkit.api` 下（含
  `from agentkit import kernel` 这种绕法）→ **失败**；
- 四个 fake 都能通过对应 `runtime_checkable` Protocol 的 `isinstance` 检查。

改动本目录时，先跑：

```bash
python -m pytest tests/test_api_boundary.py -q
```

## 这些 fake 的用途

它们是 V3 Gate B（B1 单能力 / B2 多能力 / B3 全栈）组合测试里的**外部实现**：
kernel 不认识它们，Harness 只按 Protocol 使用它们。行为是刻意可控的——
`delay` / `fail_on` / `prefix` / `keep_last` / 白名单，
都是为了把「组合起来会发生什么」变成可断言的事实。
