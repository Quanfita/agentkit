"""验收清单的可执行版本（文档 §十三）。

这些断言锁死的是架构契约本身：越权改动（把能力塞进 Loop、
把 Message 塞进 Memory、让 Agent 长出第二个构造参数）会在这里失败。
"""
from __future__ import annotations

import ast
import inspect
import re
from pathlib import Path

import pytest

from agentkit.agent import Agent
from agentkit.kernel.state import RunContext

ROOT = Path(__file__).resolve().parents[2]
PKG = ROOT / "agentkit"
KERNEL = PKG / "kernel"

CAPABILITY_WORDS = {
    "model", "models", "memory", "memories", "tool", "tools", "toolbox",
    "mcp", "skill", "skills", "context", "provider", "providers", "harness",
    "openai", "anthropic", "ollama", "sqlite", "vector",
}

# §十 冻结的事件清单里有 model.before / model.after 两个名字，
# 它们是 EventBus 的契约，不是 Loop 的能力词表。
FROZEN_EVENT_STRINGS = {"model.before", "model.after"}


def sources(package: Path) -> list[Path]:
    return sorted(package.rglob("*.py"))


def parse(path: Path) -> ast.Module:
    return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


def words(text: str) -> set[str]:
    return set(re.findall(r"[a-z]+", text.lower()))


def import_map(tree: ast.Module) -> dict[str, str]:
    """{本地名: 模块名}；`from __future__ import annotations` 记作 __future__ 模块。"""
    out: dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            module = ("." * node.level) + (node.module or "")
            for alias in node.names:
                out[alias.asname or alias.name] = module
        elif isinstance(node, ast.Import):
            for alias in node.names:
                out[alias.asname or alias.name.split(".")[0]] = alias.name
    return out


# ── 尺寸与词表 ──────────────────────────────────────────


def test_kernel_stays_within_its_line_budget():
    """V2 §2 把预算从 400 上调到 500（当前实测值见 README）。"""
    total = sum(len(p.read_text(encoding="utf-8").splitlines()) for p in sources(KERNEL))
    assert total <= 500, f"kernel 已膨胀到 {total} 行"


def test_agent_loop_body_stays_compact():
    """按「可执行逻辑」计量：语句数与非空非注释行数（V2 §2 阈值 55）。

    物理行径含空行与注释，会对注释这件好事收税，所以尺寸哨兵盯逻辑体量。
    """
    src = (KERNEL / "loop.py").read_text(encoding="utf-8")
    fn = next(
        node for node in ast.walk(ast.parse(src))
        if isinstance(node, ast.AsyncFunctionDef) and node.name == "agent_loop"
    )
    statements = [n for n in ast.walk(fn) if isinstance(n, ast.stmt)]
    body = src.splitlines()[fn.body[0].lineno - 1: fn.body[-1].end_lineno]
    code_lines = [line for line in body if line.strip() and not line.lstrip().startswith("#")]

    assert len(statements) <= 55, f"agent_loop 语句数已膨胀到 {len(statements)}"
    assert len(code_lines) <= 55, f"agent_loop 代码行已膨胀到 {len(code_lines)}"


def test_loop_vocabulary_has_no_capability_names():
    tree = parse(KERNEL / "loop.py")
    identifiers: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            identifiers.add(node.id)
        elif isinstance(node, ast.Attribute):
            identifiers.add(node.attr)
        elif isinstance(node, ast.arg):
            identifiers.add(node.arg)
        elif isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
            identifiers.add(node.name)
        elif isinstance(node, ast.alias):
            identifiers.add(node.asname or node.name.split(".")[0])
    offending = {i for i in identifiers if i.lower() in CAPABILITY_WORDS}
    assert offending == set(), f"Loop 词表里出现了能力名: {sorted(offending)}"


def test_loop_only_mentions_capability_words_in_frozen_event_names():
    tree = parse(KERNEL / "loop.py")
    strings = {
        node.value for node in ast.walk(tree)
        if isinstance(node, ast.Constant) and isinstance(node.value, str)
    }
    offending = {s for s in strings if words(s) & CAPABILITY_WORDS}
    assert offending == FROZEN_EVENT_STRINGS, sorted(offending)


def test_loop_imports_only_kernel_contract_modules():
    imports = import_map(parse(KERNEL / "loop.py"))
    # asyncio 是 V2.5 §2.3 的 CancelledError 分支所需的标准库，不是能力层依赖
    assert set(imports.values()) <= {".protocols", ".state", ".types", "__future__", "asyncio"}
    assert {n for n in imports if n not in ("annotations", "asyncio")} == {
        "Runtime", "RunContext", "TerminationReason", "Final",
    }


# ── Agent / Kernel 表面 ────────────────────────────────


def test_agent_init_takes_only_harness():
    params = list(inspect.signature(Agent.__init__).parameters)
    assert params == ["self", "harness"]


def test_agent_supports_async_with_and_close():
    assert callable(getattr(Agent, "__aenter__", None))
    assert callable(getattr(Agent, "__aexit__", None))
    assert callable(Agent.close)


def test_frozen_public_api_surface_exists():
    """§十一 Public/Stable 清单：删掉任何一个都会在这里失败。"""
    from agentkit.harness.base import Harness
    from agentkit.toolbox import Toolbox

    frozen = (
        (Toolbox, ["register", "add_provider", "refresh", "specs", "close"]),
        (Harness, ["build_runtime", "close"]),
        (Agent, ["run", "close", "__aenter__", "__aexit__"]),
    )
    for obj, names in frozen:
        missing = [n for n in names if not hasattr(obj, n)]
        assert missing == [], f"{obj.__name__} 少了冻结 API: {missing}"


def test_agent_run_signature_is_frozen():
    """V2 §6：run/run_ctx 都是 (task, **kw)，kw 透传给 RunContext。"""
    for method in (Agent.run, Agent.run_ctx):
        params = inspect.signature(method).parameters
        assert list(params) == ["self", "task", "kw"]
        assert params["kw"].kind is inspect.Parameter.VAR_KEYWORD

    defaults = RunContext(task="t")          # 默认值仍由 Kernel 契约给
    assert (defaults.max_iterations, defaults.system) == (16, "")


def test_memory_layer_never_mentions_message():
    for path in sources(PKG / "memory"):
        text = path.read_text(encoding="utf-8")
        names = {
            node.id for node in ast.walk(ast.parse(text))
            if isinstance(node, ast.Name)
        } | {
            node.attr for node in ast.walk(ast.parse(text))
            if isinstance(node, ast.Attribute)
        }
        assert "Message" not in names, f"{path.name} 里出现了 Message"
        assert "message" not in words(text), f"{path.name} 里出现了 message"


def test_kernel_exports_the_frozen_contract():
    from agentkit.kernel import loop, protocols, state, types

    assert {n for n in dir(types) if not n.startswith("_")} >= {
        "Message", "ToolCall", "ToolSpec", "ToolResult",
        "MemoryItem", "MemoryInput", "ContextItem", "Final", "ToolCalls", "Action",
    }
    assert hasattr(state, "RunContext")
    public = {n for n in dir(protocols) if not n.startswith("_")}
    assert public >= {
        "Model", "Tool", "ToolProvider", "Memory", "ContextProvider", "Runtime",
        "PreparedInput",
    }
    assert hasattr(loop, "agent_loop")


# ── 扩展点边界 ─────────────────────────────────────────


VENDOR_FILES = {
    # V2.5 封版：DeepSeek 只是 models/openai.py 的预设别名，不再自己碰 SDK
    "openai": {"models/openai.py"},
    "anthropic": {"models/anthropic.py"},
    "httpx": {"models/ollama.py"},
    "mcp": {"tools/mcp.py"},
}


@pytest.mark.parametrize("vendor,allowed", sorted(VENDOR_FILES.items()))
def test_vendor_sdks_are_confined_to_their_adapter(vendor, allowed):
    users = set()
    for path in sources(PKG):
        # 只认**绝对导入**：相对导入（.openai）是包内模块，不是厂商 SDK
        modules = {
            m for m in import_map(parse(path)).values() if m and not m.startswith(".")
        }
        roots = {m.split(".")[0] for m in modules}
        if vendor in roots:
            users.add(path.relative_to(PKG).as_posix())
    assert users == allowed, f"{vendor} 出现在 {sorted(users - allowed)}"


def test_kernel_has_no_imports_outside_the_kernel():
    for path in sources(KERNEL):
        for module in import_map(parse(path)).values():
            if module is None or not module.startswith("."):
                continue
            assert module in {".types", ".state", ".events", ".protocols"}, \
                f"{path.name} 引入了 kernel 之外的模块 {module}"


# ── README 契约 ────────────────────────────────────────


def test_readme_first_line_is_the_architecture_promise():
    first = (ROOT / "README.md").read_text(encoding="utf-8").splitlines()[0]
    assert first == "换掉任意模块，都不用动 agent_loop。"
