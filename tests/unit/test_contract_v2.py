"""V2 §9.1 契约测试 —— Kernel 扩张但不膨胀。

V2 给 Kernel 加了数据契约（ToolCalls.content / ToolResult.tool_call_id /
ToolExecutor / TerminationReason），这份文件同时是「没长歪」的哨兵：
目录树、依赖方向、词表、事件归属都必须原样。
"""
from __future__ import annotations

import ast
import re
from pathlib import Path

from agentkit.kernel import protocols, state, types
from agentkit.kernel.protocols import Runtime, ToolExecutor
from agentkit.kernel.state import RunContext, TerminationReason
from agentkit.kernel.types import ToolCall, ToolCalls, ToolResult

ROOT = Path(__file__).resolve().parents[2]
PKG = ROOT / "agentkit"
KERNEL = PKG / "kernel"

FROZEN_KERNEL_MODULES = {"types.py", "state.py", "events.py", "protocols.py", "loop.py"}
FORBIDDEN_KERNEL_MODULES = {"executor.py", "context_engine.py", "stream.py", "planner.py"}
FORBIDDEN_DEPENDENCIES = {"runtime", "contrib", "tools", "memory", "executor", "skills", "models"}
KERNEL_INTERNAL = {"types", "state", "events", "protocols"}
STDLIB_ALLOWED = {"__future__", "asyncio", "collections", "dataclasses", "enum",
                  "inspect", "traceback", "typing"}
ALLOWED_CAPABILITY_STRINGS = {"model.before", "model.after"}
LOOP_EVENT_STRINGS = {
    "agent.start", "model.before", "model.after", "iteration.done",
    "agent.finish_error", "agent.error", "agent.end",
}


def kernel_sources() -> list[Path]:
    return sorted(KERNEL.glob("*.py"))


def parse(path: Path) -> ast.Module:
    return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


def import_roots(tree: ast.Module) -> set[str]:
    """每个被 import 的模块的第一段（相对导入取 . 之后的第一段）。"""
    roots: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            roots |= {alias.name.split(".")[0] for alias in node.names}
        elif isinstance(node, ast.ImportFrom):
            module = ("." * node.level) + (node.module or "")
            if module.lstrip("."):
                roots.add(module.lstrip(".").split(".")[0])
    return roots


def string_constants(tree: ast.Module) -> set[str]:
    return {
        node.value for node in ast.walk(tree)
        if isinstance(node, ast.Constant) and isinstance(node.value, str)
    }


def docstring_constants(tree: ast.Module) -> set[str]:
    """文档字符串的**原始**常量值（get_docstring 会 dedent/strip，对不上 AST）。"""
    found: set[str] = set()
    scopes = (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)
    for node in ast.walk(tree):
        if not isinstance(node, scopes) or not node.body:
            continue
        first = node.body[0]
        if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant) \
                and isinstance(first.value.value, str):
            found.add(first.value.value)
    return found


def loop_function() -> ast.AsyncFunctionDef:
    return next(
        node for node in ast.walk(parse(KERNEL / "loop.py"))
        if isinstance(node, ast.AsyncFunctionDef) and node.name == "agent_loop"
    )


# ── §9.1 尺寸与目录 ─────────────────────────────────────


def test_kernel_module_set_is_unchanged():
    modules = {p.name for p in kernel_sources()} - {"__init__.py"}
    assert modules == FROZEN_KERNEL_MODULES


def test_kernel_gained_no_forbidden_module():
    assert {p.name for p in kernel_sources()} & FORBIDDEN_KERNEL_MODULES == set()


def test_kernel_within_the_v2_line_budget():
    total = sum(len(p.read_text(encoding="utf-8").splitlines()) for p in kernel_sources())
    assert total <= 500, f"kernel 已膨胀到 {total} 行（V2 预算 500）"


def test_agent_loop_within_the_v2_line_budget():
    fn = loop_function()
    src = (KERNEL / "loop.py").read_text(encoding="utf-8")
    body = src.splitlines()[fn.body[0].lineno - 1: fn.body[-1].end_lineno]
    code_lines = [line for line in body if line.strip() and not line.lstrip().startswith("#")]
    assert len(code_lines) <= 55, f"agent_loop 代码行 {len(code_lines)} 超过 V2 预算 55"


# ── §9.1 词表与依赖方向 ─────────────────────────────────


def test_loop_emits_only_its_own_frozen_event_names():
    tree = parse(KERNEL / "loop.py")
    event_strings = string_constants(tree) - docstring_constants(tree)
    assert event_strings == LOOP_EVENT_STRINGS


def test_loop_keeps_only_the_two_frozen_capability_words():
    tree = parse(KERNEL / "loop.py")
    capability = {
        s for s in string_constants(tree) - docstring_constants(tree)
        if re.search(r"\b(model|memory|tool|toolbox|mcp|skill|context)\b", s)
    }
    assert capability == ALLOWED_CAPABILITY_STRINGS


def test_new_v2_events_are_not_emitted_by_the_loop():
    text = (KERNEL / "loop.py").read_text(encoding="utf-8")
    for event in ("executor.before", "executor.after", "model.delta"):
        assert event not in text


def test_kernel_has_zero_knowledge_of_streaming():
    for path in kernel_sources():
        roots = import_roots(parse(path))
        assert "models" not in roots, f"{path.name} 引入了 models/"
        text = path.read_text(encoding="utf-8").lower()
        assert "stream" not in text and "delta" not in text, f"{path.name} 提到了 streaming"


def test_kernel_imports_only_stdlib_and_kernel_modules():
    for path in kernel_sources():
        roots = import_roots(parse(path))
        assert roots & FORBIDDEN_DEPENDENCIES == set(), f"{path.name} 依赖 {roots}"
        assert roots <= STDLIB_ALLOWED | KERNEL_INTERNAL, f"{path.name} 依赖 {roots}"


# ── §9.1 新增数据契约 ───────────────────────────────────


def test_tool_calls_carries_content():
    assert ToolCalls([ToolCall("1", "t")]).content == ""
    assert ToolCalls([ToolCall("1", "t")], content="先说明再调用").content == "先说明再调用"


def test_tool_result_is_in_kernel_types_with_tool_call_id():
    assert ToolResult().tool_call_id == ""
    assert ToolResult(tool_call_id="c1", content="ok").tool_call_id == "c1"
    assert ToolResult.__module__ == "agentkit.kernel.types"


def test_tool_executor_protocol_shape():
    assert getattr(ToolExecutor, "_is_protocol", False) is True
    assert getattr(Runtime, "_is_protocol", False) is True
    for name in ("execute", "close"):
        assert hasattr(ToolExecutor, name), f"ToolExecutor 少了 {name}"
    # V2.5 §2.1：per-call 原语是内部 helper，不属于公共契约
    assert not hasattr(ToolExecutor, "execute_one")


def test_run_context_has_termination_reason():
    ctx = RunContext(task="t")
    assert ctx.reason is None
    ctx.reason = TerminationReason.STOPPED
    assert ctx.reason is TerminationReason.STOPPED
    assert "reason" in RunContext.__dataclass_fields__


def test_termination_reason_is_a_four_value_str_enum():
    assert [r.value for r in TerminationReason] == [
        "final", "max_iterations", "stopped", "error",
    ]
    assert all(isinstance(r, str) for r in TerminationReason)
    assert state.TerminationReason is TerminationReason


def test_v2_additions_did_not_reshape_other_frozen_types():
    assert [f for f in ToolResult.__dataclass_fields__] == [
        "tool_call_id", "content", "error", "metadata",
    ]
    assert [f for f in ToolCalls.__dataclass_fields__] == ["calls", "content"]
    assert {"Message", "ToolSpec", "MemoryItem", "MemoryInput",
            "ContextItem", "Final", "Action", "ToolCall"} <= {
        n for n in dir(types) if not n.startswith("_")
    }
    assert protocols.PreparedInput.__dataclass_fields__.keys() == {"messages", "tools"}
