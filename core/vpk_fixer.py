import sys
import subprocess
import shutil
import re
from pathlib import Path
from .paths import BASE_DIR

# Системный флаг Windows для скрытия консоли дочернего процесса
NO_WINDOW = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0


def get_vpkmerge_path() -> Path | None:
    """Ищет vpkmerge.exe в tools/, корне, _internal или временной папке PyInstaller"""
    candidates = [
        BASE_DIR / "tools" / "vpkmerge.exe",
        BASE_DIR / "vpkmerge.exe",
        BASE_DIR / "_internal" / "vpkmerge.exe",
    ]
    _meipass = getattr(sys, "_MEIPASS", None)
    if _meipass:
        candidates.append(Path(_meipass) / "vpkmerge.exe")
    for p in candidates:
        if p.exists():
            return p
    return None


def get_base_pak(game_path: Path | None) -> Path | None:
    """Ищет оригинальный pak01_dir.vpk в папке игры"""
    if not game_path:
        return None
    pak = game_path / "pak01_dir.vpk"
    return pak if pak.exists() else None


def check_mod_stale(mod_vpk: Path, game_path: Path) -> tuple[bool, list[str], str | None]:
    """Проверяет мод через 'vpkmerge attachments check' без всплывающего окна консоли"""
    vpkmerge = get_vpkmerge_path()
    base_pak = get_base_pak(game_path)

    if not vpkmerge:
        return False, [], "vpkmerge.exe не найден"
    if not base_pak:
        return False, [], f"Оригинальный pak01_dir.vpk не найден в {game_path}"

    cmd = [
        str(vpkmerge),
        "attachments", "check",
        "--base", str(base_pak),
        str(mod_vpk)
    ]

    try:
        res = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="ignore",
            creationflags=NO_WINDOW  # <-- Прячет черное окно консоли
        )
    except Exception as e:
        return False, [], f"Сбой запуска процесса vpkmerge: {e}"

    output = res.stdout or ""
    lines = output.splitlines()

    stale_models = []
    for line in lines:
        m = re.search(r"^\s*(\S+)\s+stale:", line)
        if m:
            stale_models.append(m.group(1))

    is_stale = False
    for line in lines:
        m = re.search(r"(\d+)\s+of\s+\d+\s+mods?\s+ship", line, re.IGNORECASE)
        if m and int(m.group(1)) > 0:
            is_stale = True
            break

    if len(stale_models) > 0:
        is_stale = True

    if res.returncode != 0 and not is_stale:
        return False, [], f"vpkmerge завершился с кодом {res.returncode}: {output.strip()}"

    return is_stale, stale_models, None


def heal_mod_vpk(mod_folder: Path, game_path: Path) -> tuple[bool, str]:
    """Лечит мод через vpkmerge attachments heal без всплывающего окна консоли"""
    vpkmerge = get_vpkmerge_path()
    base_pak = get_base_pak(game_path)
    content_vpk = mod_folder / "content.vpk"

    if not vpkmerge or not base_pak or not content_vpk.exists():
        return False, "Не найден vpkmerge.exe, base pak или content.vpk мода"

    fixed_vpk = mod_folder / "content_fixed.vpk"
    last_backup = mod_folder / "content.vpk.bak"
    original_backup = mod_folder / "content.vpk.orig.bak"

    cmd_heal = [
        str(vpkmerge),
        "attachments", "heal",
        "--base", str(base_pak),
        str(content_vpk),
        "--output", str(fixed_vpk)
    ]

    try:
        res = subprocess.run(
            cmd_heal,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="ignore",
            creationflags=NO_WINDOW  # <-- Прячет черное окно консоли
        )
        if res.returncode != 0 or not fixed_vpk.exists():
            return False, f"Ошибка vpkmerge heal (код {res.returncode}):\n{res.stdout}"

        is_stale, _, check_err = check_mod_stale(fixed_vpk, game_path)
        if check_err:
            return False, f"Не удалось проверить вылеченный мод: {check_err}"
        if is_stale:
            return False, "После лечения файл всё ещё помечен как stale!"

        if not original_backup.exists():
            shutil.copy2(content_vpk, original_backup)
        # Ротируем бэкап «до последнего лечения»; оригинал остаётся в .orig.bak.
        shutil.copy2(content_vpk, last_backup)

        shutil.move(str(fixed_vpk), str(content_vpk))
        return True, "Мод успешно вылечен!"

    except Exception as e:
        return False, f"Исключение при лечении: {e}"
    finally:
        # Временный файл не должен оставаться в staging ни при каком исходе.
        try:
            fixed_vpk.unlink(missing_ok=True)
        except OSError:
            pass