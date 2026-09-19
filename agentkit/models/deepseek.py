"""DeepSeek 预设 —— **不是**一条独立的 normalization path。

DeepSeek 的 wire format 与 OpenAI 同构，所以这里**没有适配器实现**：
它只是把 base_url / 默认模型 / API key 来源预设好的 thin alias，
消息转换、工具 schema、流式归一化全部走 `models/openai.py` 那一份代码。

这对 V2.5 的 Gate A（独立 normalization path 计数）是关键的：

    Path A  models/openai.py     ← OpenAI 服务端 / DeepSeek 服务端（同一份代码，两个服务端）
    Path B  models/ollama.py     ← 独立实现
    Path C  models/anthropic.py  ← 独立实现

所以 DeepSeek 的证据是 **Path A 的跨服务端交叉验证**，不是「多了一条 path」。

配置（环境变量）：

    DEEPSEEK_API_KEY         必需，API key
    AGENTKIT_DEEPSEEK_MODEL  默认 deepseek-flash（可用 /models 查实际 id）
    DEEPSEEK_BASE_URL        默认 https://api.deepseek.com
"""
from __future__ import annotations

import os

from .openai import OpenAIModel

DEFAULT_BASE_URL = "https://api.deepseek.com"
DEFAULT_MODEL = "deepseek-flash"


def DeepSeekModel(
    model: str | None = None,
    client=None,
    *,
    base_url: str | None = None,
    api_key: str | None = None,
    **kwargs,
) -> OpenAIModel:
    """返回一个**走 OpenAI normalization path** 的 `OpenAIModel`。

    故意是函数而不是类：一个 `class DeepSeekModel(OpenAIModel)` 在 Python 语义上
    暗示「独立类型」，而这里连一行 normalization 代码都没有 —— 降级为别名后，
    「独立 path 数量」在代码层就能一眼看清。
    """
    model = model or os.environ.get("AGENTKIT_DEEPSEEK_MODEL", DEFAULT_MODEL)
    key = api_key or os.environ.get("DEEPSEEK_API_KEY")
    if client is None and not key:
        raise RuntimeError(
            "DEEPSEEK_API_KEY is not set (or pass api_key=... / client=...)"
        )
    return OpenAIModel(
        model,
        client=client,
        base_url=base_url or os.environ.get("DEEPSEEK_BASE_URL", DEFAULT_BASE_URL),
        api_key=key,
        **kwargs,
    )
