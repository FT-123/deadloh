import json
import os
import shutil
import tempfile
from pathlib import Path

from .paths import STAGING_DIR, DEPLOYED_STATE_PATH

PAK_GLOB = "pak*_dir.vpk"
TMP_SUFFIX = ".dmm_tmp"


def _load_state() -> dict:
    if DEPLOYED_STATE_PATH.exists():
        try:
            data = json.loads(DEPLOYED_STATE_PATH.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                return data
        except Exception:
            pass
    return {}


def _known_files(state: dict) -> dict:
    """Возвращает {имя_папки: имя_pak} для файлов, которыми владеет менеджер.

    Старые версии писали только список deployed, присваивая pak01..pakNN
    в порядке списка, поэтому такое состояние можно однозначно восстановить.
    """
    files = state.get("files")
    if isinstance(files, dict):
        return {str(k): str(v) for k, v in files.items() if v}

    deployed = state.get("deployed") or []
    return {str(name): f"pak{i:02d}_dir.vpk" for i, name in enumerate(deployed, 1)}


def _active_mods() -> list[tuple[str, Path]]:
    if not STAGING_DIR.exists():
        return []

    mods = []
    for folder in sorted(STAGING_DIR.iterdir(), key=lambda p: p.name):
        meta_file = folder / "meta.json"
        vpk_file = folder / "content.vpk"
        if not (folder.is_dir() and meta_file.exists() and vpk_file.exists()):
            continue
        try:
            meta = json.loads(meta_file.read_text(encoding="utf-8"))
        except Exception:
            continue
        if meta.get("enabled", True):
            mods.append((folder.name, vpk_file))
    return mods


def _install(src: Path, target: Path) -> None:
    """Копирует VPK через временное имя, чтобы игра не увидела недописанный файл."""
    tmp = target.with_name(target.name + TMP_SUFFIX)
    shutil.copy2(src, tmp)
    os.replace(tmp, target)


def _write_state(deployed: list[str], files: dict[str, str]) -> None:
    tmp = DEPLOYED_STATE_PATH.with_name(DEPLOYED_STATE_PATH.name + ".tmp")
    payload = {"deployed": deployed, "files": files}
    tmp.write_text(json.dumps(payload, indent=4, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, DEPLOYED_STATE_PATH)


def get_deployed_mods_set(game_path: Path | None = None) -> set:
    """Моды, реально присутствующие в addons (а не только записанные в состоянии).

    Без game_path доверяем состоянию: проверить файлы негде.
    """
    state = _load_state()
    deployed = set(state.get("deployed", []))
    if not game_path:
        return deployed

    addons = game_path / "addons"
    if not addons.exists():
        return set()

    files = _known_files(state)
    present = {p.name for p in addons.glob(PAK_GLOB)}
    return {name for name in deployed if files.get(name) in present}


def deploy_active_mods(game_path: Path, progress_callback=None) -> tuple[int, str | None]:
    """Развертывает включенные моды в citadel/addons как pakXX_dir.vpk.

    Удаляет только файлы, которыми управляет сам менеджер, не трогает чужие
    pakXX_dir.vpk и откатывает изменения при любой ошибке копирования.
    """
    addons = game_path / "addons"
    addons.mkdir(parents=True, exist_ok=True)

    state = _load_state()
    previous = _known_files(state)
    active = _active_mods()

    backup_dir = Path(tempfile.mkdtemp(prefix=".dmm_deploy_", dir=addons))
    backups: dict[str, Path] = {}
    installed: list[Path] = []

    try:
        # 1. Бэкап текущих файлов менеджера — для отката.
        for filename in set(previous.values()):
            source = addons / filename
            if source.exists():
                target = backup_dir / filename
                shutil.copy2(source, target)
                backups[filename] = target

        # 2. Удаление только своих предыдущих pak-файлов.
        for filename in set(previous.values()):
            target = addons / filename
            if target.exists():
                try:
                    target.unlink()
                except Exception as e:
                    raise RuntimeError(f"Не удалось удалить {target.name}. Закройте игру!\n{e}")

        # 3. Свободные номера: чужие pakXX_dir.vpk не перезаписываем.
        occupied = {p.name for p in addons.glob(PAK_GLOB)}
        numbers = iter(range(1, 10000))
        mapping: dict[str, str] = {}

        for i, (name, vpk_file) in enumerate(active, 1):
            if progress_callback:
                progress_callback(i, len(active))

            filename = next(
                f"pak{n:02d}_dir.vpk" for n in numbers
                if f"pak{n:02d}_dir.vpk" not in occupied
            )
            occupied.add(filename)

            target = addons / filename
            _install(vpk_file, target)
            installed.append(target)
            mapping[name] = filename

        _write_state([name for name, _ in active], mapping)
        return len(active), None

    except Exception as e:
        # Откат: убираем новые файлы и возвращаем предыдущие.
        for target in installed:
            try:
                target.unlink()
            except OSError:
                pass
        for filename, backup in backups.items():
            try:
                shutil.copy2(backup, addons / filename)
            except OSError:
                pass
        return 0, str(e)

    finally:
        for leftover in addons.glob(f"*{TMP_SUFFIX}"):
            try:
                leftover.unlink()
            except OSError:
                pass
        shutil.rmtree(backup_dir, ignore_errors=True)
