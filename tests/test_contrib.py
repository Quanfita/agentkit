"""contrib：SQLite Memory / Vector Memory / Local Tools。"""
from __future__ import annotations

import json
import sys

import pytest

from agentkit.contrib.local_tools import make_local_tools
from agentkit.contrib.sqlite_memory import SQLiteMemory
from agentkit.contrib.vector_memory import VectorMemory, cosine
from agentkit.kernel.protocols import Memory
from agentkit.kernel.types import MemoryInput


def by_name(tools):
    return {t.spec.name: t for t in tools}


# ── Local Tools ────────────────────────────────────────


@pytest.mark.anyio
async def test_local_tools_round_trip_inside_root(tmp_path):
    tools = by_name(make_local_tools(tmp_path))
    assert set(tools) == {"read_file", "write_file", "list_dir"}
    assert set(tools["read_file"].spec.parameters["properties"]) == {"path"}
    assert "path escapes root" not in tools["write_file"].spec.description

    out = await tools["write_file"].run({"path": "notes/a.txt", "content": "内容"})
    assert out.error is False and "wrote 2 chars" in out.content
    assert (tmp_path / "notes" / "a.txt").read_text(encoding="utf-8") == "内容"

    assert (await tools["read_file"].run({"path": "notes/a.txt"})).content == "内容"
    listing = await tools["list_dir"].run({"path": "."})
    assert listing.content.splitlines() == ["notes/"]
    assert (await tools["list_dir"].run({})).content == "notes/"


@pytest.mark.anyio
async def test_local_tools_refuse_to_escape_root(tmp_path):
    tools = by_name(make_local_tools(tmp_path))
    out = await tools["read_file"].run({"path": "../../etc/passwd"})
    assert out.error is True and "path escapes root" in out.content


@pytest.mark.anyio
async def test_shell_tool_is_opt_in(tmp_path):
    assert "run_shell" not in by_name(make_local_tools(tmp_path))


@pytest.mark.anyio
async def test_shell_tool_reports_output_and_failure(tmp_path):
    shell = by_name(make_local_tools(tmp_path, allow_shell=True))["run_shell"]

    ok = await shell.run({"command": f'"{sys.executable}" -c "print(42)"'})
    assert ok.error is False and "42" in ok.content

    bad = await shell.run({"command": f'"{sys.executable}" -c "raise SystemExit(3)"'})
    assert bad.error is True and bad.metadata["returncode"] == 3


@pytest.mark.anyio
async def test_shell_tool_kills_on_timeout(tmp_path):
    shell = by_name(
        make_local_tools(tmp_path, allow_shell=True, shell_timeout=0.5)
    )["run_shell"]
    out = await shell.run({"command": f'"{sys.executable}" -c "import time; time.sleep(5)"'})
    assert out.error is True and out.content.startswith("timeout after 0.5s")


# ── SQLite Memory ──────────────────────────────────────


@pytest.mark.anyio
async def test_sqlite_memory_recalls_by_keyword(tmp_path):
    mem = SQLiteMemory(tmp_path / "m.db", top_k=2)
    assert isinstance(mem, Memory)
    await mem.remember(MemoryInput(task="部署", result="部署脚本在 deploy.sh"))
    await mem.remember(MemoryInput(task="测试", result="测试命令是 pytest -q"))
    await mem.remember(MemoryInput(task="空结果", result=""))

    recalled = await mem.recall("部署脚本在哪")
    assert recalled[0].content == "部署脚本在 deploy.sh"
    assert recalled[0].kind == "episodic"
    assert recalled[0].metadata["task"] == "部署"

    await mem.close()


@pytest.mark.anyio
async def test_sqlite_memory_persists_across_instances(tmp_path):
    db = tmp_path / "m.db"
    first = SQLiteMemory(db)
    await first.remember(MemoryInput(task="t", result="跨进程结论"))
    await first.close()

    second = SQLiteMemory(db)
    assert [i.content for i in await second.recall("结论")] == ["跨进程结论"]
    assert json.loads(second.dump())[0]["content"] == "跨进程结论"
    await second.close()


# ── Vector Memory ──────────────────────────────────────


def bag_of_words(text: str) -> list[float]:
    return [
        float(text.count("部署")),
        float(text.count("测试")),
        float(text.count("记忆")),
    ]


def test_cosine_handles_zero_vectors():
    assert cosine([1.0, 0.0], [1.0, 0.0]) == pytest.approx(1.0)
    assert cosine([0.0, 0.0], [1.0, 0.0]) == 0.0
    assert cosine([1.0, 0.0], [0.0, 1.0]) == pytest.approx(0.0)


@pytest.mark.anyio
async def test_vector_memory_ranks_by_similarity():
    mem = VectorMemory(lambda texts: [bag_of_words(t) for t in texts], top_k=1)
    await mem.remember(MemoryInput(task="a", result="部署 部署 部署"))
    await mem.remember(MemoryInput(task="b", result="测试"))
    await mem.remember(MemoryInput(task="c", result=""))

    recalled = await mem.recall("部署 部署 部署")
    assert [i.content for i in recalled] == ["部署 部署 部署"]
    assert recalled[0].metadata["task"] == "a"
    assert mem.top_k == 1 and len(mem._items) == 2


@pytest.mark.anyio
async def test_vector_memory_min_score_filters_and_async_embed_works():
    async def embed(texts):
        return [bag_of_words(t) for t in texts]

    mem = VectorMemory(embed, min_score=0.9)
    await mem.remember(MemoryInput(task="a", result="部署"))
    assert await mem.recall("测试 测试") == []
    assert [i.content for i in await mem.recall("部署")] == ["部署"]


@pytest.mark.anyio
async def test_vector_memory_recall_is_empty_before_any_write():
    mem = VectorMemory(lambda texts: [bag_of_words(t) for t in texts])
    assert await mem.recall("任意") == []


@pytest.mark.anyio
async def test_vector_memory_trims_to_max_items():
    mem = VectorMemory(lambda texts: [bag_of_words(t) for t in texts], max_items=2)
    for i in range(4):
        await mem.remember(MemoryInput(task=str(i), result=f"部署{i}"))
    assert len(mem._items) == 2 and len(mem._vectors) == 2
    assert [i.metadata["task"] for i in mem._items] == ["2", "3"]
