"""Один скриншот-монтаж интерфейса для визуальной проверки.

Запуск: python scripts/ui_preview.py [--out build/ui_preview.png]

Скрипт рендерит окна в offscreen-режиме (ничего не показывается на экране):
главное окно в обычном и узком виде, пустое состояние и окно GameBanana.
Данные фиктивные, сеть и реальная папка staging не затрагиваются.
Результат — одна картинка: дешевле и быстрее, чем серия отдельных скриншотов.
"""

import argparse
import json
import math
import os
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QRect, Qt
from PySide6.QtGui import QColor, QFont, QFontDatabase, QPainter, QPixmap
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

SAMPLE_MODS = (
    ("violetoutfit", "Violet Outfit Remastered", ["Violet"], ["Model"], True, False),
    ("haze_recolor", "Haze Recolor Pack", ["Haze"], ["Texture"], True, True),
    ("neon_ui", "Neon UI Overhaul", [], ["UI"], True, True),
    ("yamato_blade", "Yamato Cyberblade", ["Yamato"], ["Model", "VFX"], False, False),
)

GB_NAMES = (
    "Violet Outfit Remastered",
    "Haze Recolor Pack",
    "Neon UI Overhaul",
    "Yamato Cyberblade",
    "Abrams Heavy Coat",
    "Infernus Flames VFX",
    "Mcginnis Turret Skins",
    "Paradox Elegant Suit",
    "Wraith Neon Hair",
    "Seven Storm Effects",
    "Bebop Chrome Arms",
    "Ivy Living Garden",
)


def _register_fonts() -> None:
    fonts_dir = Path(os.environ.get("WINDIR", r"C:\Windows")) / "Fonts"
    for name in ("segoeui.ttf", "seguiemj.ttf", "seguisym.ttf"):
        path = fonts_dir / name
        if path.exists():
            QFontDatabase.addApplicationFont(str(path))


def _build_fixture(base: Path) -> tuple[Path, Path, Path]:
    """Создаёт фиктивные staging, папку игры и пустой staging."""
    staging = base / "staging"
    empty = base / "staging_empty"
    game = base / "game"
    game.mkdir(parents=True, exist_ok=True)
    empty.mkdir(parents=True, exist_ok=True)
    (game / "gameinfo.gi").write_text("// preview fixture\n", encoding="utf-8")

    for folder, name, heroes, types, enabled, stale in SAMPLE_MODS:
        mod_dir = staging / folder
        mod_dir.mkdir(parents=True, exist_ok=True)
        (mod_dir / "content.vpk").write_bytes(b"")
        (mod_dir / "meta.json").write_text(
            json.dumps(
                {
                    "name": name,
                    "tags_data": {"heroes": heroes, "types": types},
                    "enabled": enabled,
                    "is_stale": stale,
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
    return staging, empty, game


def _patch_modules(staging: Path, game: Path):
    """Подменяет пути и сетевые вызовы до создания окон."""
    import ui.gamebanana_window as gbw
    import ui.main_window as mw
    from core.gamebanana import GBFile, GBMod
    from ui import widgets

    mw.STAGING_DIR = staging
    widgets.STAGING_DIR = staging
    mw.get_deployed_mods_set = lambda game_path: {"violetoutfit", "neon_ui"}
    mw.is_gameinfo_patched = lambda game_path: True
    mw.DeadlockModManager.init_game_path = lambda self: setattr(self, "game_path", game)
    mw.DeadlockModManager.save_config = lambda self: None
    widgets.ModCardWidget.play_intro = lambda self, delay=0: None

    def fake_fetch(page, days=7):
        mods = [
            GBMod(
                mod_id=index + 1,
                name=name,
                downloads=1200 * (index + 1),
                likes=10 * (index + 3),
                views=5000 * (index + 1),
                author=f"Creator{index + 1}",
                category="Skins",
                profile_url="https://gamebanana.com/mods/1",
            )
            for index, name in enumerate(GB_NAMES)
        ]
        return mods, {"_nRecordCount": 5126}

    def fake_mod(mod_id: int) -> GBMod:
        index = max(0, min(int(mod_id) - 1, len(GB_NAMES) - 1))
        return GBMod(
            mod_id=int(mod_id),
            name=GB_NAMES[index],
            downloads=1200 * (index + 1),
            likes=10 * (index + 3),
            views=5000 * (index + 1),
            author=f"Creator{index + 1}",
            category="Skins",
            profile_url="https://gamebanana.com/mods/1",
        )

    def fake_files(mod_id: int) -> list[GBFile]:
        return [
            GBFile(
                file_id=1,
                name="mod_v1.vpk",
                size=18 * 1024 * 1024,
                date_added=0,
                downloads=0,
                download_url="https://example.invalid/mod.vpk",
                version="1.2",
            )
        ]

    gbw.fetch_recent_mods_page = fake_fetch
    gbw.get_mod = fake_mod
    gbw.get_mod_files = fake_files
    return mw, gbw


def _compose(
    shots: list[tuple[str, QPixmap]],
    out: Path,
    column_width: int = 700,
    gap: int = 18,
    title_height: int = 26,
) -> tuple[int, int]:
    """Склеивает кадры в сетку 2 колонки с подписями."""
    scaled: list[tuple[str, QPixmap]] = []
    for title, pixmap in shots:
        height = max(1, round(pixmap.height() * column_width / max(1, pixmap.width())))
        scaled.append(
            (
                title,
                pixmap.scaled(
                    column_width,
                    height,
                    Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation,
                ),
            )
        )

    columns = 2
    rows = math.ceil(len(scaled) / columns)
    row_heights = [
        max(pixmap.height() for _, pixmap in scaled[row * columns : (row + 1) * columns])
        for row in range(rows)
    ]
    width = gap + columns * (column_width + gap)
    height = gap + sum(title_height + row_height + gap for row_height in row_heights)

    canvas = QPixmap(width, height)
    canvas.fill(QColor("#0b0c0d"))
    painter = QPainter(canvas)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)

    y = gap
    for row, row_height in enumerate(row_heights):
        x = gap
        for title, pixmap in scaled[row * columns : (row + 1) * columns]:
            painter.setPen(QColor("#9aa0a6"))
            painter.setFont(QFont("Segoe UI", 9))
            painter.drawText(
                QRect(x, y, column_width, title_height),
                Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                title,
            )
            painter.drawPixmap(x, y + title_height, pixmap)
            x += column_width + gap
        y += title_height + row_height + gap
    painter.end()

    out.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(str(out))
    return canvas.width(), canvas.height()


def main() -> int:
    parser = argparse.ArgumentParser(description="Монтаж UI для визуальной проверки")
    parser.add_argument(
        "--out",
        type=Path,
        default=ROOT / "build" / "ui_preview.png",
        help="Куда сохранить монтаж",
    )
    args = parser.parse_args()

    app = QApplication.instance() or QApplication([])
    app.setStyle("Fusion")
    app.setFont(QFont("Segoe UI", 10))
    _register_fonts()

    temp_dir = Path(tempfile.mkdtemp(prefix="dmm-preview-"))
    gb_window = None
    window = None
    try:
        staging, empty, game = _build_fixture(temp_dir)
        mw, gbw = _patch_modules(staging, game)

        shots: list[tuple[str, QPixmap]] = []

        window = mw.DeadlockModManager()
        window.resize(1060, 700)
        window.show()
        QTest.qWait(500)
        shots.append(("Главное окно", window.grab()))

        window.resize(900, 640)
        QTest.qWait(150)
        shots.append(("Узкое окно (компактная шапка)", window.grab()))

        mw.STAGING_DIR = empty
        window.refresh_list()
        QTest.qWait(500)
        shots.append(("Пустое состояние", window.grab()))

        mw.STAGING_DIR = staging
        window.refresh_list()
        QTest.qWait(200)

        gb_window = gbw.GameBananaWindow(window)
        gb_window.resize(1080, 720)
        gb_window.show()
        for _ in range(100):
            if not gb_window._popular_loading:
                break
            QTest.qWait(20)
        QTest.qWait(200)
        shots.append(("GameBanana", gb_window.grab()))

        width, height = _compose(shots, args.out)
        print(f"UI preview: {args.out} ({width}x{height})")
    finally:
        if gb_window is not None:
            gb_window.shutdown()
        if window is not None:
            window.close()
        app.processEvents()
        shutil.rmtree(temp_dir, ignore_errors=True)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
