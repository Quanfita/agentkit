"""V2 Contract Freeze 哨兵 —— snapshot 与当前代码的签名漂移门禁。

`docs/freeze/v2/scratch.py` 是 **Freeze snapshot，不是 canonical source**。
这份测试把 snapshot 按 AST 读出来，与**运行时真实对象**逐一比对：

- (a) dataclass 字段名 / 顺序 / 注解 / 默认值（+ kw_only / slots 等 dataclass 选项）
- (b) Protocol（及普通类）的方法名集合
- (c) 每个方法的 `async`/`def` 形态、参数名、参数默认值、返回注解字符串

PEP 563 下两侧拿到的注解都是源码字符串，因此可以直接对文本比对。

任何一处漂移都意味着「V2 契约被改动」：要么同步 `scratch.py` + 升版本，
要么把改动退回去。失败信息给出 unified diff。
"""
from __future__ import annotations

import ast
import difflib
import inspect
from dataclasses import MISSING, fields
from pathlib import Path
from typing import Any, get_args

import pytest

from agentkit.kernel import protocols as kernel_protocols
from agentkit.kernel import state as kernel_state
from agentkit.kernel import types as kernel_types
from agentkit.models import base as models_base

ROOT = Path(__file__).resolve().parents[2]
SCRATCH = ROOT / "docs" / "freeze" / "v2" / "scratch.py"

#: Freeze snapshot 覆盖的 dataclass：名字 → 运行时类。
FROZEN_DATACLASSES: dict[str, type] = {
    "Message": kernel_types.Message,
    "ToolCall": kernel_types.ToolCall,
    "ToolSpec": kernel_types.ToolSpec,
    "ToolResult": kernel_types.ToolResult,
    "MemoryItem": kernel_types.MemoryItem,
    "MemoryInput": kernel_types.MemoryInput,
    "ContextItem": kernel_types.ContextItem,
    "Final": kernel_types.Final,
    "ToolCalls": kernel_types.ToolCalls,
    "RunContext": kernel_state.RunContext,
    "PreparedInput": kernel_protocols.PreparedInput,
    "TextDelta": models_base.TextDelta,
    "ToolCallDelta": models_base.ToolCallDelta,
}

#: Freeze snapshot 覆盖的 Protocol：名字 → 运行时 Protocol。
FROZEN_PROTOCOLS: dict[str, type] = {
    "Model": kernel_protocols.Model,
    "Tool": kernel_protocols.Tool,
    "ToolProvider": kernel_protocols.ToolProvider,
    "ToolExecutor": kernel_protocols.ToolExecutor,
    "Memory": kernel_protocols.Memory,
    "ContextProvider": kernel_protocols.ContextProvider,
    "Runtime": kernel_protocols.Runtime,
    "StreamingModel": models_base.StreamingModel,
}

#: Freeze snapshot 覆盖的类型别名：名字 → 运行时别名对象。
FROZEN_ALIASES: dict[str, Any] = {
    "Role": kernel_types.Role,
    "Action": kernel_types.Action,
    "Delta": models_base.Delta,
}

#: `TerminationReason` 是 Enum（单独比对成员）；`EventBus` 是刻意保留的空壳
#: （形状不在 V2 Freeze 范围，见 scratch.py 注释）。
FROZEN_EXTRA_CLASSES = {"TerminationReason", "EventBus"}

#: snapshot 唯一允许的 import 根（自包含：不 import agentkit 任何东西）。
ALLOWED_IMPORT_ROOTS = {"__future__", "collections", "dataclasses", "enum", "typing"}

#: canonical 模块：既用来做反向完整性检查，也用来读源码找类型别名。
CANONICAL_MODULES = (kernel_types, kernel_state, kernel_protocols, models_base)

_DATACLASS_OPTION_DEFAULTS = {
    "init": True, "repr": True, "eq": True, "order": False, "unsafe_hash": False,
    "frozen": False, "match_args": True, "kw_only": False, "slots": False,
}


# ── AST 侧 ─────────────────────────────────────────────────────────────

def _scratch_tree() -> ast.Module:
    return ast.parse(SCRATCH.read_text(encoding="utf-8"), filename=str(SCRATCH))


SCRATCH_TREE = _scratch_tree()
AST_CLASSES: dict[str, ast.ClassDef] = {
    node.name: node for node in SCRATCH_TREE.body if isinstance(node, ast.ClassDef)
}
AST_ALIASES: dict[str, ast.expr] = {
    target.id: node.value
    for node in SCRATCH_TREE.body
    if isinstance(node, ast.Assign)
    for target in node.targets
    if isinstance(target, ast.Name)
}


def _norm(annotation: str) -> str:
    """剥掉引号与空白：`ast.unparse` 用单引号，PEP 563 字符串保留源码写法。"""
    return "".join(annotation.replace('"', "").replace("'", "").split())


def _ast_annotation(node: ast.expr | None) -> str:
    return _norm(ast.unparse(node)) if node is not None else ""


def _ast_default(node: ast.expr | None) -> str:
    if node is None:
        return "<required>"
    return ast.unparse(node)


def _ast_field_default(node: ast.expr | None) -> str:
    if node is None:
        return "<required>"
    if isinstance(node, ast.Call) and getattr(node.func, "id", None) == "field":
        for kw in node.keywords:
            if kw.arg == "default_factory":
                if isinstance(kw.value, ast.Lambda):
                    return "factory:<lambda>"
                return f"factory:{ast.unparse(kw.value)}"
        return "field"
    return ast.unparse(node)


def _ast_field_lines(cls: ast.ClassDef) -> list[str]:
    return [
        f"{node.target.id}: {_ast_annotation(node.annotation)} = {_ast_field_default(node.value)}"
        for node in cls.body
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name)
    ]


def _ast_dataclass_options(cls: ast.ClassDef) -> dict[str, bool]:
    options = dict(_DATACLASS_OPTION_DEFAULTS)
    for decorator in cls.decorator_list:
        if isinstance(decorator, ast.Call) and getattr(decorator.func, "id", None) == "dataclass":
            for kw in decorator.keywords:
                assert kw.arg in options, f"未知的 @dataclass 选项：{kw.arg}"
                options[kw.arg] = bool(ast.literal_eval(kw.value))
    return options


def _ast_methods(cls: ast.ClassDef) -> dict[str, ast.FunctionDef | ast.AsyncFunctionDef]:
    return {
        node.name: node
        for node in cls.body
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef)
    }


def _ast_param_lines(node: ast.FunctionDef | ast.AsyncFunctionDef) -> list[str]:
    args = node.args
    params = [*args.posonlyargs, *args.args]
    missing = (None,) * (len(params) - len(args.defaults))
    pairs = list(zip(params, [*args.defaults, *missing], strict=True))
    pairs += list(zip(args.kwonlyargs, args.kw_defaults, strict=True))
    return [f"{a.arg}: {_ast_annotation(a.annotation)} = {_ast_default(d)}" for a, d in pairs]


def _ast_signature(node: ast.FunctionDef | ast.AsyncFunctionDef) -> list[str]:
    kind = "async def" if isinstance(node, ast.AsyncFunctionDef) else "def"
    return [
        f"{kind} {node.name}",
        *_ast_param_lines(node),
        f"-> {_ast_annotation(node.returns)}",
    ]


# ── 运行时侧 ───────────────────────────────────────────────────────────

def _runtime_annotation(annotation: object) -> str:
    if annotation is inspect.Parameter.empty:
        return ""
    assert isinstance(annotation, str), (
        f"注解不是 PEP 563 字符串：{annotation!r}"
        "（canonical 模块缺 `from __future__ import annotations`？）"
    )
    return _norm(annotation)


def _runtime_field_default(field: Any) -> str:
    if field.default_factory is not MISSING:
        return f"factory:{field.default_factory.__name__}"
    if field.default is not MISSING:
        return repr(field.default)
    return "<required>"


def _runtime_field_lines(cls: type) -> list[str]:
    return [
        f"{f.name}: {_norm(f.type)} = {_runtime_field_default(f)}" for f in fields(cls)
    ]


def _runtime_dataclass_options(cls: type) -> dict[str, bool]:
    """运行时 dataclass 选项。

    `slots` 在旧版 `_DataclassParams` 上可能不存在，退回 `__slots__` 探测。
    """
    params = cls.__dataclass_params__
    options: dict[str, bool] = {}
    for name, default in _DATACLASS_OPTION_DEFAULTS.items():
        if name == "slots" and not hasattr(params, name):
            options[name] = hasattr(cls, "__slots__")
        else:
            options[name] = bool(getattr(params, name, default))
    return options


def _runtime_methods(cls: type) -> dict[str, Any]:
    return {
        name: obj
        for name, obj in vars(cls).items()
        if inspect.isfunction(obj) and not name.startswith("__")
    }


def _runtime_signature(name: str, fn: Any) -> list[str]:
    signature = inspect.signature(fn)
    parameters = [
        f"{p.name}: {_runtime_annotation(p.annotation)} = "
        f"{'<required>' if p.default is inspect.Parameter.empty else repr(p.default)}"
        for p in signature.parameters.values()
    ]
    kind = "async def" if inspect.iscoroutinefunction(fn) else "def"
    return [
        f"{kind} {name}",
        *parameters,
        f"-> {_runtime_annotation(signature.return_annotation)}",
    ]


# ── 比对 ───────────────────────────────────────────────────────────────

def _fail(label: str, snapshot: list[str], current: list[str]) -> None:
    diff = "\n".join(
        difflib.unified_diff(
            snapshot, current,
            fromfile="freeze snapshot (docs/freeze/v2/scratch.py)",
            tofile="current (agentkit/)",
            lineterm="",
        )
    )
    pytest.fail(f"契约漂移：{label}\n{diff}")


def _compare(label: str, snapshot: list[str], current: list[str]) -> None:
    if snapshot != current:
        _fail(label, snapshot, current)


# ── 测试 ───────────────────────────────────────────────────────────────

def test_scratch_is_self_contained() -> None:
    """snapshot 必须自包含；否则 drift 比对就成了自说自话。"""
    roots: set[str] = set()
    for node in ast.walk(SCRATCH_TREE):
        if isinstance(node, ast.Import):
            roots |= {alias.name.split(".")[0] for alias in node.names}
        elif isinstance(node, ast.ImportFrom):
            module = ("." * node.level) + (node.module or "")
            roots.add(module.lstrip(".").split(".")[0] or "<relative>")
    assert "agentkit" not in roots, "scratch.py 必须自包含：不得 import agentkit"
    unexpected = roots - ALLOWED_IMPORT_ROOTS
    assert not unexpected, f"scratch.py 出现了非 stdlib 依赖：{sorted(unexpected)}"


def test_scratch_covers_exactly_the_frozen_names() -> None:
    expected_classes = set(FROZEN_DATACLASSES) | set(FROZEN_PROTOCOLS) | FROZEN_EXTRA_CLASSES
    assert set(AST_CLASSES) == expected_classes, "snapshot 的类集合与冻结清单不一致"
    assert set(AST_ALIASES) == set(FROZEN_ALIASES), "snapshot 的类型别名集合与冻结清单不一致"


def test_canonical_modules_have_no_unsnapshotted_contract_names() -> None:
    """canonical 出现新的契约类型 → 必须进 snapshot，否则 Freeze 形同虚设。"""
    known = (
        set(FROZEN_DATACLASSES) | set(FROZEN_PROTOCOLS) | FROZEN_EXTRA_CLASSES | set(FROZEN_ALIASES)
    )
    declared: set[str] = set()
    for module in CANONICAL_MODULES:
        tree = ast.parse(Path(module.__file__).read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef) and not node.name.startswith("_"):
                declared.add(node.name)
            elif isinstance(node, ast.Assign) and isinstance(node.value, ast.Subscript | ast.BinOp):
                declared |= {t.id for t in node.targets if isinstance(t, ast.Name)}
    assert declared <= known, f"canonical 新增了未进 snapshot 的契约名：{sorted(declared - known)}"


def _render_options(options: dict[str, bool]) -> list[str]:
    return [f"{key}={value}" for key, value in options.items()]


@pytest.mark.parametrize("name", sorted(FROZEN_DATACLASSES))
def test_dataclass_fields_match(name: str) -> None:
    cls = FROZEN_DATACLASSES[name]
    assert name in AST_CLASSES, f"snapshot 缺少 dataclass {name}"
    _compare(f"{name} 字段", _ast_field_lines(AST_CLASSES[name]), _runtime_field_lines(cls))
    _compare(
        f"{name} @dataclass 选项",
        _render_options(_ast_dataclass_options(AST_CLASSES[name])),
        _render_options(_runtime_dataclass_options(cls)),
    )


@pytest.mark.parametrize("name", sorted(FROZEN_PROTOCOLS))
def test_protocol_methods_match(name: str) -> None:
    cls = FROZEN_PROTOCOLS[name]
    assert name in AST_CLASSES, f"snapshot 缺少 Protocol {name}"
    snapshot = _ast_methods(AST_CLASSES[name])
    current = _runtime_methods(cls)
    _compare(f"{name} 方法名集合", sorted(snapshot), sorted(current))


@pytest.mark.parametrize("name", sorted(FROZEN_PROTOCOLS))
def test_protocol_method_signatures_match(name: str) -> None:
    cls = FROZEN_PROTOCOLS[name]
    snapshot = _ast_methods(AST_CLASSES[name])
    current = _runtime_methods(cls)
    for method in sorted(set(snapshot) & set(current)):
        _compare(
            f"{name}.{method} 签名",
            _ast_signature(snapshot[method]),
            _runtime_signature(method, current[method]),
        )


@pytest.mark.parametrize("name", sorted(FROZEN_PROTOCOLS))
def test_protocol_class_attributes_match(name: str) -> None:
    """Protocol 的数据成员（`Tool.spec` / `Runtime.events`）也在契约里。"""
    cls = FROZEN_PROTOCOLS[name]
    node = AST_CLASSES[name]
    snapshot = sorted(
        f"{n.target.id}: {_ast_annotation(n.annotation)}"
        for n in node.body
        if isinstance(n, ast.AnnAssign) and isinstance(n.target, ast.Name)
    )
    current = sorted(f"{key}: {_norm(value)}" for key, value in cls.__annotations__.items())
    _compare(f"{name} 数据成员", snapshot, current)


def test_termination_reason_members_match() -> None:
    node = AST_CLASSES["TerminationReason"]
    snapshot: list[str] = []
    for statement in node.body:
        if isinstance(statement, ast.Assign) and isinstance(statement.targets[0], ast.Name):
            target = statement.targets[0]
            snapshot.append(f"{target.id} = {ast.literal_eval(statement.value)!r}")
    current = [f"{member.name} = {member.value!r}" for member in kernel_state.TerminationReason]
    _compare("TerminationReason 成员", snapshot, current)


def _ast_alias_members(node: ast.expr) -> list[str]:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return [node.value]
    if isinstance(node, ast.Name):
        return [node.id]
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.BitOr):
        return _ast_alias_members(node.left) + _ast_alias_members(node.right)
    if isinstance(node, ast.Subscript):
        elements = node.slice.elts if isinstance(node.slice, ast.Tuple) else [node.slice]
        return [member for element in elements for member in _ast_alias_members(element)]
    raise AssertionError(f"无法解析的类型别名元素：{ast.dump(node)}")


def _runtime_alias_members(alias: Any) -> list[str]:
    return [member if isinstance(member, str) else member.__name__ for member in get_args(alias)]


@pytest.mark.parametrize("name", sorted(FROZEN_ALIASES))
def test_type_alias_members_match(name: str) -> None:
    _compare(
        f"{name} 别名成员",
        _ast_alias_members(AST_ALIASES[name]),
        _runtime_alias_members(FROZEN_ALIASES[name]),
    )
