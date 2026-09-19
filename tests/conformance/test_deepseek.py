"""DeepSeek 真机 Conformance（OpenAI-compatible，附加兼容性 Provider）。

需要 `DEEPSEEK_API_KEY`；模型由 `AGENTKIT_DEEPSEEK_MODEL` 决定（默认 deepseek-chat）。
"""
from __future__ import annotations

import pytest

from .cases import CASES
from .conftest import run_cases
from .providers.deepseek import provider

pytestmark = pytest.mark.conformance


@pytest.mark.anyio
@pytest.mark.parametrize("case", CASES, ids=[c.id for c in CASES])
async def test_deepseek_scenarios(case, evidence):
    await run_cases(provider, case, evidence)
