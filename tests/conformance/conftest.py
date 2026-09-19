"""Conformance 套件的 pytest 接线：marker / skip 逻辑 / 证据收集（§4.1、§4.3）。

- `pytest.mark.conformance` 在这里注册；真机用例全部打这个 marker，
  默认被 `pyproject.toml` 的 addopts 排除；
- 所有 Provider 的 `test_*.py` 共用 `run_cases()`（§4.6 降级策略的唯一实现）；
- session 结束时把收集到的证据交给 `report.py` 的纯函数落盘，`pytest_sessionfinish` 保持最薄。
"""
from __future__ import annotations

from collections.abc import Callable

import pytest

from . import cases, report
from .cases import Scenario
from .providers import ProviderSetup
from .runner import run_scenario

EVIDENCE = report.Evidence()


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line(
        "markers",
        "conformance: real-provider conformance tests (needs API keys)",
    )


def pytest_sessionfinish(session: pytest.Session, exitstatus: int) -> None:
    paths = report.write_reports(EVIDENCE)
    if paths:
        _report_line(session, "conformance evidence: " + ", ".join(str(p) for p in paths))


def _report_line(session: pytest.Session, message: str) -> None:
    reporter = session.config.pluginmanager.get_plugin("terminalreporter")
    if reporter is None:
        print(message)
    else:
        reporter.write_line(message)


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture(scope="session")
def evidence() -> report.Evidence:
    return EVIDENCE


async def run_cases(
    factory: Callable[[], ProviderSetup], case: Scenario, evidence: report.Evidence
) -> None:
    """一条用例体：运行 → 收集证据 → 按 §4.6 决定 pytest 结果。

    非 `pass` 的结论一律用 skip 表达（不把降级策略误报成测试失败），
    `fail` 才是真正的 Contract 违反。
    """
    setup = factory()
    result = await run_scenario(setup, case)
    evidence.record(setup, case.id, result.status, result.notes)
    if result.status == cases.FAIL:
        pytest.fail(f"{case.id}: {result.notes}", pytrace=False)
    if result.status != cases.PASS:
        pytest.skip(f"{case.id}: {result.status} — {result.notes}")
