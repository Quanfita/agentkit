"""Anthropic Provider setup（Primary Conformance，Gate A 必过）。"""
from __future__ import annotations

import os

from agentkit.models.anthropic import AnthropicModel

from . import ProviderSetup, sdk_version

NAME = "anthropic"
SDK = "anthropic"
ENV_KEY = "ANTHROPIC_API_KEY"
ENV_MODEL = "AGENTKIT_ANTHROPIC_MODEL"
DEFAULT_MODEL = "claude-3-5-sonnet-latest"


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
        build=AnthropicModel,
        sdk_roots=(SDK,),
        missing=tuple(missing),
    )
