"""V3 生态边界证据：四个「外部作者」的实现，只 import `agentkit.api`。

这些模块不是 agentkit 的一部分，而是**站在 agentkit 外面的第三方**：
它们实现 `ToolExecutor` / `ContextTransform` / `SkillProvider` /
`PermissionPolicy`，并且不允许看见 `agentkit.kernel.*` 或任何其他内部模块。

`tests/test_api_boundary.py` 用 AST 静态扫描本目录，强制这条边界。
"""
