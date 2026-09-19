"""MCPBackedSkills：skill:// 资源 → Skill，命中 / 未命中 / limit / 基础设施故障。"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from agentkit.api import SkillProvider
from agentkit.skills.mcp_backed import MCPBackedSkills


def resource(name, description="", uri=None):
    return SimpleNamespace(uri=uri or f"skill://{name}", name=name, description=description)


class FakeSession:
    """只实现 MCPBackedSkills 用到的两个方法。"""

    def __init__(self, resources=(), texts=None, raise_on_list=None, raise_on_read=None):
        self.resources = list(resources)
        self.texts = dict(texts or {})
        self.raise_on_list = raise_on_list
        self.raise_on_read = raise_on_read
        self.list_calls = 0
        self.read_calls: list[str] = []

    async def list_resources(self):
        self.list_calls += 1
        if self.raise_on_list is not None:
            raise self.raise_on_list
        return SimpleNamespace(resources=self.resources)

    async def read_resource(self, uri):
        self.read_calls.append(str(uri))
        if self.raise_on_read is not None:
            raise self.raise_on_read
        return SimpleNamespace(contents=[SimpleNamespace(text=self.texts[str(uri)])])


def catalog():
    return FakeSession(
        [
            resource("pdf", "extract tables from pdf files"),
            resource("excel", "pivot tables and formulas"),
            resource("notes.txt", "not a skill", uri="file:///notes.txt"),
        ],
        {"skill://pdf": "步骤一\n步骤二", "skill://excel": "step 1"},
    )


def test_mcp_backed_skills_is_a_skill_provider():
    assert isinstance(MCPBackedSkills(FakeSession()), SkillProvider)


@pytest.mark.anyio
async def test_only_skill_uri_resources_become_skills():
    skills = await MCPBackedSkills(catalog()).search("pdf", limit=9)
    assert [s.name for s in skills] == ["pdf", "excel"]
    assert skills[0].instructions == "步骤一\n步骤二"
    assert skills[0].description == "extract tables from pdf files"


@pytest.mark.anyio
async def test_search_ranks_name_hits_above_description_word_hits():
    ds = MCPBackedSkills(catalog())
    assert [s.name for s in await ds.search("帮我处理 pivot tables 报表")][0] == "excel"
    assert [s.name for s in await ds.search("pdf")][0] == "pdf"


@pytest.mark.anyio
async def test_server_without_skills_returns_empty():
    session = FakeSession(
        [resource("notes", "not a skill", uri="file:///notes.txt")], {"file:///notes.txt": "x"}
    )
    assert await MCPBackedSkills(session).search("pdf") == []


@pytest.mark.anyio
async def test_limit_defaults_to_the_instance_limit_and_can_be_overridden():
    session = catalog()
    assert len(await MCPBackedSkills(session).search("pdf")) == 2          # limit=3 未截断
    assert len(await MCPBackedSkills(session, limit=1).search("pdf")) == 1
    assert len(await MCPBackedSkills(session).search("pdf", limit=2)) == 2
    assert await MCPBackedSkills(session).search("pdf", limit=0) == []


@pytest.mark.anyio
async def test_infrastructure_failure_on_listing_propagates():
    session = FakeSession(raise_on_list=RuntimeError("mcp server down"))
    with pytest.raises(RuntimeError, match="mcp server down"):
        await MCPBackedSkills(session).search("pdf")


@pytest.mark.anyio
async def test_infrastructure_failure_on_read_propagates():
    session = catalog()
    session.raise_on_read = RuntimeError("read failed")
    with pytest.raises(RuntimeError, match="read failed"):
        await MCPBackedSkills(session).search("pdf")


@pytest.mark.anyio
async def test_resource_name_falls_back_to_the_uri_tail():
    session = FakeSession([resource("", "", uri="skill://anonymous")], {"skill://anonymous": "b"})
    (skill,) = await MCPBackedSkills(session).search("anything", limit=1)
    assert (skill.name, skill.description, skill.instructions) == ("anonymous", "", "b")


@pytest.mark.anyio
async def test_non_text_blocks_are_skipped_like_the_tool_provider_does():
    session = FakeSession([resource("pdf", "tables")])

    async def read(uri):
        return SimpleNamespace(contents=[
            SimpleNamespace(text="第一段"), SimpleNamespace(data=b"img"),
            SimpleNamespace(text="第二段"),
        ])

    session.read_resource = read
    (skill,) = await MCPBackedSkills(session).search("pdf", limit=1)
    assert skill.instructions == "第一段\n第二段"


@pytest.mark.anyio
async def test_catalog_is_fetched_once_across_searches():
    session = catalog()
    ds = MCPBackedSkills(session)
    await ds.search("pdf")
    await ds.search("excel")
    assert session.list_calls == 1 and len(session.read_calls) == 2
