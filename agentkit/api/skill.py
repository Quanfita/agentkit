"""Skill 扩展 Protocol。"""
from __future__ import annotations

from typing import Protocol, runtime_checkable

from ..skills.skill import Skill


@runtime_checkable
class SkillProvider(Protocol):
    """Skill 查询接口。

    只承担查询职责。同步 / 缓存 / 生命周期属于 Runtime startup hook，
    **不属于** Provider Protocol —— 所以这里没有 `refresh()` / `close()`。
    """

    async def search(self, query: str, limit: int = 3) -> list[Skill]: ...
