import os
import sys
from pathlib import Path

# 1. Задаём флаги для Chromium ДО импорта Qt:
# --in-process-gpu и --no-sandbox предотвращают отдельное мигающее окно на старте
# --disable-features=CalculateNativeWinOcclusion убирает баг пересоздания окна Windows
os.environ["QTWEBENGINE_CHROMIUM_FLAGS"] = (
    "--disable-logging "
    "--in-process-gpu "
    "--disable-features=CalculateNativeWinOcclusion"
)

ROOT_DIR = Path(__file__).resolve().parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from PySide6.QtCore import QCoreApplication, Qt
from PySide6.QtGui import QFont
from PySide6.QtWidgets import QApplication

from ui.main_window import DeadlockModManager
from ui.styles import create_app_icon

if __name__ == "__main__":
    # 2. Окно сразу создаётся с поддержкой Direct3D/OpenGL
    QCoreApplication.setAttribute(Qt.ApplicationAttribute.AA_ShareOpenGLContexts, True)

    app = QApplication(sys.argv)
    app.setApplicationName("Deadlock Mod Manager")
    app.setStyle("Fusion")
    app.setFont(QFont("Segoe UI", 10))
    app.setWindowIcon(create_app_icon())

    window = DeadlockModManager()
    window.show()
    sys.exit(app.exec())