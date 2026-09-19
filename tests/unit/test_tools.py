"""本地工具：schema 生成 + FunctionTool 返回值语义。"""
from __future__ import annotations

import json

import pytest

from agentkit.kernel.types import ToolResult
from agentkit.tools.function import FunctionTool, tool
from agentkit.tools.schema import schema_from_signature


def test_schema_maps_types_and_required():
    def fn(a: str, b: int, c: float, d: bool, e: list, f: dict, g: str = "x"):
        """docstring 会被当 description。"""

    assert schema_from_signature(fn) == {
        "type": "object",
        "properties": {
            "a": {"type": "string"},
            "b": {"type": "integer"},
            "c": {"type": "number"},
            "d": {"type": "boolean"},
            "e": {"type": "array"},
            "f": {"type": "object"},
            "g": {"type": "string"},
        },
        "required": ["a", "b", "c", "d", "e", "f"],
    }


def test_schema_handles_string_annotations_and_unknown_types():
    def fn(a: "int", b: "list", c: "weird", d):   # noqa: UP037, F821
        pass

    props = schema_from_signature(fn)["properties"]
    assert props == {
        "a": {"type": "integer"},
        "b": {"type": "array"},
        "c": {"type": "string"},
        "d": {"type": "string"},
    }


def test_schema_skips_self_and_cls():
    class Holder:
        def method(self, a: int):
            pass

        @classmethod
        def cm(cls, b: str):
            pass

    assert list(schema_from_signature(Holder.method)["properties"]) == ["a"]
    assert list(schema_from_signature(Holder.cm)["properties"]) == ["b"]


@pytest.mark.anyio
async def test_function_tool_reads_name_description_and_schema():
    def read_file(path: str) -> str:
        """读取文件内容。"""
        return path

    ft = FunctionTool(read_file)
    assert ft.spec.name == "read_file"
    assert ft.spec.description == "读取文件内容。"
    assert ft.spec.parameters["required"] == ["path"]


@pytest.mark.anyio
async def test_overrides_win_over_introspection():
    def fn(a: int) -> str:
        """ignored"""
        return "x"

    ft = FunctionTool(
        fn, name="custom", description="d",
        parameters={"type": "object", "properties": {}},
    )
    assert (ft.spec.name, ft.spec.description) == ("custom", "d")
    assert ft.spec.parameters == {"type": "object", "properties": {}}


@pytest.mark.anyio
async def test_return_value_normalization():
    async def as_text(value: str) -> str:
        return value

    def as_dict() -> dict:
        return {"a": 1, "中文": "好"}

    def as_result() -> ToolResult:
        return ToolResult(content="explicit", metadata={"n": 1})

    assert (await FunctionTool(as_text, name="t").run({"value": "hi"})).content == "hi"
    assert (await FunctionTool(as_dict, name="d").run({})).content == json.dumps(
        {"a": 1, "中文": "好"}, ensure_ascii=False
    )
    got = await FunctionTool(as_result, name="r").run({})
    assert got.content == "explicit" and got.metadata == {"n": 1}


@pytest.mark.anyio
async def test_tool_exception_is_captured_as_error_result():
    def boom(a: int) -> str:
        raise ValueError("bad input")

    out = await FunctionTool(boom, name="boom").run({"a": 1})
    assert out == ToolResult(content="ValueError: bad input", error=True)


@pytest.mark.anyio
async def test_wrong_arguments_are_captured_as_error_result():
    def fn(a: int) -> str:
        return str(a)

    out = await FunctionTool(fn, name="fn").run({"b": 1})
    assert out.error is True and out.content.startswith("TypeError:")


@pytest.mark.anyio
async def test_tool_decorator_bare_and_called():
    @tool
    def bare(a: str) -> str:
        """bare doc"""
        return a

    @tool(name="aliased", description="explicit")
    def _aliased(a: str) -> str:
        return a

    assert bare.spec.name == "bare" and bare.spec.description == "bare doc"
    assert _aliased.spec.name == "aliased" and _aliased.spec.description == "explicit"
    assert (await bare.run({"a": "v"})).content == "v"


def test_decorated_callable_is_usable_outside_the_kernel():
    @tool
    def length(text: str) -> int:
        """len"""
        return len(text)

    # 装饰器返回 FunctionTool，不是原始函数：协议优先于便利
    assert isinstance(length, FunctionTool)
    assert length.spec.parameters["properties"] == {"text": {"type": "string"}}
