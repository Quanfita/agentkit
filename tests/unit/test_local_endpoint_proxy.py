"""真机验证发现的缺陷：本地端点被系统代理劫持。

httpx 默认 `trust_env=True` → `urllib.request.getproxies()` 在 Windows 上只读
注册表的 ProxyEnable / ProxyServer，**忽略 ProxyOverride（bypass 列表）**，
于是 `localhost:11434` 也走系统代理（实测 502 Bad Gateway）。

端到端证明在 `tests/conformance`：本机开着系统代理（127.0.0.1:7890）时，
Path B（`models/ollama.py`）的 6 个场景仍然全部 `pass`。
"""
from __future__ import annotations

import pytest

from agentkit.models.ollama import OllamaModel, trust_env_for


@pytest.mark.parametrize("host", [
    "http://localhost:11434",
    "localhost:11434",
    "http://127.0.0.1:11434",
    "http://127.0.0.2:11434",
    "http://[::1]:11434",
    "http://[::1]",
])
def test_loopback_hosts_never_trust_the_system_proxy(host):
    assert trust_env_for(host) is False


@pytest.mark.parametrize("host", [
    "https://api.deepseek.com",
    "http://192.168.1.10:11434",
    "http://ollama.internal:11434",
    "http://10.0.0.5:11434",
])
def test_remote_hosts_keep_the_system_proxy_configuration(host):
    assert trust_env_for(host) is True


def test_loopback_rule_applies_to_the_adapter_host():
    assert trust_env_for(OllamaModel("m", host="http://127.0.0.1:11434").host) is False
    assert trust_env_for(OllamaModel("m", host="http://gpu-box:11434").host) is True
