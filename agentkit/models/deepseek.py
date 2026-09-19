"""DeepSeek 适配器：OpenAI-compatible /chat/completions。

DeepSeek 的 wire format 与 OpenAI 同构（含 tool calling 与流式），
所以这里只覆盖「客户端构造 + 默认值」，消息/工具/流的转换全部复用
`OpenAIModel`，避免出现第二套映射逻辑。

配置（环境变量）：

    DEEPSEEK_API_KEY      必需，API key
    AGENTKIT_DEEPSEEK_MODEL  默认 deepseek-chat（可用 /models 查实际 id）
    DEEPSEEK_BASE_URL     默认 https://api.deepseek.com
"""
from __future__ import annotations

import os

from .openai import OpenAIModel

DEFAULT_BASE_URL = "https://api.deepseek.com"
DEFAULT_MODEL = "deepseek-chat"


class DeepSeekModel(OpenAIModel):
    def __init__(
        self,
        model: str | None = None,
        client=None,
        *,
        base_url: str | None = None,
        api_key: str | None = None,
        **kwargs,
    ) -> None:
        model = model or os.environ.get("AGENTKIT_DEEPSEEK_MODEL", DEFAULT_MODEL)
        owns_client = client is None
        if client is None:
            from openai import AsyncOpenAI
            api_key = api_key or os.environ.get("DEEPSEEK_API_KEY")
            if not api_key:
                raise RuntimeError(
                    "DEEPSEEK_API_KEY is not set (or pass api_key=... / client=...)"
                )
            client = AsyncOpenAI(
                api_key=api_key,
                base_url=base_url or os.environ.get("DEEPSEEK_BASE_URL", DEFAULT_BASE_URL),
            )
        super().__init__(model, client=client, **kwargs)
        self._owns_client = owns_client      # 自建的客户端才由我们关闭
