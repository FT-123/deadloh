# core/model_exporter.py
import os
import subprocess
import sys
from pathlib import Path


def get_s2v_cli_path() -> Path | None:
    """Ищет Source2Viewer-CLI.exe в корне проекта или в tools/."""
    base = Path(sys.argv[0]).resolve().parent
    candidates = [
        base / "Source2Viewer-CLI.exe",
        base / "tools" / "Source2Viewer-CLI.exe",
        Path.cwd() / "Source2Viewer-CLI.exe",
    ]
    for c in candidates:
        if c.exists():
            return c
    return None


def resolve_gameinfo_path(game_path: Path | None) -> Path | None:
    """Находит файл gameinfo.gi для корректного поиска текстур Valve."""
    if not game_path:
        return None
    candidates = [
        game_path / "gameinfo.gi",
        game_path / "citadel" / "gameinfo.gi",
        game_path.parent / "citadel" / "gameinfo.gi",
    ]
    for p in candidates:
        if p.exists() and p.is_file():
            return p
    return None


def pick_best_model(glb_files: list[Path]) -> Path | None:
    """Умно выбирает основную модель героя, отсеивая хитбоксы и пропы."""
    if not glb_files:
        return None
    if len(glb_files) == 1:
        return glb_files[0]

    # Мусорные слова: хитбоксы, физика, LOD-ы низкого качества, эффекты
    IGNORE_KEYWORDS = (
        "phys", "collision", "agdo", "hitbox", "box",
        "lod1", "lod2", "lod3", "card", "portrait", "fx"
    )

    scored = []
    for p in glb_files:
        name = p.name.lower()
        full_path = str(p).lower()

        # Если это явный хитбокс или лод — пропускаем
        if any(ign in name for ign in IGNORE_KEYWORDS):
            continue

        score = 0
        # Модель в папке героев — это 100% наш кандидат
        if "heroes" in full_path:
            score += 20
        if "heroes_staging" in full_path or "heroes_wip" in full_path:
            score += 15
        if "_v2" in full_path:
            score += 10
        # Основные меши героев обычно названы как сам герой
        if p.stem.lower() in ("doorman", "infernus", "yamato", "haze", "bebop", "gman", "hero", "base"):
            score += 25

        scored.append((score, p.stat().st_size, p))

    if scored:
        # Сортируем: сначала самые релевантные по очкам, потом самые детальные по весу
        scored.sort(key=lambda item: (item[0], item[1]), reverse=True)
        return scored[0][2]

    # Если всё отфильтровалось — берём самый большой файл
    return max(glb_files, key=lambda p: p.stat().st_size)


def export_vpk_model_to_glb(
        vpk_path: Path,
        output_dir: Path,
        game_path: Path | None = None,
        force_rebuild: bool = False
) -> tuple[bool, Path | None, str]:
    """Декомпилирует модель из VPK в совместимый со всеми вьюверами GLB.

    Возвращает: (успех, путь_к_файлу, сообщение)
    """
    cli_path = get_s2v_cli_path()
    if not cli_path:
        return False, None, "Source2Viewer-CLI.exe не найден рядом с приложением!"

    if not vpk_path.exists():
        return False, None, f"VPK файл не найден: {vpk_path}"

    output_dir.mkdir(parents=True, exist_ok=True)

    # 1. Проверяем кеш
    if not force_rebuild:
        existing = list(output_dir.rglob("*.glb"))
        if existing:
            best = pick_best_model(existing)
            if best and best.stat().st_size > 1024:
                print(f"[ModelExporter] Найден готовый кеш: {best.name} ({best.stat().st_size} байт)")
                return True, best, ""

    out_str = str(output_dir.resolve()).rstrip("\\/") + "\\"
    gi_path = resolve_gameinfo_path(game_path)

    # 2. Формируем надёжную команду CLI
    cmd = [
        str(cli_path.resolve()),
        "-i", str(vpk_path.resolve()),
        "-o", out_str,
        "-d",
        "--vpk_extensions", "vmdl_c",
        "--gltf_export_format", "glb",
        "--gltf_textures_adapt",  # Адаптация текстур под стандарт glTF
        "--gltf_export_materials",  # Вшивать текстуры внутрь
        "--gltf_export_animations",
        "--gltf_animation_list", "respawn_countdown_idle",
    ]

    if gi_path:
        cmd.extend(["--game", str(gi_path.resolve())])
        print(f"[ModelExporter] gameinfo.gi подключен: {gi_path}")
    else:
        print("[ModelExporter] Предупреждение: gameinfo.gi не найден, экспортируем автономно")

    print(f"[ModelExporter] Запуск декомпилятора: {' '.join(cmd)}")

    try:
        flags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
        proc = subprocess.run(cmd, capture_output=True, text=True, creationflags=flags)

        if proc.returncode != 0:
            err = proc.stderr.strip() or proc.stdout.strip()
            print(f"[ModelExporter] Ошибка CLI (код {proc.returncode}): {err}")
            return False, None, f"Ошибка CLI: {err[:200]}"

        # 3. Сканируем результаты
        found_models = list(output_dir.rglob("*.glb"))
        if not found_models:
            print("[ModelExporter] В выводе не обнаружено файлов .glb")
            return False, None, "В моде нет 3D-моделей (.vmdl_c)"

        print(f"[ModelExporter] Успешно сгенерировано моделей: {len(found_models)}")
        for m in found_models:
            print(f"  • {m.relative_to(output_dir)} ({m.stat().st_size} байт)")

        chosen = pick_best_model(found_models)
        if not chosen:
            return False, None, "Не удалось выбрать корректный меш"

        print(f"[ModelExporter] Выбрана главная модель: {chosen.name}")
        return True, chosen, ""

    except Exception as exc:
        print(f"[ModelExporter] Критическое исключение: {exc}")
        return False, None, f"Сбой скрипта: {exc}"