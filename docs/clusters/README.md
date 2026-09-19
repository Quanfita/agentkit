# 聚类（Clustering）

**触发条件**：观察期退出（4 周上限 2026-10-17 到点，或提前退出条件 A/B/C 满足）。
**当前状态**：未触发 —— 观察期窗口进行中（见 `docs/signals/README.md`）。

## 方法

```text
1. 读所有 docs/signals/NNNN.md
2. 提取：
   - Trigger
   - Expected / Actual 差异类型
   - Architectural implication
3. 归类成 clusters
```

一个 Cluster = 一个反复出现的结构性问题，**至少 2 个信号支撑**。

聚类阶段允许分析；观察阶段不允许。这条分界线是本流程的核心。

## 产出

- 每个 Cluster 一个 `docs/clusters/NNNN.md`（模板见下方「Cluster 模板」）
- 每个 Cluster 必须明确回答「是否升级为 V4」
- 满足五项升级判据的 → 写 `docs/v4/` 下的 V4 Proposal（见 `docs/v4/PROPOSAL_TEMPLATE.md`）
- 不满足的 → 归档，进入下一个观察期

## Cluster 模板（待冷却期结束才填）

```markdown
# Cluster NNNN: <一句话描述>

- **Evidence**: Signal 0001, 0005, 0012
- **Signal count**: 3
- **Affected layer**: Context / Executor / Runtime / ...
- **Pattern**: <结构上重复出现的是什么>
- **Candidate Contract**:
    - 需要冻结什么 invariant？
    - 需要什么新 Protocol（如果有）？
- **Kernel impact**:
    - 是否需要改 Kernel？为什么？
    - 如果不需要，能力落在哪一层？
- **Is this V4?**

## 是否升级为 V4 Proposal

- [ ] 至少 3 个独立信号
- [ ] 现有 Contract 无法解决
- [ ] 提出新的 invariant
- [ ] 证明不是 V3.x patch
- [ ] Persistence（随能力增长重复出现 / 阻碍组合能力增长 / 导致 Contract 不可维护）

五项全满足 → 写 V4 Proposal；否则 → 继续观察或归档。
```
