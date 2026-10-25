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


def _handle_pending_update():
    """Проверяет незавершённую установку обновления.

    Работающий exe и загруженные DLL из ``_internal`` нельзя перезаписать,
    поэтому распаковку делает отдельный helper-процесс уже после выхода
    приложения. Если маркер всё ещё существует на старте — helper не сработал:
    сообщаем об этом (тост) и убираем маркер, чтобы не зациклиться.
    """
    if not getattr(sys, "frozen", False):
        return

    from core.paths import APP_DATA_DIR
    from core.updater import (
        UPDATE_ERROR_HELPER,
        clear_pending_update,
        read_pending_update,
    )

    if read_pending_update() is None:
        return
    try:
        (APP_DATA_DIR / "update_error.txt").write_text(UPDATE_ERROR_HELPER, encoding="utf-8")
    except OSError:
        pass
    clear_pending_update()


def _show_update_toasts(window):
    """Показывает тост о результате установки обновления (успех / ошибка)."""
    from PySide6.QtCore import QTimer

    messages = {
        "UPDATE_EXTRACT_FAILED": "Не удалось распаковать обновление. Файлы не изменены.",
        "UPDATE_HELPER_FAILED": "Установка обновления не завершилась. Попробуйте ещё раз.",
    }

    def _do():
        from core.paths import APP_DATA_DIR

        error_text = ""
        error_file = APP_DATA_DIR / "update_error.txt"
        if error_file.exists():
            try:
                raw = error_file.read_text(encoding="utf-8").strip()
            except OSError:
                raw = ""
            error_text = messages.get(
                raw, raw or "Неизвестная ошибка при установке обновления."
            )
            try:
                error_file.unlink()
            except OSError:
                pass
        if error_text:
            window.toast.show_message(f"⚠️ Ошибка установки обновления:\n{error_text}", 6000)
        elif "--update-installed" in sys.argv:
            window.toast.show_message("✅ Обновление установлено")

    QTimer.singleShot(300, _do)


if __name__ == "__main__":
    # 2. Сначала обрабатываем отложенное обновление (может завершить процесс).
    _handle_pending_update()

    from PySide6.QtCore import QCoreApplication, Qt
    from PySide6.QtGui import QFont
    from PySide6.QtWidgets import QApplication

    from ui.main_window import DeadlockModManager
    from ui.styles import create_app_icon

    # 3. Окно сразу создаётся с поддержкой Direct3D/OpenGL
    QCoreApplication.setAttribute(Qt.ApplicationAttribute.AA_ShareOpenGLContexts, True)

    app = QApplication(sys.argv)
    app.setApplicationName("Deadlock Mod Manager")
    app.setStyle("Fusion")
    app.setFont(QFont("Segoe UI", 10))
    app.setWindowIcon(create_app_icon())

    window = DeadlockModManager()
    window.show()
    _show_update_toasts(window)
    sys.exit(app.exec())
