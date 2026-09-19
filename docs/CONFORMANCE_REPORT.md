# V2.5 Conformance Report

- Contract revision: v2
- Git revision: aa410c5
- Verified at: 2026-09-19T08:07:20+00:00

## Gate A — Contract verification

| Scenario | OpenAI | Anthropic |
|---|---|---|
| Normal | not_verified | not_verified |
| Tool | not_verified | not_verified |
| Stream Text | not_verified | not_verified |
| Error | not_verified | not_verified |

**Gate A: NOT VERIFIED — 环境缺失，Contract 未行使**

> Gate A 的 Provider 名单由 §3.7 冻结（OpenAI / Anthropic），本机缺 key 时该表只能是 `not_verified`。
> 下表列出**实际行使**同一组 Contract 场景的真机 Provider —— 二者不可互相替代，
> 但报告必须同时呈现，否则读者会误判「没有做过 Contract 验证」。

## Gate A（替代验证）— 可用真机 Provider 行使 Contract

| Scenario | Ollama | DeepSeek |
|---|---|---|
| Normal | ✓ pass | ✓ pass |
| Tool | ✓ pass | ✓ pass |
| Stream Text | ✓ pass | ✓ pass |
| Error | ✓ pass | ✓ pass |

**Gate A（替代验证）— 可用真机 Provider 行使 Contract: PASS**

## Gate B — Capability verification

| Scenario | OpenAI | Anthropic | Ollama | DeepSeek |
|---|---|---|---|---|
| Parallel Tool | not_verified | not_verified | ✓ pass | ✓ pass |
| Stream Tool | not_verified | not_verified | ✓ pass | ✓ pass |

**Gate B: OK（尽力验证，不阻塞）— openai/parallel_tool=not_verified, openai/stream_tool=not_verified, anthropic/parallel_tool=not_verified, anthropic/stream_tool=not_verified, ollama/parallel_tool=pass, ollama/stream_tool=pass, deepseek/parallel_tool=pass, deepseek/stream_tool=pass**

## Gate C — Compatibility

| Scenario | Ollama |
|---|---|
| Normal | ✓ pass |
| Error | ✓ pass |

**Gate C: PASS**

## Failures / Findings

Contract 层（4/4 场景 `pass` 且 `contract_verified: true`）由以下真机 Provider 行使：Ollama, DeepSeek。

无 Contract 违反。

### Findings（非 fail，但未证明 Contract）

- ⚠ openai: 全部 6 个场景 not_verified（missing: OPENAI_API_KEY）
- ⚠ anthropic: 全部 6 个场景 not_verified（missing: ANTHROPIC_API_KEY）

## Provider Support Matrix

| Capability | OpenAI | Anthropic | Ollama | DeepSeek |
|---|---|---|---|---|
| Tool calling | 未验证 | 未验证 | ✓ | ✓ |
| Parallel tool calling | 未验证 | 未验证 | ✓ | ✓ |
| Streaming text | 未验证 | 未验证 | ✓ | ✓ |
| Streaming tool | 未验证 | 未验证 | ✓ | ✓ |

`模型未触发` / `provider 限制` / `未验证` 均不构成 Contract 证明（§3.6）。

