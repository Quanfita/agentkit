"""内置 `ContextTransform` 实现（V3 §六.1）。

每个实现都满足 `agentkit.api.ContextTransform`：

    async def apply(self, messages: Sequence[Message]) -> list[Message]

Invariant（V3.1 冻结，与算法无关）：

    当前用户任务消息（`Sequence` 里最后一条 user 消息）
    在经过本模块任何 transform 之后必须仍然存在。

截断类 transform 可以决定"保留哪些下标"，但不能把当前任务丢掉 ——
否则模型会收到一段没有问题的上下文。

I3（Transform Closure，Message Sequence Contract 冻结）：

    ∀ Transform T，若 Valid(messages) 则 Valid(T(messages))。

截断类实现因此必须先清掉开头可能出现的**孤儿 tool 消息**
（失去宿主 `assistant(tool_calls)` 的结果），见 `_trim_leading_orphan_tools()`。

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


def _trim_leading_orphan_tools(
    kept: list[int], messages: Sequence[Message],
) -> list[int]:
    """I1/I2（Message Sequence Contract）：丢掉开头连续的孤儿 tool 消息。

    截断只切前缀，所以孤儿只可能出现在**开头**：只要宿主 `assistant(tool_calls)`
    被保留，它声明的整批结果就都在（批次不会被从中间切开）。

    必须在 `_kept_with_current_task()` **之前**调用 —— 先清掉非法前缀，
    再补当前任务；反过来会让孤儿 tool 消息被当前任务"挡"在保留集里。
    """
    start = 0
    while start < len(kept) and messages[kept[start]].role == "tool":
        start += 1
    return kept[start:]


def _current_task_index(messages: Sequence[Message]) -> int | None:
    """当前用户任务 = 最后一条 user 消息（V3.1 的识别依据）。"""
    for index in range(len(messages) - 1, -1, -1):
        if messages[index].role == "user":
            return index
    return None


def _kept_with_current_task(
    kept: list[int], messages: Sequence[Message],
) -> list[int]:
    """把当前用户任务并入保留下标（invariant，不是 algorithm）。

    具体实现（保尾部 / 提权 / 其他）是算法；"必须存在"是契约。
    """
    index = _current_task_index(messages)
    if index is None or index in kept:
        return kept
    return sorted([*kept, index])


class BudgetTransform:
    """超预算时保留 system + 尾部消息。

    `estimate` 是可注入的 token 估算函数，默认 `len`（字符数），
    因此字符预算与真实 token 预算都能表达。

    system 消息永不被丢弃：先全部保留，再从**非 system 消息的尾部往前**收，
    遇到第一条放不下的消息就停止（保留的是一个连续后缀，顺序不变）。
    系统消息本身已超预算时，结果是「system + 当前用户任务」（V3.1 invariant）。
    """

    def __init__(self, max_tokens: int, estimate: Estimator = len) -> None:
        self.max_tokens = max_tokens
        self.estimate = estimate

    def _cost(self, messages: Sequence[Message]) -> int:
        return sum(self.estimate(m.content) for m in messages)

    async def apply(self, messages: Sequence[Message]) -> list[Message]:
        system_indexes = [i for i, m in enumerate(messages) if m.role == "system"]
        rest_indexes = [i for i, m in enumerate(messages) if m.role != "system"]
        system = [messages[i] for i in system_indexes]

        budget = self.max_tokens - self._cost(system)
        kept: list[int] = []
        spent = 0
        for index in reversed(rest_indexes):
            cost = self.estimate(messages[index].content)
            if spent + cost > budget:
                break
            spent += cost
            kept.append(index)
        kept.reverse()
        # I3 closure：先清掉孤儿 tool 前缀，再补当前任务（顺序不可颠倒）
        kept = _kept_with_current_task(
            _trim_leading_orphan_tools(kept, messages), messages,
        )
        return system + [messages[i] for i in kept]


class SlidingWindowTransform:
    """只保留最后 `max_messages` 条；当前用户任务例外（V3.1 invariant）。

    `max_messages <= 0` 表示"清空历史"，但**当前用户任务仍然保留** ——
    否则模型收到的上下文里没有问题，这个窗口就失去意义。
    """

    def __init__(self, max_messages: int) -> None:
        self.max_messages = max_messages

    async def apply(self, messages: Sequence[Message]) -> list[Message]:
        if self.max_messages <= 0:
            kept: list[int] = []
        else:
            kept = list(range(max(0, len(messages) - self.max_messages), len(messages)))
        kept = _kept_with_current_task(
            _trim_leading_orphan_tools(kept, messages), messages,
        )
        return [messages[i] for i in kept]


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
