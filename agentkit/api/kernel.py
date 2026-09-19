"""Kernel 公共 ABI 的官方重导出。

第三方实现若需要 Kernel 内的 Protocol / 类型，**一律从 `agentkit.api` 导入**；
直接 `import agentkit.kernel.*` 的第三方代码会被 `tests/test_api_boundary.py` 拒绝。

本模块只做重导出，不含任何逻辑。
"""
from __future__ import annotations

from agentkit.kernel.events import EventBus
from agentkit.kernel.protocols import (
    ContextProvider,
    Memory,
    Model,
    PreparedInput,
    Runtime,
    Tool,
    ToolExecutor,
    ToolProvider,
)
from agentkit.kernel.state import RunContext, TerminationReason
from agentkit.kernel.types import (
    Action,
    ContextItem,
    Final,
    MemoryInput,
    MemoryItem,
    Message,
    ToolCall,
    ToolCalls,
    ToolResult,
    ToolSpec,
)

__all__ = [
    # kernel.protocols
    "ContextProvider", "Memory", "Model", "PreparedInput",
    "Runtime", "Tool", "ToolExecutor", "ToolProvider",
    # kernel.state
    "RunContext", "TerminationReason",
    # kernel.types
    "Action", "ContextItem", "Final", "MemoryInput", "MemoryItem",
    "Message", "ToolCall", "ToolCalls", "ToolResult", "ToolSpec",
    # kernel.events
    "EventBus",
]
