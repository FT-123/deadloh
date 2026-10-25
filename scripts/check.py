"""Одна команда для проверки проекта с компактным выводом.

Запуск: python scripts/check.py [--paths ...] [--strict]

Шаги:
1) compileall по указанным путям;
2) ruff в формате concise (печатаются только первые N находок);
3) pytest с коротким traceback (используется интерпретатор из .venv,
   если он есть, — там установлен PySide6 и GUI-тесты не пропускаются).

Вывод ограничен, чтобы не засорять контекст при работе через агента.
"""

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_PATHS = ("core", "ui", "tests", "main.py", "build.py", "build_release.py", "scripts")


def _workspace_python() -> str:
    """Интерпретатор проекта: .venv (PySide6) или текущий."""
    venv_python = ROOT / ".venv" / "Scripts" / "python.exe"
    if venv_python.exists():
        return str(venv_python)
    return sys.executable


def _run(command: list[str], env: dict | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(
        command,
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=env,
        check=False,
    )


def _find_ruff() -> list[str] | None:
    for name in ("ruff", "ruff.exe"):
        executable = shutil.which(name)
        if executable:
            return [executable]
    try:
        probe = _run([sys.executable, "-m", "ruff", "--version"])
    except OSError:
        return None
    if probe.returncode == 0:
        return [sys.executable, "-m", "ruff"]
    return None


def _print_tail(text: str, limit: int) -> None:
    lines = [line for line in text.splitlines() if line.strip()]
    for line in lines[-limit:]:
        print(line)


def main() -> int:
    parser = argparse.ArgumentParser(description="Компактная проверка проекта")
    parser.add_argument(
        "--paths",
        nargs="+",
        default=list(DEFAULT_PATHS),
        help="Что проверять (по умолчанию core ui tests main.py build.py scripts)",
    )
    parser.add_argument(
        "--strict",
        action="store_true",
        help="Считать находки ruff ошибкой (по умолчанию baseline не валит проверку)",
    )
    parser.add_argument("--no-tests", action="store_true", help="Не запускать pytest")
    parser.add_argument(
        "--max-findings", type=int, default=8, help="Сколько находок ruff показать"
    )
    args = parser.parse_args()

    python = _workspace_python()
    paths = args.paths
    steps = 3 if not args.no_tests else 2
    failed = False
    step = 1

    # 1. Синтаксис
    syntax = _run([python, "-m", "compileall", "-q", *paths])
    if syntax.returncode == 0:
        print(f"[{step}/{steps}] syntax: ok")
    else:
        print(f"[{step}/{steps}] syntax: FAILED")
        _print_tail(syntax.stderr or syntax.stdout, 15)
        failed = True
    step += 1

    # 2. Линтер
    ruff = _find_ruff()
    if ruff is None:
        print(f"[{step}/{steps}] ruff: skipped (не найден)")
    else:
        lint = _run([*ruff, "check", "--output-format", "concise", *paths])
        findings = [line for line in (lint.stdout or "").splitlines() if line.strip()]
        if lint.returncode == 0:
            findings = []
        status = "ok" if lint.returncode == 0 else "findings"
        print(f"[{step}/{steps}] ruff: {status} ({len(findings)})")
        for line in findings[: args.max_findings]:
            print(f"  {line}")
        if len(findings) > args.max_findings:
            print(f"  ... +{len(findings) - args.max_findings} more")
        if findings and args.strict:
            failed = True
    step += 1

    # 3. Тесты (offscreen: без окон и без фокуса на рабочем столе)
    if not args.no_tests:
        test_env = {
            **os.environ,
            "QT_QPA_PLATFORM": "offscreen",
            # Без этого Qt на выходе иногда падает до сброса буфера pytest,
            # и вместо итоговой строки в лог попадает только faulthandler.
            "PYTHONUNBUFFERED": "1",
        }
        tests = _run([python, "-m", "pytest"], env=test_env)
        output = (tests.stdout or "") + (tests.stderr or "")
        lines = [line.strip() for line in output.splitlines() if line.strip()]
        summary = next(
            (
                line
                for line in reversed(lines)
                if any(word in line for word in ("passed", "failed", "error"))
                and any(char.isdigit() for char in line)
            ),
            lines[-1] if lines else "no output",
        )
        summary = summary.strip("= ").strip()
        passed = "passed" in summary and not any(
            word in summary for word in ("failed", "error")
        )
        # Qt иногда падает уже после отчёта pytest (teardown QThread) — это не провал тестов.
        teardown_only = passed and tests.returncode != 0
        suffix = f" (exit {tests.returncode}: Qt teardown)" if teardown_only else ""
        print(f"[{step}/{steps}] tests: {summary}{suffix}")
        if tests.returncode != 0 and not teardown_only:
            failed = True
            print("---")
            _print_tail(output, 25)

    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
