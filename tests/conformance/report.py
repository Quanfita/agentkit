"""Conformance 证据：Result → JSON / Markdown（纯函数 + 落盘）。

`runner.py` 只产出 `ScenarioResult`；本模块把它变成两种可评审的产物：

    docs/conformance/<timestamp>.json   机读，字段按 §5.1
    docs/CONFORMANCE_REPORT.md          人读，模板按 §5.2

复现（从已落盘的机读 JSON 重新生成 Markdown）：

    python -m tests.conformance.report [docs/conformance/<timestamp>.json]
"""
from __future__ import annotations

import json
import subprocess
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Protocol

from . import cases
from .cases import (
    COMPATIBILITY_PROVIDERS,
    FAIL,
    GATE_A_CASES,
    GATE_A_PROVIDERS,
    GATE_B_CASES,
    GATE_C_CASES,
    GATE_C_PROVIDER,
    MODEL_DID_NOT_TRIGGER,
    NOT_VERIFIED,
    PASS,
    SKIPPED_BY_PROVIDER,
)

CONTRACT_REVISION = "v2"
JSON_DIR = Path("docs/conformance")
MARKDOWN_PATH = Path("docs/CONFORMANCE_REPORT.md")

#: 报告里的 Provider 列顺序（Gate A → 兼容性 → 其他）
PROVIDER_ORDER: tuple[str, ...] = ("openai", "anthropic", "ollama", "deepseek")

#: Provider → 它实际行使的 AgentKit normalization path（V2.5 封版决定 §二）。
#: 同一 path 下的多个 Provider = 同一份适配器代码 + 不同服务端，**不是**独立 path。
ADAPTER_PATHS: dict[str, tuple[str, str]] = {
    "openai": ("A", "models/openai.py"),
    "deepseek": ("A", "models/openai.py"),
    "ollama": ("B", "models/ollama.py"),
    "anthropic": ("C", "models/anthropic.py"),
}
PATH_ORDER: tuple[str, ...] = ("A", "B", "C")
#: 封版决定：>= 2 条独立 normalization path 拿到真机证据即 Gate A PASS
MIN_VERIFIED_PATHS = 2

_STATUS_CELL = {
    PASS: "✓ pass",
    FAIL: "✗ fail",
    SKIPPED_BY_PROVIDER: "skipped_by_provider",
    MODEL_DID_NOT_TRIGGER: "model_did_not_trigger",
    NOT_VERIFIED: "not_verified",
}

_SUPPORT_CELL = {
    PASS: "✓",
    FAIL: "✗ fail",
    MODEL_DID_NOT_TRIGGER: "模型未触发",
    SKIPPED_BY_PROVIDER: "provider 限制",
    NOT_VERIFIED: "未验证",
}

#: Provider Support Matrix 行 = 能力 → 场景（§5.2 模板）
SUPPORT_ROWS: tuple[tuple[str, str], ...] = (
    ("Tool calling", "tool"),
    ("Parallel tool calling", "parallel_tool"),
    ("Streaming text", "stream_text"),
    ("Streaming tool", "stream_tool"),
)


class ProviderInfo(Protocol):
    """收集器需要的 Provider 描述（`providers.ProviderSetup` 满足它）。"""

    name: str
    sdk_name: str
    sdk_version: str
    model: str
    missing: tuple[str, ...]


@dataclass(slots=True)
class ProviderEvidence:
    """一个 Provider 的证据记录（JSON 的 `providers.<name>`）。"""

    name: str
    sdk: dict[str, str]
    model: str
    missing: tuple[str, ...] = ()
    scenarios: dict[str, dict[str, Any]] = field(default_factory=dict)


@dataclass(slots=True)
class Evidence:
    """一次 session 收集到的全部证据（conftest 持有，session 结束时落盘）。"""

    providers: dict[str, ProviderEvidence] = field(default_factory=dict)

    def observe(self, provider: ProviderInfo) -> ProviderEvidence:
        record = self.providers.get(provider.name)
        if record is None:
            record = ProviderEvidence(
                name=provider.name,
                sdk={"name": provider.sdk_name, "version": provider.sdk_version},
                model=provider.model,
                missing=tuple(provider.missing),
            )
            self.providers[provider.name] = record
        return record

    def record(
        self, provider: ProviderInfo, case_id: str, status: str, notes: str = ""
    ) -> None:
        """记下一个场景结论（§3.6：`contract_verified` 只由 status 决定）。"""
        if status not in cases.STATUSES:
            raise ValueError(f"unknown conformance status: {status!r}")
        entry: dict[str, Any] = {
            "status": status,
            "contract_verified": cases.contract_verified(status),
        }
        if notes:
            entry["notes"] = notes
        self.observe(provider).scenarios[case_id] = entry

    def ordered(self) -> list[ProviderEvidence]:
        known = {name: self.providers[name] for name in PROVIDER_ORDER if name in self.providers}
        extra = {n: r for n, r in self.providers.items() if n not in known}
        return [*known.values(), *extra.values()]


# ── 机读 JSON（§5.1） ───────────────────────────────────


def git_revision() -> str:
    try:
        out = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return "unknown"
    if out.returncode != 0 or not out.stdout.strip():
        return "unknown"
    return out.stdout.strip()


def build_document(
    evidence: Evidence,
    *,
    git_rev: str | None = None,
    verified_at: str | None = None,
) -> dict[str, Any]:
    """`Evidence` → 机读报告（§5.1）。纯函数（git/时间可注入）。"""
    return {
        "contract_revision": CONTRACT_REVISION,
        "git_revision": git_rev or git_revision(),
        "verified_at": verified_at or datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "providers": {
            record.name: {
                "sdk": dict(record.sdk),
                "model": record.model,
                **({"missing": list(record.missing)} if record.missing else {}),
                "scenarios": dict(record.scenarios),
            }
            for record in evidence.ordered()
        },
    }


# ── 人读 Markdown（§5.2） ───────────────────────────────


def render_markdown(doc: dict[str, Any]) -> str:
    """机读报告 → 人读报告。纯函数。"""
    providers: dict[str, Any] = doc["providers"]
    lines = [
        "# V2.5 Conformance Report",
        "",
        f"- Contract revision: {doc['contract_revision']}",
        f"- Git revision: {doc['git_revision']}",
        f"- Verified at: {doc['verified_at']}",
        "",
    ]
    lines += _paths_section(providers)
    lines += _gate_section(
        "Gate B — Capability verification", _all_providers(providers), GATE_B_CASES,
        providers, _gate_b_verdict(providers),
    )
    lines += _gate_section(
        "Gate C — Compatibility", COMPATIBILITY_PROVIDERS, GATE_C_CASES,
        providers, _gate_c_verdict(providers),
    )
    lines += _findings_section(providers)
    lines += _support_section(providers)
    return "\n".join(lines) + "\n"


def _gate_section(
    title: str,
    provider_names: tuple[str, ...],
    case_ids: tuple[str, ...],
    providers: dict[str, Any],
    verdict: str,
) -> list[str]:
    gate = title.split(" — ")[0]
    columns = [name for name in provider_names if name in providers]
    header = f"## {title}"
    if not columns:
        return [
            header,
            "",
            f"无数据 —— 未运行 {', '.join(provider_names)}。",
            "",
            f"**{gate}: {verdict}**",
            "",
        ]
    titles = {case.id: case.title for case in cases.CASES}
    lines = [header, "", "| Scenario | " + " | ".join(_display(n) for n in columns) + " |"]
    lines.append("|---" * (len(columns) + 1) + "|")
    for case_id in case_ids:
        cells = [_status_cell(providers[name], case_id) for name in columns]
        lines.append(f"| {titles[case_id]} | " + " | ".join(cells) + " |")
    lines += ["", f"**{gate}: {verdict}**", ""]
    return lines


_DISPLAY = {
    "openai": "OpenAI",
    "anthropic": "Anthropic",
    "ollama": "Ollama",
    "deepseek": "DeepSeek",
}


def _display(name: str) -> str:
    return _DISPLAY.get(name, name)


def _status_cell(record: dict[str, Any], case_id: str) -> str:
    status = _raw_status(record, case_id)
    if status is None:
        return "— 未运行"
    return _STATUS_CELL.get(status, status)


def _gate_a_verdict(providers: dict[str, Any]) -> str:
    states = {
        f"{name}/{case_id}": _raw_status(providers.get(name, {}), case_id)
        for name in GATE_A_PROVIDERS
        for case_id in GATE_A_CASES
    }
    return _verdict_of(states, blocking=True)


def _all_providers(providers: dict[str, Any]) -> tuple[str, ...]:
    """报告里的全部 Provider 列（已知顺序优先，其余按出现顺序）。"""
    ordered = [name for name in PROVIDER_ORDER if name in providers]
    ordered += [name for name in providers if name not in ordered]
    return tuple(ordered)


def _servers_of(providers: dict[str, Any], path: str) -> tuple[str, ...]:
    """走这条 path 的服务端（有证据的 Provider）。"""
    return tuple(
        n for n in _all_providers(providers) if ADAPTER_PATHS.get(n, (None, ""))[0] == path
    )


def _path_state(providers: dict[str, Any], path: str) -> str:
    """一条 path 的状态：verified / fail / pending / missing（封版决定 §二）。"""
    servers = _servers_of(providers, path)
    if not servers:
        return "missing"
    if any(
        all(_raw_status(providers[n], case_id) == PASS for case_id in GATE_A_CASES)
        for n in servers
    ):
        return "verified"
    if any(
        _raw_status(providers[n], case_id) == FAIL
        for n in servers
        for case_id in GATE_A_CASES
    ):
        return "fail"
    return "pending"


def _paths_summary(providers: dict[str, Any]) -> str:
    parts = []
    for path in PATH_ORDER:
        code = next((c for pid, c in ADAPTER_PATHS.values() if pid == path), "—")
        parts.append(f"{path}({code})={_path_state(providers, path)}")
    return "；".join(parts)


def _paths_section(providers: dict[str, Any]) -> list[str]:
    """Gate A（V2.5 封版决定 §二）：按**独立 normalization path** 计数。"""
    lines = [
        "## Gate A — Independent Normalization Paths",
        "",
        "> 判据（封版决定 §二）：必须存在 **>= 2 条独立的 AgentKit normalization path**，",
        "> 每条 path 至少有一个服务端跑通 Normal / Tool / Stream Text / Error",
        "> 且 `contract_verified: true`。",
        "> 「独立」指**不同的 `models/*.py` 实现**，不是不同厂商 / base_url / model。",
        "",
        "| Path | Adapter 代码 | 服务端证据 | 状态 |",
        "|---|---|---|---|",
    ]
    for path in PATH_ORDER:
        code = next((c for pid, c in ADAPTER_PATHS.values() if pid == path), "—")
        servers = _servers_of(providers, path)
        if not servers:
            detail = "无"
        else:
            cells = []
            for name in servers:
                passed = all(
                    _raw_status(providers[name], case_id) == PASS
                    for case_id in GATE_A_CASES
                )
                mark = "4/4 ✓" if passed else _STATUS_CELL.get(
                    _raw_status(providers[name], GATE_A_CASES[0]) or "", "未跑全"
                )
                cells.append(f"{_display(name)} {mark}")
            detail = "，".join(cells)
        state = _path_state(providers, path)
        lines.append(f"| {path} | `{code}` | {detail} | {state} |")

    verified = [p for p in PATH_ORDER if _path_state(providers, p) == "verified"]
    failed = [p for p in PATH_ORDER if _path_state(providers, p) == "fail"]
    if failed:
        verdict = f"FAIL — path {'/'.join(failed)} 出现 Contract 违反"
    elif len(verified) >= MIN_VERIFIED_PATHS:
        verdict = (
            f"PASS — {len(verified)}/{MIN_VERIFIED_PATHS} 条独立 path 已获真机证据"
            f"（{'、'.join(verified)}）"
        )
    else:
        verdict = f"INCOMPLETE — 仅 {len(verified)}/{MIN_VERIFIED_PATHS} 条独立 path 获证据"
    lines += [
        "",
        f"**Gate A: {verdict}**",
        "",
        "> Path A 上的 DeepSeek 与 OpenAI 共享同一份 `models/openai.py`：",
        "> DeepSeek 是**跨服务端交叉验证**，不计入「独立 path」数量。",
        "> OpenAI 原生 / Anthropic 原生服务端缺 key 时记 `pending`。",
        "",
    ]
    return lines


def _gate_b_verdict(providers: dict[str, Any]) -> str:
    states = {
        f"{name}/{case_id}": _raw_status(providers.get(name, {}), case_id)
        for name in _all_providers(providers)
        for case_id in GATE_B_CASES
    }
    if any(status == FAIL for status in states.values()):
        return _verdict_of(states, blocking=False, suffix="（不阻塞）")
    available = {
        key: status
        for key, status in states.items()
        if status not in (None, NOT_VERIFIED)
    }
    pending = sorted({
        key.split("/")[0] for key, st in states.items() if st in (None, NOT_VERIFIED)
    })
    if available and all(status == PASS for status in available.values()):
        suffix = f"；{'/'.join(pending)} pending（缺 key，不阻塞）" if pending else ""
        return f"PASS — 可用 Provider 全部 pass{suffix}"
    return _verdict_of(states, blocking=False, suffix="（不阻塞）")


def _verdict_of(states: dict[str, str | None], *, blocking: bool, suffix: str = "") -> str:
    """§3.7：`fail` 是唯一的「验证不通过」；缺证据是「未验证」。"""
    failed = [key for key, status in states.items() if status == FAIL]
    if failed:
        return "FAIL — " + ", ".join(f"{key}={states[key]}" for key in failed)
    if all(status == PASS for status in states.values()):
        return "PASS"
    unverified = [
        key for key, status in states.items() if status in (None, NOT_VERIFIED)
    ]
    if len(unverified) == len(states):
        return f"NOT VERIFIED — 环境缺失，Contract 未行使{suffix}"
    detail = ", ".join(f"{key}={status or 'missing'}" for key, status in states.items())
    if blocking:
        return "INCOMPLETE — " + detail
    return "OK（尽力验证，不阻塞）— " + detail


def _gate_c_verdict(providers: dict[str, Any]) -> str:
    record = providers.get(GATE_C_PROVIDER)
    if record is None:
        return f"NOT VERIFIED — 缺少 {_display(GATE_C_PROVIDER)} 证据（不阻塞）"
    states = {case_id: _raw_status(record, case_id) for case_id in GATE_C_CASES}
    return _verdict_of(states, blocking=False, suffix="（不阻塞）")


def _raw_status(record: dict[str, Any], case_id: str) -> str | None:
    scenario = (record.get("scenarios") or {}).get(case_id)
    return scenario["status"] if scenario else None


def _findings_section(providers: dict[str, Any]) -> list[str]:
    case_ids = [case.id for case in cases.CASES]
    failures: list[str] = []
    findings: list[str] = []
    for name, record in providers.items():
        scenarios = record.get("scenarios") or {}
        groups: dict[tuple[str, str], list[str]] = {}
        for case_id in case_ids:
            scenario = scenarios.get(case_id)
            if scenario is None:
                groups.setdefault(("未运行", ""), []).append(case_id)
            elif scenario["status"] == FAIL:
                title = cases.by_id(case_id).title
                failures.append(
                    f"- ❌ {name}/{case_id} ({title}): fail — {scenario.get('notes', '')}"
                )
            elif not scenario["contract_verified"]:
                groups.setdefault(
                    (scenario["status"], scenario.get("notes", "")), []
                ).append(case_id)
        for (status, notes), ids in groups.items():
            scope = (
                f"全部 {len(ids)} 个场景"
                if len(ids) == len(case_ids)
                else ", ".join(ids)
            )
            detail = f"（{notes}）" if notes else ""
            findings.append(f"- ⚠ {name}: {scope} {status}{detail}")
    lines = ["## Failures / Findings", ""]
    lines.append("独立 normalization path：" + _paths_summary(providers) + "。")
    lines.append("")
    lines += failures or ["无 Contract 违反。"]
    lines.append("")
    if failures:
        lines += [
            "> 每个 fail 需分类为「Contract bug」或「Provider limitation」（§七）。",
            "",
        ]
    lines.append("### Findings（非 fail，但未证明 Contract）")
    lines.append("")
    lines += findings or ["无 —— 全部场景 `pass`。"]
    lines.append("")
    return lines


def _support_section(providers: dict[str, Any]) -> list[str]:
    columns = [name for name in PROVIDER_ORDER if name in providers]
    columns += [name for name in providers if name not in columns]
    if not columns:
        columns = list(PROVIDER_ORDER)
    lines = [
        "## Provider Support Matrix",
        "",
        "| Capability | "
        + " | ".join(
            f"{_display(name)} [{ADAPTER_PATHS.get(name, ('?', ''))[0]}]" for name in columns
        )
        + " |",
    ]
    lines.append("|---" * (len(columns) + 1) + "|")
    for capability, case_id in SUPPORT_ROWS:
        cells = [_support_cell(providers.get(name, {}), case_id) for name in columns]
        lines.append(f"| {capability} | " + " | ".join(cells) + " |")
    legend = "，".join(
        f"{path} = `{code}`" for path, code in dict.fromkeys(ADAPTER_PATHS.values())
    )
    lines += [
        "",
        f"Adapter Path：{legend}。**同一 Path 下的多个 Provider 是同一份适配器代码、不同服务端。**",
        "",
        "`模型未触发` / `provider 限制` / `未验证` 均不构成 Contract 证明（§3.6）。",
        "",
    ]
    return lines


def _support_cell(record: dict[str, Any], case_id: str) -> str:
    status = _raw_status(record, case_id)
    if status is None:
        return "—"
    return _SUPPORT_CELL.get(status, status)


# ── 落盘 ────────────────────────────────────────────────


def write_reports(
    evidence: Evidence,
    *,
    json_dir: Path = JSON_DIR,
    markdown_path: Path = MARKDOWN_PATH,
    git_rev: str | None = None,
    verified_at: str | None = None,
) -> tuple[Path, Path] | None:
    """写机读 + 人读报告。没有任何 Provider 证据 → 不写，返回 None。"""
    if not evidence.providers:
        return None
    doc = build_document(evidence, git_rev=git_rev, verified_at=verified_at)
    json_path = json_dir / f"{_stamp(doc['verified_at'])}.json"
    _write(json_path, json.dumps(doc, ensure_ascii=False, indent=2) + "\n")
    _write(markdown_path, render_markdown(doc))
    return json_path, markdown_path


def latest_json(json_dir: Path = JSON_DIR) -> Path | None:
    files = sorted(json_dir.glob("*.json")) if json_dir.is_dir() else []
    return files[-1] if files else None


def _stamp(verified_at: str) -> str:
    """ISO 时间戳 → 文件名安全形式（20260919T100000Z）。"""
    head = verified_at.split(".")[0].split("+")[0].removesuffix("Z")
    return head.replace("-", "").replace(":", "") + "Z"


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


# ── CLI（从机读 JSON 复现 Markdown） ────────────────────


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    path = Path(args[0]) if args else latest_json()
    if path is None or not path.exists():
        print(f"no machine-readable report found in {JSON_DIR}", file=sys.stderr)
        return 1
    doc = json.loads(path.read_text(encoding="utf-8"))
    _write(MARKDOWN_PATH, render_markdown(doc))
    print(f"markdown: {MARKDOWN_PATH} (from {path})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
