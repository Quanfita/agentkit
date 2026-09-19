"""FastMCP stdio server —— 供 test_mcp.py 做真实子进程端到端验证。"""
from mcp.server.fastmcp import FastMCP

server = FastMCP("echo")


@server.tool()
def echo(text: str) -> str:
    """Echo the given text back."""
    return f"echo:{text}"


if __name__ == "__main__":
    server.run()
