"""Anthropic 真机 Conformance（Primary，Gate A 必须全部 `pass`）。"""
from __future__ import annotations

import pytest

from .cases import CASES
from .conftest import run_cases
from .providers.anthropic import provider

pytestmark = pytest.mark.conformance


@pytest.mark.anyio
@pytest.mark.parametrize("case", CASES, ids=[c.id for c in CASES])
async def test_anthropic_scenarios(case, evidence):
    await run_cases(provider, case, evidence)
