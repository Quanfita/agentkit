"""AgentKit 公共扩展 API —— 第三方实现的**唯一**入口。

第三方实现必须只依赖此模块。

禁止 import（`tests/test_api_boundary.py` 强制）：

    agentkit.kernel.*      （除本模块内部）
    agentkit.runtime / context / skills / executor / memory / tools / models / harness

`__all__` 与 V3 Freeze 文档 §4.4 的清单一致，另有三个**闭合性补全**
（文档清单缺了它们，第三方就无法实现对应 Protocol）：

    Skill          —— SkillProvider.search 的返回类型
    PreparedInput  —— Runtime.prepare 的返回类型
    EventBus       —— Runtime.events 的类型
"""
from __future__ import annotations

from ..skills.skill import Skill
from .context import ContextTransform
from .executor import PermissionPolicy
from .kernel import *  # noqa: F401,F403
from .kernel import __all__ as _kernel_all
from .skill import SkillProvider

__all__ = [
    *_kernel_all,
    # 闭合性补全
    "Skill",
    # 扩展 Protocol
    "ContextTransform", "SkillProvider", "PermissionPolicy",
]
