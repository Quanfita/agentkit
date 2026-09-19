"""内置 `ContextTransform` 实现（V3 §六.1）。

每个实现都满足 `agentkit.api.ContextTransform`：

    async def apply(self, messages: Sequence[Message]) -> list[Message]

严格语义（全部是纯函数）：

  - **不接收 `RunContext`**：策略是纯函数，不读取 Runtime 状态；
  - **不访问外部世界**：无 memory / 无 tool / 无 event；
  - **不修改输入序列**：永远返回新 list。

允许：过滤、截断、排序、去重、替换 content。
禁止：读取 ctx / 调用 LLM / 写 memory / 发 event。

`async` 只是为未来可能的实现留出空间；这里没有任何 `await` 外部资源。
"""
from __future__ import annotations

from collections.abc import Callable, Sequence

from ..kernel.types import Message

# token 估算函数：作用于 `Message.content`，返回该消息的「成本」。
Estimator = Callable[[str], int]


class BudgetTransform:
    """超预算时保留 system + 尾部消息。

    `estimate` 是可注入的 token 估算函数，默认 `len`（字符数），
    因此字符预算与真实 token 预算都能表达。

    system 消息永不被丢弃：先全部保留，再从**非 system 消息的尾部往前**收，
    遇到第一条放不下的消息就停止（保留的是一个连续后缀，顺序不变）。
    系统消息本身已超预算时，结果是「只剩 system 消息」。
    """

    def __init__(self, max_tokens: int, estimate: Estimator = len) -> None:
        self.max_tokens = max_tokens
        self.estimate = estimate

    def _cost(self, messages: Sequence[Message]) -> int:
        return sum(self.estimate(m.content) for m in messages)

    async def apply(self, messages: Sequence[Message]) -> list[Message]:
        system = [m for m in messages if m.role == "system"]
        rest = [m for m in messages if m.role != "system"]

        budget = self.max_tokens - self._cost(system)
        kept: list[Message] = []
        spent = 0
        for message in reversed(rest):
            cost = self.estimate(message.content)
            if spent + cost > budget:
                break
            spent += cost
            kept.append(message)
        kept.reverse()
        return system + kept


class SlidingWindowTransform:
    """只保留最后 `max_messages` 条（`max_messages <= 0` 时清空）。"""

    def __init__(self, max_messages: int) -> None:
        self.max_messages = max_messages

    async def apply(self, messages: Sequence[Message]) -> list[Message]:
        if self.max_messages <= 0:
            return []
        return list(messages[-self.max_messages:])


class DedupeTransform:
    """相邻重复消息去重（保留每组重复中的第一条）。

    比较用 `Message` 的完整相等性：role / content / tool_calls / tool_call_id
    全部相同才算重复，因此相邻的 `user:"x"` 与 `assistant:"x"` 都会保留。
    """

    async def apply(self, messages: Sequence[Message]) -> list[Message]:
        deduped: list[Message] = []
        for message in messages:
            if not deduped or deduped[-1] != message:
                deduped.append(message)
        return deduped


class SystemPriorityTransform:
    """保证 system 消息优先：稳定地把它们移到最前面。"""

    async def apply(self, messages: Sequence[Message]) -> list[Message]:
        system = [m for m in messages if m.role == "system"]
        return system + [m for m in messages if m.role != "system"]
