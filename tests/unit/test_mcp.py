"""MCP：ToolProvider 适配 + 真实 stdio server 端到端。"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from agentkit.kernel.protocols import ToolProvider
from agentkit.kernel.types import ToolCall, ToolCalls, ToolResult
from agentkit.toolbox import Toolbox
from agentkit.tools.mcp import MCPProvider, stdio_provider

SERVER = Path(__file__).with_name("mcp_echo_server.py")


class FakeSession:
    def __init__(self, tools, result=None, raise_on_call=None):
        self._tools = tools
        self._result = result
        self._raise = raise_on_call
        self.calls: list[tuple[str, dict]] = []

    async def list_tools(self):
        return SimpleNamespace(tools=self._tools)

    async def call_tool(self, name, arguments):
        self.calls.append((name, arguments))
        if self._raise is not None:
            raise self._raise
        return self._result


def tool_defn(name="echo", description="Echo text", schema=None):
    return SimpleNamespace(
        name=name, description=description,
        inputSchema=schema or {"type": "object", "properties": {"text": {"type": "string"}}},
    )


def text_result(*texts, is_error=False):
    return SimpleNamespace(
        content=[SimpleNamespace(text=t) for t in texts], isError=is_error,
    )


def test_provider_shape_matches_the_protocol():
    assert isinstance(MCPProvider(FakeSession([])), ToolProvider)


@pytest.mark.anyio
async def test_tools_are_exposed_as_kernel_toolspecs():
    provider = MCPProvider(FakeSession([tool_defn()]))
    (spec,) = [t.spec for t in await provider.tools()]
    assert spec.name == "echo" and spec.description == "Echo text"
    assert spec.parameters["properties"] == {"text": {"type": "string"}}


@pytest.mark.anyio
async def test_missing_input_schema_falls_back_to_empty_object():
    defn = SimpleNamespace(name="raw", description=None, inputSchema=None)
    provider = MCPProvider(FakeSession([defn]))
    (tool,) = await provider.tools()
    assert tool.spec.parameters == {"type": "object", "properties": {}}
    assert tool.spec.description == ""


@pytest.mark.anyio
async def test_call_tool_concatenates_text_and_skips_non_text_blocks():
    session = FakeSession(
        [tool_defn()],
        result=SimpleNamespace(
            content=[SimpleNamespace(text="第一段"), SimpleNamespace(data=b"img"),
                     SimpleNamespace(text="第二段")],
            isError=False,
        ),
    )
    (tool,) = await MCPProvider(session).tools()
    out = await tool.run({"text": "x"})
    assert out == ToolResult(content="第一段\n第二段")
    assert session.calls == [("echo", {"text": "x"})]


@pytest.mark.anyio
async def test_server_reported_failure_becomes_error_result():
    session = FakeSession([tool_defn()], result=text_result("bad args", is_error=True))
    (tool,) = await MCPProvider(session).tools()
    out = await tool.run({})
    assert out.error is True and out.content == "bad args"


@pytest.mark.anyio
async def test_transport_exception_becomes_error_result():
    session = FakeSession([tool_defn()], raise_on_call=RuntimeError("pipe closed"))
    (tool,) = await MCPProvider(session).tools()
    out = await tool.run({})
    assert out == ToolResult(content="RuntimeError: pipe closed", error=True)


@pytest.mark.anyio
async def test_close_uses_callback_then_aclose_fallback():
    seen = []

    class Session:
        async def aclose(self):
            seen.append("aclose")

        async def list_tools(self):
            return SimpleNamespace(tools=[])

    await MCPProvider(Session()).close()
    assert seen == ["aclose"]

    async def callback():
        seen.append("callback")

    # 有回调时优先回调：session 的宿主由 Harness 决定
    await MCPProvider(Session(), close=callback).close()
    assert seen == ["aclose", "callback"]


@pytest.mark.anyio
async def test_real_stdio_mcp_server_end_to_end():
    pytest.importorskip("mcp")

    async def scenario():
        async with stdio_provider(sys.executable, [str(SERVER)]) as provider:
            box = Toolbox().add_provider(provider)
            specs = await box.specs()
            assert [s.name for s in specs] == ["echo"]
            assert specs[0].parameters["required"] == ["text"]

            (ok,) = await box.execute(ToolCalls([ToolCall("1", "echo", {"text": "hi"})]))
            assert ok == ToolResult(tool_call_id="1", content="echo:hi")

            (bad,) = await box.execute(ToolCalls([ToolCall("2", "echo", {})]))
            assert bad.error is True

    await asyncio.wait_for(scenario(), timeout=90)
