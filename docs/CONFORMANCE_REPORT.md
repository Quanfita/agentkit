# V2.5 Conformance Report

- Contract revision: v2
- Git revision: aa410c5
- Verified at: 2026-09-19T08:07:20+00:00

## Gate A — Independent Normalization Paths

> 判据（封版决定 §二）：必须存在 **>= 2 条独立的 AgentKit normalization path**，
> 每条 path 至少有一个服务端跑通 Normal / Tool / Stream Text / Error
> 且 `contract_verified: true`。
> 「独立」指**不同的 `models/*.py` 实现**，不是不同厂商 / base_url / model。

| Path | Adapter 代码 | 服务端证据 | 状态 |
|---|---|---|---|
| A | `models/openai.py` | OpenAI not_verified，DeepSeek 4/4 ✓ | verified |
| B | `models/ollama.py` | Ollama 4/4 ✓ | verified |
| C | `models/anthropic.py` | Anthropic not_verified | pending |

**Gate A: PASS — 2/2 条独立 path 已获真机证据（A、B）**

> Path A 上的 DeepSeek 与 OpenAI 共享同一份 `models/openai.py`：
> DeepSeek 是**跨服务端交叉验证**，不计入「独立 path」数量。
> OpenAI 原生 / Anthropic 原生服务端缺 key 时记 `pending`。

## Gate B — Capability verification

| Scenario | OpenAI | Anthropic | Ollama | DeepSeek |
|---|---|---|---|---|
| Parallel Tool | not_verified | not_verified | ✓ pass | ✓ pass |
| Stream Tool | not_verified | not_verified | ✓ pass | ✓ pass |

**Gate B: PASS — 可用 Provider 全部 pass；anthropic/openai pending（缺 key，不阻塞）**

## Gate C — Compatibility

| Scenario | Ollama |
|---|---|
| Normal | ✓ pass |
| Error | ✓ pass |

**Gate C: PASS**

## Failures / Findings

独立 normalization path：A(models/openai.py)=verified；B(models/ollama.py)=verified；C(models/anthropic.py)=pending。

无 Contract 违反。

### Findings（非 fail，但未证明 Contract）

- ⚠ openai: 全部 6 个场景 not_verified（missing: OPENAI_API_KEY）
- ⚠ anthropic: 全部 6 个场景 not_verified（missing: ANTHROPIC_API_KEY）

## Provider Support Matrix

| Capability | OpenAI [A] | Anthropic [C] | Ollama [B] | DeepSeek [A] |
|---|---|---|---|---|
| Tool calling | 未验证 | 未验证 | ✓ | ✓ |
| Parallel tool calling | 未验证 | 未验证 | ✓ | ✓ |
| Streaming text | 未验证 | 未验证 | ✓ | ✓ |
| Streaming tool | 未验证 | 未验证 | ✓ | ✓ |

Adapter Path：A = `models/openai.py`，B = `models/ollama.py`，C = `models/anthropic.py`。**同一 Path 下的多个 Provider 是同一份适配器代码、不同服务端。**

`模型未触发` / `provider 限制` / `未验证` 均不构成 Contract 证明（§3.6）。

