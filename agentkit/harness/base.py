"""Harness 基类。"""
from __future__ import annotations

from ..kernel.protocols import Runtime


class Harness:
    """Harness 只做两件事：

    1. build_runtime() —— 组装 Model / Toolbox / Context / Memory / Events
    2. close()         —— 释放自己持有的资源（MCP session / HTTP client...）

    Harness 拥有 Provider 的生命周期，Agent 只负责调用它。
    """

    def build_runtime(self) -> Runtime:
        raise NotImplementedError

    async def close(self) -> None:
        return None
