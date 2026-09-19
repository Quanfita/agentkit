"""最朴素的 Memory —— 只有两个方法。"""
from __future__ import annotations

from ..kernel.types import MemoryInput, MemoryItem


class NullMemory:
    async def recall(self, task: str) -> list[MemoryItem]:
        return []

    async def remember(self, run: MemoryInput) -> None:
        return None


class InMemoryMemory:
    """最朴素的长期记忆，仅用于演示与测试。生产请替换。"""

    def __init__(self, max_items: int = 200, top_k: int = 10):
        self.items: list[MemoryItem] = []
        self.max_items = max_items
        self.top_k = top_k

    async def recall(self, task: str) -> list[MemoryItem]:
        return self.items[-self.top_k:]

    async def remember(self, run: MemoryInput) -> None:
        if not run.result:
            return
        self.items.append(MemoryItem(
            content=run.result,
            kind="episodic",
            metadata={"task": run.task},
        ))
        self.items = self.items[-self.max_items:]
