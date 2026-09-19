"""Skill 数据契约。"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Skill:
    """Skill ≠ Tool。

    Skill 是一组能力定义，包含 instructions、resources 和 tools。
    V1 只实现 instructions 注入；tools 走 SkillProvider。
    """
    name: str
    description: str = ""
    instructions: str = ""
    tools: list = field(default_factory=list)
    metadata: dict = field(default_factory=dict)
