"""DeepSeek Provider setup（OpenAI-compatible，附加兼容性 Provider）。

SDK 侧变化维度记 openai（`DeepSeekModel` 复用 OpenAI wire format），
模型侧由 `AGENTKIT_DEEPSEEK_MODEL` 决定。
"""
from __future__ import annotations

import os

from agentkit.models.deepseek import DeepSeekModel

from . import ProviderSetup, sdk_version

NAME = "deepseek"
SDK = "openai"
ENV_KEY = "DEEPSEEK_API_KEY"
ENV_MODEL = "AGENTKIT_DEEPSEEK_MODEL"
DEFAULT_MODEL = "deepseek-chat"


def provider() -> ProviderSetup:
    missing: list[str] = []
    if not os.environ.get(ENV_KEY):
        missing.append(ENV_KEY)
    version = sdk_version(SDK)
    if version is None:
        missing.append(f"{SDK} package")
    return ProviderSetup(
        name=NAME,
        sdk_name=SDK,
        sdk_version=version or "not-installed",
        model=os.environ.get(ENV_MODEL, DEFAULT_MODEL),
        build=DeepSeekModel,
        sdk_roots=(SDK,),
        missing=tuple(missing),
    )
