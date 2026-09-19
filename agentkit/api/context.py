"""Context 扩展 Protocol。"""
from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol, runtime_checkable

from .kernel import Message


@runtime_checkable
class ContextTransform(Protocol):
    """消息序列的纯变换。

    严格语义：

      - 输入 / 输出都是 `Message` 序列；
      - **不接收 `RunContext`**（策略是纯函数，不读取 Runtime 状态）；
      - **不访问外部世界**（无 memory / 无 tool / 无 event）。

    允许：过滤、截断、排序、去重、替换 content。
    禁止：读取 ctx / 调用 LLM / 写 memory / 发 event。

    `async` 只是为未来可能的实现留出空间；当前实现不 await 任何外部资源。
    """

    async def apply(self, messages: Sequence[Message]) -> list[Message]: ...
