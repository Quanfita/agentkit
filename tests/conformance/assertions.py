"""每个场景的 Contract 断言（§3.3 / §3.4 / §3.5）。

判定语义（V2.5 冻结，写进 README 与报告）：

    fail                  → Adapter 映射违反 V2 Contract（可复现的适配器缺陷）
    model_did_not_trigger → Provider / 模型没有走出该场景期望的行为，
                            Contract 未被行使，因此 **不假装验证过**

第二条对 Contract 层同样成立：V2.5 验证的是「Contract 是否成立」，不是
「Provider 是否总能触发某个行为」（§4.6）。Gate 由报告读者判读。

每个 checker 只做判定 + 记录 notes：不跑 Provider、不重试、不做 IO。
`assemble_tool_calls()` 是 §3.5 的组装语义，`test_malformed_stream.py` 复用同一实现。
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from agentkit.kernel.types import Final, ToolCall, ToolCalls
from agentkit.models.base import TextDelta, ToolCallDelta

from .cases import EXPECTED_TOOL, Scenario

if TYPE_CHECKING:
    from .providers import ProviderSetup
    from .runner import Observation


class ContractViolation(AssertionError):
    """Adapter 映射违反 V2 Contract。"""


class ModelDidNotTrigger(AssertionError):
    """Provider / 模型没有走出期望行为 —— Contract 未被行使。"""


@dataclass(slots=True)
class Verdict:
    """一个场景的判定结果（runner 把它翻译成 `ScenarioResult`）。"""

    status: str
    notes: str = ""


def verify(case: Scenario, obs: Observation, provider: ProviderSetup) -> Verdict:
    """跑该场景的 Contract 断言。违反 → 抛；通过 → `Verdict("pass", notes)`。"""
    return Verdict("pass", _CHECKS[case.id](obs, provider))


# ── 组装（§3.5：组装后结构一致） ────────────────────────


@dataclass(slots=True)
class AssembledCall:
    """按 index 组装出的 ToolCall。"""

    index: int
    id: str = ""
    name: str = ""
    args_json: str = ""

    def arguments(self) -> dict[str, Any]:
        """拼装后的 arguments 必须可解析为 dict（§3.5）。"""
        if not self.args_json.strip():
            raise ModelDidNotTrigger(
                f"call {self.index} ({self.name or '?'}) carried no arguments"
            )
        try:
            parsed = json.loads(self.args_json)
        except json.JSONDecodeError as exc:
            raise ContractViolation(
                f"arguments of {self.name or '?'!r} are not valid JSON: {exc}"
            ) from exc
        if not isinstance(parsed, dict):
            raise ContractViolation(
                f"arguments of {self.name or '?'!r} must decode to a dict, "
                f"got {type(parsed).__name__}"
            )
        return parsed


def assemble_tool_calls(deltas: list[ToolCallDelta]) -> list[AssembledCall]:
    """`ToolCallDelta` → ToolCall 组装（按 index 累积 id / name / args_delta）。

    Adapter 只负责把原始分片归一化成 delta；组装是 Harness 侧语义，
    这里复刻它以便判定 name / arguments / id / index。
    """
    acc: dict[int, AssembledCall] = {}
    for delta in deltas:
        if not isinstance(delta.index, int) or isinstance(delta.index, bool) or delta.index < 0:
            raise ContractViolation(
                f"ToolCallDelta.index must be a non-negative int, got {delta.index!r}"
            )
        call = acc.setdefault(delta.index, AssembledCall(index=delta.index))
        if delta.id:
            call.id = delta.id
        if delta.name:
            call.name = delta.name
        if delta.args_delta is not None:
            call.args_json += delta.args_delta
    return [acc[index] for index in sorted(acc)]


def concat_text(deltas: list[TextDelta]) -> str:
    """流式文本的规范组装：`concat(delta.text)`（§3.4）。"""
    return "".join(delta.text for delta in deltas if isinstance(delta, TextDelta))


# ── 场景 checker ────────────────────────────────────────


def _check_normal(obs: Observation, provider: ProviderSetup) -> str:
    action = obs.action
    if not isinstance(action, Final):
        raise ModelDidNotTrigger(
            f"expected Final, adapter returned {type(action).__name__}"
        )
    if not isinstance(action.content, str):
        raise ContractViolation(
            f"Final.content must be str, got {type(action.content).__name__}"
        )
    return f"{len(action.content)} chars"


def _check_tool(obs: Observation, provider: ProviderSetup) -> str:
    return _tool_note(_expect_tool_calls(obs))


def _check_parallel_tool(obs: Observation, provider: ProviderSetup) -> str:
    calls = _expect_tool_calls(obs)
    note = _tool_note(calls)
    if len(calls) < 2:
        return f"{note} — capability not exercised (single call)"
    return note


def _expect_tool_calls(obs: Observation) -> list[ToolCall]:
    action = obs.action
    if not isinstance(action, ToolCalls):
        raise ModelDidNotTrigger(
            f"expected ToolCalls, adapter returned {type(action).__name__}"
        )
    calls = list(action.calls)
    if not calls:
        raise ContractViolation("ToolCalls.calls is empty")
    for call in calls:
        if not isinstance(call.name, str) or not call.name:
            raise ContractViolation("ToolCall.name must be a non-empty str")
        if not isinstance(call.id, str) or not call.id:
            raise ContractViolation(f"ToolCall.id must be a non-empty str ({call.name})")
        if not isinstance(call.arguments, dict):
            raise ContractViolation(
                f"ToolCall.arguments must be a parsed dict (name={call.name}, "
                f"got {type(call.arguments).__name__})"
            )
    names = [call.name for call in calls]
    if EXPECTED_TOOL not in names:
        raise ModelDidNotTrigger(f"model called {names!r}, not {EXPECTED_TOOL!r}")
    return calls


def _tool_note(calls: list[ToolCall]) -> str:
    return f"{len(calls)} calls: " + ", ".join(call.name for call in calls)


def _check_error(obs: Observation, provider: ProviderSetup) -> str:
    """Error 场景两层（§4.5）：Adapter 原样传播，Agent 记为 ERROR 并 re-raise。"""
    if obs.error is None:
        raise ContractViolation(
            "adapter did not propagate the SDK exception "
            f"(action={type(obs.action).__name__ if obs.action else None})"
        )
    root = type(obs.error).__module__.split(".")[0]
    if root not in provider.sdk_roots:
        raise ContractViolation(
            "adapter must propagate the SDK exception as-is, got "
            f"{type(obs.error).__module__}.{type(obs.error).__name__}"
        )
    ctx = obs.ctx
    if ctx is None:
        raise ContractViolation("agent-level run did not happen")
    if ctx.reason is None or ctx.reason.value != "error":
        raise ContractViolation(f"ctx.reason == {ctx.reason!r}, expected ERROR")
    if ctx.error is None:
        raise ContractViolation("ctx.error is None after model error")
    if obs.agent_error is None:
        raise ContractViolation("agent_loop did not re-raise the model error")
    if type(obs.agent_error) is not type(obs.error):
        raise ContractViolation(
            "adapter and agent surfaced different error types: "
            f"{type(obs.error).__name__} vs {type(obs.agent_error).__name__}"
        )
    return (
        f"adapter: {type(obs.error).__name__} propagated; "
        f"agent: reason=ERROR, re-raised {type(obs.agent_error).__name__}"
    )


def _check_stream_text(obs: Observation, provider: ProviderSetup) -> str:
    if not obs.stream_exhausted:
        raise ContractViolation("stream did not terminate (iterator not exhausted)")
    non_text = [delta for delta in obs.deltas if not isinstance(delta, TextDelta)]
    if non_text:
        raise ContractViolation(
            f"no tools were passed but {len(non_text)} non-text delta(s) arrived"
        )
    if not obs.deltas:
        raise ModelDidNotTrigger("stream produced no TextDelta")
    wrong = [delta for delta in obs.deltas if not isinstance(delta.text, str)]
    if wrong:
        raise ContractViolation(
            f"TextDelta.text must be str, got {type(wrong[0].text).__name__}"
        )
    assembled = concat_text(obs.deltas)
    notes = f"{len(obs.deltas)} deltas, {len(assembled)} chars"
    empty = sum(1 for delta in obs.deltas if not delta.text)
    if empty:
        # informational：adapter 未过滤空分片不构成 Contract 违反（§3.4）
        notes += f", {empty} empty delta(s)"
    return notes


def _check_stream_tool(obs: Observation, provider: ProviderSetup) -> str:
    if not obs.stream_exhausted:
        raise ContractViolation("stream did not terminate (iterator not exhausted)")
    deltas = [delta for delta in obs.deltas if isinstance(delta, ToolCallDelta)]
    if not deltas:
        raise ModelDidNotTrigger("stream produced no ToolCallDelta")
    calls = assemble_tool_calls(deltas)
    indices = [call.index for call in calls]
    if indices != list(range(len(indices))):
        raise ContractViolation(
            f"ToolCallDelta.index must be contiguous from 0, got {indices}"
        )
    for call in calls:
        if not call.id:
            raise ContractViolation(f"assembled ToolCall {call.index} has empty id")
        if not call.name:
            raise ContractViolation(f"assembled ToolCall {call.index} has empty name")
    names = [call.name for call in calls]
    if EXPECTED_TOOL not in names:
        raise ModelDidNotTrigger(f"model streamed {names!r}, not {EXPECTED_TOOL!r}")
    for call in calls:
        if call.name == EXPECTED_TOOL:
            call.arguments()
    return f"{len(calls)} calls from {len(deltas)} deltas: " + ", ".join(names)


_CHECKS = {
    "normal": _check_normal,
    "tool": _check_tool,
    "error": _check_error,
    "stream_text": _check_stream_text,
    "parallel_tool": _check_parallel_tool,
    "stream_tool": _check_stream_tool,
}
