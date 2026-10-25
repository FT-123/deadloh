import os
import subprocess
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
    """Двухфазная установка: если есть отложенное обновление — извлекаем и перезапускаем.

    Вызывается ДО создания GUI (Qt ещё не запущен). Работает только для frozen-сборки.
    После успешного извлечения перезапускает exe с флагом ``--update-installed`` и
    завершает текущий процесс. При ошибке пишет update_error.txt и продолжает со
    старой версией (GUI покажет предупреждение).
    """
    if not getattr(sys, "frozen", False):
        return

    import zipfile

    from core.paths import APP_DATA_DIR
    from core.updater import clear_pending_update, read_pending_update

    marker = read_pending_update()
    if not marker:
        return

    zip_path = Path(marker.get("zip", ""))
    install_dir = Path(marker.get("install_dir", "")).resolve()
    error_file = APP_DATA_DIR / "update_error.txt"

    try:
        if not zip_path.exists():
            raise RuntimeError("файл обновления не найден")
        with zipfile.ZipFile(zip_path, "r") as zf:
            base_str = str(install_dir)
            for member in zf.namelist():
                target = (install_dir / member).resolve()
                if not (str(target) == base_str or str(target).startswith(base_str + os.sep)):
                    raise RuntimeError("небезопасный путь в архиве")
                zf.extract(member, install_dir)
        clear_pending_update()
    except Exception as exc:  # noqa: BLE001 — не хотим терять обновление молча
        try:
            error_file.write_text(str(exc), encoding="utf-8")
        except OSError:
            pass
        return  # продолжаем со старой версией; GUI покажет ошибку

    new_argv = [sys.executable] + ["--update-installed"] + [a for a in sys.argv[1:] if a != "--update-installed"]
    try:
        subprocess.Popen(new_argv, cwd=str(install_dir))
    except Exception as exc:  # noqa: BLE001 — перезапуск не должен ронять процесс
        try:
            error_file.write_text(f"не удалось перезапустить приложение: {exc}", encoding="utf-8")
        except OSError:
            pass
    os._exit(0)


def _show_update_toasts(window):
    """Показывает тосты после установки обновления (успех / ошибка)."""
    from PySide6.QtCore import QTimer

    def _do():
        if "--update-installed" in sys.argv:
            window.toast.show_message("✅ Обновление установлено")
        try:
            from core.paths import APP_DATA_DIR

            error_file = APP_DATA_DIR / "update_error.txt"
            if error_file.exists():
                msg = ""
                try:
                    msg = error_file.read_text(encoding="utf-8").strip()
                except OSError:
                    msg = "Неизвестная ошибка при установке обновления."
                window.toast.show_message(f"⚠️ Ошибка установки обновления:\n{msg}", 6000)
                try:
                    error_file.unlink()
                except OSError:
                    pass
        except Exception:  # noqa: BLE001, S110 — тост не должен ронять приложение
            pass

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
