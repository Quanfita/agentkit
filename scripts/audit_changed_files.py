#!/usr/bin/env python3
"""V3.1 Changed Files Audit（M5）：把 Changed Files Policy 变成可执行门禁。

政策 ``docs/v3_1/CHANGED_FILES_POLICY.yml`` 分级 forbidden / allowed / warning。变更文件 =
``git diff --name-only <base>`` ∪ ``git ls-files --others --exclude-standard``（未跟踪的新文件
必须参与审计，否则新文件直接绕过门禁）。匹配：先精确路径、再最长 glob（``kernel/**`` /
``tests/**`` 这类前缀 glob），都不中落 ``default``；政策路径有仓库根相对（``tests/**``）与
``agentkit/`` 相对（``kernel/**`` / ``agent.py``）两种书写基准，都作为候选形式参与匹配。

退出码：0 全部 allowed；1 存在 forbidden（fail_immediately）；2 存在 warning（人工评审）。

用法：
  python scripts/audit_changed_files.py --base b61a739
  python scripts/audit_changed_files.py --paths agentkit/agent.py docs/v3_1/notes.md
"""
from __future__ import annotations

import argparse
import fnmatch
import subprocess
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
POLICY_PATH = ROOT / "docs" / "v3_1" / "CHANGED_FILES_POLICY.yml"
EXIT_ALLOWED, EXIT_FORBIDDEN, EXIT_WARNING = 0, 1, 2
LEVELS = ("forbidden", "warning", "allowed")
VERDICTS = {"forbidden": "fail_immediately", "warning": "requires review"}

Rule = tuple[str, str, str]


def load_policy(path: Path = POLICY_PATH) -> tuple[list[Rule], tuple[str, str]]:
    """读政策，返回 (rules, default)；rules 是 (pattern, level, reason) 列表。"""
    data = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    fallback = data.pop("default", None) or {}
    rules: list[Rule] = []
    for pattern, spec in data.items():
        spec = spec or {}
        level = str(spec.get("level", "warning"))
        generic = "allowlisted" if level == "allowed" else "requires review"
        reason = str(spec.get("reason") or spec.get("action") or generic)
        rules.append((str(pattern), level, reason))
    default = (str(fallback.get("level", "warning")),
               str(fallback.get("reason") or fallback.get("action") or "manual_review"))
    return rules, default


def _candidates(path: str) -> list[str]:
    """一条路径参与匹配的候选形式（仓库根相对 + ``agentkit/`` 相对）。"""
    p = Path(str(path))
    if p.is_absolute() and ROOT in p.parents:
        p = p.relative_to(ROOT)
    norm = p.as_posix()
    if norm.startswith("agentkit/") and norm != "agentkit/":
        return [norm, norm[len("agentkit/"):]]
    return [norm]


def classify(path: str, rules: list[Rule], default: tuple[str, str]) -> tuple[str, str]:
    """把一条路径分类成 (level, reason)：精确路径优先，其次最长 glob，都不中落 ``default``。"""
    best: tuple[tuple[bool, int], Rule] | None = None
    for candidate in _candidates(path):
        globs = (r for r in rules if fnmatch.fnmatchcase(candidate, r[0]))
        hit = next((r for r in rules if r[0] == candidate), None) or max(
            globs, key=lambda r: len(r[0]), default=None)
        if hit is None:
            continue
        score = (hit[0] == candidate, len(hit[0]))
        if best is None or score > best[0]:
            best = (score, hit)
    return (best[1][1], best[1][2]) if best else default


def changed_files(base: str) -> list[str]:
    """``git diff --name-only <base>`` ∪ 未跟踪文件（新文件也要被审计）。"""
    paths: set[str] = set()
    for args in (("diff", "--name-only", base), ("ls-files", "--others", "--exclude-standard")):
        # ``-z`` 关闭引号转义：中文文件名不会被 escape 成八进制。
        proc = subprocess.run(["git", *args, "-z"], capture_output=True, check=True)
        paths |= set(proc.stdout.decode("utf-8", "replace").split("\0"))
    return sorted(p for p in paths if p)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="V3.1 Changed Files Audit（M5）")
    parser.add_argument("--base", default="HEAD", help="git 基线 ref（默认 HEAD）")
    parser.add_argument("--paths", nargs="*", default=None, help="直接指定路径，替代 git 探测")
    args = parser.parse_args(argv)

    rules, default = load_policy()
    files = args.paths if args.paths is not None else changed_files(args.base)
    rows = sorted(((*classify(p, rules, default), p) for p in files),
                  key=lambda row: (LEVELS.index(row[0]), row[2]))

    counts = dict.fromkeys(LEVELS, 0)
    notes: list[str] = []
    for level, reason, path in rows:
        counts[level] += 1
        print(f"{level:<9} {path:<44} {reason}")
        if level != "allowed":
            notes.append(f"Changed files audit: {path} {VERDICTS[level]}")
    print()
    print(f"Changed files audit: {len(rows)} file(s) — allowed={counts['allowed']} "
          f"warning={counts['warning']} forbidden={counts['forbidden']}")
    for note in notes:
        print(note)

    if counts["forbidden"]:
        return EXIT_FORBIDDEN
    if counts["warning"]:
        return EXIT_WARNING
    return EXIT_ALLOWED


if __name__ == "__main__":
    sys.exit(main())
