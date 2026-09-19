"""V3.1 Changed Files Audit（M5）——分类器与门禁退出码的回归测试。

纯逻辑用例覆盖政策三级（forbidden / allowed / warning）与 ``default`` 回退，以及
"精确路径优先于 glob"、"最长 glob 取胜"。端到端用例覆盖两个来源
（``git diff`` 与未跟踪文件）和三个退出码。

真实自检以 V3 提交 ``b61a739`` 为 base：此时工作树就是 V3.1 的改动，
要求 ``agentkit/kernel/**`` 零改动、无 forbidden。
"""
from __future__ import annotations

import importlib.util
import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "audit_changed_files.py"
V3_BASE = "b61a739"

_spec = importlib.util.spec_from_file_location("audit_changed_files", SCRIPT)
assert _spec is not None and _spec.loader is not None
audit = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(audit)

RULES, DEFAULT = audit.load_policy()


def _classify(path: str) -> tuple[str, str]:
    return audit.classify(path, RULES, DEFAULT)


def _git_paths(*args: str) -> list[str]:
    """独立于被测模块直接问 git（``-z`` 避免中文名被八进制 escape）。"""
    proc = subprocess.run(["git", *args, "-z"], cwd=ROOT, capture_output=True, check=True)
    return [c for c in proc.stdout.decode("utf-8", "replace").split("\0") if c]


def _kernel_paths(files: list[str]) -> list[str]:
    return [f for f in files if f.startswith(("agentkit/kernel/", "kernel/"))]


@pytest.fixture
def git_repo(tmp_path: Path) -> Path:
    """干净 temp 仓库：一个已提交文件，便于探测 diff 与未跟踪两个来源。"""
    def git(*args: str) -> None:
        subprocess.run(["git", *args], cwd=tmp_path, capture_output=True, check=True)

    git("init", "-q")
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "notes.md").write_text("base\n", encoding="utf-8")
    git("add", "-A")
    git("-c", "user.email=t@example.com", "-c", "user.name=t", "commit", "-qm", "base")
    return tmp_path


def _run_script(repo: Path) -> subprocess.CompletedProcess[str]:
    # PYTHONUTF8：政策 reason 含中文，CI 管道不应因控制台编码炸掉门禁。
    env = {**os.environ, "PYTHONUTF8": "1"}
    return subprocess.run([sys.executable, str(SCRIPT), "--base", "HEAD"],
                          cwd=repo, capture_output=True, text=True, encoding="utf-8",
                          env=env, check=False)


# ── 分类器：真实政策的四个桶 ─────────────────────────────────────────

@pytest.mark.parametrize("path", [
    "agentkit/kernel/loop.py", "kernel/state.py", "agentkit/kernel/types.py",
])
def test_kernel_is_forbidden(path: str) -> None:
    assert _classify(path) == ("forbidden", "fail_immediately")


@pytest.mark.parametrize("path", [
    "agentkit/executor/retry.py", "executor/retry.py",
    "agentkit/executor/permission.py", "agentkit/context/engine.py", "context/transform.py",
    "tests/unit/test_permission.py", "tests/property/test_transform_protocol.py",
    "docs/v3_1/CHANGED_FILES_POLICY.yml", "scripts/audit_changed_files.py",
    "CONTRIBUTING.md", "CHANGELOG_v3_1.md", "pyproject.toml",
])
def test_allowed_zones(path: str) -> None:
    assert _classify(path)[0] == "allowed"


@pytest.mark.parametrize("path", [
    "agentkit/agent.py", "agent.py",
    "agentkit/runtime/loop.py", "runtime/observability.py",
    "agentkit/api/__init__.py", "api/registry.py",
    "agentkit/harness/streaming.py", "harness/policy.py",
])
def test_warning_zones(path: str) -> None:
    assert _classify(path) == ("warning", "manual_review")


@pytest.mark.parametrize("path", [
    "README.md", "agentkit/models/registry.py", "setup.cfg",
])
def test_unmatched_falls_back_to_default(path: str) -> None:
    assert _classify(path) == ("warning", "manual_review")


@pytest.mark.parametrize("path,expected", [
    ("agentkit/kernel/hotfix_2026.py", "forbidden"),   # 新增文件同样受 kernel 约束
    ("tests/unit/test_brand_new.py", "allowed"),
])
def test_new_file_paths_go_through_the_same_policy(path: str, expected: str) -> None:
    assert _classify(path)[0] == expected


# ── 分类器：匹配优先级（合成政策，隔离于真实政策） ──────────────────

def _synthetic(tmp_path: Path, text: str):
    policy = tmp_path / "policy.yml"
    policy.write_text(textwrap.dedent(text).lstrip(), encoding="utf-8")
    return audit.load_policy(policy)


def test_exact_path_beats_prefix_glob(tmp_path: Path) -> None:
    rules, default = _synthetic(tmp_path, """
        kernel/**:
          level: forbidden
          action: fail_immediately
        kernel/allowed_leaf.py:
          level: allowed
          reason: exact
        default:
          level: warning
    """)
    assert audit.classify("agentkit/kernel/allowed_leaf.py", rules, default) == ("allowed", "exact")
    assert audit.classify("agentkit/kernel/other.py", rules, default)[0] == "forbidden"


def test_longest_glob_wins(tmp_path: Path) -> None:
    rules, default = _synthetic(tmp_path, """
        src/**:
          level: allowed
        src/secret/**:
          level: forbidden
          action: fail_immediately
        default:
          level: warning
    """)
    assert audit.classify("src/secret/key.py", rules, default)[0] == "forbidden"
    assert audit.classify("src/open/x.py", rules, default)[0] == "allowed"


# ── 门禁退出码 ──────────────────────────────────────────────────────

def test_exit_zero_when_all_allowed(capsys: pytest.CaptureFixture[str]) -> None:
    assert audit.main(["--paths", "docs/a.md", "tests/unit/test_a.py"]) == 0


def test_exit_two_on_warning(capsys: pytest.CaptureFixture[str]) -> None:
    code = audit.main(["--paths", "agentkit/agent.py", "docs/a.md"])
    out = capsys.readouterr().out
    assert code == 2
    assert "Changed files audit: agentkit/agent.py requires review" in out


def test_exit_one_on_forbidden(capsys: pytest.CaptureFixture[str]) -> None:
    code = audit.main(["--paths", "kernel/loop.py"])
    out = capsys.readouterr().out
    assert code == 1
    assert "Changed files audit: kernel/loop.py fail_immediately" in out


def test_forbidden_outranks_warning(capsys: pytest.CaptureFixture[str]) -> None:
    assert audit.main(["--paths", "agentkit/agent.py", "kernel/loop.py"]) == 1


# ── 变更文件来源：diff ∪ 未跟踪（新文件不许绕过审计） ────────────────

def test_untracked_new_file_is_audited(git_repo: Path) -> None:
    (git_repo / "docs" / "notes.md").write_text("modified\n", encoding="utf-8")
    kernel = git_repo / "agentkit" / "kernel"
    kernel.mkdir(parents=True)
    (kernel / "new_module.py").write_text("x = 1\n", encoding="utf-8")

    proc = _run_script(git_repo)
    assert "docs/notes.md" in proc.stdout, "已跟踪改动未被审计"
    assert "agentkit/kernel/new_module.py" in proc.stdout, "未跟踪的新文件绕过了审计"
    assert proc.returncode == 1
    assert "fail_immediately" in proc.stdout


# ── 真实自检：V3 工作树（base b61a739） ─────────────────────────────

def test_v31_working_tree_has_no_kernel_change_and_no_forbidden(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(ROOT)
    tracked = _git_paths("diff", "--name-only", V3_BASE)
    untracked = _git_paths("ls-files", "--others", "--exclude-standard")
    files = tracked + untracked
    assert files, f"base {V3_BASE} 之后没有任何改动，说明 base 不对"
    assert not _kernel_paths(files), f"kernel 出现改动：{_kernel_paths(files)}"

    code = audit.main(["--base", V3_BASE])
    out = capsys.readouterr().out
    with capsys.disabled():
        print(f"\n[changed files audit @ {V3_BASE}] exit={code}\n{out}")

    assert "forbidden=0" in out
    assert "fail_immediately" not in out
    warnings = [ln for ln in out.splitlines() if ln.startswith("warning")]
    assert code == (2 if warnings else 0)
