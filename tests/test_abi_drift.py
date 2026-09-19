"""V3 Kernel ABI drift 哨兵 —— snapshot 与运行中 kernel 的签名漂移门禁。

`docs/freeze/v3/scratch.py` 是 **Freeze snapshot，不是 canonical source**。
这份测试把 v2 / v3 两份快照按 AST 读出来，与**运行时真实对象**逐一比对，
并额外强制两件事：

- **v3 快照的 kernel 部分与 v2 快照逐字一致**（V3 命题的判据：Kernel 公共 ABI 不变）；
- **EventBus 的 `on / off / emit` 签名 == `docs/freeze/v3/ABI.md` §1.4 的清单**
  （v2 快照刻意不含 EventBus 形状，所以它的期望值来自 ABI.md 而不是 v2）。

比对口径与 `tests/unit/test_freeze_snapshot_v2.py` 一致：
dataclass 字段名 / 顺序 / 注解 / 默认值 + `@dataclass` 选项、Protocol 方法名集合、
每个方法的 `async`/`def` 形态 + 参数名 + 默认值 + 返回注解字符串、类型别名成员、
Enum 成员。PEP 563 下两侧拿到的注解都是源码字符串，因此直接对文本比对。

任何一处漂移都意味着「Kernel 契约被改动」：V3 期间不允许 —— 见
`docs/freeze/v3/ABI.md` §四（改 ABI = 命题证伪）。失败信息给出 unified diff。
"""
from __future__ import annotations

import ast
import difflib
import inspect
import re
from dataclasses import MISSING, fields
from pathlib import Path
from typing import Any, get_args

import pytest

from agentkit.api import context as api_context
from agentkit.api import executor as api_executor
from agentkit.api import skill as api_skill
from agentkit.kernel import events as kernel_events
from agentkit.kernel import protocols as kernel_protocols
from agentkit.kernel import state as kernel_state
from agentkit.kernel import types as kernel_types

ROOT = Path(__file__).resolve().parents[1]
KERNEL_DIR = ROOT / "agentkit" / "kernel"

#: 两份 Freeze snapshot：v2.5 基线 与 v3 基线。
SNAPSHOTS: dict[str, Path] = {
    "v2": ROOT / "docs" / "freeze" / "v2" / "scratch.py",
    "v3": ROOT / "docs" / "freeze" / "v3" / "scratch.py",
}

#: Freeze snapshot 覆盖的 kernel dataclass：名字 → 运行时类。
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
}

#: Freeze snapshot 覆盖的 kernel Protocol：名字 → 运行时 Protocol。
FROZEN_PROTOCOLS: dict[str, type] = {
    "Model": kernel_protocols.Model,
    "Tool": kernel_protocols.Tool,
    "ToolProvider": kernel_protocols.ToolProvider,
    "ToolExecutor": kernel_protocols.ToolExecutor,
    "Memory": kernel_protocols.Memory,
    "ContextProvider": kernel_protocols.ContextProvider,
    "Runtime": kernel_protocols.Runtime,
}

#: Freeze snapshot 覆盖的 kernel 类型别名：名字 → 运行时别名对象。
FROZEN_ALIASES: dict[str, Any] = {
    "Role": kernel_types.Role,
    "Action": kernel_types.Action,
}

#: 非 dataclass / 非 Protocol 的冻结类：Enum 成员单独比对，EventBus 见 FROZEN_EVENTBUS。
FROZEN_PLAIN_CLASSES = {"TerminationReason", "EventBus"}

#: kernel 公共 ABI 的全部类名（反向完整性检查的期望集合）。
FROZEN_KERNEL_CLASSES = (
    set(FROZEN_DATACLASSES) | set(FROZEN_PROTOCOLS) | FROZEN_PLAIN_CLASSES
)

#: v3 快照的 Public Extension API 部分（V3 §四）—— **不属于** Kernel ABI。
#: 定义源是 `agentkit/api/*.py`；这里同样做漂移比对，否则 Freeze 文档会说谎。
FROZEN_EXTENSION_PROTOCOLS: dict[str, type] = {
    "ContextTransform": api_context.ContextTransform,
    "SkillProvider": api_skill.SkillProvider,
    "PermissionPolicy": api_executor.PermissionPolicy,
}

#: §3.1 冻结的 EventBus 方法名 —— 期望签名从 `docs/freeze/v3/ABI.md` §1.4 读出来
#: （v2 快照刻意不含 EventBus 形状，所以它的期望值只能来自 Freeze 文档本身）。
FROZEN_EVENTBUS_METHODS = ("on", "off", "emit")

#: v3 的 Kernel ABI 快照文档（人读版本）。
ABI_DOC = ROOT / "docs" / "freeze" / "v3" / "ABI.md"

_TEXT_BLOCK = re.compile(r"^```text\r?\n(.*?)^```", re.DOTALL | re.MULTILINE)

#: 每份快照里**超出 kernel ABI** 的额外契约（v2 含 models/base，v3 含扩展 API）。
SNAPSHOT_EXTRA_CLASSES: dict[str, set[str]] = {
    "v2": {"TextDelta", "ToolCallDelta", "StreamingModel"},
    "v3": {"Skill", *FROZEN_EXTENSION_PROTOCOLS},
}
SNAPSHOT_EXTRA_ALIASES: dict[str, set[str]] = {
    "v2": {"Delta"},
    "v3": {"Handler"},
}

#: snapshot 唯一允许的 import 根（自包含：不 import agentkit 任何东西）。
ALLOWED_IMPORT_ROOTS = {"__future__", "collections", "dataclasses", "enum", "typing"}

_DATACLASS_OPTION_DEFAULTS = {
    "init": True, "repr": True, "eq": True, "order": False, "unsafe_hash": False,
    "frozen": False, "match_args": True, "kw_only": False, "slots": False,
}


# ── AST 侧 ─────────────────────────────────────────────────────────────

def _trees() -> dict[str, ast.Module]:
    return {
        name: ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for name, path in SNAPSHOTS.items()
    }


TREES = _trees()
AST_CLASSES: dict[str, dict[str, ast.ClassDef]] = {
    name: {node.name: node for node in tree.body if isinstance(node, ast.ClassDef)}
    for name, tree in TREES.items()
}
AST_ALIASES: dict[str, dict[str, ast.expr]] = {
    name: {
        target.id: node.value
        for node in tree.body
        if isinstance(node, ast.Assign)
        for target in node.targets
        if isinstance(target, ast.Name)
    }
    for name, tree in TREES.items()
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


def _ast_class_attributes(cls: ast.ClassDef) -> list[str]:
    return [
        f"{node.target.id}: {_ast_annotation(node.annotation)}"
        for node in cls.body
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name)
    ]


def _ast_enum_members(cls: ast.ClassDef) -> list[str]:
    return [
        f"{statement.targets[0].id} = {ast.literal_eval(statement.value)!r}"
        for statement in cls.body
        if isinstance(statement, ast.Assign) and isinstance(statement.targets[0], ast.Name)
    ]


def _dataclass_decorator(cls: ast.ClassDef) -> ast.expr | None:
    """匹配 `@dataclass` 与 `@dataclass(...)` 两种写法。"""
    for decorator in cls.decorator_list:
        target = decorator.func if isinstance(decorator, ast.Call) else decorator
        if isinstance(target, ast.Name) and target.id == "dataclass":
            return decorator
    return None


def _ast_is_dataclass(cls: ast.ClassDef) -> bool:
    return _dataclass_decorator(cls) is not None


def _ast_dataclass_options(cls: ast.ClassDef) -> dict[str, bool]:
    options = dict(_DATACLASS_OPTION_DEFAULTS)
    decorator = _dataclass_decorator(cls)
    if isinstance(decorator, ast.Call):
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
    pairs = list(zip(params, [*missing, *args.defaults], strict=True))
    pairs += list(zip(args.kwonlyargs, args.kw_defaults, strict=True))
    return [f"{a.arg}: {_ast_annotation(a.annotation)} = {_ast_default(d)}" for a, d in pairs]


def _ast_signature(node: ast.FunctionDef | ast.AsyncFunctionDef) -> list[str]:
    kind = "async def" if isinstance(node, ast.AsyncFunctionDef) else "def"
    return [
        f"{kind} {node.name}",
        *_ast_param_lines(node),
        f"-> {_ast_annotation(node.returns)}",
    ]


def _compact_param(prefix: str, name: str, annotation: str, default: str) -> str:
    """单行渲染：`self` 这类无注解参数不写多余冒号，其余与源码同形。"""
    typed = f"{name}: {annotation}" if annotation else name
    return f"{prefix}{typed}{'' if default == '<required>' else f' = {default}'}"


def _ast_compact_signature(node: ast.FunctionDef | ast.AsyncFunctionDef) -> str:
    """单行签名（含 `self`）—— 供 EventBus 与 ABI.md §1.4 逐字对读。"""
    args = node.args
    entries: list[str] = []
    params = [*args.posonlyargs, *args.args]
    missing = (None,) * (len(params) - len(args.defaults))
    for arg, default in zip(params, [*missing, *args.defaults], strict=True):
        entries.append(
            _compact_param("", arg.arg, _ast_annotation(arg.annotation), _ast_default(default))
        )
    if args.vararg is not None:
        entries.append(f"*{args.vararg.arg}: {_ast_annotation(args.vararg.annotation)}")
    for arg, default in zip(args.kwonlyargs, args.kw_defaults, strict=True):
        entries.append(
            _compact_param("", arg.arg, _ast_annotation(arg.annotation), _ast_default(default))
        )
    if args.kwarg is not None:
        entries.append(f"**{args.kwarg.arg}: {_ast_annotation(args.kwarg.annotation)}")
    kind = "async def" if isinstance(node, ast.AsyncFunctionDef) else "def"
    returns = _ast_annotation(node.returns)
    return f"{kind} {node.name}({', '.join(entries)}) -> {returns}"


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


def _ast_abi_lines(snapshot: str, name: str) -> list[str]:
    """一个冻结类的 ABI 渲染 —— 用于「v3 快照 == v2 快照」的类间对读。"""
    node = AST_CLASSES[snapshot][name]
    if _ast_is_dataclass(node):
        return [*_ast_field_lines(node), *_render_options(_ast_dataclass_options(node))]
    methods = _ast_methods(node)
    if methods:
        return [
            *(line for method in methods.values() for line in _ast_signature(method)),
            *_ast_class_attributes(node),
        ]
    return _ast_enum_members(node)


def _abi_doc_eventbus_signatures() -> dict[str, str]:
    """从 ABI.md 的 ```text 块里读出 EventBus 的三个签名（§3.1 的期望值）。"""
    blocks = _TEXT_BLOCK.findall(ABI_DOC.read_text(encoding="utf-8"))
    block = next((text for text in blocks if "async def emit(" in text), None)
    assert block is not None, f"{ABI_DOC} §1.4 缺少 EventBus 的 ```text 签名块"
    signatures = {
        stripped.split("(")[0].split()[-1]: stripped
        for line in block.splitlines()
        if (stripped := line.strip()).startswith(("def ", "async def "))
    }
    assert sorted(signatures) == sorted(FROZEN_EVENTBUS_METHODS), (
        f"{ABI_DOC} 必须且只能写下 {list(FROZEN_EVENTBUS_METHODS)} 三个签名，"
        f"实际读到：{sorted(signatures)}"
    )
    return signatures


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
    """运行时 dataclass 选项；`slots` 在旧版 `_DataclassParams` 上退回 `__slots__` 探测。"""
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


def _runtime_compact_signature(name: str, fn: Any) -> str:
    signature = inspect.signature(fn)
    entries: list[str] = []
    for parameter in signature.parameters.values():
        prefix = {
            inspect.Parameter.VAR_POSITIONAL: "*",
            inspect.Parameter.VAR_KEYWORD: "**",
        }.get(parameter.kind, "")
        default = (
            "<required>" if parameter.default is inspect.Parameter.empty
            else repr(parameter.default)
        )
        entries.append(
            _compact_param(
                prefix, parameter.name, _runtime_annotation(parameter.annotation), default
            )
        )
    kind = "async def" if inspect.iscoroutinefunction(fn) else "def"
    returns = _runtime_annotation(signature.return_annotation)
    return f"{kind} {name}({', '.join(entries)}) -> {returns}"


def _runtime_alias_members(alias: Any) -> list[str]:
    return [member if isinstance(member, str) else member.__name__ for member in get_args(alias)]


# ── 比对 ───────────────────────────────────────────────────────────────

def _render_options(options: dict[str, bool]) -> list[str]:
    return [f"{key}={value}" for key, value in options.items()]


def _compare(
    label: str,
    snapshot: list[str],
    current: list[str],
    files: tuple[str, str] = ("freeze snapshot", "current (agentkit/)"),
) -> None:
    if snapshot != current:
        diff = "\n".join(
            difflib.unified_diff(snapshot, current, fromfile=files[0], tofile=files[1], lineterm="")
        )
        pytest.fail(f"契约漂移：{label}\n{diff}")


# ── 快照自身的完整性 ───────────────────────────────────────────────────

@pytest.mark.parametrize("snapshot", sorted(SNAPSHOTS))
def test_snapshot_is_self_contained(snapshot: str) -> None:
    """snapshot 必须自包含；否则 drift 比对就成了自说自话。"""
    roots: set[str] = set()
    for node in ast.walk(TREES[snapshot]):
        if isinstance(node, ast.Import):
            roots |= {alias.name.split(".")[0] for alias in node.names}
        elif isinstance(node, ast.ImportFrom):
            module = ("." * node.level) + (node.module or "")
            roots.add(module.lstrip(".").split(".")[0] or "<relative>")
    assert "agentkit" not in roots, f"{SNAPSHOTS[snapshot]} 必须自包含：不得 import agentkit"
    unexpected = roots - ALLOWED_IMPORT_ROOTS
    assert not unexpected, f"snapshot 出现了非 stdlib 依赖：{sorted(unexpected)}"


@pytest.mark.parametrize("snapshot", sorted(SNAPSHOTS))
def test_snapshot_covers_exactly_the_frozen_names(snapshot: str) -> None:
    expected_classes = FROZEN_KERNEL_CLASSES | SNAPSHOT_EXTRA_CLASSES[snapshot]
    assert set(AST_CLASSES[snapshot]) == expected_classes, "snapshot 的类集合与冻结清单不一致"
    expected_aliases = set(FROZEN_ALIASES) | SNAPSHOT_EXTRA_ALIASES[snapshot]
    assert set(AST_ALIASES[snapshot]) == expected_aliases, "snapshot 的类型别名集合与冻结清单不一致"


# ── V3 命题：kernel 部分与 V2.5 逐字一致 ───────────────────────────────

@pytest.mark.parametrize("name", sorted(FROZEN_KERNEL_CLASSES - {"EventBus"}))
def test_v3_kernel_part_matches_v2_snapshot(name: str) -> None:
    """V3 §三：v3 快照的 kernel 部分与 v2 快照完全一致（EventBus 见下一条）。"""
    for snapshot in sorted(SNAPSHOTS):
        assert name in AST_CLASSES[snapshot], f"{snapshot} 快照缺少冻结类 {name}"
    _compare(
        f"v3 快照 vs v2 快照：{name}",
        _ast_abi_lines("v2", name),
        _ast_abi_lines("v3", name),
        files=("docs/freeze/v2/scratch.py", "docs/freeze/v3/scratch.py"),
    )


def test_v3_eventbus_is_frozen_and_v2_leaves_it_open() -> None:
    """v2 刻意不冻结 EventBus 形状；v3 §3.1 冻结 `on / off / emit`。"""
    v2_methods = _ast_methods(AST_CLASSES["v2"]["EventBus"])
    v3_methods = _ast_methods(AST_CLASSES["v3"]["EventBus"])
    assert v2_methods == {}, "v2 快照的 EventBus 必须保持空壳（V2 保留 Event 对象化空间）"
    assert {
        name: _ast_compact_signature(node) for name, node in v3_methods.items()
    } == _abi_doc_eventbus_signatures()


@pytest.mark.parametrize("name", sorted(FROZEN_ALIASES))
def test_v3_kernel_aliases_match_v2_snapshot(name: str) -> None:
    _compare(
        f"v3 快照 vs v2 快照：{name}",
        _ast_alias_members(AST_ALIASES["v2"][name]),
        _ast_alias_members(AST_ALIASES["v3"][name]),
    )


# ── 运行时 ↔ 快照 ──────────────────────────────────────────────────────

@pytest.mark.parametrize("snapshot", sorted(SNAPSHOTS))
@pytest.mark.parametrize("name", sorted(FROZEN_DATACLASSES))
def test_dataclass_fields_match(snapshot: str, name: str) -> None:
    cls = FROZEN_DATACLASSES[name]
    assert name in AST_CLASSES[snapshot], f"{snapshot} 快照缺少 dataclass {name}"
    node = AST_CLASSES[snapshot][name]
    assert _ast_is_dataclass(node), f"{snapshot} 快照的 {name} 必须是 @dataclass"
    _compare(
        f"{snapshot} 快照 vs runtime：{name} 字段",
        _ast_field_lines(node),
        _runtime_field_lines(cls),
    )
    _compare(
        f"{snapshot} 快照 vs runtime：{name} @dataclass 选项",
        _render_options(_ast_dataclass_options(node)),
        _render_options(_runtime_dataclass_options(cls)),
    )


@pytest.mark.parametrize("snapshot", sorted(SNAPSHOTS))
@pytest.mark.parametrize("name", sorted(FROZEN_PROTOCOLS))
def test_protocol_methods_match(snapshot: str, name: str) -> None:
    node = AST_CLASSES[snapshot][name]
    current = _runtime_methods(FROZEN_PROTOCOLS[name])
    _compare(
        f"{snapshot} 快照 vs runtime：{name} 方法名集合",
        sorted(_ast_methods(node)),
        sorted(current),
    )


@pytest.mark.parametrize("snapshot", sorted(SNAPSHOTS))
@pytest.mark.parametrize("name", sorted(FROZEN_PROTOCOLS))
def test_protocol_method_signatures_match(snapshot: str, name: str) -> None:
    snapshot_methods = _ast_methods(AST_CLASSES[snapshot][name])
    current = _runtime_methods(FROZEN_PROTOCOLS[name])
    for method in sorted(set(snapshot_methods) & set(current)):
        _compare(
            f"{snapshot} 快照 vs runtime：{name}.{method} 签名",
            _ast_signature(snapshot_methods[method]),
            _runtime_signature(method, current[method]),
        )


@pytest.mark.parametrize("snapshot", sorted(SNAPSHOTS))
@pytest.mark.parametrize("name", sorted(FROZEN_PROTOCOLS))
def test_protocol_class_attributes_match(snapshot: str, name: str) -> None:
    """Protocol 的数据成员（`Tool.spec` / `Runtime.events`）也在契约里。"""
    cls = FROZEN_PROTOCOLS[name]
    _compare(
        f"{snapshot} 快照 vs runtime：{name} 数据成员",
        sorted(_ast_class_attributes(AST_CLASSES[snapshot][name])),
        sorted(f"{key}: {_norm(value)}" for key, value in cls.__annotations__.items()),
    )


@pytest.mark.parametrize("snapshot", sorted(SNAPSHOTS))
@pytest.mark.parametrize("name", sorted(FROZEN_ALIASES))
def test_type_alias_members_match(snapshot: str, name: str) -> None:
    _compare(
        f"{snapshot} 快照 vs runtime：{name} 别名成员",
        _ast_alias_members(AST_ALIASES[snapshot][name]),
        _runtime_alias_members(FROZEN_ALIASES[name]),
    )


@pytest.mark.parametrize("snapshot", sorted(SNAPSHOTS))
def test_termination_reason_members_match(snapshot: str) -> None:
    _compare(
        f"{snapshot} 快照 vs runtime：TerminationReason 成员",
        _ast_enum_members(AST_CLASSES[snapshot]["TerminationReason"]),
        [f"{member.name} = {member.value!r}" for member in kernel_state.TerminationReason],
    )


def test_eventbus_signatures_match_abi_doc() -> None:
    """§3.1 冻结的 EventBus 签名 == `docs/freeze/v3/ABI.md` §1.4 写下的清单 == runtime。"""
    documented = _abi_doc_eventbus_signatures()
    current = _runtime_methods(kernel_events.EventBus)
    assert sorted(current) == sorted(documented), (
        "EventBus 的公共方法集合已漂移（V3 §3.1 只冻结 on / off / emit）"
    )
    assert {
        name: _runtime_compact_signature(name, current[name]) for name in documented
    } == documented


# ── 反向完整性：kernel 不得出现未进快照的契约类型 ───────────────────────

def test_kernel_has_no_unsnapshotted_public_classes() -> None:
    """kernel 出现新的公共类 → 必须进 snapshot，否则 Freeze 形同虚设。"""
    declared: set[str] = set()
    for path in sorted(KERNEL_DIR.glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        declared |= {
            node.name
            for node in ast.walk(tree)
            if isinstance(node, ast.ClassDef) and not node.name.startswith("_")
        }
    assert declared <= FROZEN_KERNEL_CLASSES, (
        f"kernel 新增了未进 Freeze snapshot 的契约类型：{sorted(declared - FROZEN_KERNEL_CLASSES)}"
    )


# ── Public Extension API（V3 §四）不属于 Kernel，但同样是冻结契约 ──────

@pytest.mark.parametrize("name", sorted(FROZEN_EXTENSION_PROTOCOLS))
def test_extension_protocol_matches_v3_snapshot(name: str) -> None:
    """v3 快照抄录的 3 个扩展 Protocol 必须与 `agentkit/api/` 逐字一致。"""
    node = AST_CLASSES["v3"][name]
    current = _runtime_methods(FROZEN_EXTENSION_PROTOCOLS[name])
    _compare(
        f"v3 快照 vs agentkit/api：{name} 方法名集合",
        sorted(_ast_methods(node)),
        sorted(current),
    )
    snapshot_methods = _ast_methods(node)
    for method in sorted(set(snapshot_methods) & set(current)):
        _compare(
            f"v3 快照 vs agentkit/api：{name}.{method} 签名",
            _ast_signature(snapshot_methods[method]),
            _runtime_signature(method, current[method]),
        )
