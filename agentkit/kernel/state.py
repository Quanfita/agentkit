"""Loop State —— 单次 Agent Run 的全部状态。"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from .types import Message


class TerminationReason(str, Enum):
    """RunContext.reason 的取值：Run 为什么结束（V2 冻结）。"""

    FINAL = "final"
    MAX_ITERATIONS = "max_iterations"
    STOPPED = "stopped"
    ERROR = "error"


@dataclass
class RunContext:
    """Loop State。

    这是 Loop 的单次运行状态。它不属于 Runtime，也不属于 Harness。

    绝不要往里面加：model / memory / toolbox / skills / mcp / workspace
    / user / session / trace / token_usage / cost。
    """
    task: str
    system: str = ""
    messages: list[Message] = field(default_factory=list)

    step: int = 0
    max_iterations: int = 16

    stop: bool = False                 # Hook 可置位；Loop 在副作用前检查
    done: bool = False
    result: str = ""
    last_assistant: Message | None = None
    error: BaseException | None = None
    reason: TerminationReason | None = None

    scratch: dict[str, Any] = field(default_factory=dict)
