# Provider Conformance（V2.5-2 骨架）

V2.5 的判据只有一条：**V2 冻结的 Protocol / dataclass / 事件 / 异常语义，在真实 Provider 下行为符合预期。**
本目录是这条判据的执行器与证据生成器。

## 目录结构

```
tests/conformance/
├── conftest.py            # marker 注册 / skip 逻辑 / 证据收集（session 结束落盘）
├── cases.py               # 6 个场景定义（纯数据）+ 状态枚举 + Gate 定义
├── runner.py              # Provider → Scenario → run → assert → Result（≤150 行）
├── assertions.py          # 每个场景的 Contract 断言 + ToolCall 组装语义
├── report.py              # Result → JSON / Markdown（纯函数）+ 复现 CLI
├── providers/             # Provider setup（fixture，非生产 Adapter）
│   ├── __init__.py        # ProviderSetup / weather Tool / sdk_version
│   └── openai.py anthropic.py ollama.py deepseek.py
├── test_openai.py test_anthropic.py test_ollama.py test_deepseek.py
├── test_malformed_stream.py   # adapter-level，fake provider，不真机，不打 marker
└── README.md
```

## 运行

```bash
# 默认（pyproject 的 addopts = -q -m "not conformance"）：只跑离线用例
python -m pytest tests/conformance -m "not conformance" -v

# 真机（命令行 -m 覆盖 addopts 的 -m）
python -m pytest tests/conformance -m conformance -v

# 只看会跑哪些真机用例
python -m pytest tests/conformance -m conformance --collect-only -q
```

无 API key 时**不会有失败**：对应 Provider 的全部场景记 `not_verified` 并 skip。

## 环境变量（模型名不进 Contract）

| 变量 | 默认 | 说明 |
|---|---|---|
| `OPENAI_API_KEY` | — | 缺失 → openai 全部 `not_verified` |
| `AGENTKIT_OPENAI_MODEL` | `gpt-4o-mini` | |
| `ANTHROPIC_API_KEY` | — | 缺失 → anthropic 全部 `not_verified` |
| `AGENTKIT_ANTHROPIC_MODEL` | `claude-3-5-sonnet-latest` | |
| `AGENTKIT_OLLAMA_MODEL` | `qwen2.5:7b` | 模型未 pull → ollama 全部 `not_verified` |
| `AGENTKIT_OLLAMA_HOST` | `http://localhost:11434` | 不可达 → 同上 |
| `DEEPSEEK_API_KEY` | — | 缺失 → deepseek 全部 `not_verified` |
| `AGENTKIT_DEEPSEEK_MODEL` | `deepseek-chat` | |
| `DEEPSEEK_BASE_URL` | `https://api.deepseek.com` | 由 Adapter 读取 |

## 场景与判定

| 场景 | 层 | 输入 | Contract 判定 |
|---|---|---|---|
| `normal` | contract | `[user: "说你好"]`，无工具 | `Final(content: str)` |
| `tool` | contract | `[user: "北京天气怎么样？"]` + `weather(city)` | `ToolCalls`，`name/id/arguments` 合法 |
| `error` | contract | 无效 model name | Adapter: SDK 异常原样传播；Agent: `reason == ERROR`、`error != None`、re-raise |
| `stream_text` | contract | 同 normal | `>= 1 TextDelta`；`text: str`；组装闭合；正常结束 |
| `parallel_tool` | capability | `[user: "北京和上海的天气怎么样？"]` + `weather` | `ToolCalls` 或 `model_did_not_trigger` |
| `stream_tool` | capability | 同 tool | 组装后结构一致（name / arguments / id / index） |

判定语义（`assertions.py` 的模块 docstring 是权威）：

- `fail` —— Adapter 映射违反 V2 Contract（可复现的适配器缺陷）；
- `model_did_not_trigger` —— Provider / 模型没有走出期望行为，Contract **未被行使**；
  这一条对 Contract 层同样成立：V2.5 验证「Contract 是否成立」，不是「Provider 是否总能触发某个行为」。
  Gate 因此可能显示 `INCOMPLETE`，而不是假装通过。

`contract_verified` 只由状态决定（§3.6）：**只有 `pass` 为 `true`**。

`skipped_by_provider` 由 `ProviderSetup.unsupported` 声明（某个 Provider 的 Adapter
已知不具备的能力，例如没有 stream 实现）；V2.5 的四个 Provider 当前均未声明，
Phase V2.5-3 按真机结论补。

| status | contract_verified |
|---|---|
| `pass` | `true` |
| `skipped_by_provider` | `false` |
| `model_did_not_trigger` | `false` |
| `fail` | `false` |
| `not_verified` | `false` |

### Stream Text 的边界（§3.4）

跨请求的 `非流式 Final.content == 流式组装文本` **不参与判定**（LLM 非确定）。
`stream_text` 只验证流式自身的增量组装闭合；Adapter 不暴露「组装结果」，
组装语义由 `assertions.concat_text()` 表述。空分片只记 notes，不算违反。

### Error 场景分两层（§4.5）

`runner._observe_error()` 先跑 Adapter 层（`generate()` 必须把 SDK 异常原样抛出，
异常类型必须来自 Provider SDK 命名空间），再跑 Agent 层
（`agent_loop` → `ctx.reason == ERROR`、`ctx.error != None`、异常 re-raise）。

### Malformed Stream（§3.5）

不属于真机矩阵：`test_malformed_stream.py` 用 fake client 离线验证
`args_delta` 在 JSON / UTF-8 中间分片、断流、多 call 交错，复用
`assertions.assemble_tool_calls()`（与真机判定同一套组装实现）。

## Gate（§3.7）

- **Gate A（必过）**：OpenAI / Anthropic 的 `normal` / `tool` / `stream_text` / `error` 全部 `pass`；
- **Gate A（替代验证）**：Gate A 名单由 §3.7 冻结，本机缺 key 时报告额外给出「实际行使同一组 Contract
  场景的真机 Provider」一栏 —— 二者不可互相替代，但必须同时呈现，避免读者误判「没做过 Contract 验证」；
- **Gate B（尽力）**：`parallel_tool` / `stream_tool` → `pass` / `skipped_by_provider` / `model_did_not_trigger`；
- **Gate C（不阻塞）**：Ollama 至少 `normal` + `error` 真实运行。

## Provider 支持矩阵（真机结果）

2026-09-19 真机运行（`docs/conformance/20260919T080407Z.json`）：

| Capability | OpenAI | Anthropic | Ollama `qwen3.5:9b` | DeepSeek `deepseek-flash` |
|---|---|---|---|---|
| Normal（Contract） | 未验证（无 key） | 未验证（无 key） | ✓ pass | ✓ pass |
| Tool（Contract） | 未验证（无 key） | 未验证（无 key） | ✓ pass | ✓ pass |
| Stream Text（Contract） | 未验证（无 key） | 未验证（无 key） | ✓ pass（15 deltas） | ✓ pass（10 deltas） |
| Error（Contract） | 未验证（无 key） | 未验证（无 key） | ✓ pass（HTTPStatusError） | ✓ pass（BadRequestError） |
| Parallel Tool（Capability） | 未验证（无 key） | 未验证（无 key） | ✓ pass（2 calls） | ✓ pass（2 calls） |
| Stream Tool（Capability） | 未验证（无 key） | 未验证（无 key） | ✓ pass | ✓ pass |

带 key 的环境用同一条命令即可补齐 OpenAI / Anthropic 两列：

```bash
OPENAI_API_KEY=... ANTHROPIC_API_KEY=... \
AGENTKIT_OLLAMA_MODEL=<已 pull 的模型> \
python -m pytest tests/conformance -m conformance -v
```


## 报告与复现

一次真机 session 结束时会写两份产物（`pytest_sessionfinish` → `report.write_reports()`）：

```
docs/conformance/<timestamp>.json     # 机读：contract_revision / git_revision / verified_at
                                      #       providers.<name>.sdk.{name,version} / model / scenarios.*
docs/CONFORMANCE_REPORT.md            # 人读：Gate A / Gate B / Gate C / Failures / Provider Support Matrix
```

从已落盘的机读 JSON 重新生成 Markdown（评审复现）：

```bash
python -m tests.conformance.report                       # 取 docs/conformance 下最新的 JSON
python -m tests.conformance.report docs/conformance/<timestamp>.json
```

`runner.py` 的边界是硬约束：**不重试、不超时、不归一化、不含 Runtime 逻辑、不生成报告。**
