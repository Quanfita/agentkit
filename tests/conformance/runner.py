"""统一 Conformance runner：Provider → Scenario → run → assert Contract → Result。

严格边界（§4.2）：**不得包含** retry / timeout / normalization /
runtime logic / markdown 生成。run 只做「把场景打给 Provider，把原始观测
交给 `assertions`」，报告生成是 `report.py` 里的纯函数。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from agentkit.kernel.loop import agent_loop
from agentkit.kernel.state import RunContext
from agentkit.kernel.types import Action, Message
from agentkit.models.base import Delta

from . import assertions
from .cases import (
    ERROR,
    FAIL,
    MODEL_DID_NOT_TRIGGER,
    NOT_VERIFIED,
    SKIPPED_BY_PROVIDER,
    Scenario,
)
from .providers import ProviderSetup

__all__ = ["Observation", "ScenarioResult", "run_scenario"]

#: Provider 构建出的真机 Adapter 同时满足 `Model` 与 `StreamingModel`
#: （`generate()` + `stream()`）；这里按别名透传，避免在 runner 里
#: 再造一套类型抽象（Protocol 由 kernel 拥有）。
ModelLike = Any


@dataclass(slots=True)
class Observation:
    """一次场景 run 的原始观测（未判定）。"""

    action: Action | None = None
    deltas: list[Delta] = field(default_factory=list)
    stream_exhausted: bool = False
    error: BaseException | None = None          # Adapter 层：SDK 异常
    agent_error: BaseException | None = None    # Agent 层：loop 重新抛出的异常
    ctx: RunContext | None = None


@dataclass(slots=True)
class ScenarioResult:
    """一个 (Provider, Scenario) 的结论（`report.py` 的输入）。

    `contract_verified` 不在这里重复实现：§3.6 的规则只有
    `cases.contract_verified()` 一处定义。
    """

    scenario: str
    layer: str
    status: str
    notes: str = ""


async def run_scenario(provider: ProviderSetup, case: Scenario) -> ScenarioResult:
    """跑一个场景并判定。任何结果都变成 `ScenarioResult`，不向外抛异常。"""
    if provider.missing:
        return ScenarioResult(
            case.id, case.layer, NOT_VERIFIED, "missing: " + ", ".join(provider.missing)
        )
    if case.id in provider.unsupported:
        return ScenarioResult(
            case.id, case.layer, SKIPPED_BY_PROVIDER, "provider does not support this capability"
        )
    model = provider.new_model(case.model)
    try:
        observation = await _observe(provider, model, case)
        verdict = assertions.verify(case, observation, provider)
        return ScenarioResult(case.id, case.layer, verdict.status, verdict.notes)
    except assertions.ContractViolation as exc:
        return ScenarioResult(case.id, case.layer, FAIL, str(exc))
    except assertions.ModelDidNotTrigger as exc:
        return ScenarioResult(case.id, case.layer, MODEL_DID_NOT_TRIGGER, str(exc))
    except Exception as exc:                    # 预期外的 Provider / SDK 故障
        return ScenarioResult(
            case.id, case.layer, FAIL, f"unexpected {type(exc).__name__}: {exc}"
        )
    finally:
        await model.close()


async def _observe(
    provider: ProviderSetup, model: ModelLike, case: Scenario
) -> Observation:
    observation = Observation()
    if case.stream:
        await _observe_stream(provider, model, case, observation)
    elif case.id == ERROR.id:
        await _observe_error(provider, model, case, observation)
    else:
        observation.action = await model.generate(
            _messages(case), await provider.tool_specs(case)
        )
    return observation


async def _observe_stream(
    provider: ProviderSetup, model: ModelLike, case: Scenario, observation: Observation
) -> None:
    stream = model.stream(_messages(case), await provider.tool_specs(case))
    async for delta in stream:
        observation.deltas.append(delta)
    observation.stream_exhausted = True


async def _observe_error(
    provider: ProviderSetup, model: ModelLike, case: Scenario, observation: Observation
) -> None:
    """Error 场景两层（§4.5）：Adapter 原样传播 SDK 异常；Agent 记为 ERROR。"""
    try:
        await model.generate(_messages(case), [])
    except Exception as exc:
        observation.error = exc
    if observation.error is None:
        return                                  # 没抛 → assertions 判 fail
    runtime = provider.runtime(model, case)
    ctx = RunContext(task=case.prompt, messages=_messages(case))
    try:
        await agent_loop(runtime, ctx)
    except Exception as exc:
        observation.agent_error = exc
    finally:
        await runtime.close()
    observation.ctx = ctx


def _messages(case: Scenario) -> list[Message]:
    return [Message("user", case.prompt)]
