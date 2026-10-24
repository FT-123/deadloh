import sys
import struct
from pathlib import Path
from PySide6.QtWidgets import QApplication
from PySide6.QtGui import QPainter, QColor, QPixmap, QPolygon
from PySide6.QtCore import Qt, QPoint, QByteArray, QBuffer
import PyInstaller.__main__


def draw_icon_layer(size: int) -> QPixmap:
    """Рисует четкую иконку под конкретный размер (16, 32, 48, 256 и т.д.)"""
    pix = QPixmap(size, size)
    pix.fill(Qt.transparent)

    p = QPainter(pix)
    p.setRenderHint(QPainter.Antialiasing)

    # Радиус скругления фона
    radius = max(3, int(size * 0.18))
    margin = max(1, int(size * 0.05))

    p.setBrush(QColor("#2563eb"))
    p.setPen(Qt.NoPen)
    p.drawRoundedRect(margin, margin, size - margin * 2, size - margin * 2, radius, radius)

    # Пропорции молнии
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
    """Собирает настоящий многослойный Windows .ico файл (16, 24, 32, 48, 64, 128, 256)"""
    app = QApplication.instance() or QApplication(sys.argv)
    sizes = [16, 24, 32, 48, 64, 128, 256]

    png_buffers = []
    for s in sizes:
        pix = draw_icon_layer(s)
        ba = QByteArray()
        buf = QBuffer(ba)
        buf.open(QBuffer.WriteOnly)
        pix.save(buf, "PNG")
        png_buffers.append((s, bytes(ba)))

    # Собираем заголовок ICO контейнера
    num_images = len(png_buffers)
    header = struct.pack("<HHH", 0, 1, num_images)

    offset = 6 + (num_images * 16)
    directory = []
    image_data = []

    for s, data in png_buffers:
        w = 0 if s >= 256 else s
        h = 0 if s >= 256 else s
        entry = struct.pack(
            "<BBBBHHII",
            w, h, 0, 0, 1, 32, len(data), offset
        )
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

    # Генерируем слоеный .ico
    save_multires_ico(ico_file)

    args = [
        "main.py",
        "--noconsole",
        "--onefile",
        "--name=DeadlockModManager",
        f"--icon={ico_file}",
        "--add-data=ui/style.qss;ui",
        "--clean",
        "-y"
    ]

    unrar_path = root_dir / "unrar.exe"
    if unrar_path.exists():
        args.append(f"--add-binary={unrar_path};.")

    # Упаковываем vpkmerge.exe (если есть)
    vpkmerge_path = root_dir / "vpkmerge.exe"
    if vpkmerge_path.exists():
        args.append(f"--add-binary={vpkmerge_path};.")
    else:
        print("⚠️ Внимание: vpkmerge.exe не найден рядом с build.py!")

    print("\n🚀 Сборка через PyInstaller...")
    PyInstaller.__main__.run(args)
    print("\n🎉 Готово! Проверь dist/DeadlockModManager/")


if __name__ == "__main__":
    build()