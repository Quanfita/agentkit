"""第三方 `ContextTransform` 实现 —— 唯一 import 来源是 `agentkit.api`。

`ContextTransform` 是**纯函数**：`Sequence[Message] -> list[Message]`。
协议故意不给它 `RunContext`，所以第三方作者根本读不到 Runtime 状态；
这份实现也不 await 任何外部资源（async 只是协议形状）。
"""
from __future__ import annotations

from collections.abc import Sequence
from dataclasses import replace

from agentkit.api import Message


class FakeThirdPartyTransform:
    """给每条消息的 `content` 加可识别前缀，可选只保留最后 `keep_last` 条。

    - `prefix` 是探针：组合测试用它证明自己的 transform 真的跑在 Context 管线里；
    - `keep_last > 0` 时先截尾再打前缀（截断与改写都是协议允许的动作）；
    - 从不修改输入对象：每条消息都用 `dataclasses.replace` 造新的。
    """

    def __init__(self, prefix: str = "[third-party] ", keep_last: int = 0) -> None:
        self.prefix = prefix
        self.keep_last = keep_last
        self.applied = 0

    async def apply(self, messages: Sequence[Message]) -> list[Message]:
        self.applied += 1
        source = list(messages)
        if self.keep_last > 0:
            source = source[-self.keep_last:]
        return [replace(m, content=f"{self.prefix}{m.content}") for m in source]
