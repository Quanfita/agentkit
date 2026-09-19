# V4 Proposal: <Claim 一句话>

## 1. 它解决哪个真实信号？

引用 Signal NNNN / NNNN / NNNN（至少 3 个独立信号）。

## 2. 为什么现有三个防线解决不了？

- Architecture Firewall：为什么不是"某模块越权"？
- ABI Freeze：为什么不是"Kernel 契约需要变"？
- Conformance：为什么不是"Provider 适配问题"？

若三者之一能解决，则这不是 V4。

## 3. 要冻结的新 Contract 是什么？

- 新 Protocol 名 + 方法签名（若有）
- 新 invariant（若有）
- 为什么不属于现有 Protocol 的自然延伸

## 4. Kernel Impact 预判

- 是否需要修改 `kernel/`？如果需要，为什么？
- 如果不需要，新能力落在哪一层？

## 5. 验收方式

沿用 V3 的四道 Gate：

- Gate A：Kernel ABI 是否仍 frozen
- Gate B：新能力与 V3 三个能力的组合
- Gate C：新 Protocol 对第三方开放
- Gate D：V2.5 Conformance 是否仍通过

## 6. Rejected Alternatives

为什么 V3.x patch / Runtime extension / Existing Protocol 不够？

1. <方案 A> —— 为什么拒绝
2. <方案 B> —— 为什么拒绝
3. <方案 C> —— 为什么拒绝

此字段防止"看到问题就加 Manager / Router / Controller"。

## 7. Why now?

为什么不是继续观察？

- 已观察周期：<N 周>
- 信号数量：<N>
- 若继续观察，预期会新增什么信号？
- 为什么现有信号已经足够启动 V4？
- 若延后 4 周启动 V4，代价是什么？

此字段防止「看到漂亮架构机会就启动 V4」，而不是「为了解决真实问题启动 V4」。

## 8. 立项声明

- V4 Claim（一句话）
- V4 的第一个 Gate
- V4 明确不做什么
