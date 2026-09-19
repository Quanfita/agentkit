# V4 Entry Criteria

V4 Proposal 必须满足以下**全部五项**（缺一不可）：

1. **至少 3 个独立真实信号**
   - 来自 `docs/signals/`，来自冷却期（观察期）记录
   - **不能是理论推演**

2. **现有 Contract 无法解决**
   - Architecture Firewall 拦不住
   - ABI Freeze 不涉及
   - Conformance 不涉及
   - 现有 Protocol 无法承载

   若三者之一能解决 → 那不是 V4，是 V3.x patch。

3. **提出新的 invariant**
   - 不是新功能，是新的**不可变约束**
   - 新 invariant 需要 Contract Freeze

4. **证明不是 V3.x patch**
   - 通过 Proposal 的 `Rejected Alternatives` 字段

5. **Persistence（新增）**
   该问题必须表现为以下至少一项：

   a. **随能力增长重复出现**
      不是孤立事件，而是"再加一个能力就会再撞一次"

   b. **阻碍组合能力增长**
      当前 Contract 限制了多个能力的组合方式

   c. **导致 Contract 不可维护**
      现有 Contract 无法同时表达两种真实需求

   单点 bug 不进入 V4。

   判断标准：
     问自己——这个问题"修一次就完了"，
     还是"每次加能力都要面对"？
       修一次就完了 → V3.x
       每次加能力都要面对 → V4 候选

五项全满足 → 写 V4 Proposal
否则 → 继续观察或归档

## 与三层防线的分工

```text
Architecture Firewall  → 拦"越权"：某个模块做了不属于它的事
ABI Freeze             → 拦"契约漂移"：Kernel / Message Sequence Contract 被悄悄改
Conformance            → 拦"适配器错"：Provider 语义被错误地塞进内部契约
```

三个防线都拦不住的问题，才有资格进入 V4。
