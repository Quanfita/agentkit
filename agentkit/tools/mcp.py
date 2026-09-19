"""MCP —— 只是 ToolProvider 的一个实现。"""
from __future__ import annotations

from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from typing import Any

from ..kernel.types import ToolResult, ToolSpec


class _MCPTool:
    def __init__(self, session, defn):
        self.session = session
        self.spec = ToolSpec(
            name=defn.name,
            description=defn.description or "",
            parameters=getattr(defn, "inputSchema", None)
            or {"type": "object", "properties": {}},
        )

    async def run(self, arguments: dict[str, Any]) -> ToolResult:
        try:
            r = await self.session.call_tool(self.spec.name, arguments)
        except Exception as e:
            return ToolResult(content=f"{type(e).__name__}: {e}", error=True)
        text = "\n".join(c.text for c in r.content if hasattr(c, "text"))
        return ToolResult(content=text, error=bool(getattr(r, "isError", False)))


class MCPProvider:
    """MCP 只是 ToolProvider 的一个实现。Loop 永远不知道 MCP 存在。

    session 的生命周期归创建者所有：Harness 若通过 `stdio_provider`
    打开 session，则关闭动作由那个 async context manager 完成，
    这里传 close=None 即可；自己管理 session 的调用方把关闭回调传进来。
    """

    def __init__(
        self,
        session,
        close: Callable[[], Awaitable[None]] | None = None,
    ) -> None:
        self.session = session
        self._close = close

    async def tools(self):
        resp = await self.session.list_tools()
        return [_MCPTool(self.session, t) for t in resp.tools]

    async def close(self):
        if self._close is not None:
            await self._close()
            return
        aclose = getattr(self.session, "aclose", None)
        if aclose is not None:
            await aclose()


@asynccontextmanager
async def stdio_provider(
    command: str,
    args: list[str] | None = None,
    env: dict[str, str] | None = None,
) -> AsyncIterator[MCPProvider]:
    """启动一个 stdio MCP server 并交出 ToolProvider。

    ```python
    async with stdio_provider("uvx", ["mcp-server-git"]) as provider:
        toolbox.add_provider(provider)
    ```

    with 块退出时 session 与子进程一起回收。
    """
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    params = StdioServerParameters(command=command, args=list(args or []), env=env)
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            yield MCPProvider(session)
