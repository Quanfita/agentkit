"""CLI Harness：参数校验 / 单次执行 / 真 REPL（子进程验证真实程序行为）。"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from agentkit.cli import main

ROOT = Path(__file__).resolve().parents[1]


def run_cli(*args: str, stdin: str = "") -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-m", "agentkit", *args],
        cwd=ROOT,
        input=stdin,
        capture_output=True,
        text=True,
        timeout=120,
    )


def test_non_echo_model_requires_explicit_model_name(capsys):
    with pytest.raises(SystemExit) as exc:
        main(["--model", "openai", "-t", "hi"])
    assert exc.value.code == 2
    assert "--model-name is required" in capsys.readouterr().err


def test_echo_one_shot_runs_the_whole_stack(capsys):
    assert main(["--model", "echo", "--no-trace", "-t", "总结 README"]) == 0
    assert capsys.readouterr().out.strip() == "echo: 总结 README"


def test_one_shot_subprocess_prints_result():
    proc = run_cli("--model", "echo", "--no-trace", "-t", "hi")
    assert proc.returncode == 0
    assert proc.stdout.strip() == "echo: hi"
    assert proc.stderr == ""


def test_trace_goes_to_stderr_not_stdout():
    proc = run_cli("--model", "echo", "-t", "hi")
    assert proc.stdout.strip() == "echo: hi"
    assert "[trace] agent.start" in proc.stderr
    assert "[trace] agent.end" in proc.stderr


def test_skills_are_loaded_from_the_given_directory(tmp_path):
    skill = tmp_path / "pdf"
    skill.mkdir()
    (skill / "SKILL.md").write_text(
        "---\nname: pdf\ndescription: extract pdf tables\n---\n用 pdfplumber", encoding="utf-8",
    )
    proc = run_cli("--model", "echo", "--no-trace", "--skills", str(tmp_path),
                   stdin="/skills\n/quit\n")
    assert proc.returncode == 0
    assert "- pdf: extract pdf tables" in proc.stdout


def test_budget_hook_stops_before_the_model_call():
    proc = run_cli("--model", "echo", "--budget", "0", "-t", "12345678")
    assert proc.returncode == 0
    assert proc.stdout.strip() == ""
    assert "[cost] budget=0 exceeded" in proc.stderr


def test_log_level_routes_events_to_standard_logging():
    proc = run_cli("--model", "echo", "--no-trace", "--log-level", "INFO", "-t", "hi")
    assert proc.stdout.strip() == "echo: hi"
    assert "INFO agentkit agent.start task='hi'" in proc.stderr


def test_repl_answers_and_serves_commands():
    proc = run_cli(
        "--model", "echo", "--no-trace",
        stdin="第一问\n/tools\n/skills\n/memory\n/system 你是测试助手\n第二问\n/quit\n",
    )
    assert proc.returncode == 0
    assert proc.stdout.splitlines()[0].startswith("agentkit 1.0.0 — model=EchoModel")
    assert "echo: 第一问" in proc.stdout
    assert "- read_file" in proc.stdout
    assert "- write_file" in proc.stdout
    assert "- list_dir" in proc.stdout
    assert "- code-review:" in proc.stdout
    assert "[memory] 1 items" in proc.stdout
    assert "[system] updated" in proc.stdout
    assert "echo: 第二问" in proc.stdout


def test_repl_exits_on_eof_without_traceback():
    proc = run_cli("--model", "echo", "--no-trace", stdin="")
    assert proc.returncode == 0
    assert "Traceback" not in proc.stderr


def test_repl_survives_empty_lines_and_unknown_commands():
    proc = run_cli("--model", "echo", "--no-trace", stdin="\n/nope\n/help\n/quit\n")
    assert proc.returncode == 0
    assert "unknown command: /nope" in proc.stdout
    assert "/quit" in proc.stdout
    assert "Traceback" not in proc.stderr
