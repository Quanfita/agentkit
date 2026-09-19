"""OpenAI Provider setup（Primary Conformance，Gate A 必过）。"""
from __future__ import annotations

import os

from agentkit.models.openai import OpenAIModel

from . import ProviderSetup, sdk_version

NAME = "openai"
SDK = "openai"
ENV_KEY = "OPENAI_API_KEY"
ENV_MODEL = "AGENTKIT_OPENAI_MODEL"
DEFAULT_MODEL = "gpt-4o-mini"


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
        build=OpenAIModel,
        sdk_roots=(SDK,),
        missing=tuple(missing),
    )
