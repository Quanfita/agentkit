# Signal NNNN

- **Date**: YYYY-MM-DD
- **Trigger**: <用户做了什么>
- **Expected**: <用户以为 Agent 会怎么做>
- **Actual**: <Agent 实际怎么做>
- **Reproduction**: <最小可复现命令 / 输入 / 配置>
- **Severity**:
    - [ ] S1 = 无法完成用户任务
    - [ ] S2 = 完成了但方式意外
    - [ ] S3 = 完成但体验不佳
- **Architectural implication**:
    - 现有三个防线（Firewall / ABI Freeze / Conformance）能否拦住？
    - 拦不住的，它落在哪一层？
        - [ ] Kernel（罕见）
        - [ ] Runtime / Harness
        - [ ] Context / Executor / Provider
        - [ ] 实现 bug
    - 初步判断（不做归类，只描述现象）：

**注意**：不写 Cluster hint。
