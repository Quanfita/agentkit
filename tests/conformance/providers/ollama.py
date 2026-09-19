"""Ollama Provider setup（Local / Compatibility，Gate C 非阻塞）。

不需要 API key：就绪条件 = httpx 已安装 + `AGENTKIT_OLLAMA_HOST` 上有真实
Ollama 服务 + `AGENTKIT_OLLAMA_MODEL` 已经 pull。

任一条件不满足 → 全部场景 `not_verified`（而不是把环境问题判成 fail）。
"""
from __future__ import annotations

import os

import httpx

from agentkit.models.ollama import OllamaModel, trust_env_for

from . import ProviderSetup, sdk_version

NAME = "ollama"
SDK = "httpx"
ENV_MODEL = "AGENTKIT_OLLAMA_MODEL"
ENV_HOST = "AGENTKIT_OLLAMA_HOST"
DEFAULT_MODEL = "qwen2.5:7b"
DEFAULT_HOST = "http://localhost:11434"


def _probe(host: str, model: str) -> str | None:
    """就绪探测：返回 None 表示就绪，否则返回未就绪原因。

    必须与 Adapter 用同一条代理规则：`urllib` / 默认 httpx 会把 localhost 送进
    系统代理（Windows 注册表 ProxyServer，bypass 列表不被 Python 读取）→ 502。
    """
    base = (host if "://" in host else f"http://{host}").rstrip("/")
    try:
        with httpx.Client(
            base_url=base, timeout=2.0, trust_env=trust_env_for(base),
        ) as client:
            response = client.get("/api/tags")
            response.raise_for_status()
            payload = response.json()
    except (httpx.HTTPError, ValueError, OSError) as exc:
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
