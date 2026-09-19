"""DirectorySkills —— 把 skills/<name>/SKILL.md 当 ContextProvider 用。"""
from __future__ import annotations

from pathlib import Path

from ..kernel.state import RunContext
from ..kernel.types import ContextItem
from .skill import Skill


def _parse_frontmatter(text: str) -> tuple[dict, str]:
    if not text.startswith("---"):
        return {}, text
    _, fm, body = text.split("---", 2)
    meta = {}
    for line in fm.strip().splitlines():
        if ":" in line:
            k, v = line.split(":", 1)
            meta[k.strip()] = v.strip()
    return meta, body.strip()


class DirectorySkills:
    """V1：静态加载 skills/<name>/SKILL.md。

    - 作为 ContextProvider 注入相关技能指令
    - V1 不做动态工具注入（V2 接 tools.py）
    """

    def __init__(self, root: str, top_k: int = 3):
        self.root = Path(root)
        self.top_k = top_k
        self._cache: list[Skill] | None = None

    def _load(self) -> list[Skill]:
        out = []
        if not self.root.exists():
            return out
        for d in sorted(self.root.iterdir()):
            f = d / "SKILL.md"
            if not d.is_dir() or not f.exists():
                continue
            meta, body = _parse_frontmatter(f.read_text(encoding="utf-8"))
            out.append(Skill(
                name=meta.get("name", d.name),
                description=meta.get("description", ""),
                instructions=body,
            ))
        return out

    def all(self) -> list[Skill]:
        """已加载的全部 Skill（进程内缓存一次）。"""
        if self._cache is None:
            self._cache = self._load()
        return self._cache

    async def search(self, query: str, k: int | None = None) -> list[Skill]:
        q = query.lower()
        scored = []
        for s in self.all():
            score = 2 if s.name.lower() in q else 0
            score += sum(1 for w in s.description.lower().split() if w and w in q)
            scored.append((score, s))
        scored.sort(key=lambda x: -x[0])
        return [s for _, s in scored[: (k or self.top_k)]]

    # ── ContextProvider ──
    async def provide(self, ctx: RunContext) -> list[ContextItem]:
        skills = await self.search(ctx.task)
        if not skills:
            return []
        blocks = ["# Available Skills"]
        for s in skills:
            blocks.append(f"## {s.name}\n{s.description}\n{s.instructions}".strip())
        return [ContextItem("\n\n".join(blocks), role="system", source="skills")]
