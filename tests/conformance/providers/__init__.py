"""Provider setup（Conformance fixture）。

这里只做组装：把 `agentkit.models.*` 的真机 Adapter 装配成测试用的 Model，
并描述 sdk / model / 环境是否就绪。消息与流的转换一律复用生产代码，
fixture 不复制任何 Adapter 逻辑（§4.1）。

每个 Provider 一个模块，暴露 `provider() -> ProviderSetup`。
"""
from __future__ import annotations

import importlib.metadata
from collections.abc import Callable
from dataclasses import dataclass

from agentkit.kernel.protocols import Model
from agentkit.kernel.types import ToolSpec
from agentkit.runtime.default import ContextEngine, DefaultRuntime
from agentkit.toolbox import Toolbox
from agentkit.tools.function import tool

from ..cases import Scenario


@tool
def weather(city: str) -> str:
    """查询某个城市的当前天气（conformance fixture，不访问网络）。"""
    return f"{city}: 晴, 24°C"


TOOLS = {weather.spec.name: weather}


def tools_for(case: Scenario) -> list:
    """场景声明需要的 Tool 实例。"""
    return [TOOLS[name] for name in case.tools]


def sdk_version(distribution: str) -> str | None:
    """已安装 SDK 版本；未安装 → None（该 Provider 标记为未就绪）。"""
    try:
        return importlib.metadata.version(distribution)
    except importlib.metadata.PackageNotFoundError:
        return None


@dataclass(slots=True)
class ProviderSetup:
    """一个 Provider 的 Conformance 装配（fixture，非生产 Adapter）。"""

    name: str
    sdk_name: str
    sdk_version: str
    model: str
    build: Callable[[str], Model]
    sdk_roots: tuple[str, ...] = ()
    missing: tuple[str, ...] = ()
    unsupported: tuple[str, ...] = ()   # 该 Provider 已知不支持的能力（场景 id）

    def new_model(self, model_name: str | None = None) -> Model:
        """按场景构建 Model；`model_name` 覆盖时用于 Error 场景。"""
        return self.build(model_name or self.model)

    async def tool_specs(self, case: Scenario) -> list[ToolSpec]:
        return await Toolbox(tools_for(case)).specs()

    def runtime(self, model: Model, case: Scenario) -> DefaultRuntime:
        """Agent 层场景用的 Runtime —— 组装生产实现，不复制 Runtime 逻辑。"""
        return DefaultRuntime(
            model=model,
            toolbox=Toolbox(tools_for(case)),
            context=ContextEngine(),
        )


__all__ = [
    "TOOLS",
    "ProviderSetup",
    "sdk_version",
    "tools_for",
    "weather",
]
