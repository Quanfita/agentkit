"""V3 Architecture Firewall —— 负向 Contract（V3 §五）。

五条规则，全部用 `ast` 静态扫描（不看 grep 结果），对应 V3 §5.2 的五个测试名：

```text
规则 1  test_kernel_no_external_imports          kernel 只允许 stdlib + kernel 内部
规则 2  test_kernel_no_agentkit_non_kernel_imports  kernel 不得 import 任何非 kernel 模块
规则 3  test_kernel_no_manager_classes           kernel 不得定义 Manager/Registry/... 与能力实现
规则 4  test_kernel_file_count                   kernel 只有 5 个模块 + __init__.py
规则 5  test_agent_loop_line_count               agent_loop <= 55 行
```

**V3 期间这五条必须始终全绿：任何一个变红，V3 命题（Kernel 不增长）当场证伪。**

相对 import（`from .types import ...`）在 kernel 内部解析，规则 1 / 2 都放行 ——
它是 kernel 内部组织方式，不是对外依赖。
"""
from __future__ import annotations

import ast
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
KERNEL_DIR = ROOT / "agentkit" / "kernel"

#: 规则 4 的期望：kernel 只允许 5 个模块 + `__init__.py`（V3 §九）。
KERNEL_MODULES = {
    "__init__.py", "types.py", "state.py", "events.py", "protocols.py", "loop.py",
}

#: 规则 1 的允许集：Python 标准库（`sys.stdlib_module_names` 是权威清单）。
STDLIB_MODULES = frozenset(sys.stdlib_module_names)

#: 规则 2：kernel 不得触碰的 agentkit 非 kernel 模块（V3 §5.1）。
FORBIDDEN_AGENTKIT_MODULES = (
    "agentkit.api",
    "agentkit.runtime",
    "agentkit.context",
    "agentkit.skills",
    "agentkit.executor",
    "agentkit.memory",
    "agentkit.tools",
    "agentkit.models",
    "agentkit.harness",
    "agentkit.observability",
    "agentkit.cli",
    "agentkit.contrib",
)

#: 规则 2 唯一被允许的 agentkit 命名空间。
KERNEL_NAMESPACE = "agentkit.kernel"

#: 规则 3 的命名模式（`*Provider` 除外：ToolProvider / ContextProvider 是 Kernel 语义）。
BANNED_CLASS_SUFFIXES = ("Manager", "Registry", "Factory", "Adapter")
PROVIDER_EXEMPT_SUFFIX = "Provider"

#: 规则 3 的另一半：具体能力实现不得进 kernel（V3 §6 / §九 点名的那批）。
CAPABILITY_CLASS_NAMES = frozenset({
    # Context 层
    "ContextEngine", "BudgetCompactor", "BudgetTransform", "SlidingWindowTransform",
    "DedupeTransform", "SystemPriorityTransform", "TrimMiddleTransform",
    # Skill 层
    "SkillDirectory", "DirectorySkills", "MCPBackedSkills",
    # Executor 层
    "PermissionExecutor", "AllowListPolicy", "DenyListPolicy", "InteractivePolicy",
    "RetryExecutor", "TimeoutExecutor", "ParallelExecutor", "SequentialExecutor",
    "Toolbox",
    # Runtime / Memory / Model / Harness 层
    "DefaultRuntime", "InMemoryMemory", "EchoModel", "ScriptedModel",
    "Logger", "Tracer", "CostTracker", "Agent", "Harness",
})

#: 规则 5 的预算（沿用 V2 度量口径：非空非注释行）。
AGENT_LOOP_LINE_BUDGET = 55


def _kernel_files() -> list[Path]:
    return sorted(KERNEL_DIR.rglob("*.py"))


def _parse(path: Path) -> ast.Module:
    return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


def _absolute_imports(path: Path) -> list[str]:
    """文件的绝对 import 模块名（相对 import 在 kernel 内部解析，不在此列）。"""
    modules: list[str] = []
    for node in ast.walk(_parse(path)):
        if isinstance(node, ast.Import):
            modules += [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            modules.append(node.module)
    return modules


def _kernel_classes() -> list[tuple[Path, ast.ClassDef]]:
    return [
        (path, node)
        for path in _kernel_files()
        for node in ast.walk(_parse(path))
        if isinstance(node, ast.ClassDef)
    ]


# ── 规则 1 / 2：依赖方向（主判据） ─────────────────────────────────────

def test_kernel_no_external_imports() -> None:
    """规则 1：kernel 的 import 只能来自 stdlib 或 kernel 内部，不得有第三方包。"""
    violations = [
        f"{path.name}: {module}"
        for path in _kernel_files()
        for module in _absolute_imports(path)
        if module.split(".")[0] != "agentkit"
        and module.split(".")[0] not in STDLIB_MODULES
    ]
    assert not violations, "kernel 出现了第三方依赖：\n" + "\n".join(violations)


def test_kernel_no_agentkit_non_kernel_imports() -> None:
    """规则 2：kernel 不得 import api / runtime / context / skills / executor / ...
    —— agentkit 内任何非 kernel 模块。"""
    violations: list[str] = []
    for path in _kernel_files():
        for module in _absolute_imports(path):
            if not module.startswith("agentkit"):
                continue
            if module == KERNEL_NAMESPACE or module.startswith(f"{KERNEL_NAMESPACE}."):
                continue
            target = next(
                (name for name in FORBIDDEN_AGENTKIT_MODULES if module.startswith(name)), module
            )
            violations.append(f"{path.name}: {module}  →  触碰 {target}")
    assert not violations, (
        "kernel 出现了指向非 kernel 模块的依赖（依赖方向被反转）：\n" + "\n".join(violations)
    )


# ── 规则 3：类名模式 + 能力实现（辅助判据） ────────────────────────────

def test_kernel_no_manager_classes() -> None:
    """规则 3：kernel 内不得定义 `*Manager` / `*Registry` / `*Factory` / `*Adapter`
    （`*Provider` 除外：ToolProvider / ContextProvider 是 Kernel 语义），
    也不得定义具体能力实现（BudgetCompactor / RetryExecutor / ...）。

    **依赖方向是主判据，关键词扫描为辅**（V3 §5.1）：这条规则会漏掉刻意改名的
    能力类，真正的结构性保证来自规则 1 + 2 —— kernel 无法 import 任何非 kernel
    模块，因此它即便定义了什么也接不进来；`tests/test_abi_drift.py` 的反向完整性
    检查再兜住「新增未进 Freeze 的类」。
    """
    violations: list[str] = []
    for path, node in _kernel_classes():
        if node.name in CAPABILITY_CLASS_NAMES:
            violations.append(f"{path.name}:{node.lineno}: 具体能力实现类 `{node.name}`")
            continue
        if node.name.endswith(BANNED_CLASS_SUFFIXES) and not node.name.endswith(
            PROVIDER_EXEMPT_SUFFIX
        ):
            violations.append(f"{path.name}:{node.lineno}: 被禁的类名模式 `{node.name}`")
    assert not violations, (
        "kernel 出现了 Manager/Registry/Factory/Adapter 或能力实现类：\n" + "\n".join(violations)
    )


# ── 规则 4：文件数 ─────────────────────────────────────────────────────

def test_kernel_file_count() -> None:
    """规则 4：kernel/ 只能有 5 个模块 + `__init__.py`（多一个文件就是新层）。"""
    actual = {path.relative_to(KERNEL_DIR).as_posix() for path in _kernel_files()}
    assert actual == KERNEL_MODULES, (
        f"kernel/ 文件集合漂移：\n  多出：{sorted(actual - KERNEL_MODULES)}\n"
        f"  缺失：{sorted(KERNEL_MODULES - actual)}"
    )


# ── 规则 5：agent_loop 预算 ────────────────────────────────────────────

def test_agent_loop_line_count() -> None:
    """规则 5：`agent_loop` <= 55 行（非空非注释行，沿用 V2 度量口径）。"""
    path = KERNEL_DIR / "loop.py"
    source = path.read_text(encoding="utf-8")
    functions = [
        node
        for node in ast.walk(_parse(path))
        if isinstance(node, ast.AsyncFunctionDef) and node.name == "agent_loop"
    ]
    assert len(functions) == 1, "loop.py 必须且只能定义一个 agent_loop"
    function = functions[0]
    body = source.splitlines()[function.body[0].lineno - 1 : function.body[-1].end_lineno]
    code_lines = [line for line in body if line.strip() and not line.lstrip().startswith("#")]
    assert len(code_lines) <= AGENT_LOOP_LINE_BUDGET, (
        f"agent_loop 代码行已膨胀到 {len(code_lines)} 行（预算 {AGENT_LOOP_LINE_BUDGET}）"
    )
