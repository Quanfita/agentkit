"""V2.5 Provider Conformance 套件。

两层矩阵（§3.1）：

  Contract 层    normal / tool / error / stream_text      必须 pass
  Capability 层  parallel_tool / stream_tool              尽力验证

真机用例打 `@pytest.mark.conformance`，默认被 `pyproject.toml` 的
`addopts = '-q -m "not conformance"'` 排除；显式运行：

    python -m pytest tests/conformance -m conformance -v
"""
