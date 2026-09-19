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

- 每个 Cluster 一个 `docs/clusters/NNNN.md`（见 `TEMPLATE.md`）
- 每个 Cluster 必须明确回答「是否升级为 V4」
- 满足四项升级判据的 → 写 `docs/v4/` 下的 V4 Proposal（见 `docs/v4/PROPOSAL_TEMPLATE.md`）
- 不满足的 → 归档，进入下一个观察期
