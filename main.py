import sys
from pathlib import Path

# Гарантируем, что корневая папка проекта всегда известна Python и линтерам
ROOT_DIR = Path(__file__).resolve().parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from PySide6.QtGui import QFont
from PySide6.QtWidgets import QApplication

from ui.main_window import DeadlockModManager
from ui.styles import create_app_icon

if __name__ == "__main__":
    app = QApplication(sys.argv)
    app.setApplicationName("Deadlock Mod Manager")
    app.setStyle("Fusion")
    app.setFont(QFont("Segoe UI", 10))
    app.setWindowIcon(create_app_icon())
    window = DeadlockModManager()
    window.show()
    sys.exit(app.exec())
