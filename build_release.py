"""Сборка релиза одной командой: версия → PyInstaller → zip для GitHub Releases.

Запуск:
    python build_release.py 1.4            # явная версия
    python build_release.py --bump minor   # 1.2 → 1.3 (и т.п.)
    python build_release.py                # спросит версию (Enter — bump patch)

Результат: ``dist/DeadlockModManager-v<version>-win64.zip``. Внутри архива —
содержимое ``dist/DeadlockModManager/`` в корне (exe + _internal), именно так
его ожидает автообновление. Zip нужно прикрепить к GitHub Release с тегом
``v<version>`` (не draft и не pre-release).

Скрипт также обновляет ``APP_VERSION`` в ``core/updater.py`` ДО сборки, чтобы
внутри exe была правильная версия. После успешной сборки не забудьте закоммитить
это изменение.
"""

from __future__ import annotations

import argparse
import hashlib
import os
import re
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
UPDATER_PATH = ROOT / "core" / "updater.py"
DIST_DIR = ROOT / "dist"
BUNDLE_NAME = "DeadlockModManager"
BUNDLE_DIR = DIST_DIR / BUNDLE_NAME
EXE_NAME = f"{BUNDLE_NAME}.exe"
DEFAULT_REPO = "FT-123/deadloh"

REQUIRED_BINARIES = (
    "unrar.exe",
    "vpkmerge.exe",
    "Source2Viewer-CLI.exe",
    "libSkiaSharp.dll",
    "spirv-cross.dll",
)

_VERSION_RE = re.compile(r"^\d+(?:\.\d+)*$")
_APP_VERSION_RE = re.compile(r'(?m)^APP_VERSION\s*=\s*"[^"]*"')
_SKIP_SUFFIXES = {".pyc", ".pyo"}
_SKIP_DIRS = {"__pycache__"}


class BuildError(RuntimeError):
    """Понятная ошибка сборки релиза."""


def normalize_version(value: str) -> str:
    """``v1.4`` → ``1.4``; проверяет, что версия числовая."""
    version = str(value).strip().lstrip("vV")
    if not _VERSION_RE.match(version):
        raise BuildError(
            f"Некорректная версия: {value!r}. Ожидается 1, 1.2 или 1.2.3"
        )
    return version


def bump_version(version: str, part: str = "patch") -> str:
    """Поднимает major/minor/patch, сохраняя число компонентов версии."""
    parts = [int(piece) for piece in normalize_version(version).split(".")]
    if part == "major":
        index = 0
    elif part == "minor":
        index = 1 if len(parts) >= 2 else 0
    else:
        index = len(parts) - 1
    parts[index] += 1
    for i in range(index + 1, len(parts)):
        parts[i] = 0
    return ".".join(str(piece) for piece in parts)


def read_app_version(updater_path: Path = UPDATER_PATH) -> str:
    text = updater_path.read_text(encoding="utf-8")
    match = re.search(r'(?m)^APP_VERSION\s*=\s*"([^"]*)"', text)
    if not match:
        raise BuildError(f"Не найден APP_VERSION в {updater_path}")
    return match.group(1)


def write_app_version(version: str, updater_path: Path = UPDATER_PATH) -> str:
    """Пишет APP_VERSION и возвращает исходный текст файла (для отката)."""
    original = updater_path.read_text(encoding="utf-8")
    new_text, count = _APP_VERSION_RE.subn(f'APP_VERSION = "{version}"', original)
    if count != 1:
        raise BuildError(f"Не удалось однозначно обновить APP_VERSION в {updater_path}")
    if new_text != original:
        updater_path.write_text(new_text, encoding="utf-8")
    return original


def restore_app_version(original: str, updater_path: Path = UPDATER_PATH) -> None:
    updater_path.write_text(original, encoding="utf-8")


def resolve_version(
    current: str,
    explicit: str | None,
    bump: str | None,
    *,
    interactive: bool,
) -> str:
    """Выбирает версию: явная → bump → интерактивный ввод → текущая."""
    if explicit:
        return normalize_version(explicit)
    suggested = bump_version(current, bump or "patch")
    if bump:
        return suggested
    if interactive:
        answer = input(f"Версия релиза [{suggested}]: ").strip()
        return normalize_version(answer) if answer else suggested
    return normalize_version(current)


def find_bundle_dir(root: Path = ROOT) -> Path:
    bundle = root / "dist" / BUNDLE_NAME
    exe = bundle / EXE_NAME
    if not exe.is_file():
        raise BuildError(f"Не найден {exe}. Сначала соберите проект (build.py).")
    return bundle


def missing_binaries(bundle_dir: Path) -> list[str]:
    present = {p.name.lower() for p in bundle_dir.rglob("*") if p.is_file()}
    return [name for name in REQUIRED_BINARIES if name.lower() not in present]


def make_release_zip(bundle_dir: Path, zip_path: Path) -> tuple[int, str]:
    """Пакует содержимое bundle_dir в корень zip. Возвращает (файлов, sha256)."""
    zip_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = zip_path.with_name(zip_path.name + ".tmp")
    files = 0
    digest = hashlib.sha256()
    try:
        with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as archive:
            for path in sorted(bundle_dir.rglob("*")):
                if path.is_dir():
                    continue
                rel = path.relative_to(bundle_dir)
                if any(part in _SKIP_DIRS for part in rel.parts):
                    continue
                if path.suffix.lower() in _SKIP_SUFFIXES:
                    continue
                archive.write(path, rel.as_posix())
                files += 1
        with open(tmp, "rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
        tmp.replace(zip_path)
    finally:
        tmp.unlink(missing_ok=True)
    return files, digest.hexdigest()


def detect_repo(root: Path = ROOT) -> str:
    """owner/repo из git remote origin, иначе DEFAULT_REPO."""
    try:
        result = subprocess.run(
            ["git", "remote", "get-url", "origin"],
            cwd=root,
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return DEFAULT_REPO
    match = re.search(r"github\.com[:/]([^/\s]+/[^/\s]+?)(?:\.git)?$", result.stdout.strip())
    return match.group(1) if match else DEFAULT_REPO


def run_build(root: Path = ROOT) -> None:
    build_script = root / "build.py"
    if not build_script.is_file():
        raise BuildError(f"Не найден {build_script}")
    try:
        import PyInstaller  # noqa: F401
        import PySide6  # noqa: F401
    except ImportError as exc:
        raise BuildError(
            "PyInstaller/PySide6 не установлены в этом интерпретаторе. "
            "Запустите через .venv\\Scripts\\python.exe (build_release.bat делает это сам)."
        ) from exc
    print("\n🚀 PyInstaller: сборка onedir...")
    env = {
        **os.environ,
        "QT_QPA_PLATFORM": "offscreen",
        # build.py печатает emoji; на cp1251-консоли это UnicodeEncodeError.
        "PYTHONUTF8": "1",
        "PYTHONIOENCODING": "utf-8",
    }
    result = subprocess.run(
        [sys.executable, str(build_script)],
        cwd=str(root),
        env=env,
        check=False,
    )
    if result.returncode != 0:
        raise BuildError(f"Сборка завершилась с кодом {result.returncode}")


def print_publish_instructions(tag: str, zip_path: Path, repo: str, notes: str | None) -> None:
    rel_zip = zip_path.relative_to(ROOT) if zip_path.is_relative_to(ROOT) else zip_path
    notes = notes or f"Сборка Deadlock Mod Manager {tag}"
    print("\nЗагрузите zip в GitHub Release:")
    print(
        f'  gh release create {tag} "{rel_zip}" --title "{tag}" '
        f'--notes "{notes}" --repo {repo}'
    )
    print("\nИли вручную: GitHub → Releases → Draft a new release →")
    print(f"  тег {tag} → прикрепите {zip_path.name} → Publish release.")
    print("\nВажно для кнопки «Обновить»:")
    print("  • релиз должен быть опубликован (не Draft и не Pre-release);")
    print("  • тег новее установленной версии (сравниваются числа, v не важен);")
    print("  • к релизу прикреплён .zip (имя содержит «deadlock»);")
    print("  • публичному репозиторию токен не нужен (для приватного — GITHUB_TOKEN).")


def publish_release(tag: str, zip_path: Path, repo: str, notes: str | None) -> None:
    gh = shutil.which("gh")
    if not gh:
        print("\n⚠️ gh CLI не найден — загрузите zip вручную (см. команду выше).")
        return
    notes = notes or f"Сборка Deadlock Mod Manager {tag}"
    view = subprocess.run(
        [gh, "release", "view", tag, "--repo", repo],
        capture_output=True,
        text=True,
        check=False,
    )
    if view.returncode == 0:
        command = [gh, "release", "upload", tag, str(zip_path), "--repo", repo, "--clobber"]
    else:
        command = [
            gh, "release", "create", tag, str(zip_path),
            "--repo", repo, "--title", tag, "--notes", notes,
        ]
    print("\n🚀 Публикация:", " ".join(command))
    if subprocess.run(command, check=False).returncode != 0:
        print("⚠️ Не удалось опубликовать релиз. Загрузите zip вручную.")


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass

    parser = argparse.ArgumentParser(
        description="Собрать onedir-билд и zip для GitHub Releases"
    )
    parser.add_argument("version", nargs="?", help="Версия, например 1.4 или v1.4")
    parser.add_argument("--bump", choices=("major", "minor", "patch"), help="Поднять версию")
    parser.add_argument("--repo", default=None, help="GitHub owner/repo (по умолчанию из git)")
    parser.add_argument("--notes", default=None, help="Заметки к релизу для gh")
    parser.add_argument("--out", type=Path, default=None, help="Путь к итоговому zip")
    parser.add_argument("--no-build", action="store_true", help="Не запускать PyInstaller")
    parser.add_argument("--publish", action="store_true", help="Опубликовать через gh CLI")
    args = parser.parse_args(argv)

    try:
        current = read_app_version()
        version = resolve_version(
            current, args.version, args.bump, interactive=sys.stdin.isatty()
        )
        tag = f"v{version}"
        repo = args.repo or detect_repo()

        print(f"📦 Релиз {tag}  (репозиторий: {repo})")
        original: str | None = None
        if version != current:
            original = write_app_version(version)
            print(f"   APP_VERSION: {current} → {version}")
        else:
            print(f"   APP_VERSION без изменений: {current}")
            print("   ⚠️ Версия совпадает с текущей — установленная копия не увидит обновление.")

        try:
            if args.no_build:
                print("⏭ Сборка пропущена (--no-build)")
            else:
                run_build()
        except Exception:  # откатываем версию и пробрасываем ошибку
            if original is not None:
                restore_app_version(original)
                print("↩ APP_VERSION восстановлен после ошибки сборки")
            raise

        bundle = find_bundle_dir()
        missing = missing_binaries(bundle)
        if missing:
            raise BuildError("В сборке нет обязательных файлов: " + ", ".join(missing))

        zip_path = args.out or (DIST_DIR / f"{BUNDLE_NAME}-{tag}-win64.zip")
        zip_path.unlink(missing_ok=True)
        files, digest = make_release_zip(bundle, zip_path)
        size_mb = zip_path.stat().st_size / (1024 * 1024)
        sha_path = zip_path.with_name(zip_path.name + ".sha256")
        sha_path.write_text(f"{digest}  {zip_path.name}\n", encoding="utf-8")

        print(f"\n✅ Готово: {zip_path}")
        print(f"   файлов: {files}, размер: {size_mb:.1f} МБ")
        print(f"   SHA256: {digest}")
        if original is not None:
            print("   Не забудьте закоммитить core/updater.py (APP_VERSION).")

        print_publish_instructions(tag, zip_path, repo, args.notes)
        if args.publish:
            publish_release(tag, zip_path, repo, args.notes)
        return 0
    except BuildError as exc:
        print(f"\n❌ {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
