# agentkit
一个 **微内核** 形态的 Agent 框架：`agent_loop` 是唯一不可替换的控制流， 它只认识 `Runtime` 这一个协议。模型厂商、工具、记忆、技能、上下文策略、 观测与预算——全部是协议实现，挂在 Runtime 或 EventBus 上。
