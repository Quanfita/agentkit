"""DirectorySkills：frontmatter 解析 / 检索打分 / 指令注入。"""
from __future__ import annotations

import pytest
from support import make_ctx

from agentkit.skills.directory import DirectorySkills
from agentkit.skills.skill import Skill


def write_skill(root, name: str, frontmatter: str, body: str = "步骤一\n步骤二"):
    d = root / name
    d.mkdir(parents=True, exist_ok=True)
    (d / "SKILL.md").write_text(frontmatter + body, encoding="utf-8")
    return d


@pytest.fixture
def skills_root(tmp_path):
    write_skill(tmp_path, "pdf",
                "---\nname: pdf\ndescription: extract tables from pdf files\n---\n")
    write_skill(tmp_path, "excel",
                "---\nname: excel\ndescription: pivot tables and formulas\n---\n")
    (tmp_path / "notes.txt").write_text("not a skill")
    (tmp_path / "empty").mkdir()
    return tmp_path


def test_load_reads_frontmatter_and_skips_non_skills(skills_root):
    skills = {s.name: s for s in DirectorySkills(str(skills_root)).all()}
    assert set(skills) == {"pdf", "excel"}
    assert skills["pdf"].description == "extract tables from pdf files"
    assert skills["pdf"].instructions == "步骤一\n步骤二"


def test_load_without_frontmatter_uses_dir_name_and_full_body(tmp_path):
    (tmp_path / "plain").mkdir()
    (tmp_path / "plain" / "SKILL.md").write_text("只有正文", encoding="utf-8")
    (skill,) = DirectorySkills(str(tmp_path)).all()
    assert (skill.name, skill.description, skill.instructions) == ("plain", "", "只有正文")


def test_missing_root_is_empty_not_an_error(tmp_path):
    assert DirectorySkills(str(tmp_path / "nope")).all() == []


@pytest.mark.anyio
async def test_search_prefers_name_hit_then_description_words(skills_root):
    ds = DirectorySkills(str(skills_root))
    assert [s.name for s in await ds.search("帮我处理 pdf 文件")][0] == "pdf"
    assert [s.name for s in await ds.search("做个 pivot tables 报表")][0] == "excel"


@pytest.mark.anyio
async def test_search_honours_top_k(skills_root):
    ds = DirectorySkills(str(skills_root), top_k=1)
    assert len(await ds.search("pdf")) == 1
    assert len(await ds.search("pdf", k=2)) == 2


@pytest.mark.anyio
async def test_provide_injects_matched_instructions(skills_root):
    ds = DirectorySkills(str(skills_root), top_k=1)
    (item,) = await ds.provide(make_ctx("解析 pdf 表格"))
    assert item.source == "skills" and item.role == "system"
    assert item.content.startswith("# Available Skills")
    assert "## pdf" in item.content and "步骤一" in item.content
    assert "## excel" not in item.content


@pytest.mark.anyio
async def test_provide_is_empty_without_skills(tmp_path):
    assert await DirectorySkills(str(tmp_path)).provide(make_ctx("任意")) == []


@pytest.mark.anyio
async def test_skills_are_loaded_once_then_cached(skills_root):
    ds = DirectorySkills(str(skills_root))
    assert len(ds.all()) == 2
    write_skill(skills_root, "late", "---\nname: late\ndescription: late arrival\n---\n")
    assert len(ds.all()) == 2                      # 进程内缓存，不重扫磁盘
    fresh = DirectorySkills(str(skills_root))
    assert len(fresh.all()) == 3


def test_skill_is_a_plain_dataclass():
    a, b = Skill("a"), Skill("b")
    a.tools.append(object())
    assert b.tools == [] and b.metadata == {}
