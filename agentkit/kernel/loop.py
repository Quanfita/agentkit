"""唯一不可替换的控制流。

这个文件里的词表只有 Runtime / RunContext / Action：
它不认识任何能力实现，也不认识任何能力字段。
"""
from __future__ import annotations

from .protocols import Runtime
from .state import RunContext
from .types import Final


async def agent_loop(runtime: Runtime, ctx: RunContext) -> RunContext:
    """30 行。只认识 Runtime。永不膨胀。

    停止检查点（stop invariant）：
      任何可产生外部副作用的动作之前，检查 ctx.stop。
      目前副作用动作有两个：reason 调用、act 批量执行。
    """
    await runtime.events.emit("agent.start", ctx=ctx)
    try:
        while not ctx.done and ctx.step < ctx.max_iterations:

            # ── 检查点 1：进入新 iteration 之前 ──
            if ctx.stop:
                break

            # prepare 无副作用，不需要 stop 检查
            inp = await runtime.prepare(ctx)
            await runtime.events.emit("model.before", ctx=ctx, inp=inp)

            # ── 检查点 2：reason 调用之前 ──
            if ctx.stop:
                break

            action = await runtime.reason(ctx, inp)
            await runtime.events.emit("model.after", ctx=ctx, action=action)

            if isinstance(action, Final):
                await runtime.observe(ctx, action, None)
                ctx.result = action.content
                ctx.done = True
                break

            # ── 检查点 3：act 批量执行之前 ──
            # （act 内部还会按单次调用粒度再检查一次；见 DefaultRuntime）
            if ctx.stop:
                break

            results = await runtime.act(ctx, action)
            await runtime.observe(ctx, action, results)

            ctx.step += 1
            await runtime.events.emit(
                "iteration.done", ctx=ctx, action=action, results=results,
            )

    except BaseException as e:
        ctx.error = e
        ctx.done = True
        await runtime.events.emit("agent.error", ctx=ctx, error=e)
        raise
    finally:
        await runtime.finish(ctx)
        await runtime.events.emit("agent.end", ctx=ctx)
    return ctx
