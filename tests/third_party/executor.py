"""第三方 `ToolExecutor` 实现 —— 唯一 import 来源是 `agentkit.api`。

这份实现回答 V3 的生态问题：**一个外部作者不看 kernel 源码，能不能写出一条
可用的执行策略？** 能 —— 他只需要 `ToolExecutor` 的签名和 `ToolResult` 契约，
两者都在 `agentkit.api` 里。

实现刻意做成「可控的假货」而不是玩具：组合测试需要它可注入延迟（和
`TimeoutExecutor` 组合）、可注入失败（和 `RetryExecutor` 组合），并且能证明
契约里那条 `tool_call_id` 的 ownership 规则确实被 Executor 遵守。
"""
from __future__ import annotations

import asyncio
from collections.abc import Iterable

from agentkit.api import RunContext, ToolCall, ToolCalls, ToolResult


class FakeThirdPartyExecutor:
    """把每个 `ToolCall` 应答成一条固定文本的 `ToolResult`。

    契约（对齐 `ToolExecutor` Protocol）：

      - `execute()` 返回的每条 `ToolResult.tool_call_id` 都等于输入 `call.id`；
      - `ctx.stop` 置位后不再开始新的调用（批内 stop：结果可以短于 calls）；
      - `close()` 只关闭自己的资源 —— 这里没有外部资源，只记状态。

    可观测状态（供组合测试断言）：

      - `calls`       —— 真正被应答过的 `ToolCall`，按顺序
      - `seen_tasks`  —— 调用方传进来的 `ctx.task`（证明 ctx 是显式参数）
      - `started`     —— `execute()` 被调用次数
      - `closed`      —— `close()` 是否被调用过

    注入点：

      - `delay`   —— 每次调用前 await 的秒数（0 表示不 await）
      - `fail_on` —— 名字命中的调用返回 `error=True`（模拟工具失败）
    """

    def __init__(
        self,
        content: str = "[third-party] ok",
        *,
        delay: float = 0.0,
        fail_on: Iterable[str] = (),
    ) -> None:
        self.content = content
        self.delay = delay
        self.fail_on = frozenset(fail_on)

        self.calls: list[ToolCall] = []
        self.seen_tasks: list[str] = []
        self.started = 0
        self.closed = False

    async def execute(self, ctx: RunContext, action: ToolCalls) -> list[ToolResult]:
        self.started += 1
        self.seen_tasks.append(ctx.task)

        results: list[ToolResult] = []
        for call in action.calls:
            if ctx.stop:
                break
            self.calls.append(call)
            if self.delay:
                await asyncio.sleep(self.delay)

            failed = call.name in self.fail_on
            results.append(ToolResult(
                tool_call_id=call.id,
                content=f"[third-party] {call.name} failed" if failed
                else f"{self.content}: {call.name}",
                error=failed,
                metadata={"third_party": True},
            ))
        return results

    async def close(self) -> None:
        self.closed = True
