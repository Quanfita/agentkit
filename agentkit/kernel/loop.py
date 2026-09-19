"""唯一不可替换的控制流。

这个文件里的词表只有 Runtime / RunContext / Action：
它不认识任何能力实现，也不认识任何能力字段。
"""
from __future__ import annotations

from .protocols import Runtime
from .state import RunContext, TerminationReason
from .types import Final


async def agent_loop(runtime: Runtime, ctx: RunContext) -> RunContext:
    """30 行。只认识 Runtime。永不膨胀。

    停止检查点：任何副作用动作之前检查 ctx.stop（reason 调用、act 批量执行）。
    V2 新增仅两处：4 个退出点记录 ctx.reason、finish 异常保护。
    """
    await runtime.events.emit("agent.start", ctx=ctx)
    try:
        while not ctx.done and ctx.step < ctx.max_iterations:

            # ── 检查点 1：进入新 iteration 之前 ──
            if ctx.stop:
                ctx.reason = TerminationReason.STOPPED
                break

            # prepare 无副作用，不需要 stop 检查
            inp = await runtime.prepare(ctx)
            await runtime.events.emit("model.before", ctx=ctx, inp=inp)

            # ── 检查点 2：reason 调用之前 ──
            if ctx.stop:
                ctx.reason = TerminationReason.STOPPED
                break

            action = await runtime.reason(ctx, inp)
            await runtime.events.emit("model.after", ctx=ctx, action=action)

            if isinstance(action, Final):
                await runtime.observe(ctx, action, None)
                ctx.result = action.content
                ctx.reason = TerminationReason.FINAL
                ctx.done = True
                break

            # ── 检查点 3：act 批量执行之前 ──
            # （act 内部还会按单次调用粒度再检查一次；见 DefaultRuntime）
            if ctx.stop:
                ctx.reason = TerminationReason.STOPPED
                break

            results = await runtime.act(ctx, action)
            await runtime.observe(ctx, action, results)

            ctx.step += 1
            await runtime.events.emit(
                "iteration.done", ctx=ctx, action=action, results=results,
            )

        # 额度耗尽且没等到 Final —— 由 while 条件自然退出
        if ctx.reason is None:
            ctx.reason = TerminationReason.MAX_ITERATIONS

    except BaseException as e:
        ctx.error = e
        ctx.done = True
        ctx.reason = TerminationReason.ERROR
        await runtime.events.emit("agent.error", ctx=ctx, error=e)
        raise
    finally:
        try:
            await runtime.finish(ctx)
        except Exception as e:
            # finish 失败不能覆盖循环里的原始错误，也不能吞掉 agent.end
            if ctx.error is None:
                ctx.error = e
            await runtime.events.emit("agent.finish_error", ctx=ctx, error=e)
        finally:
            await runtime.events.emit("agent.end", ctx=ctx)
    return ctx
