"""Vector Memory —— 最小可用的向量长期记忆，零第三方依赖。

embed 是一个回调：`(list[str]) -> list[list[float]]`，同步/异步均可。
真实项目把 embed 换成任意 embedding API，这个类不用改。
"""
from __future__ import annotations

import inspect
import math
from collections.abc import Awaitable, Callable

from ..kernel.types import MemoryInput, MemoryItem

Embed = Callable[[list[str]], "list[list[float]] | Awaitable[list[list[float]]]"]


def cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b, strict=False))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na == 0.0 or nb == 0.0:
        return 0.0
    return dot / (na * nb)


class VectorMemory:
    def __init__(
        self,
        embed: Embed,
        top_k: int = 5,
        max_items: int = 1000,
        min_score: float = 0.0,
    ) -> None:
        self.embed = embed
        self.top_k = top_k
        self.max_items = max_items
        self.min_score = min_score
        self._items: list[MemoryItem] = []
        self._vectors: list[list[float]] = []

    async def _embed(self, texts: list[str]) -> list[list[float]]:
        r = self.embed(texts)
        if inspect.isawaitable(r):
            r = await r
        return [list(v) for v in r]

    async def recall(self, task: str) -> list[MemoryItem]:
        if not self._items:
            return []
        (query,) = await self._embed([task])
        scored = sorted(
            ((cosine(query, v), i) for i, v in enumerate(self._vectors)),
            key=lambda x: -x[0],
        )
        return [
            self._items[i] for score, i in scored[: self.top_k] if score > self.min_score
        ]

    async def remember(self, run: MemoryInput) -> None:
        if not run.result:
            return
        (vector,) = await self._embed([run.result])
        self._items.append(MemoryItem(
            content=run.result,
            kind="episodic",
            metadata={"task": run.task},
        ))
        self._vectors.append(vector)
        if len(self._items) > self.max_items:
            self._items = self._items[-self.max_items:]
            self._vectors = self._vectors[-self.max_items:]
