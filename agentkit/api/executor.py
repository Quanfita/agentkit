"""Executor 扩展 Protocol。"""
from __future__ import annotations

from typing import Protocol, runtime_checkable

from .kernel import RunContext, ToolCall


@runtime_checkable
class PermissionPolicy(Protocol):
    """执行前的权限判定。

    返回 `True` = 允许；`False` = 拒绝。
    拒绝行为由 `PermissionExecutor` 决定：转成一个 `error=True` 的 `ToolResult`
    （从模型视角是"这次调用没成功"），并带 `metadata={"blocked": True, ...}`
    供人类/观测层区分「策略拦截」与「工具崩溃」。

    Policy 通过**构造注入**，不通过 Executor 读 ctx 里的隐式状态；
    读 `ctx` 是通过显式参数，不是通过环境变量或单例。
    """

    async def allow(self, call: ToolCall, ctx: RunContext) -> bool: ...
