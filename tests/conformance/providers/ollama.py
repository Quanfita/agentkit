"""Ollama Provider setup（Local / Compatibility，Gate C 非阻塞）。

不需要 API key：就绪条件 = httpx 已安装 + `AGENTKIT_OLLAMA_HOST` 上有真实
Ollama 服务 + `AGENTKIT_OLLAMA_MODEL` 已经 pull。

任一条件不满足 → 全部场景 `not_verified`（而不是把环境问题判成 fail）。
"""
from __future__ import annotations

import json
import os
from urllib.error import URLError
from urllib.request import urlopen

from agentkit.models.ollama import OllamaModel

from . import ProviderSetup, sdk_version

NAME = "ollama"
SDK = "httpx"
ENV_MODEL = "AGENTKIT_OLLAMA_MODEL"
ENV_HOST = "AGENTKIT_OLLAMA_HOST"
DEFAULT_MODEL = "qwen2.5:7b"
DEFAULT_HOST = "http://localhost:11434"


def _probe(host: str, model: str) -> str | None:
    """就绪探测：返回 None 表示就绪，否则返回未就绪原因。"""
    base = host if "://" in host else f"http://{host}"
    try:
        with urlopen(f"{base.rstrip('/')}/api/tags", timeout=2.0) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except (OSError, URLError, ValueError) as exc:
        return f"unreachable {ENV_HOST}={host} ({type(exc).__name__})"
    names = {
        entry.get("name")
        for entry in payload.get("models") or []
        if isinstance(entry, dict)
    }
    if model not in names:
        return f"model {model!r} not pulled on {host}"
    return None


def provider() -> ProviderSetup:
    missing: list[str] = []
    version = sdk_version(SDK)
    if version is None:
        missing.append(f"{SDK} package")
    host = os.environ.get(ENV_HOST, DEFAULT_HOST)
    model = os.environ.get(ENV_MODEL, DEFAULT_MODEL)
    reason = _probe(host, model)
    if reason:
        missing.append(reason)
    return ProviderSetup(
        name=NAME,
        sdk_name=SDK,
        sdk_version=version or "not-installed",
        model=model,
        build=lambda model_name: OllamaModel(model_name, host=host),
        sdk_roots=(SDK,),
        missing=tuple(missing),
    )
