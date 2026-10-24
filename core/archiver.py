import json
import os
import shutil
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

from .jsonio import write_json_atomic
from .paths import BASE_DIR, STAGING_DIR
from .vpk_parser import analyze_vpk

# Флаг для скрытия черного окна консоли Windows
NO_WINDOW = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0

try:
    import py7zr
except ImportError:
    py7zr = None

try:
    import rarfile

    # Приоритет поиска unrar.exe: портативный (корень, _internal, временная папка PyInstaller), затем системный
    possible_unrar_paths = [
        BASE_DIR / "unrar.exe",
        BASE_DIR / "UnRAR.exe",
        BASE_DIR / "_internal" / "unrar.exe",
        BASE_DIR / "_internal" / "UnRAR.exe",
    ]
    _meipass = getattr(sys, "_MEIPASS", None)
    if _meipass:
        possible_unrar_paths += [
            Path(_meipass) / "unrar.exe",
            Path(_meipass) / "UnRAR.exe",
        ]
    possible_unrar_paths += [
        Path(r"C:\Program Files\WinRAR\UnRAR.exe"),
        Path(r"C:\Program Files (x86)\WinRAR\UnRAR.exe"),
        Path(r"C:\Program Files (x86)\Unrar\UnRAR.exe"),
    ]

    for p in possible_unrar_paths:
        if p.exists():
            rarfile.UNRAR_TOOL = str(p)
            break
except ImportError:
    rarfile = None


def _safe_folder_name(mod_name: str) -> str:
    safe = "".join([c for c in mod_name if c.isalnum() or c in (" ", "_", "-")]).strip()
    return safe or "mod"


def _is_managed_folder(folder: Path) -> bool:
    return folder.is_dir() and ((folder / "meta.json").exists() or (folder / "content.vpk").exists())


def _read_meta(folder: Path) -> dict:
    meta_file = folder / "meta.json"
    if not meta_file.exists():
        return {}
    try:
        data = json.loads(meta_file.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def stage_vpk_file(vpk_path: Path, mod_name: str) -> Path:
    """Изолирует VPK в подпапку staging/.

    Повторный импорт того же мода обновляет существующую папку (сохраняя
    enabled), а не создаёт дубль. Папки с другим/испорченным meta.json и
    чужие папки не перезаписываются — для них берётся имя с суффиксом.
    """
    tags_data = analyze_vpk(vpk_path)
    safe = _safe_folder_name(mod_name)
    folder = STAGING_DIR / safe
    old_meta = _read_meta(folder) if _is_managed_folder(folder) else {}

    old_name = str(old_meta.get("name", "")).strip().casefold()
    same_mod = bool(old_meta) and old_name == mod_name.strip().casefold()

    if folder.exists() and not same_mod:
        counter = 1
        original = folder
        while folder.exists():
            folder = STAGING_DIR / f"{original.name}_{counter}"
            counter += 1
        old_meta = {}

    folder.mkdir(parents=True, exist_ok=True)
    meta_file = folder / "meta.json"

    # Атомарная замена, чтобы не оставить недописанный VPK.
    tmp_vpk = folder / "content.vpk.tmp"
    try:
        shutil.copy2(vpk_path, tmp_vpk)
        os.replace(tmp_vpk, folder / "content.vpk")
    finally:
        try:
            tmp_vpk.unlink(missing_ok=True)
        except OSError:
            pass

    meta = {
        "name": mod_name,
        "tags_data": tags_data,
        "enabled": bool(old_meta.get("enabled", True)),
    }
    write_json_atomic(meta_file, meta)
    return folder


def extract_and_stage_archive(archive_path: Path) -> tuple[int, str | None]:
    """Распаковывает zip, 7z или rar во временную папку и стейджит найденные VPK.

    Возвращает (количество_модов, текст_ошибки | None).
    """
    ext = archive_path.suffix.lower()
    count = 0

    with tempfile.TemporaryDirectory() as tmp_dir:
        tmp = Path(tmp_dir)
        try:
            # 1. Обычные .zip архивы
            if ext == ".zip":
                with zipfile.ZipFile(archive_path, "r") as z:
                    z.extractall(tmp)

            # 2. Архивы .7z
            elif ext == ".7z":
                if not py7zr:
                    return 0, "Для .7z требуется модуль py7zr (pip install py7zr)"
                with py7zr.SevenZipFile(archive_path, mode="r") as z:
                    z.extractall(tmp)

            # 3. Архивы .rar (двухуровневая распаковка)
            elif ext == ".rar":
                unpacked = False
                errors = []

                # План А: через rarfile + unrar.exe
                if rarfile:
                    try:
                        with rarfile.RarFile(archive_path) as r:
                            r.extractall(tmp)
                        unpacked = True
                    except Exception as rar_err:
                        errors.append(f"rarfile: {rar_err}")

                # План Б: через встроенный в Windows tar.exe (тихий запуск без консоли)
                if not unpacked:
                    try:
                        res = subprocess.run(
                            ["tar", "-xf", str(archive_path), "-C", str(tmp)],
                            capture_output=True,
                            creationflags=NO_WINDOW  # <-- Прячет окно консоли
                        )
                        if res.returncode == 0:
                            unpacked = True
                        else:
                            err_text = res.stderr.decode("cp866", errors="ignore")
                            errors.append(f"tar.exe: {err_text}".strip())
                    except Exception as tar_err:
                        errors.append(f"tar.exe: {tar_err}")

                if not unpacked:
                    details = "; ".join(errors) if errors else "нет доступного распаковщика"
                    return 0, f"Не удалось распаковать .rar ({details})"

            else:
                return 0, f"Формат {ext or 'без расширения'} не поддерживается"

        except Exception as e:
            return 0, f"Ошибка распаковки архива: {e}"

        # Собираем все найденные внутри VPK
        vpks = sorted(tmp.rglob("*.vpk"))
        for v in vpks:
            name = archive_path.stem if len(vpks) == 1 else f"{archive_path.stem} ({v.stem})"
            stage_vpk_file(v, name)
            count += 1

    return count, None