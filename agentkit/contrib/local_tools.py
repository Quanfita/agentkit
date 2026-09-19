"""Local Tools —— 一组被 root 目录约束的本地工具。

越界访问不会炸循环：FunctionTool 会把 ValueError 转成 ToolResult(error=True)。
"""
from __future__ import annotations

import asyncio
from pathlib import Path

from ..kernel.types import ToolResult
from ..tools.function import FunctionTool


def make_local_tools(
    root: str | Path = ".",
    *,
    allow_shell: bool = False,
    shell_timeout: float = 30.0,
    max_bytes: int = 100_000,
) -> list[FunctionTool]:
    """生成 read_file / write_file / list_dir（可选 run_shell）。"""
    base = Path(root).resolve()

    def resolve(path: str) -> Path:
        p = Path(path)
        p = p if p.is_absolute() else base / p
        p = p.resolve()
        if p != base and base not in p.parents:
            raise ValueError(f"path escapes root {base}: {path}")
        return p

    def read_file(path: str) -> str:
        """读取 root 目录内某个文本文件的内容。"""
        data = resolve(path).read_bytes()[:max_bytes]
        return data.decode("utf-8", errors="replace")

    def write_file(path: str, content: str) -> str:
        """把文本写入 root 目录内某个文件（覆盖写）。"""
        p = resolve(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
        return f"wrote {len(content)} chars to {p}"

    def list_dir(path: str = ".") -> str:
        """列出 root 目录内某个目录的条目。"""
        p = resolve(path)
        return "\n".join(
            sorted(f"{e.name}/" if e.is_dir() else e.name for e in p.iterdir())
        )

    tools: list[FunctionTool] = [
        FunctionTool(read_file),
        FunctionTool(write_file),
        FunctionTool(list_dir),
    ]

    if allow_shell:

        async def run_shell(command: str) -> ToolResult:
            """在 root 目录内执行一条 shell 命令（有超时）。"""
            proc = await asyncio.create_subprocess_shell(
                command,
                cwd=str(base),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
            )
            try:
                out, _ = await asyncio.wait_for(proc.communicate(), timeout=shell_timeout)
            except asyncio.TimeoutError:
                proc.kill()
                await proc.wait()
                return ToolResult(f"timeout after {shell_timeout}s: {command}", error=True)
            return ToolResult(
                out.decode("utf-8", errors="replace"),
                error=proc.returncode != 0,
                metadata={"returncode": proc.returncode},
            )

        tools.append(FunctionTool(run_shell))

    return tools
