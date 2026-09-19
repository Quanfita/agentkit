"""CLI Harness —— 既是可交互 REPL，也是可脚本化的单次执行入口。

它同时是 Phase 3 的验收物：MCP / Skills / 观测 Hook 都能在这一层装配，
而 kernel 一行不动。
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import sys

from . import __version__
from .agent import Agent
from .context.providers import MemoryContext, SystemPrompt
from .contrib.local_tools import make_local_tools
from .harness.base import Harness
from .kernel.events import EventBus
from .memory.simple import InMemoryMemory
from .observability import CostTracker, Logger, Tracer
from .runtime.default import ContextEngine, DefaultRuntime
from .skills.directory import DirectorySkills
from .toolbox import Toolbox

DEFAULT_SYSTEM = (
    "You are a careful agent. Use tools when they help; "
    "answer in the user's language."
)

HELP = """\
/help            显示本帮助
/tools           列出已注册工具
/skills          列出已加载技能
/system <text>   更新 system prompt（下一条任务生效）
/memory          查看记忆条数与估算 token
/reset           清空记忆
/quit            退出
直接输入内容即执行一次 Agent.run()。
"""


def build_model(name: str, model_name: str | None):
    """按名字惰性构造 Model —— 没装 SDK / 没配 key 时也能跑 echo。"""
    if name == "echo":
        from .models.echo import EchoModel
        return EchoModel()
    if name == "openai":
        from .models.openai import OpenAIModel
        return OpenAIModel(model_name)
    if name == "anthropic":
        from .models.anthropic import AnthropicModel
        return AnthropicModel(model_name)
    if name == "ollama":
        from .models.ollama import OllamaModel
        return OllamaModel(model_name)
    raise ValueError(f"unknown model: {name}")


class CLIHarness(Harness):
    def __init__(self, args: argparse.Namespace) -> None:
        self.system = args.system
        self.max_iterations = args.max_iterations
        self.events = EventBus()

        self.tracer = None if args.no_trace else Tracer(printer=self._err)
        if self.tracer is not None:
            self.events.on("*", self.tracer)
        if args.log_level:
            self.events.on("*", Logger(level=getattr(logging, args.log_level)))

        self.cost = CostTracker(budget=args.budget, printer=self._err)
        self.events.on("model.before", self.cost)

        self.memory = InMemoryMemory()
        self.skills = DirectorySkills(args.skills)
        self.toolbox = Toolbox(make_local_tools(args.root, allow_shell=args.allow_shell))
        self.model = build_model(args.model, args.model_name)
        self.context = ContextEngine([
            SystemPrompt(self.system),
            self.skills,
            MemoryContext(self.memory),
        ])

    @staticmethod
    def _err(text: str) -> None:
        print(text, file=sys.stderr)

    def build_runtime(self) -> DefaultRuntime:
        return DefaultRuntime(
            model=self.model,
            toolbox=self.toolbox,
            context=self.context,
            memory=self.memory,
            events=self.events,
        )

    async def close(self) -> None:
        await self.toolbox.close()
        close = getattr(self.model, "close", None)
        if close is not None:
            await close()


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="agentkit", description=__doc__)
    p.add_argument("--version", action="version", version=f"agentkit {__version__}")
    p.add_argument("--model", default="echo",
                   choices=["echo", "openai", "anthropic", "ollama"])
    p.add_argument("--model-name", default=None,
                   help="模型 id；除 echo 外必填")
    p.add_argument("--system", default=DEFAULT_SYSTEM)
    p.add_argument("--skills", default="skills", help="SKILL.md 根目录")
    p.add_argument("--root", default=".", help="本地工具的活动根目录")
    p.add_argument("--allow-shell", action="store_true", help="额外开放 run_shell 工具")
    p.add_argument("--budget", type=int, default=None, help="估算 token 预算，超限即停")
    p.add_argument("--max-iterations", type=int, default=16)
    p.add_argument("--no-trace", action="store_true", help="关闭事件追踪输出")
    p.add_argument("--log-level", default=None, help="同时用标准 logging 记录事件")
    p.add_argument("-t", "--task", default=None, help="单次执行；省略则进入 REPL")
    return p


async def _command(line: str, agent: Agent, harness: CLIHarness) -> bool:
    """返回 False 表示退出 REPL。"""
    name, _, rest = line[1:].partition(" ")
    name = name.lower()
    if name in ("quit", "exit", "q"):
        return False
    if name == "help":
        print(HELP, end="")
    elif name == "tools":
        specs = await harness.toolbox.specs()
        print("\n".join(f"- {s.name}: {s.description.splitlines()[0] if s.description else ''}"
                        for s in specs) or "(no tools)")
    elif name == "skills":
        print("\n".join(f"- {s.name}: {s.description}" for s in harness.skills.all())
              or "(no skills)")
    elif name == "system":
        harness.system = rest.strip()
        print("[system] updated")
    elif name == "memory":
        print(f"[memory] {len(harness.memory.items)} items, "
              f"~{harness.cost.estimated_tokens} tokens / {harness.cost.calls} calls")
    elif name == "reset":
        harness.memory.items.clear()
        print("[memory] cleared")
    else:
        print(f"unknown command: /{name} (try /help)")
    return True


async def _repl(agent: Agent, harness: CLIHarness) -> int:
    print(f"agentkit {__version__} — model={harness.model.__class__.__name__}, "
          f"/help for commands, /quit to exit")
    while True:
        try:
            line = (await asyncio.to_thread(input, "› ")).strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return 0
        if not line:
            continue
        if line.startswith("/"):
            if not await _command(line, agent, harness):
                return 0
            continue
        try:
            result = await agent.run(
                line,
                max_iterations=harness.max_iterations,
                system=harness.system,
            )
        except Exception as e:
            print(f"[error] {type(e).__name__}: {e}", file=sys.stderr)
            continue
        print(result)


async def _run(args: argparse.Namespace) -> int:
    if args.log_level:
        logging.basicConfig(
            level=getattr(logging, args.log_level),
            format="%(levelname)s %(name)s %(message)s",
        )
    harness = CLIHarness(args)
    async with Agent(harness) as agent:
        if args.task is not None:
            print(await agent.run(
                args.task,
                max_iterations=args.max_iterations,
                system=harness.system,
            ))
            return 0
        return await _repl(agent, harness)


def main(argv: list[str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    if args.model != "echo" and not args.model_name:
        parser.error("--model-name is required unless --model echo")
    try:
        return asyncio.run(_run(args))
    except KeyboardInterrupt:
        return 130
