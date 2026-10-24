import struct
import sys
from pathlib import Path

import PyInstaller.__main__
from PySide6.QtCore import QBuffer, QByteArray, QPoint, Qt
from PySide6.QtGui import QColor, QPainter, QPixmap, QPolygon
from PySide6.QtWidgets import QApplication


def draw_icon_layer(size: int) -> QPixmap:
    """Рисует четкую иконку под конкретный размер."""
    pix = QPixmap(size, size)
    pix.fill(Qt.transparent)

    p = QPainter(pix)
    p.setRenderHint(QPainter.Antialiasing)

    radius = max(3, int(size * 0.18))
    margin = max(1, int(size * 0.05))

    p.setBrush(QColor("#2563eb"))
    p.setPen(Qt.NoPen)
    p.drawRoundedRect(margin, margin, size - margin * 2, size - margin * 2, radius, radius)

    p.setBrush(QColor("#ffffff"))
    pts = [
        QPoint(int(size * 0.53), int(size * 0.18)),
        QPoint(int(size * 0.31), int(size * 0.53)),
        QPoint(int(size * 0.47), int(size * 0.53)),
        QPoint(int(size * 0.41), int(size * 0.82)),
        QPoint(int(size * 0.69), int(size * 0.44)),
        QPoint(int(size * 0.53), int(size * 0.44)),
    ]
    p.drawPolygon(QPolygon(pts))
    p.end()
    return pix


def save_multires_ico(output_path: Path):
    """Собирает настоящий многослойный Windows .ico файл."""
    _app = QApplication.instance() or QApplication(sys.argv)
    sizes = [16, 24, 32, 48, 64, 128, 256]

    png_buffers = []
    for s in sizes:
        pix = draw_icon_layer(s)
        ba = QByteArray()
        buf = QBuffer(ba)
        buf.open(QBuffer.WriteOnly)
        pix.save(buf, "PNG")
        png_buffers.append((s, bytes(ba)))

    num_images = len(png_buffers)
    header = struct.pack("<HHH", 0, 1, num_images)
    offset = 6 + (num_images * 16)
    directory = []
    image_data = []

    for s, data in png_buffers:
        w = 0 if s >= 256 else s
        h = 0 if s >= 256 else s
        entry = struct.pack("<BBBBHHII", w, h, 0, 0, 1, 32, len(data), offset)
        directory.append(entry)
        image_data.append(data)
        offset += len(data)

    with open(output_path, "wb") as f:
        f.write(header)
        for d in directory:
            f.write(d)
        for img in image_data:
            f.write(img)

    print(f"✅ Создан мульти-размерный .ico (слои: {sizes}) -> {output_path.name}")

def build():
    root_dir = Path(__file__).resolve().parent
    ico_file = root_dir / "app_icon.ico"

    # Генерируем .ico
    save_multires_ico(ico_file)

    args = [
        "main.py",
        "--noconsole",
        "--onedir",
        "--name=DeadlockModManager",
        f"--icon={ico_file}",
        "--add-data=ui/style.qss;ui",
        "--collect-all=PySide6.QtWebEngineWidgets",
        "--collect-all=PySide6.QtWebEngineCore",
        "--clean",
        "-y",
    ]

    # Список внешних утилит и библиотек
    binaries = [
        "unrar.exe",
        "vpkmerge.exe",
        "Source2Viewer-CLI.exe",
        "libSkiaSharp.dll",
        "spirv-cross.dll",
    ]

    for b in binaries:
        # Ищем файл в tools/ или в корне
        candidate = root_dir / "tools" / b
        if not candidate.exists():
            candidate = root_dir / b

        if candidate.exists():
            # Упаковываем в подпапку tools (а не в корень):
            args.append(f"--add-binary={candidate};tools")
        else:
            print(f"⚠️ Внимание: {b} не найден!")

    print("\n🚀 Сборка через PyInstaller...")
    PyInstaller.__main__.run(args)
    print("\n🎉 Готово! Проверь dist/DeadlockModManager.exe")


if __name__ == "__main__":
    build()