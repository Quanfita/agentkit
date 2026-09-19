"""第三方 `SkillProvider` 实现 —— 唯一 import 来源是 `agentkit.api`。

注意 `Skill` 也是从 `agentkit.api` 拿的：`Skill` 的 canonical 位置在
`agentkit/skills/skill.py`，第三方不该知道这件事 —— `api` 把它重导出成公共契约。
`search()` 是协议里**唯一**的方法：没有 `refresh()` / `close()`，
同步与生命周期属于 Runtime startup hook。
"""
from __future__ import annotations

from collections.abc import Sequence

from agentkit.api import Skill


class FakeThirdPartySkillProvider:
    """对 `name` + `description` 做大小写不敏感子串匹配的只读 Skill 源。

    - 空 query（或纯空白）匹配全部 skill；
    - `limit <= 0` 返回空列表，`limit` 只截上界；
    - 顺序稳定 = 构造时给的顺序（组合测试可依赖）；
    - 无状态副作用，除 `queries` 记录（供测试断言搜索确实发生过）。
    """

    def __init__(self, skills: Sequence[Skill] = ()) -> None:
        self.skills = list(skills)
        self.queries: list[str] = []

    async def search(self, query: str, limit: int = 3) -> list[Skill]:
        self.queries.append(query)
        if limit <= 0:
            return []
        needle = query.strip().lower()
        hits = [
            s for s in self.skills
            if needle in s.name.lower() or needle in s.description.lower()
        ]
        return hits[:limit]
