"""SQLite Memory —— 只用 stdlib 的持久化长期记忆。

演示一件重要的事：Memory 只要实现 recall/remember 两个方法，
里面不需要出现任何 Message。
"""
from __future__ import annotations

import asyncio
import json
import re
import sqlite3
import time
from pathlib import Path

from ..kernel.types import MemoryInput, MemoryItem

_SCHEMA = """
CREATE TABLE IF NOT EXISTS memory (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    content TEXT NOT NULL,
    kind TEXT NOT NULL DEFAULT 'memory',
    task TEXT NOT NULL DEFAULT '',
    ts REAL NOT NULL
)
"""


def _tokens(text: str) -> list[str]:
    return [t for t in re.findall(r"[\w\u4e00-\u9fff]+", text.lower()) if len(t) >= 2]


class SQLiteMemory:
    """关键字打分 + 近期优先的长期记忆。

    `path=":memory:"` 时退化为进程内数据库，方便测试。
    """

    def __init__(self, path: str | Path = "agentkit_memory.db", top_k: int = 5,
                 max_scan: int = 200) -> None:
        self.path = str(path)
        self.top_k = top_k
        self.max_scan = max_scan
        self._db = sqlite3.connect(self.path, check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        self._db.execute(_SCHEMA)

    async def recall(self, task: str) -> list[MemoryItem]:
        return await asyncio.to_thread(self._recall, task)

    async def remember(self, run: MemoryInput) -> None:
        await asyncio.to_thread(self._remember, run)

    async def close(self) -> None:
        await asyncio.to_thread(self._db.close)

    # ── 同步实现 ──

    def _recall(self, task: str) -> list[MemoryItem]:
        rows = self._db.execute(
            "SELECT content, kind, task, ts FROM memory ORDER BY id DESC LIMIT ?",
            (self.max_scan,),
        ).fetchall()
        want = _tokens(task)
        scored = []
        for i, row in enumerate(rows):
            hay = row["content"].lower()
            score = sum(1 for t in want if t in hay)
            scored.append((score, -i, row))
        scored.sort(key=lambda x: (-x[0], x[1]))
        return [
            MemoryItem(
                content=row["content"],
                kind=row["kind"],
                metadata={"task": row["task"], "ts": row["ts"]},
            )
            for _, _, row in scored[: self.top_k]
        ]

    def _remember(self, run: MemoryInput) -> None:
        if not run.result:
            return
        self._db.execute(
            "INSERT INTO memory (content, kind, task, ts) VALUES (?, ?, ?, ?)",
            (run.result, "episodic", run.task, time.time()),
        )
        self._db.commit()

    def dump(self) -> str:
        """调试用：导出全部记忆为 JSON。"""
        rows = self._db.execute(
            "SELECT content, kind, task, ts FROM memory ORDER BY id"
        ).fetchall()
        return json.dumps([dict(r) for r in rows], ensure_ascii=False, indent=2)
