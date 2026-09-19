"""V3 生态边界门禁 —— 第三方实现只能 import `agentkit.api`。

对应 V3 Contract Freeze §4.5（及其落地 §七 Gate C）。

`tests/third_party/` 里的四个 fake 是「外部作者」：他们能看到的 agentkit 表面
**只有** `agentkit.api`。只要有一个模块去 import `agentkit.kernel.*`（或任何
其他 agentkit 内部模块），V3 的生态命题就被证伪——扩展又要把 Kernel 公共表面
往外拽。这份测试就是那条边界的可执行证据。

为什么用 `ast` 而不是 grep：只有真正解析成 import 语句才算越界。
字符串、注释、README 里出现的 `agentkit.kernel` 不是 import，
把它们判成违规会让门禁变成「关键词过敏」。
"""
from __future__ import annotations

import ast
import importlib.util
import sys
from pathlib import Path

import pytest

from agentkit.api import (
    ContextTransform,
    Message,
    PermissionPolicy,
    RunContext,
    Skill,
    SkillProvider,
    ToolCall,
    ToolCalls,
    ToolExecutor,
)

THIRD_PARTY = Path(__file__).parent / "third_party"
ROOT = Path(__file__).parents[1]

#: V3 Contract Freeze §4.5 的清单，逐条照抄。
FORBIDDEN_PREFIXES: tuple[str, ...] = (
    "agentkit.kernel.",
    "agentkit.runtime",
    "agentkit.context",
    "agentkit.skills",
    "agentkit.executor",
    "agentkit.memory",
    "agentkit.tools",
    "agentkit.models",
    "agentkit.harness",
)

#: 第三方唯一合法的 agentkit 入口。
API_ROOT = "agentkit.api"

#: Gate C 要求的四个实现（`__init__.py` 只是包文档，不承担实现）。
FAKES = ("executor", "context", "skill", "permission")


def _source_files() -> list[Path]:
    return sorted(THIRD_PARTY.rglob("*.py"))


def _imported_modules(tree: ast.AST) -> list[tuple[int, str]]:
    """返回 (行号, 模块名)。`from X import a` 展开成 X 与 X.a。

    相对 import 被跳过：本目录的 `__init__.py` 是包的顶端，
    相对名最多只能指到同目录的兄弟文件，碰不到 `agentkit` 内部。
    """
    found: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.extend((node.lineno, alias.name) for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            found.append((node.lineno, node.module))
            found.extend(
                (node.lineno, f"{node.module}.{alias.name}") for alias in node.names
            )
    return found


def _load(name: str):
    """按路径加载第三方模块（不依赖 sys.path，也不遮住真实包名）。"""
    path = THIRD_PARTY / f"{name}.py"
    spec = importlib.util.spec_from_file_location(f"_third_party_{name}", path)
    assert spec is not None and spec.loader is not None, f"cannot load {path}"
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _formatted(violations: list[str]) -> str:
    return "\n".join(violations)


def test_third_party_has_no_forbidden_imports() -> None:
    """§4.5 的 FORBIDDEN_PREFIXES 逐条断言。"""
    violations = []
    for path in _source_files():
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for lineno, module in _imported_modules(tree):
            for prefix in FORBIDDEN_PREFIXES:
                # 前缀表里的 "agentkit.kernel." 带尾点；`import agentkit.kernel`
                # 会绕过它，所以顺带比一次去掉尾点的形式。
                if module == prefix.rstrip(".") or module.startswith(prefix):
                    where = path.relative_to(ROOT).as_posix()
                    violations.append(
                        f"{where}:{lineno}: import {module!r} 命中禁用前缀 {prefix!r}"
                    )
    assert not violations, (
        "第三方实现只能依赖 agentkit.api（V3 Contract Freeze §4.5）：\n"
        + _formatted(violations)
    )


def test_third_party_only_imports_agentkit_api() -> None:
    """闭包断言：任何 agentkit 相关的 import 都必须落在 `agentkit.api` 下。

    这条比前缀表更严，因为前缀表漏得掉 `from agentkit import kernel`：
    它的 `node.module` 是 `agentkit`，不对任何前缀成立，
    但 `agentkit.kernel` 照样进了这台机器。§4.5 的原话是
    「第三方实现只能 import agentkit.api」，这里按原话判。
    """
    violations = []
    for path in _source_files():
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for lineno, module in _imported_modules(tree):
            if module != "agentkit" and not module.startswith("agentkit."):
                continue
            if module == API_ROOT or module.startswith(API_ROOT + "."):
                continue
            where = path.relative_to(ROOT).as_posix()
            violations.append(f"{where}:{lineno}: import {module!r} 不在 agentkit.api 下")
    assert not violations, (
        "第三方实现只能 import agentkit.api：\n" + _formatted(violations)
    )


def test_third_party_fakes_actually_use_the_api() -> None:
    """四个实现必须真的 import `agentkit.api`，否则上面的边界证据是空转。

    一个不 import 任何东西的模块当然不会越界——但它也证明不了边界可用。
    """
    for name in FAKES:
        path = THIRD_PARTY / f"{name}.py"
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        modules = {m for _, m in _imported_modules(tree)}
        assert any(
            m == API_ROOT or m.startswith(API_ROOT + ".") for m in modules
        ), f"{path.name} 没有 import agentkit.api，边界证据不成立"


def test_third_party_fakes_satisfy_extension_protocols() -> None:
    """四个 fake 都必须通过对应 `runtime_checkable` Protocol 的 `isinstance`。

    这是「外部实现无需继承、无需注册，只按形状就能被接受」的直接证据。
    """
    executor = _load("executor").FakeThirdPartyExecutor()
    transform = _load("context").FakeThirdPartyTransform()
    skills = _load("skill").FakeThirdPartySkillProvider()
    permission = _load("permission").FakeThirdPartyPermissionPolicy(["read_file"])

    assert isinstance(executor, ToolExecutor)
    assert isinstance(transform, ContextTransform)
    assert isinstance(skills, SkillProvider)
    assert isinstance(permission, PermissionPolicy)


@pytest.mark.anyio
async def test_third_party_fakes_work_through_api_types_only() -> None:
    """走一遍四条扩展点，确认它们拿 `agentkit.api` 的类型就能干活。

    只断言**可观察结果**：结果条数 / `tool_call_id` ownership / 前缀探针 /
    搜索命中 / 白名单判定。这些 fake 是 Gate B 组合测试的地基，
    地基歪了，后面的组合证据全是假的。
    """
    executor = _load("executor").FakeThirdPartyExecutor(fail_on={"boom"})
    results = await executor.execute(
        RunContext(task="t"),
        ToolCalls(calls=[ToolCall("c1", "read_file"), ToolCall("c2", "boom")]),
    )
    assert [r.tool_call_id for r in results] == ["c1", "c2"]
    assert [r.error for r in results] == [False, True]
    assert executor.seen_tasks == ["t"]
    await executor.close()
    assert executor.closed

    transform = _load("context").FakeThirdPartyTransform(keep_last=1)
    messages = [Message("system", "sys"), Message("user", "hi")]
    assert [m.content for m in await transform.apply(messages)] == ["[third-party] hi"]
    assert messages[1].content == "hi", "transform 不得改动输入"

    provider = _load("skill").FakeThirdPartySkillProvider([
        Skill(name="code-review", description="review a diff"),
        Skill(name="search", description="检索资料"),
    ])
    assert [s.name for s in await provider.search("CODE")] == ["code-review"]
    assert await provider.search("review", limit=1) == provider.skills[:1]
    assert [s.name for s in await provider.search("检索")] == ["search"]
    assert await provider.search("nothing-matches") == []

    policy = _load("permission").FakeThirdPartyPermissionPolicy(["read_file"])
    ctx = RunContext(task="t")
    assert await policy.allow(ToolCall("c1", "read_file"), ctx)
    assert not await policy.allow(ToolCall("c2", "write_file"), ctx)
    ctx.stop = True
    assert not await policy.allow(ToolCall("c3", "read_file"), ctx)
