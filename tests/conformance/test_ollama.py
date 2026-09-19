"""Ollama 真机 Conformance（Local / Compatibility，Gate C 不阻塞）。

需要本地 `AGENTKIT_OLLAMA_HOST` 可连接且已 pull `AGENTKIT_OLLAMA_MODEL`；
未就绪时全部场景记 `not_verified` 并 skip，不算失败。
"""
from __future__ import annotations

import pytest

from .cases import CASES
from .conftest import run_cases
from .providers.ollama import provider

pytestmark = pytest.mark.conformance


@pytest.mark.anyio
@pytest.mark.parametrize("case", CASES, ids=[c.id for c in CASES])
async def test_ollama_scenarios(case, evidence):
    await run_cases(provider, case, evidence)
