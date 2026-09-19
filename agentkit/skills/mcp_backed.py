"""MCPBackedSkills —— 把 MCP server 的 skill 资源当 SkillProvider 用。

协议面（V3 选定的最小可行面）：

    session.list_resources()          → 列出资源，只认 `skill://<name>` 的
    session.read_resource(uri)        → 读资源正文，作为 instructions

`Skill` 的 name / description 取资源清单里的字段，instructions 取资源正文。
查询打分在本地做（MCP 侧只提供目录），`limit` 是**本地**截断语义，
排序口径与 `DirectorySkills` 一致（分数降序，同分保持目录顺序；不按分数过滤）。

异常模型（V2.5 继承）：

    基础设施故障（list_resources / read_resource 抛异常）→ **冒泡**，
        不吞成静默空列表：MCP 挂了和「server 上没有 skill」是两件事。
    server 上没有任何 `skill://` 资源 → 返回 `[]`。

目录在进程内缓存一次（§6.2：Provider 自己管理缓存，不污染 Protocol）；
需要刷新请由 Runtime startup hook / Harness 层重建 Provider。
"""
from __future__ import annotations

from .skill import Skill

SKILL_URI_PREFIX = "skill://"


def _score(skill: Skill, query: str) -> int:
    """与 `DirectorySkills` 同一口径：名字命中优先，其次描述词命中。"""
    q = query.lower()
    score = 2 if skill.name.lower() in q else 0
    return score + sum(1 for w in skill.description.lower().split() if w and w in q)


def _text_of(result) -> str:
    """拼接 `ReadResourceResult.contents` 里的文本块（跳过二进制块）。"""
    blocks = getattr(result, "contents", None) or []
    return "\n".join(c.text for c in blocks if hasattr(c, "text"))


class MCPBackedSkills:
    """从 MCP server 拉 skill 列表，实现 `SkillProvider.search()`。"""

    def __init__(self, session, limit: int = 3) -> None:
        self.session = session            # borrow，不拥有（无 close()）
        self.limit = limit
        self._cache: list[Skill] | None = None

    async def _load(self) -> list[Skill]:
        listing = await self.session.list_resources()      # 基础设施故障冒泡
        out: list[Skill] = []
        for res in getattr(listing, "resources", None) or []:
            uri = str(getattr(res, "uri", ""))
            if not uri.startswith(SKILL_URI_PREFIX):
                continue                                   # 非 skill 资源：忽略
            out.append(Skill(
                name=getattr(res, "name", "") or uri[len(SKILL_URI_PREFIX):],
                description=getattr(res, "description", "") or "",
                instructions=_text_of(await self.session.read_resource(uri)),
            ))
        return out

    async def search(self, query: str, limit: int | None = None) -> list[Skill]:
        catalog = self._cache
        if catalog is None:
            catalog = self._cache = await self._load()
        top_k = self.limit if limit is None else limit
        ranked = sorted(catalog, key=lambda s: -_score(s, query))
        return ranked[:top_k]
