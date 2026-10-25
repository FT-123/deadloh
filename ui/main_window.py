import json
from pathlib import Path

from PySide6.QtCore import QSize, Qt, QThread, QUrl, Signal
from PySide6.QtGui import QDesktopServices, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QButtonGroup,
    QComboBox,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QVBoxLayout,
    QWidget,
    QStackedLayout
)

from core.archiver import extract_and_stage_archive, stage_vpk_file
from core.constants import DEADLOCK_APP_ID, HEROES, VALID_EXTS, clean_display_name
from core.deployer import deploy_active_mods, get_deployed_mods_set
from core.gameinfo import is_gameinfo_patched, patch_gameinfo_file
from core.jsonio import write_json_atomic
from core.locator import detect_deadlock_path
from core.model_exporter import export_vpk_model_to_glb
from core.paths import CONFIG_PATH, STAGING_DIR, ensure_data_dir
from core.vpk_fixer import check_mod_stale, get_vpkmerge_path, heal_mod_vpk
from core.vpk_parser import analyze_vpk

from .gamebanana_window import GameBananaWindow, GBDownloadWorker, GBSearchWorker
from .model_viewer import create_model_viewer, model_viewer_html
from .styles import apply_dark_titlebar, create_app_icon, load_stylesheet, refresh_style
from .vpk_editor_window import VPKEditorWindow
from .widgets import (
    AuroraHeader,
    DropListWidget,
    ElidedLabel,
    ModCardWidget,
    SparkleMark,
    StatBadge,
    Toast,
)

# Роль элемента списка: состояние карточки для фильтрации.
FILTER_ROLE = int(Qt.ItemDataRole.UserRole) + 1

# Значения фильтра по персонажу (в combo хранится data, а не текст).
HERO_FILTER_ALL = ""
HERO_FILTER_NONE = "__none__"


def _has_valid_tags(meta: dict) -> bool:
    tags = meta.get("tags_data")
    return (
        isinstance(tags, dict)
        and isinstance(tags.get("heroes"), list)
        and isinstance(tags.get("types"), list)
    )


class ImportWorker(QThread):
    progress_changed = Signal(int, int, str)
    import_finished = Signal(int, list)

    def __init__(self, paths: list[Path]):
        super().__init__()
        self.paths = paths

    def run(self):
        total = len(self.paths)
        imported = 0
        errors = []
        try:
            for i, p in enumerate(self.paths, 1):
                self.progress_changed.emit(i, total, f"Распаковка: {p.name} ({i}/{total})...")
                try:
                    if p.suffix.lower() == ".vpk":
                        stage_vpk_file(p, p.stem)
                        imported += 1
                    else:
                        count, err = extract_and_stage_archive(p)
                        imported += count
                        if err:
                            errors.append(f"{p.name}: {err}")
                except Exception as e:
                    errors.append(f"{p.name}: {e}")
        finally:
            # Сигнал должен уйти всегда, иначе UI останется с заблокированной кнопкой.
            self.import_finished.emit(imported, errors)


class ScanStaleWorker(QThread):
    """Фоновый сканер модов через vpkmerge attachments check"""
    progress_changed = Signal(int, int, str)
    scan_finished = Signal(int, list)

    def __init__(self, game_path: Path):
        super().__init__()
        self.game_path = game_path

    def run(self):
        stale_found = 0
        errors = []
        try:
            mod_folders = []
            if STAGING_DIR.exists():
                mod_folders = [
                    f for f in STAGING_DIR.iterdir()
                    if f.is_dir() and (f / "content.vpk").exists()
                ]
            total = len(mod_folders)

            for i, folder in enumerate(mod_folders, 1):
                self.progress_changed.emit(i, total, f"Проверка: {folder.name}...")
                content_vpk = folder / "content.vpk"
                meta_file = folder / "meta.json"

                try:
                    is_stale, stale_models, err = check_mod_stale(content_vpk, self.game_path)
                except Exception as e:
                    errors.append(f"{folder.name}: {e}")
                    continue

                if err:
                    # Не выдаём «всё в порядке», если проверка не состоялась.
                    errors.append(f"{folder.name}: {err}")
                    continue

                if meta_file.exists():
                    try:
                        meta = json.loads(meta_file.read_text(encoding="utf-8"))
                        if isinstance(meta, dict):
                            meta["is_stale"] = is_stale
                            meta["stale_models"] = stale_models
                            write_json_atomic(meta_file, meta)
                    except Exception:
                        pass

                if is_stale:
                    stale_found += 1
        finally:
            self.scan_finished.emit(stale_found, errors)


class DeployWorker(QThread):
    """Копирование модов в игру без блокировки UI-потока."""
    progress_changed = Signal(int, int)
    deploy_finished = Signal(int, str)

    def __init__(self, game_path: Path):
        super().__init__()
        self.game_path = game_path

    def run(self):
        try:
            total, err = deploy_active_mods(self.game_path, self.progress_changed.emit)
        except Exception as e:
            total, err = 0, f"Непредвиденная ошибка: {e}"
        self.deploy_finished.emit(total, err or "")


class HealWorker(QThread):
    """Лечение VPK через vpkmerge в фоне."""
    heal_finished = Signal(bool, str)

    def __init__(self, mod_folder: Path, game_path: Path):
        super().__init__()
        self.mod_folder = mod_folder
        self.game_path = game_path

    def run(self):
        try:
            ok, message = heal_mod_vpk(self.mod_folder, self.game_path)
        except Exception as e:
            ok, message = False, f"Непредвиденная ошибка: {e}"
        self.heal_finished.emit(ok, message)


class HealAllWorker(QThread):
    """Последовательно лечит все сломанные моды, не блокируя интерфейс."""
    progress_changed = Signal(int, int, str)
    heal_all_finished = Signal(int, list, list)

    def __init__(self, mod_folders: list[Path], game_path: Path):
        super().__init__()
        self.mod_folders = list(mod_folders)
        self.game_path = game_path

    def run(self):
        healed: list[str] = []
        errors: list[str] = []
        total = len(self.mod_folders)

        for index, folder in enumerate(self.mod_folders, 1):
            self.progress_changed.emit(index - 1, total, folder.name)
            try:
                ok, message = heal_mod_vpk(folder, self.game_path)
            except Exception as e:
                ok, message = False, str(e)
            if ok:
                healed.append(folder.name)
            else:
                errors.append(f"{folder.name}: {message}")

        self.progress_changed.emit(total, total, "")
        self.heal_all_finished.emit(len(healed), healed, errors)


class TagsWorker(QThread):
    """Досчитывает теги для старых meta.json, не подвешивая список."""
    tags_finished = Signal(int, list)

    def __init__(self, folders: list[Path]):
        super().__init__()
        self.folders = folders

    def run(self):
        updated = 0
        failed = []
        for folder in self.folders:
            try:
                meta_file = folder / "meta.json"
                vpk_file = folder / "content.vpk"
                if not (meta_file.exists() and vpk_file.exists()):
                    continue

                meta = json.loads(meta_file.read_text(encoding="utf-8"))
                if not isinstance(meta, dict) or _has_valid_tags(meta):
                    continue

                tags_data = analyze_vpk(vpk_file)

                # Пока шёл анализ, пользователь мог переключить enabled —
                # перечитываем meta и меняем только теги.
                fresh = json.loads(meta_file.read_text(encoding="utf-8"))
                if not isinstance(fresh, dict):
                    fresh = meta
                fresh["tags_data"] = tags_data
                write_json_atomic(meta_file, fresh)
                updated += 1
            except Exception:
                failed.append(folder.name)

        self.tags_finished.emit(updated, failed)


class ModelPreviewWorker(QThread):
    """Фоновый экспорт 3D модели из мода."""
    preview_ready = Signal(str, bool, str, str)  # folder_name, success, glb_path_str, error_msg

    def __init__(self, folder_name: str, mod_folder: Path, game_path: Path | None):
        super().__init__()
        self.folder_name = folder_name
        self.mod_folder = mod_folder
        self.game_path = game_path

    def run(self):
        vpk_path = self.mod_folder / "content.vpk"
        cache_dir = self.mod_folder / ".preview_cache"

        if not vpk_path.exists():
            self.preview_ready.emit(self.folder_name, False, "", "Файл content.vpk не найден")
            return

        ok, glb_path, err = export_vpk_model_to_glb(vpk_path, cache_dir, self.game_path)

        self.preview_ready.emit(self.folder_name, ok, str(glb_path) if glb_path else "", err)


class DeadlockModManager(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setAttribute(Qt.WidgetAttribute.WA_NativeWindow, True)
        self.setAttribute(Qt.WidgetAttribute.WA_DontCreateNativeAncestors, True)
        self.setWindowTitle("Deadlock Mod Manager")
        self.resize(1060, 700)
        self.setStyleSheet(load_stylesheet())
        self.setWindowIcon(create_app_icon())

        self.game_path = None
        self.import_worker = None
        self.scan_worker = None
        self.deploy_worker = None
        self.heal_worker = None
        self.heal_all_worker = None
        self.tags_worker = None
        self.gb_window: GameBananaWindow | None = None
        self.vpk_editor_window: VPKEditorWindow | None = None
        self.gb_search_worker: GBSearchWorker | None = None
        self.gb_download_worker: GBDownloadWorker | None = None
        self.deploy_silent = False
        self.heal_target = None
        self.tags_skip = set()

        ensure_data_dir()

        self.init_game_path()
        self.init_ui()
        self.refresh_list()

    def showEvent(self, event):
        super().showEvent(event)
        apply_dark_titlebar(self)

    def closeEvent(self, event):
        # Не даём Qt уничтожить работающий QThread (это аварийно завершает процесс).
        if self.gb_window is not None:
            self.gb_window.shutdown()
        if self.vpk_editor_window is not None:
            self.vpk_editor_window.shutdown()
        workers = (
            self.import_worker, self.scan_worker, self.deploy_worker,
            self.heal_worker, self.heal_all_worker, self.tags_worker,
            self.gb_search_worker, self.gb_download_worker,
        )
        running = [w for w in workers if w and w.isRunning()]
        if running:
            self.statusBar().showMessage("⏳ Завершение текущей операции перед выходом...")
            for worker in running:
                # Сетевые операции не должны блокировать выход навсегда:
                # скачивание отменяется, поиск по таймауту, затем поток снимается.
                if worker is self.gb_download_worker or worker is self.gb_search_worker:
                    if not worker.wait(3000):
                        worker.terminate()
                        worker.wait(1000)
                else:
                    worker.wait()
        super().closeEvent(event)

    def _worker_running(self, name: str) -> bool:
        worker = getattr(self, name, None)
        return bool(worker and worker.isRunning())

    def _any_worker_running(self, include_tags: bool = True) -> bool:
        names = (
            "import_worker", "scan_worker", "deploy_worker", "heal_worker",
            "heal_all_worker", "gb_search_worker", "gb_download_worker",
        )
        if include_tags:
            names += ("tags_worker",)
        return any(self._worker_running(name) for name in names)

    def _show_busy(self):
        self.statusBar().showMessage("⏳ Дождитесь завершения текущей операции", 4000)

    def init_game_path(self):
        self.load_config()
        if self.game_path and not (self.game_path / "gameinfo.gi").exists():
            # Конфиг мог устареть (перенос библиотеки Steam, удаление игры).
            self.game_path = None

        if not self.game_path:
            self.game_path = detect_deadlock_path()
            self.save_config()

    def init_ui(self):
        central = QWidget()
        central.setObjectName("root")
        self.setCentralWidget(central)
        self.setMinimumSize(840, 580)

        layout = QVBoxLayout(central)
        layout.setContentsMargins(22, 18, 22, 14)
        layout.setSpacing(14)

        # ── Шапка с живым градиентом ──────────────────────────────────────
        header = AuroraHeader()
        header_row = QHBoxLayout(header)
        header_row.setContentsMargins(22, 16, 18, 16)
        header_row.setSpacing(12)

        header_row.addWidget(SparkleMark(42), 0, Qt.AlignmentFlag.AlignVCenter)

        title_box = QVBoxLayout()
        title_box.setSpacing(3)
        app_title = QLabel('Deadlock <span style="color:#a8c7fa;">Mods</span>')
        app_title.setObjectName("appTitle")
        app_title.setMinimumWidth(150)
        title_box.addWidget(app_title)

        self.lbl_path = ElidedLabel()
        self.lbl_path.setObjectName("subtitle")
        title_box.addWidget(self.lbl_path)
        header_row.addLayout(title_box, 1)

        self.btn_check_mods = QPushButton("🔍 Проверить моды")
        self.btn_check_mods.setObjectName("ghostBtn")
        self.btn_check_mods.setCursor(Qt.PointingHandCursor)
        self.btn_check_mods.setToolTip("Проверить моды на поломки моделей через vpkmerge")
        self.btn_check_mods.clicked.connect(self.start_scan_stale_mods)
        header_row.addWidget(self.btn_check_mods)

        self.btn_fix_all = QPushButton("🔧 Починить все")
        self.btn_fix_all.setObjectName("ghostBtn")
        self.btn_fix_all.setCursor(Qt.PointingHandCursor)
        self.btn_fix_all.setToolTip(
            "Починить все сломанные моды через vpkmerge (создаются резервные копии)"
        )
        self.btn_fix_all.setEnabled(False)
        self.btn_fix_all.clicked.connect(self.start_heal_all)
        header_row.addWidget(self.btn_fix_all)

        self.btn_status_patch = QPushButton()
        self.btn_status_patch.setObjectName("stateBtn")
        self.btn_status_patch.setCursor(Qt.PointingHandCursor)
        self.btn_status_patch.setToolTip(
            "Патчит gameinfo.gi, чтобы игра видела моды. Бэкап создаётся автоматически."
        )
        self.btn_status_patch.clicked.connect(self.patch_gameinfo)
        header_row.addWidget(self.btn_status_patch)

        self.btn_gb = QPushButton("🌐 GameBanana")
        self.btn_gb.setCursor(Qt.PointingHandCursor)
        self.btn_gb.setToolTip("Поиск и скачивание модов через GameBanana")
        self.btn_gb.clicked.connect(self.open_gb_window)
        header_row.addWidget(self.btn_gb)

        self.btn_vpk_edit = QPushButton("🧩 VPK-редактор")
        self.btn_vpk_edit.setCursor(Qt.PointingHandCursor)
        self.btn_vpk_edit.setToolTip(
            "Открыть VPK в отдельном окне: замена ресурсов и звуков"
        )
        self.btn_vpk_edit.clicked.connect(self.open_vpk_editor_window)
        header_row.addWidget(self.btn_vpk_edit)

        btn_folder = QPushButton("📁")
        btn_folder.setObjectName("iconBtn")
        btn_folder.setToolTip("Выбрать папку citadel вручную")
        btn_folder.setCursor(Qt.PointingHandCursor)
        btn_folder.clicked.connect(self.manual_select_game_folder)
        header_row.addWidget(btn_folder)
        layout.addWidget(header)

        # ── Статистика ────────────────────────────────────────────────────
        stats_row = QHBoxLayout()
        stats_row.setSpacing(10)
        self.stat_total = StatBadge("Всего модов", "#a8c7fa")
        self.stat_active = StatBadge("Активно", "#81c995")
        self.stat_deployed = StatBadge("В игре", "#fdd663")
        self.stat_stale = StatBadge("Требуют починки", "#f28b82")
        for badge in (
            self.stat_total,
            self.stat_active,
            self.stat_deployed,
            self.stat_stale,
        ):
            stats_row.addWidget(badge)
        stats_row.addStretch(1)
        layout.addLayout(stats_row)

        # ── Поиск ─────────────────────────────────────────────────────────
        search_box = QHBoxLayout()
        search_box.setSpacing(12)
        self.search_input = QLineEdit()
        self.search_input.setObjectName("searchField")
        self.search_input.setFixedHeight(46)
        self.search_input.setClearButtonEnabled(True)
        self.search_input.setPlaceholderText("Поиск по названию, герою или типу мода…")
        self.search_input.textChanged.connect(self.filter_list)
        search_box.addWidget(self.search_input, 1)

        self.hero_combo = QComboBox()
        self.hero_combo.setObjectName("characterFilter")
        self.hero_combo.setFixedHeight(46)
        self.hero_combo.setMinimumWidth(190)
        self.hero_combo.setCursor(Qt.PointingHandCursor)
        self.hero_combo.setToolTip("Показать моды выбранного персонажа")
        self.hero_combo.addItem("Все герои", HERO_FILTER_ALL)
        self.hero_combo.addItem("Без героя", HERO_FILTER_NONE)
        self.hero_combo.currentIndexChanged.connect(lambda _index: self.apply_filters())
        self._hero_filter_values: tuple[str, ...] = ()
        search_box.addWidget(self.hero_combo)

        self.lbl_count = QLabel("0 модов")
        self.lbl_count.setObjectName("mutedLabel")
        search_box.addWidget(self.lbl_count)
        layout.addLayout(search_box)

        # ── Быстрые фильтры ───────────────────────────────────────────────
        chips_row = QHBoxLayout()
        chips_row.setSpacing(8)
        self._filter_group = QButtonGroup(self)
        self._filter_group.setExclusive(True)
        self._active_filter = "all"
        self._filter_chips: dict[str, QPushButton] = {}
        for key, title in (
            ("all", "Все"),
            ("active", "Активные"),
            ("deployed", "В игре"),
            ("disabled", "Выключенные"),
        ):
            chip = QPushButton(title)
            chip.setObjectName("filterChip")
            chip.setCheckable(True)
            chip.setCursor(Qt.PointingHandCursor)
            chip.setProperty("filterKey", key)
            self._filter_group.addButton(chip)
            chips_row.addWidget(chip)
            self._filter_chips[key] = chip
        self._filter_chips["all"].setChecked(True)
        self._filter_group.buttonClicked.connect(self._on_filter_chip_clicked)
        chips_row.addStretch(1)
        layout.addLayout(chips_row)

        # ── Список модов + Боковая панель ─────────────────────────────────
        content_box = QHBoxLayout()
        content_box.setSpacing(12)

        self.list_widget = DropListWidget()
        content_box.addWidget(self.list_widget, 1)

        # Создаём панель предпросмотра (пока скрыта)
        self.preview_panel = self._build_preview_panel()
        self.preview_panel.setVisible(False)
        content_box.addWidget(self.preview_panel)

        layout.addLayout(content_box, 1)

        # ── Нижняя панель действий ────────────────────────────────────────
        bottom = QHBoxLayout()
        bottom.setSpacing(10)
        btn_open = QPushButton("📂 Папка стейджинга")
        btn_open.setObjectName("ghostBtn")
        btn_open.setCursor(Qt.PointingHandCursor)
        btn_open.clicked.connect(lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(str(STAGING_DIR))))
        bottom.addWidget(btn_open)

        bottom.addStretch(1)

        self.progress_bar = QProgressBar()
        self.progress_bar.setFixedSize(170, 6)
        self.progress_bar.setTextVisible(False)
        self.progress_bar.setVisible(False)
        bottom.addWidget(self.progress_bar, 0, Qt.AlignmentFlag.AlignVCenter)

        btn_launch = QPushButton("▶ Запустить игру")
        btn_launch.setObjectName("launchBtn")
        btn_launch.setCursor(Qt.PointingHandCursor)
        btn_launch.clicked.connect(lambda: QDesktopServices.openUrl(QUrl(f"steam://rungameid/{DEADLOCK_APP_ID}")))
        bottom.addWidget(btn_launch)

        self.btn_deploy = QPushButton("⚡ Применить моды")
        self.btn_deploy.setObjectName("primaryBtn")
        self.btn_deploy.setCursor(Qt.PointingHandCursor)
        self.btn_deploy.clicked.connect(lambda: self.deploy_mods(silent=False))
        bottom.addWidget(self.btn_deploy)
        layout.addLayout(bottom)

        self.statusBar().showMessage("Готово к работе")
        self.toast = Toast(central)
        self.update_header_status()
        self._apply_header_density()

        QShortcut(QKeySequence("Ctrl+F"), self, activated=self.focus_search)

    def focus_search(self):
        """Ставит курсор в поле поиска (Ctrl+F)."""
        self.search_input.setFocus()
        self.search_input.selectAll()


    def update_header_status(self):
        compact = bool(getattr(self, "_compact_header", False))
        if self.game_path and (self.game_path / "gameinfo.gi").exists():
            self.lbl_path.set_full_text(str(self.game_path))
            self.lbl_path.setToolTip(str(self.game_path))
            if is_gameinfo_patched(self.game_path):
                self.btn_status_patch.setText("🟢 Готово" if compact else "🟢 Игра готова к модам")
                state = "ok"
            else:
                self.btn_status_patch.setText("⚠️ Патч" if compact else "⚠️ Пропатчить gameinfo.gi")
                state = "warn"
        else:
            self.lbl_path.set_full_text("Папка citadel не найдена — укажите путь к игре")
            self.lbl_path.setToolTip("")
            self.btn_status_patch.setText("Указать путь к игре")
            state = "warn"

        self.btn_status_patch.setProperty("state", state)
        refresh_style(self.btn_status_patch)
        self._sync_fix_all_button()

    def notify(self, text: str, msec: int = 2600) -> None:
        """Показывает всплывающее уведомление и дублирует его в статус-баре."""
        toast = getattr(self, "toast", None)
        if toast is not None:
            toast.show_message(text, msec)
        self.statusBar().showMessage(text, max(msec, 3000))

    def resizeEvent(self, event):
        super().resizeEvent(event)
        toast = getattr(self, "toast", None)
        if toast is not None:
            toast.reposition()
        if hasattr(self, "btn_check_mods"):
            self._apply_header_density()

    def _apply_header_density(self) -> None:
        """На узком окне оставляет у кнопок только иконки — шапка не ломается."""
        if not all(
            hasattr(self, name)
            for name in (
                "btn_check_mods",
                "btn_fix_all",
                "btn_gb",
                "btn_vpk_edit",
                "btn_status_patch",
            )
        ):
            return
        compact = self.width() < 1040
        if getattr(self, "_compact_header", None) == compact:
            return
        self._compact_header = compact
        self.btn_check_mods.setText("🔍" if compact else "🔍 Проверить моды")
        self.btn_fix_all.setText("🔧" if compact else "🔧 Починить все")
        self.btn_gb.setText("🌐" if compact else "🌐 GameBanana")
        self.btn_vpk_edit.setText("🧩" if compact else "🧩 VPK-редактор")
        self.update_header_status()


    def open_gb_window(self) -> None:
        """Открывает (или поднимает уже открытое) окно-браузер GameBanana."""
        if self.gb_window is None:
            self.gb_window = GameBananaWindow(self)
        window = self.gb_window
        window.show()
        window.raise_()
        window.activateWindow()

    def open_vpk_editor_window(self) -> None:
        """Открывает (или поднимает) отдельное окно редактора VPK."""
        if self.vpk_editor_window is None:
            self.vpk_editor_window = VPKEditorWindow(self)
        window = self.vpk_editor_window
        window.show()
        window.raise_()
        window.activateWindow()

    def _gb_busy(self, message: str) -> None:
        """Показывает сообщение о занятости и в окне GameBanana, и в статус-баре."""
        if self.gb_window is not None:
            self.gb_window.show_busy(message)
        self.statusBar().showMessage(message, 4000)

    def start_gb_search(self, query: str, character: str = "") -> bool:
        """Запускает поиск модов в фоне. Возвращает True, если поиск запущен."""
        if not (query.strip() or character.strip()):
            return False
        if self._worker_running("gb_search_worker"):
            self._gb_busy("⏳ Поиск уже выполняется…")
            return False
        if self.gb_window is not None:
            self.gb_window.set_searching()
        self.gb_search_worker = GBSearchWorker(query, character)
        self.gb_search_worker.search_finished.connect(self.on_gb_results)
        self.gb_search_worker.search_error.connect(self.on_gb_error)
        self.gb_search_worker.start()
        return True

    def start_gb_download(self, mod_id: int, file_id: int | None = None) -> bool:
        """Запускает скачивание последней версии мода с GameBanana в фоне."""
        if self._worker_running("gb_download_worker"):
            self._gb_busy("⏳ Скачивание уже выполняется…")
            return False
        if self._worker_running("deploy_worker"):
            self._gb_busy("⏳ Дождитесь завершения деплоя модов…")
            return False
        self.btn_deploy.setEnabled(False)
        self.progress_bar.setVisible(True)
        self.progress_bar.setRange(0, 0)
        worker = GBDownloadWorker(mod_id, file_id)
        self.gb_download_worker = worker
        worker.download_finished.connect(self.on_gb_download_finished)
        worker.download_error.connect(self.on_gb_download_error)
        worker.download_cancelled.connect(self.on_gb_download_cancelled)
        worker.progress_changed.connect(self._on_gb_download_progress)
        if self.gb_window is not None:
            self.gb_window.set_downloading()
        worker.start()
        return True

    def on_gb_results(self, mods: list) -> None:
        """Отображает результаты поиска в окне GameBanana."""
        if self.gb_window is None:
            return
        self.gb_window.set_results(mods)

    def on_gb_error(self, message: str) -> None:
        """Показывает ошибку поиска в окне GameBanana и в статус-баре."""
        if self.gb_window is not None:
            self.gb_window.show_error(message)
        self.statusBar().showMessage(f"❌ Ошибка GameBanana: {message}", 8000)

    def _on_gb_download_progress(self, received: int, total: int) -> None:
        """Обновляет прогресс-бар скачивания мода (total == 0 — размер неизвестен)."""
        if total > 0:
            self.progress_bar.setMaximum(total)
            self.progress_bar.setValue(received)
        else:
            self.progress_bar.setRange(0, 0)
        if self.gb_window is not None:
            self.gb_window.on_download_progress(received, total)

    def cancel_gb_download(self) -> bool:
        """Прерывает текущее скачивание мода; возвращает True, если оно шло."""
        worker = self.gb_download_worker
        if worker is None or not worker.isRunning():
            return False
        worker.cancel()
        self.statusBar().showMessage("⏹ Прерываем скачивание мода…", 5000)
        return True

    def on_gb_download_cancelled(self) -> None:
        """Обрабатывает прерванное пользователем скачивание мода."""
        self.btn_deploy.setEnabled(True)
        self.progress_bar.setVisible(False)
        self.progress_bar.setRange(0, 100)
        if self.gb_window is not None:
            self.gb_window.on_download_cancelled()
        self.statusBar().showMessage("⏹ Скачивание мода прервано", 5000)

    def on_gb_download_finished(self, mod_name: str, file_name: str, staged: str) -> None:
        """Обрабатывает успешное завершение скачивания мода."""
        del staged  # путь стейджа уже учтён через refresh_list()
        self.btn_deploy.setEnabled(True)
        self.progress_bar.setVisible(False)
        self.progress_bar.setRange(0, 100)
        self.refresh_list()
        if self.gb_window is not None:
            self.gb_window.on_download_finished(mod_name, file_name)
        self.statusBar().showMessage(
            f"✅ Мод «{clean_display_name(mod_name)}» скачан: {file_name}", 6000
        )

    def on_gb_download_error(self, message: str) -> None:
        """Обрабатывает ошибку скачивания мода."""
        self.btn_deploy.setEnabled(True)
        self.progress_bar.setVisible(False)
        self.progress_bar.setRange(0, 100)
        if self.gb_window is not None:
            self.gb_window.on_download_error(message)
        self.statusBar().showMessage(f"❌ Не удалось скачать мод: {message}", 8000)

    def refresh_list(self):
        STAGING_DIR.mkdir(parents=True, exist_ok=True)
        self.list_widget.clear()
        deployed_set = get_deployed_mods_set(self.game_path)
        pending_tags = []
        known_heroes: set[str] = set()

        for index, folder in enumerate(sorted(STAGING_DIR.iterdir())):
            meta_file = folder / "meta.json"
            vpk_file = folder / "content.vpk"

            if folder.is_dir() and meta_file.exists() and vpk_file.exists():
                try:
                    meta = json.loads(meta_file.read_text(encoding="utf-8"))
                except Exception:
                    continue
                if not isinstance(meta, dict):
                    continue

                if not _has_valid_tags(meta) and folder.name not in self.tags_skip:
                    pending_tags.append(folder)

                tags_data = meta.get("tags_data")
                if not isinstance(tags_data, dict):
                    tags_data = {}
                heroes = tags_data.get("heroes")
                types = tags_data.get("types")
                heroes = [str(x) for x in heroes] if isinstance(heroes, list) else []
                types = [str(x) for x in types] if isinstance(types, list) else []

                name = meta.get("name")
                if not isinstance(name, str) or not name.strip():
                    name = folder.name

                enabled = meta.get("enabled", True)
                if not isinstance(enabled, bool):
                    enabled = True
                is_stale = meta.get("is_stale", False)
                if not isinstance(is_stale, bool):
                    is_stale = False

                is_deployed = folder.name in deployed_set
                card = ModCardWidget(folder.name, meta, is_deployed, self)
                item = QListWidgetItem(self.list_widget)
                item.setSizeHint(QSize(0, 66))
                known_heroes.update(heroes)
                search_text = " ".join([card.display_name, name, *heroes, *types]).lower()
                item.setData(Qt.UserRole, search_text)
                item.setData(
                    FILTER_ROLE,
                    {
                        "enabled": enabled,
                        "deployed": is_deployed,
                        "stale": is_stale,
                        "heroes": heroes,
                        "types": types,
                    },
                )

                self.list_widget.addItem(item)
                self.list_widget.setItemWidget(item, card)
                if index < 14:
                    card.play_intro(index * 22)

        self._sync_hero_filter(known_heroes)
        self.update_mod_counter()
        self.filter_list(self.search_input.text())
        self._schedule_tags_scan(pending_tags)

    def _sync_hero_filter(self, known_heroes: set[str]) -> None:
        """Дополняет фильтр персонажами из ростера и тегами из staging."""
        combo = getattr(self, "hero_combo", None)
        if combo is None:
            return
        heroes = sorted(set(HEROES) | known_heroes, key=str.casefold)
        signature = tuple(heroes)
        if getattr(self, "_hero_filter_values", ()) == signature:
            return

        current = combo.currentData()
        self._hero_filter_values = signature
        combo.blockSignals(True)
        combo.clear()
        combo.addItem("Все герои", HERO_FILTER_ALL)
        combo.addItem("Без героя", HERO_FILTER_NONE)
        for hero in heroes:
            combo.addItem(hero, hero)
        index = combo.findData(current) if current is not None else 0
        combo.setCurrentIndex(max(0, index))
        combo.blockSignals(False)

    def on_mod_renamed(self, folder_name: str, display_name: str) -> None:
        """Обновляет название в поисковом индексе после переименования."""
        for i in range(self.list_widget.count()):
            item = self.list_widget.item(i)
            card = self.list_widget.itemWidget(item)
            if card is None or card.folder_name != folder_name:
                continue
            data = item.data(FILTER_ROLE)
            if not isinstance(data, dict):
                data = {}
            heroes = [str(h) for h in data.get("heroes") or []]
            types = [str(t) for t in data.get("types") or []]
            item.setData(
                Qt.UserRole,
                " ".join([display_name, card.raw_name, *heroes, *types]).lower(),
            )
            break
        self.apply_filters()

    def _schedule_tags_scan(self, folders: list[Path]):
        if not folders:
            return
        if self._any_worker_running():
            return

        self.tags_worker = TagsWorker(folders)
        self.tags_worker.tags_finished.connect(self.on_tags_finished)
        self.tags_worker.start()

    def on_tags_finished(self, updated: int, failed: list):
        if self.tags_worker:
            self.tags_worker.wait()
        self.tags_skip.update(failed)
        if updated:
            self.refresh_list()

    def update_mod_counter(self):
        total = self.list_widget.count()
        active = deployed = stale = 0
        for i in range(total):
            item = self.list_widget.item(i)
            card = self.list_widget.itemWidget(item)
            if card is None:
                continue
            data = item.data(FILTER_ROLE)
            if not isinstance(data, dict):
                data = {}
            data["enabled"] = card.switch.isChecked()
            item.setData(FILTER_ROLE, data)
            if card.switch.isChecked():
                active += 1
            if card.is_deployed:
                deployed += 1
            if data.get("stale"):
                stale += 1

        self.stat_total.set_value(total)
        self.stat_active.set_value(active)
        self.stat_deployed.set_value(deployed)
        self.stat_stale.set_value(stale)
        self._stale_count = stale
        self._sync_fix_all_button()
        self.apply_filters()

    def _on_filter_chip_clicked(self, button: QPushButton):
        key = button.property("filterKey")
        self._active_filter = key if isinstance(key, str) else "all"
        self.apply_filters()

    def filter_list(self, query: str):
        self.apply_filters(query)

    def apply_filters(self, query: str | None = None):
        """Применяет поиск и выбранный быстрый фильтр к списку модов."""
        if not hasattr(self, "list_widget") or not hasattr(self, "search_input"):
            return
        if query is None:
            query = self.search_input.text()
        query = query.lower().strip()

        visible = 0
        total = self.list_widget.count()
        for i in range(total):
            item = self.list_widget.item(i)
            data = item.data(FILTER_ROLE)
            if not isinstance(data, dict):
                data = {}
            match_query = not query or query in (item.data(Qt.UserRole) or "")

            active_filter = getattr(self, "_active_filter", "all")
            if active_filter == "active":
                match_filter = bool(data.get("enabled"))
            elif active_filter == "deployed":
                match_filter = bool(data.get("deployed"))
            elif active_filter == "disabled":
                match_filter = not bool(data.get("enabled"))
            else:
                match_filter = True

            match_hero = True
            hero_combo = getattr(self, "hero_combo", None)
            if hero_combo is not None:
                hero_filter = hero_combo.currentData()
                item_heroes = data.get("heroes") or []
                if hero_filter == HERO_FILTER_NONE:
                    match_hero = not item_heroes
                elif hero_filter:
                    match_hero = hero_filter in item_heroes

            hidden = not (match_query and match_filter and match_hero)
            item.setHidden(hidden)
            if not hidden:
                visible += 1

        if total == 0:
            self.lbl_count.setText("0 модов")
        else:
            self.lbl_count.setText(f"{visible} из {total}")

    # --- СКАНИРОВАНИЕ И ЛЕЧЕНИЕ СЛОМАННЫХ МОДОВ ---
    def start_scan_stale_mods(self):
        if not self.game_path:
            QMessageBox.warning(self, "Внимание", "Сначала выберите папку игры citadel!")
            return
        if not get_vpkmerge_path():
            QMessageBox.warning(self, "Ошибка", "Файл vpkmerge.exe не найден рядом с программой!")
            return
        if self._any_worker_running():
            self._show_busy()
            return

        self.btn_check_mods.setEnabled(False)
        self.btn_fix_all.setEnabled(False)
        self.progress_bar.setVisible(True)

        self.scan_worker = ScanStaleWorker(self.game_path)
        self.scan_worker.progress_changed.connect(lambda cur, tot, txt: (
        self.progress_bar.setMaximum(tot), self.progress_bar.setValue(cur), self.statusBar().showMessage(f"🔍 {txt}")))
        self.scan_worker.scan_finished.connect(self.on_scan_finished)
        self.scan_worker.start()

    def on_scan_finished(self, stale_count: int, errors: list | None = None):
        if self.scan_worker:
            self.scan_worker.wait()
        self.btn_check_mods.setEnabled(True)
        self.progress_bar.setVisible(False)
        self.refresh_list()

        if stale_count > 0 and errors:
            self.statusBar().showMessage(
                f"⚠️ Сломанных модов: {stale_count}. Не удалось проверить: {len(errors)}", 8000)
        elif stale_count > 0:
            self.statusBar().showMessage(
                f"⚠️ Найдено сломанных модов: {stale_count}. Нажмите 🔧 на карточке или «Починить все».",
                7000,
            )
        elif errors:
            self.statusBar().showMessage(f"❌ Не удалось проверить модов: {len(errors)}. Причина: {errors[0]}", 8000)
        else:
            self.statusBar().showMessage("✅ Все моды совместимы и в порядке!", 4000)

    def fix_mod_card(self, folder_name: str, card_widget: ModCardWidget):
        """Запускает лечение мода через vpkmerge в фоне."""
        if not self.game_path:
            self.statusBar().showMessage("❌ Сначала выберите папку citadel!", 4000)
            return
        if self._any_worker_running():
            self._show_busy()
            return

        mod_folder = STAGING_DIR / folder_name
        disp_name = clean_display_name(folder_name)
        self.heal_target = (folder_name, card_widget.is_deployed)
        self.statusBar().showMessage(f"🔧 Лечение мода «{disp_name}» через vpkmerge...", 3000)
        self.progress_bar.setVisible(True)
        self.progress_bar.setRange(0, 0)

        self.heal_worker = HealWorker(mod_folder, self.game_path)
        self.heal_worker.heal_finished.connect(self.on_heal_finished)
        self.heal_worker.start()

    def on_heal_finished(self, ok: bool, msg_text: str):
        if self.heal_worker:
            self.heal_worker.wait()
        self.progress_bar.setVisible(False)
        self.progress_bar.setRange(0, 100)

        folder_name, was_deployed = self.heal_target or ("", False)
        self.heal_target = None
        disp_name = clean_display_name(folder_name) if folder_name else ""

        if not ok:
            self.statusBar().showMessage(f"❌ Не удалось починить мод: {msg_text}", 6000)
            return

        meta_file = STAGING_DIR / folder_name / "meta.json"
        if meta_file.exists():
            try:
                meta = json.loads(meta_file.read_text(encoding="utf-8"))
                if isinstance(meta, dict):
                    meta["is_stale"] = False
                    meta["stale_models"] = []
                    write_json_atomic(meta_file, meta)
            except Exception:
                pass

        if was_deployed:
            self.statusBar().showMessage(f"✅ Мод «{disp_name}» починен, обновляю в игре...", 5000)
            self.deploy_mods(silent=True)
        else:
            self.refresh_list()
            self.statusBar().showMessage(
                f"✅ Мод «{disp_name}» починен (бэкапы: content.vpk.bak, content.vpk.orig.bak)", 5000)

    def _stale_mod_folders(self) -> list[Path]:
        """Возвращает папки модов, у которых meta.json помечен как is_stale."""
        folders: list[Path] = []
        if not STAGING_DIR.exists():
            return folders
        for folder in sorted(STAGING_DIR.iterdir()):
            if not folder.is_dir() or not (folder / "content.vpk").exists():
                continue
            meta_file = folder / "meta.json"
            if not meta_file.exists():
                continue
            try:
                meta = json.loads(meta_file.read_text(encoding="utf-8"))
            except Exception:
                continue
            if isinstance(meta, dict) and meta.get("is_stale"):
                folders.append(folder)
        return folders

    def _sync_fix_all_button(self) -> None:
        """Включает «Починить все» только когда есть что чинить."""
        button = getattr(self, "btn_fix_all", None)
        if button is None:
            return
        can_fix = getattr(self, "_stale_count", 0) > 0 and bool(self.game_path)
        if not self._worker_running("heal_all_worker"):
            button.setEnabled(can_fix)

    def start_heal_all(self):
        """Чинит все сломанные моды подряд в фоновом потоке."""
        if not self.game_path:
            QMessageBox.warning(self, "Внимание", "Сначала выберите папку игры citadel!")
            return
        if not get_vpkmerge_path():
            QMessageBox.warning(self, "Ошибка", "Файл vpkmerge.exe не найден рядом с программой!")
            return
        if self._any_worker_running():
            self._show_busy()
            return

        folders = self._stale_mod_folders()
        if not folders:
            self.notify("✅ Сломанных модов нет — починка не нужна", 3200)
            return

        answer = QMessageBox.question(
            self,
            "Починка модов",
            f"Починить сломанных модов: {len(folders)}?\n\n"
            "Для каждого мода создаются резервные копии content.vpk.bak "
            "и content.vpk.orig.bak.",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.Yes,
        )
        if answer != QMessageBox.Yes:
            return

        self.btn_fix_all.setEnabled(False)
        self.btn_check_mods.setEnabled(False)
        self.progress_bar.setVisible(True)
        self.progress_bar.setRange(0, len(folders))
        self.progress_bar.setValue(0)
        self.statusBar().showMessage(f"🔧 Починка модов: 0/{len(folders)}…")

        self.heal_all_worker = HealAllWorker(folders, self.game_path)
        self.heal_all_worker.progress_changed.connect(self.on_heal_all_progress)
        self.heal_all_worker.heal_all_finished.connect(self.on_heal_all_finished)
        self.heal_all_worker.start()

    def on_heal_all_progress(self, current: int, total: int, name: str):
        self.progress_bar.setRange(0, max(total, 1))
        self.progress_bar.setValue(current)
        if name:
            self.statusBar().showMessage(f"🔧 Лечение «{name}» ({current + 1}/{total})…")
        else:
            self.statusBar().showMessage(f"🔧 Готово: {current}/{total}")

    def on_heal_all_finished(self, healed_count: int, healed: list, errors: list):
        if self.heal_all_worker:
            self.heal_all_worker.wait()
        self.btn_check_mods.setEnabled(True)
        self.progress_bar.setVisible(False)
        self.progress_bar.setRange(0, 100)

        for folder_name in healed:
            meta_file = STAGING_DIR / folder_name / "meta.json"
            if not meta_file.exists():
                continue
            try:
                meta = json.loads(meta_file.read_text(encoding="utf-8"))
                if isinstance(meta, dict):
                    meta["is_stale"] = False
                    meta["stale_models"] = []
                    write_json_atomic(meta_file, meta)
            except Exception:
                pass

        deployed_set = get_deployed_mods_set(self.game_path) if self.game_path else set()
        needs_deploy = bool(set(healed) & deployed_set)
        self.refresh_list()

        if healed_count:
            self.notify(f"✅ Починено модов: {healed_count}", 3600)
            if needs_deploy:
                self.deploy_mods(silent=True)
        elif not errors:
            self.notify("Починка завершена без изменений", 3000)

        if errors:
            details = "\n".join(f"• {error}" for error in errors[:5])
            if len(errors) > 5:
                details += f"\n… и ещё {len(errors) - 5}"
            self.statusBar().showMessage(
                f"⚠️ Починено: {healed_count}. Ошибок: {len(errors)}", 8000
            )
            QMessageBox.warning(self, "Починка завершена с ошибками", details)

    # --- ИМПОРТ ---
    def start_import_worker(self, urls):
        if self._any_worker_running():
            self._show_busy()
            return

        paths = [Path(u.toLocalFile()) for u in urls if Path(u.toLocalFile()).suffix.lower() in VALID_EXTS]
        if not paths:
            return

        self.tags_skip.clear()
        self.btn_deploy.setEnabled(False)
        self.progress_bar.setMaximum(len(paths))
        self.progress_bar.setValue(0)
        self.progress_bar.setVisible(True)

        self.import_worker = ImportWorker(paths)
        self.import_worker.progress_changed.connect(
            lambda cur, tot, txt: (self.progress_bar.setValue(cur), self.statusBar().showMessage(f"📥 {txt}")))
        self.import_worker.import_finished.connect(self.on_import_finished)
        self.import_worker.start()

    def on_import_finished(self, count, errors):
        if self.import_worker:
            self.import_worker.wait()
        self.btn_deploy.setEnabled(True)
        self.progress_bar.setVisible(False)
        self.refresh_list()

        if errors:
            self.statusBar().showMessage(f"⚠️ Импортировано: {count}. Ошибок: {len(errors)}", 8000)
            details = "\n".join(f"• {e}" for e in errors[:5])
            if len(errors) > 5:
                details += f"\n… и ещё {len(errors) - 5}"
            QMessageBox.warning(self, "Импорт завершён с ошибками", details)
        else:
            self.notify(f"✅ Импортировано модов: {count}", 3200)

    # --- ДЕПЛОЙ ---
    def deploy_mods(self, silent: bool = False) -> bool:
        """Запускает деплой в фоне. Возвращает False, если запуск не состоялся."""
        if self._any_worker_running(include_tags=False):
            self._show_busy()
            return False
        if not self.game_path or not (self.game_path / "gameinfo.gi").exists():
            self.game_path = None
            self.save_config()
            self.update_header_status()
            QMessageBox.warning(self, "Ошибка", "Папка игры не найдена или в ней нет gameinfo.gi.\nУкажите путь к .../Deadlock/game/citadel.")
            return False

        self.deploy_silent = silent
        self.btn_deploy.setEnabled(False)
        self.progress_bar.setVisible(True)
        self.progress_bar.setRange(0, 0)

        self.deploy_worker = DeployWorker(self.game_path)
        self.deploy_worker.progress_changed.connect(self.on_deploy_progress)
        self.deploy_worker.deploy_finished.connect(self.on_deploy_finished)
        self.deploy_worker.start()
        return True

    def on_deploy_progress(self, current: int, total: int):
        self.progress_bar.setRange(0, max(total, 1))
        self.progress_bar.setValue(current)
        self.statusBar().showMessage(f"⚡ Развертывание: {current}/{total}...")

    def on_deploy_finished(self, total: int, err: str):
        if self.deploy_worker:
            self.deploy_worker.wait()

        self.btn_deploy.setEnabled(True)
        self.progress_bar.setVisible(False)
        self.progress_bar.setRange(0, 100)

        if err:
            self.refresh_list()
            self.statusBar().showMessage("❌ Деплой отменён, изменения откачены", 6000)
            QMessageBox.critical(self, "Ошибка деплоя", err)
            return

        self.refresh_list()
        self.deploy_silent = False

        if total == 0:
            self.notify("🧹 Все моды отключены — файлы убраны из addons", 4200)
        else:
            self.notify(f"⚡ Моды применены в игру: {total}", 3200)

    def patch_gameinfo(self):
        if not self.game_path:
            # Кнопка в состоянии «путь не найден» должна открывать выбор папки.
            self.manual_select_game_folder()
            if not self.game_path:
                return
        if is_gameinfo_patched(self.game_path):
            return

        ok, message = patch_gameinfo_file(self.game_path)
        if ok:
            self.update_header_status()
            self.notify("✅ gameinfo.gi настроен — бэкап: gameinfo.gi.bak", 4200)
        else:
            QMessageBox.critical(self, "Ошибка", message)

    def manual_select_game_folder(self):
        folder = QFileDialog.getExistingDirectory(self, "Выберите папку game/citadel")
        if folder:
            p = Path(folder)
            if (p / "game" / "citadel").exists():
                self.game_path = p / "game" / "citadel"
            elif p.name == "citadel":
                self.game_path = p
            else:
                QMessageBox.warning(self, "Ошибка", "Выберите каталог 'citadel' внутри Deadlock/game/!")
                return
            if not (self.game_path / "gameinfo.gi").exists():
                QMessageBox.warning(self, "Ошибка", "В выбранной папке нет gameinfo.gi. Выберите .../Deadlock/game/citadel.")
                self.game_path = None
                return
            self.save_config()
            self.update_header_status()

    def save_config(self):
        CONFIG_PATH.write_text(
            json.dumps({"game_path": str(self.game_path) if self.game_path else None}, indent=4),
            encoding="utf-8",
        )

    def load_config(self):
        if CONFIG_PATH.exists():
            try:
                data = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
                if data.get("game_path"):
                    self.game_path = Path(data["game_path"])
            except Exception:
                pass

# ── БОКОВАЯ ПАНЕЛЬ ПРЕДПРОСМОТРА ──────────────────────────────────────

    def _build_preview_panel(self) -> QWidget:
        """Создаёт боковую панель с 3D-просмотром."""
        panel = QWidget()
        panel.setObjectName("previewPanel")
        panel.setFixedWidth(380)
        panel.setStyleSheet("""
            QWidget#previewPanel {
                background-color: #17191e;
                border: 1px solid rgba(255, 255, 255, 0.08);
                border-radius: 14px;
            }
        """)

        layout = QVBoxLayout(panel)
        layout.setContentsMargins(14, 14, 14, 14)
        layout.setSpacing(10)

        # 1. Шапка панели
        header_row = QHBoxLayout()
        header_row.setSpacing(8)

        self.lbl_preview_title = ElidedLabel()
        self.lbl_preview_title.setStyleSheet("font-size: 14px; font-weight: bold; color: #a8c7fa;")
        self.lbl_preview_title.set_full_text("Предпросмотр")
        header_row.addWidget(self.lbl_preview_title, 1)

        btn_close = QPushButton("✕")
        btn_close.setObjectName("iconBtn")
        btn_close.setFixedSize(26, 26)
        btn_close.setCursor(Qt.PointingHandCursor)
        btn_close.clicked.connect(self.hide_preview)
        header_row.addWidget(btn_close)
        layout.addLayout(header_row)

        # 2. Область для 3D: единственный контейнер со стеком
        container = QWidget()
        container.setStyleSheet("background-color: #121316; border-radius: 10px;")
        self.preview_stack = QStackedLayout(container)

        # Виджет 0: Статус (загрузка / ошибка)
        self.lbl_preview_status = QLabel("Загрузка...")
        self.lbl_preview_status.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.lbl_preview_status.setWordWrap(True)
        self.lbl_preview_status.setStyleSheet("color: rgba(255, 255, 255, 0.5); font-size: 13px; padding: 20px;")
        self.preview_stack.addWidget(self.lbl_preview_status)

        layout.addWidget(container, 1)

        # Виджет 1: Браузерный 3D-вьювер (создаётся лениво)
        self.web_view = None

        # Подсказка по управлению снизу
        lbl_hint = QLabel("🖱 ЛКМ: вращение • ПКМ: сдвиг • Колесо: зум")
        lbl_hint.setStyleSheet("font-size: 11px; color: rgba(255, 255, 255, 0.3);")
        lbl_hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(lbl_hint)

        self._active_preview_folder: str | None = None
        self.model_preview_worker: ModelPreviewWorker | None = None
        return panel

    def toggle_preview(self, folder_name: str):
        if self.preview_panel.isVisible() and self._active_preview_folder == folder_name:
            self.hide_preview()
        else:
            self.show_preview(folder_name)

    def show_preview(self, folder_name: str):
        self._active_preview_folder = folder_name
        self.lbl_preview_title.set_full_text(clean_display_name(folder_name))
        self.preview_panel.setVisible(True)

        # Показываем статус загрузки
        self.lbl_preview_status.setText("⏳ Извлечение и подготовка 3D модели…\n(первый раз может занять пару секунд)")
        self.preview_stack.setCurrentIndex(0)

        mod_folder = STAGING_DIR / folder_name

        # Запускаем фоновый экспорт
        self.model_preview_worker = ModelPreviewWorker(folder_name, mod_folder, self.game_path)
        self.model_preview_worker.preview_ready.connect(self._on_model_ready)
        self.model_preview_worker.start()

    def _ensure_web_view(self):
        """Создаёт QWebEngineView при первом показе предпросмотра."""
        if self.web_view is not None:
            return self.web_view

        view = create_model_viewer(self)
        self.web_view = view
        self.preview_stack.addWidget(view)
        return view

    def _on_model_ready(self, folder_name: str, success: bool, glb_path_str: str, error_msg: str):
        # Если пока шла конвертация пользователь уже кликнул на другой мод — игнорируем
        if self._active_preview_folder != folder_name:
            return

        if not success or not glb_path_str:
            self.lbl_preview_status.setText(f"ℹ️ {error_msg}")
            self.preview_stack.setCurrentIndex(0)
            return

        # Модель готова — загружаем в <model-viewer>
        glb_path = Path(glb_path_str)

        print(f"[DEBUG] Загружаем GLB файл: {glb_path_str} (Размер: {glb_path.stat().st_size} байт)")

        html = model_viewer_html(glb_path)
        view = self._ensure_web_view()
        view.setHtml(html, baseUrl=QUrl.fromLocalFile(str(glb_path.parent) + "/"))
        self.preview_stack.setCurrentWidget(view)

    def hide_preview(self):
        self._active_preview_folder = None
        self.preview_panel.setVisible(False)
        # Очищаем WebView, чтобы освободить видеопамять
        if getattr(self, "web_view", None) is not None:
            self.web_view.setHtml("<html><body style='background:#121316;'></body></html>")