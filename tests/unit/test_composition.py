"""V3 Gate B —— Composition（三级递进 B1 → B2 → B3）。

命题：**能力可以增长，组合复杂度可以增长，但 Kernel 不增长。**

这套测试同时承担四件事：

1. 逐级验证「新能力 × Kernel」「新能力 × 新能力」「全栈」；
2. 证明组合过程中 `kernel/` 零改动（末条字节指纹门禁）；
3. 证明每个能力**独立可观察**、且**可单独替换**而不需要改其他能力；
4. 让 Gate C 的 4 个第三方 fake 在这里被真实使用一次 —— 生态边界不是纸上声明。
"""
from __future__ import annotations

import hashlib
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from support import RuntimeHarness

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT / "tests") not in sys.path:          # third_party 是 tests/ 下的包
    sys.path.insert(0, str(ROOT / "tests"))

from third_party.context import FakeThirdPartyTransform  # noqa: E402
from third_party.executor import FakeThirdPartyExecutor  # noqa: E402
from third_party.permission import FakeThirdPartyPermissionPolicy  # noqa: E402
from third_party.skill import FakeThirdPartySkillProvider  # noqa: E402

from agentkit.agent import Agent  # noqa: E402
from agentkit.api import (  # noqa: E402
    ContextTransform,
    PermissionPolicy,
    Skill,
    SkillProvider,
    ToolExecutor,
)
from agentkit.context.engine import ContextEngine  # noqa: E402
from agentkit.context.providers import (  # noqa: E402
    CallableProvider,
    MemoryContext,
    SystemPrompt,
)
from agentkit.context.transform import (  # noqa: E402
    BudgetTransform,
    DedupeTransform,
    SlidingWindowTransform,
)
from agentkit.executor.builtin import ParallelExecutor, SequentialExecutor  # noqa: E402
from agentkit.executor.permission import (  # noqa: E402
    AllowListPolicy,
    DenyListPolicy,
    InteractivePolicy,
    PermissionExecutor,
)
from agentkit.executor.retry import RetryExecutor  # noqa: E402
from agentkit.executor.timeout import TimeoutExecutor  # noqa: E402
from agentkit.kernel.events import EventBus  # noqa: E402
from agentkit.kernel.state import TerminationReason  # noqa: E402
from agentkit.kernel.types import (  # noqa: E402
    ContextItem,
    Final,
    Message,
    ToolCall,
    ToolCalls,
)
from agentkit.memory.simple import InMemoryMemory  # noqa: E402
from agentkit.models.echo import EchoModel, ScriptedModel  # noqa: E402
from agentkit.skills.directory import DirectorySkills  # noqa: E402
from agentkit.skills.mcp_backed import MCPBackedSkills  # noqa: E402
from agentkit.toolbox import Toolbox  # noqa: E402
from agentkit.tools.function import tool  # noqa: E402

#: V3 开始时 kernel/ 的字节指纹 —— 「组合不碰 Kernel」的最强证据
#: （ABI 门禁管语义，这里管「一个字节都没动」）
KERNEL_FINGERPRINTS = {
    "__init__.py": "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
    "events.py": "c0aaea0dc2c613318522db6e19bb799ce84a8408f2010a71b1c08846fd108a10",
    "loop.py": "efacfabfb2e130521e17288fd46bb482ce887e5e9d79da02f1411272893a3c79",
    "protocols.py": "4fc40751a37a65ba813cfa6a3ae41e35fdee6a46645166e98db4031b6e37583e",
    "state.py": "01c6c503b0b64e7077132915dc5f433ec2750cbfde849741819d148a6f1818c3",
    "types.py": "b95509a095634cdaa4e9d3f83086eaffbbb8391bc9428127c491d817c0394a21",
}

BLOCKED_PREFIX = "[blocked by policy]"


# ── 测试用工具 / session ────────────────────────────────


def weather_tool(log: list[str]):
    @tool
    def weather(city: str) -> str:
        """查询城市天气。"""
        log.append(city)
        return f"{city}: 晴"

    return weather


def rm_tool(log: list[str]):
    @tool
    def rm(path: str) -> str:
        """删除文件（危险操作，权限门禁的示例目标）。"""
        log.append(path)
        return f"deleted {path}"

    return rm


class FakeMCPSession:
    """只实现 MCPBackedSkills 用到的两个方法（list_resources / read_resource）。"""

    def __init__(self, resources: dict[str, str] | None = None) -> None:
        self.resources = resources or {}
        self.list_calls = 0

    async def list_resources(self):
        self.list_calls += 1
        items = [
            SimpleNamespace(uri=uri, name=uri.rsplit("/", 1)[-1].removesuffix(".md"),
                            description="从 MCP 拉到的技能")
            for uri in self.resources
        ]
        return SimpleNamespace(resources=items)

    async def read_resource(self, uri: str):
        return SimpleNamespace(contents=[SimpleNamespace(text=self.resources[uri])])


def one_call(name: str, **arguments) -> ToolCalls:
    return ToolCalls([ToolCall("t1", name, arguments)])


def tool_messages(ctx):
    return [m.content for m in ctx.messages if m.role == "tool"]


# ══════════════════════════════════════════════════════
# Gate B1 —— Single Capability
# ══════════════════════════════════════════════════════


@pytest.mark.anyio
async def test_b1_permission_executor_with_toolbox():
    log: list[str] = []
    tools = [weather_tool(log), rm_tool(log)]
    executor = PermissionExecutor(
        ParallelExecutor(Toolbox(tools)), policy=AllowListPolicy({"weather"}),
    )
    harness = RuntimeHarness(
        ScriptedModel([one_call("rm", path="/etc/hosts"), Final("换条路")]),
        tools=tools, executor=executor,
    )

    async with Agent(harness) as agent:
        ctx = await agent.run_ctx("删掉 hosts")

    assert log == []                                    # 危险工具从未执行
    assert ctx.reason is TerminationReason.FINAL
    assert tool_messages(ctx) == [f"[tool_error] {BLOCKED_PREFIX} rm"]


@pytest.mark.anyio
async def test_b1_budget_transform_with_context_engine():
    model = EchoModel()

    # 预算紧到装不下历史：system 优先保留，其余裁掉
    tight = RuntimeHarness(
        model, context=ContextEngine(
            providers=[SystemPrompt("规则")], transform=BudgetTransform(2),
        ),
        history_limit=50,
    )
    async with Agent(tight) as agent:
        await agent.run("查询")
    assert model.calls[0][0] == [Message("system", "规则")]

    # 预算充足：system + 历史都在，顺序不变
    generous = RuntimeHarness(
        EchoModel(), context=ContextEngine(
            providers=[SystemPrompt("规则")], transform=BudgetTransform(10_000),
        ),
    )
    generous_model = generous.model
    async with Agent(generous) as agent:
        await agent.run("查询")
    assert generous_model.calls[0][0] == [Message("system", "规则"), Message("user", "查询")]


@pytest.mark.anyio
async def test_b1_directory_skills_as_context_provider(tmp_path):
    skill_dir = tmp_path / "pdf"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text(
        "---\nname: pdf\ndescription: extract pdf tables\n---\n用 pdfplumber",
        encoding="utf-8",
    )
    model = EchoModel()
    harness = RuntimeHarness(
        model, context=ContextEngine([SystemPrompt("S"), DirectorySkills(str(tmp_path))]),
    )

    async with Agent(harness) as agent:
        await agent.run("extract pdf tables")

    # 顺序按 V2.5 冻结的 ContextEngine 契约：provider 的 system 产物在 history 之前
    # （ContextItem 默认 role="system"，所以 skills 块属于 system 分区）
    sent = model.calls[0][0]
    assert [m.role for m in sent] == ["system", "system", "user"]
    assert "## pdf" in sent[1].content and "用 pdfplumber" in sent[1].content


@pytest.mark.anyio
async def test_b1_third_party_executor_with_default_runtime():
    """Gate C 的实现第一次被真实使用：第三方只依赖 agentkit.api。"""
    executor = FakeThirdPartyExecutor()
    assert isinstance(executor, ToolExecutor)
    log: list[str] = []
    harness = RuntimeHarness(
        ScriptedModel([one_call("weather", city="北京"), Final("好")]),
        tools=[weather_tool(log)], executor=executor,
    )

    async with Agent(harness) as agent:
        ctx = await agent.run_ctx("北京天气")

    assert ctx.result == "好"
    assert log == []                                    # 真实工具没被执行，是第三方在应答
    assert executor.calls[0].name == "weather"
    assert executor.seen_tasks == ["北京天气"]           # ctx 是显式参数
    assert tool_messages(ctx) == ["[third-party] ok: weather"]


@pytest.mark.anyio
async def test_b1_third_party_transform_with_context_engine():
    executor = FakeThirdPartyExecutor()
    assert isinstance(executor, ToolExecutor)
    transform = FakeThirdPartyTransform(prefix="[3rd] ", keep_last=2)
    assert isinstance(transform, ContextTransform)
    model = EchoModel()
    harness = RuntimeHarness(
        model, context=ContextEngine(
            providers=[SystemPrompt("S")], transform=transform,
        ), history_limit=50,
    )

    async with Agent(harness) as agent:
        await agent.run("任务")

    sent = model.calls[0][0]
    assert sent[-1].content.startswith("[3rd] ")        # 第三方 transform 的效果可见


# ══════════════════════════════════════════════════════
# Gate B2 —— Multi Capability（能力之间无隐式耦合）
# ══════════════════════════════════════════════════════


@pytest.mark.anyio
async def test_b2_retry_permission_parallel_composition():
    log: list[str] = []
    policy = AllowListPolicy({"weather"})
    harness = RuntimeHarness(
        ScriptedModel([
            one_call("rm", path="/x"),
            one_call("weather", city="北京"),
            Final("完成"),
        ]),
        tools=[weather_tool(log), rm_tool(log)],
        executor_factory=lambda box: RetryExecutor(
            PermissionExecutor(ParallelExecutor(box), policy=policy),
            max_attempts=2, backoff=0,
        ),
    )

    async with Agent(harness) as agent:
        ctx = await agent.run_ctx("先删文件再查天气")

    assert log == ["北京"]                                # 被拦截的调用既不执行也不"重试成功"
    assert tool_messages(ctx) == [
        f"[tool_error] {BLOCKED_PREFIX} rm",
        "北京: 晴",
    ]


@pytest.mark.anyio
async def test_b2_budget_transform_composes_with_a_skill_provider():
    provider = FakeThirdPartySkillProvider([
        Skill("deploy", description="部署流程", instructions="先跑测试"),
        Skill("review", description="代码评审", instructions="只评审 diff"),
    ])
    assert isinstance(provider, SkillProvider)

    async def skill_context(ctx):
        found = await provider.search(ctx.task, limit=1)
        return [ContextItem(f"## {s.name}\n{s.instructions}", source="skills") for s in found]

    model = EchoModel()
    engine = ContextEngine(
        providers=[SystemPrompt("S"), CallableProvider(skill_context)],
        transform=BudgetTransform(10_000),
    )
    harness = RuntimeHarness(model, context=engine)

    async with Agent(harness) as agent:
        # 该 fake 的匹配方向：query ⊆ skill 名/描述
        await agent.run("deploy")

    sent = model.calls[0][0]
    assert provider.queries == ["deploy"]                  # provider 真被查询过
    assert sent[0] == Message("system", "S")
    assert sent[1].content == "## deploy\n先跑测试"        # skill provider 的产物真的进了上下文
    assert sent[-1] == Message("user", "deploy")           # history 在其后
    assert sum("## " in m.content for m in sent) == 1      # limit=1 生效


@pytest.mark.anyio
async def test_b2_permission_and_budget_constrain_one_run():
    log: list[str] = []
    model = ScriptedModel([one_call("rm", path="/x"), Final("被拦住也继续")])
    harness = RuntimeHarness(
        model,
        tools=[rm_tool(log)],
        context=ContextEngine(
            providers=[SystemPrompt("S")], transform=SlidingWindowTransform(6),
        ),
        executor_factory=lambda box: PermissionExecutor(
            ParallelExecutor(box), policy=DenyListPolicy({"rm"}),
        ),
    )

    async with Agent(harness) as agent:
        ctx = await agent.run_ctx("删文件。" + "啰嗦的上下文" * 30)

    assert log == []                                       # 执行被权限约束
    assert ctx.result == "被拦住也继续"
    assert tool_messages(ctx) == [f"[tool_error] {BLOCKED_PREFIX} rm"]
    assert all(len(call[0]) <= 6 for call in model.calls)   # 上下文被 transform 约束


@pytest.mark.anyio
async def test_b2_interactive_policy_denies_before_the_tool_runs():
    asked: list[tuple[str, str]] = []
    log: list[str] = []

    async def prompt(call, ctx):
        asked.append((call.name, ctx.task))
        return "n"                                         # 用户拒绝

    harness = RuntimeHarness(
        ScriptedModel([one_call("rm", path="/x"), Final("好的")]),
        tools=[rm_tool(log)],
        executor_factory=lambda box: PermissionExecutor(
            ParallelExecutor(box), policy=InteractivePolicy(prompt),
        ),
    )

    async with Agent(harness) as agent:
        ctx = await agent.run_ctx("删掉它")

    assert asked == [("rm", "删掉它")]
    assert log == []
    assert tool_messages(ctx) == [f"[tool_error] {BLOCKED_PREFIX} rm"]


@pytest.mark.anyio
async def test_b2_third_party_permission_composes_with_builtin_executors():
    """第三方 Policy × 内置 Executor：能力之间没有隐式耦合。"""
    log: list[str] = []
    policy = FakeThirdPartyPermissionPolicy(allowed={"weather"})
    assert isinstance(policy, PermissionPolicy)
    harness = RuntimeHarness(
        ScriptedModel([one_call("rm", path="/x"), Final("ok")]),
        tools=[weather_tool(log), rm_tool(log)],
        executor_factory=lambda box: TimeoutExecutor(
            RetryExecutor(
                PermissionExecutor(ParallelExecutor(box), policy=policy),
                max_attempts=2, backoff=0,
            ),
            seconds=5,
        ),
    )

    async with Agent(harness) as agent:
        ctx = await agent.run_ctx("删掉它")

    assert log == []
    assert tool_messages(ctx) == [f"[tool_error] {BLOCKED_PREFIX} rm"]


# ══════════════════════════════════════════════════════
# Gate B3 —— Full Stack
# ══════════════════════════════════════════════════════


def full_stack_harness(
    model,
    *,
    log: list[str],
    memory: InMemoryMemory | None = None,
    events: EventBus | None = None,
    policy=None,
    transform: ContextTransform | None = None,
    executor_factory=None,
    tools: list | None = None,
) -> RuntimeHarness:
    """Runtime = Model + ContextEngine(providers + transform) + Toolbox + Memory + Executor。"""
    memory = memory if memory is not None else InMemoryMemory()
    tools = tools if tools is not None else [weather_tool(log), rm_tool(log)]

    directory = DirectorySkills(str(ROOT / "skills"))
    mcp_skills = MCPBackedSkills(FakeMCPSession({"skill://deploy.md": "先跑测试再部署"}))

    async def skill_context(ctx):
        found = await directory.search(ctx.task, limit=1)
        found += await mcp_skills.search(ctx.task, limit=1)
        return [ContextItem(f"## {s.name}\n{s.instructions}", source="skills") for s in found]

    engine = ContextEngine(
        providers=[
            SystemPrompt("你是谨慎的助手"),
            CallableProvider(skill_context),
            MemoryContext(memory),
        ],
        history_limit=20,
        transform=transform or BudgetTransform(4_000),
    )
    factory = executor_factory or (
        lambda box: TimeoutExecutor(
            RetryExecutor(
                PermissionExecutor(
                    ParallelExecutor(box), policy=policy or AllowListPolicy({"weather"}),
                ),
                max_attempts=2, backoff=0,
            ),
            seconds=5,
        )
    )
    return RuntimeHarness(
        model, tools=tools, memory=memory, context=engine,
        events=events, executor_factory=factory,
    )


@pytest.mark.anyio
async def test_b3_full_stack_runs_end_to_end():
    log: list[str] = []
    memory = InMemoryMemory()
    model = ScriptedModel([
        one_call("rm", path="/etc/hosts"),                  # 被策略拦住
        one_call("weather", city="北京"),                    # 放行
        Final("北京晴，删除被拦截"),
    ])
    harness = full_stack_harness(model, log=log, memory=memory)

    async with Agent(harness) as agent:
        ctx = await agent.run_ctx("先删 hosts 再查北京天气")

    assert log == ["北京"]
    assert ctx.result == "北京晴，删除被拦截"
    assert ctx.reason is TerminationReason.FINAL
    assert tool_messages(ctx) == [f"[tool_error] {BLOCKED_PREFIX} rm", "北京: 晴"]
    assert [i.content for i in memory.items] == ["北京晴，删除被拦截"]


@pytest.mark.anyio
async def test_b3_same_assembly_works_on_every_model_path():
    """同一套装配换 Model 实现（三条 normalization path）都能跑通。"""
    from agentkit.models.anthropic import AnthropicModel
    from agentkit.models.ollama import OllamaModel
    from agentkit.models.openai import OpenAIModel

    class Stateful:
        def __init__(self) -> None:
            self.calls = 0

        def next_is_tool_call(self) -> bool:
            self.calls += 1
            return self.calls == 1

    def openai_path():
        state = Stateful()

        class Completions:
            async def create(self, **kwargs):
                if state.next_is_tool_call():
                    message = SimpleNamespace(content=None, tool_calls=[SimpleNamespace(
                        id="c1", function=SimpleNamespace(
                            name="weather", arguments='{"city": "北京"}',
                        ),
                    )])
                else:
                    message = SimpleNamespace(content="北京晴", tool_calls=None)
                return SimpleNamespace(choices=[SimpleNamespace(message=message)])

        return OpenAIModel("fake", client=SimpleNamespace(
            chat=SimpleNamespace(completions=Completions()),
        ))

    def anthropic_path():
        state = Stateful()

        class Messages:
            async def create(self, **kwargs):
                if state.next_is_tool_call():
                    return SimpleNamespace(content=[SimpleNamespace(
                        type="tool_use", id="tu1", name="weather", input={"city": "北京"},
                    )])
                return SimpleNamespace(content=[SimpleNamespace(type="text", text="北京晴")])

        return AnthropicModel("fake", client=SimpleNamespace(messages=Messages()))

    def ollama_path():
        state = Stateful()

        class Response:
            def __init__(self, payload):
                self._payload = payload

            def raise_for_status(self):
                return None

            def json(self):
                return self._payload

        class Client:
            async def post(self, url, json):
                if state.next_is_tool_call():
                    return Response({"message": {"tool_calls": [
                        {"id": "c1",
                         "function": {"name": "weather", "arguments": {"city": "北京"}}},
                    ]}})
                return Response({"message": {"content": "北京晴"}})

        model = OllamaModel("fake", client=Client())
        model._owns_client = False
        return model

    for make_model in (openai_path, anthropic_path, ollama_path):
        log: list[str] = []
        harness = full_stack_harness(make_model(), log=log)
        async with Agent(harness) as agent:
            ctx = await agent.run_ctx("查北京天气")
        assert log == ["北京"], make_model.__name__
        assert ctx.reason is TerminationReason.FINAL
        assert "晴" in ctx.result


@pytest.mark.anyio
async def test_b3_every_capability_is_independently_observable():
    """每个能力的效果都能被独立看到（EventBus trace + 组装结果 + Observation metadata）。"""
    log: list[str] = []
    events = EventBus()
    seen: list[str] = []
    prepared: list[list[str]] = []
    executor_results: list[list] = []
    events.on("*", lambda event, **payload: seen.append(event))
    events.on(
        "model.before",
        lambda event, **payload: prepared.append([m.content for m in payload["inp"].messages]),
    )
    events.on(
        "executor.after",
        lambda event, **payload: executor_results.append(payload["results"]),
    )

    harness = full_stack_harness(
        ScriptedModel([one_call("rm", path="/x"), Final("ok")]),
        log=log, events=events,
    )
    async with Agent(harness) as agent:
        ctx = await agent.run_ctx("删掉 /x")

    assert ctx.reason is TerminationReason.FINAL
    # 执行层组合：一次 batch 一条事件对
    assert seen.count("executor.before") == 1 and seen.count("executor.after") == 1
    assert "model.before" in seen and "iteration.done" in seen
    # PermissionPolicy：决策在 Observation 里可辨认
    blocked = executor_results[0][0]
    assert blocked.metadata["blocked"] is True and blocked.metadata["reason"] == "permission"
    assert log == []
    # ContextTransform + SkillProvider：以「组装结果」形式可观察（按契约它们本身不发事件）
    assert prepared[0][0] == "你是谨慎的助手"
    assert prepared[0][1].startswith("## ")          # skill provider 的产物（system 分区）
    # 组装顺序（V2.5 冻结）在组合之后依然成立：provider system 产物 → history
    assert prepared[0][-1] == "删掉 /x"


@pytest.mark.anyio
async def test_b3_single_capability_replacement_does_not_touch_the_others():
    """单独替换任意一个能力，其余能力不需要改。"""
    task = "查北京天气"

    def script():
        return ScriptedModel([one_call("weather", city="北京"), Final("晴")])

    log: list[str] = []
    baseline = full_stack_harness(script(), log=log)
    async with Agent(baseline) as agent:
        base_ctx = await agent.run_ctx(task)

    variants = {
        "executor（并行 → 串行）": full_stack_harness(
            script(), log=log, executor_factory=lambda box: SequentialExecutor(box),
        ),
        "transform（预算 → 去重）": full_stack_harness(
            script(), log=log, transform=DedupeTransform(),
        ),
        "policy（白名单 → 第三方）": full_stack_harness(
            script(), log=log, policy=FakeThirdPartyPermissionPolicy(allowed={"weather"}),
        ),
        "toolbox（本地工具 → 第三方执行器）": full_stack_harness(
            script(), log=log, executor_factory=lambda box: FakeThirdPartyExecutor(),
        ),
    }
    for name, harness in variants.items():
        async with Agent(harness) as agent:
            ctx = await agent.run_ctx(task)
        assert ctx.reason is TerminationReason.FINAL, name
        if name != "toolbox（本地工具 → 第三方执行器）":
            assert ctx.result == base_ctx.result, name


def test_kernel_bytes_unchanged_during_v3():
    """V3 的负向 Contract：组合与扩张过程中 kernel/ 一个字节都没动过。"""
    actual = {
        p.name: hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sorted((ROOT / "agentkit" / "kernel").glob("*.py"))
    }
    assert actual == KERNEL_FINGERPRINTS, (
        "kernel/ 在 V3 期间被修改 —— 命题证伪。若这是有意的 ABI 变更，"
        "必须先走 Kernel Change Review 并更新本指纹。"
    )
