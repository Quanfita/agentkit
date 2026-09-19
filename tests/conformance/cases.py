"""Conformance 场景定义（纯数据）。

6 个场景的 id / layer / 输入按 §3.3 冻结。这里只有数据与查表：
执行在 `runner.py`，判定在 `assertions.py`，报告在 `report.py`。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

Layer = Literal["contract", "capability"]

#: Error 场景用的无效 model name（Adapter 层与 Agent 层共用）。
INVALID_MODEL = "agentkit-conformance-nonexistent-model"

#: Tool 场景期望的 Tool 名（fixture 里只注册这一个）。
EXPECTED_TOOL = "weather"


@dataclass(frozen=True, slots=True)
class Scenario:
    """一个 Conformance 场景：输入 + 所属层 + 需要哪些 Tool。"""

    id: str
    layer: Layer
    title: str
    prompt: str
    stream: bool = False
    tools: tuple[str, ...] = ()
    model: str | None = None            # None → 用 Provider 默认模型


NORMAL = Scenario("normal", "contract", "Normal", "说你好")
TOOL = Scenario("tool", "contract", "Tool", "北京天气怎么样？", tools=(EXPECTED_TOOL,))
ERROR = Scenario("error", "contract", "Error", "说你好", model=INVALID_MODEL)
STREAM_TEXT = Scenario(
    "stream_text", "contract", "Stream Text", "说你好", stream=True,
)
PARALLEL_TOOL = Scenario(
    "parallel_tool", "capability", "Parallel Tool", "北京和上海的天气怎么样？",
    tools=(EXPECTED_TOOL,),
)
STREAM_TOOL = Scenario(
    "stream_tool", "capability", "Stream Tool", "北京天气怎么样？",
    stream=True, tools=(EXPECTED_TOOL,),
)

#: 顺序 = §3.3 表格顺序
CASES: tuple[Scenario, ...] = (
    NORMAL, TOOL, ERROR, STREAM_TEXT, PARALLEL_TOOL, STREAM_TOOL,
)

_BY_ID = {case.id: case for case in CASES}


def by_id(case_id: str) -> Scenario:
    return _BY_ID[case_id]


# ── 状态枚举（§3.6 冻结） ────────────────────────────────

PASS = "pass"
SKIPPED_BY_PROVIDER = "skipped_by_provider"
MODEL_DID_NOT_TRIGGER = "model_did_not_trigger"
FAIL = "fail"
NOT_VERIFIED = "not_verified"

STATUSES: tuple[str, ...] = (
    PASS, SKIPPED_BY_PROVIDER, MODEL_DID_NOT_TRIGGER, FAIL, NOT_VERIFIED,
)


def contract_verified(status: str) -> bool:
    """§3.6：只有 `pass` 能证明 Contract 成立。"""
    return status == PASS


# ── Provider Gate（§3.7 冻结） ──────────────────────────

GATE_A_PROVIDERS: tuple[str, ...] = ("openai", "anthropic")
GATE_A_CASES: tuple[str, ...] = ("normal", "tool", "stream_text", "error")
GATE_B_CASES: tuple[str, ...] = ("parallel_tool", "stream_tool")
GATE_C_PROVIDER = "ollama"
GATE_C_CASES: tuple[str, ...] = ("normal", "error")
#: 兼容性 Provider（Gate C 非阻塞，§3.7 只列 Ollama）
COMPATIBILITY_PROVIDERS: tuple[str, ...] = ("ollama",)
