"""Окно-браузер модов GameBanana: поиск, обложки, карточка мода и скачивание."""

import math
import tempfile
import webbrowser
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import (
    QEasingCurve,
    QPropertyAnimation,
    QRectF,
    QSize,
    Qt,
    QThread,
    Signal,
)
from PySide6.QtGui import (
    QColor,
    QFont,
    QIcon,
    QPainter,
    QPainterPath,
    QPixmap,
)
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QDialog,
    QFrame,
    QGraphicsOpacityEffect,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListView,
    QListWidget,
    QListWidgetItem,
    QProgressBar,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from core.archiver import extract_and_stage_archive, stage_vpk_file
from core.constants import HEROES
from core.gamebanana import (
    POPULAR_SCAN_PERPAGE,
    DownloadCancelled,
    DownloadCancelToken,
    GBMod,
    download_file_bytes,
    download_image_bytes,
    fetch_recent_mods_page,
    get_mod,
    get_mod_files,
    search_mods,
)

from .styles import apply_dark_titlebar, load_stylesheet

THUMB_SIZE = QSize(170, 100)
DETAIL_IMAGE_SIZE = QSize(296, 180)
GB_CHARACTER_ALL = "Все персонажи"


def _stop_worker(worker: QThread | None, wait_ms: int = 2000) -> None:
    """Останавливает поток с ограничением ожидания, чтобы выход не зависал."""
    if worker is None or not worker.isRunning():
        return
    stop = getattr(worker, "stop", None)
    if callable(stop):
        stop()
    if not worker.wait(wait_ms):
        worker.terminate()
        worker.wait(500)


class GBSearchWorker(QThread):
    """Поиск модов на GameBanana в отдельном потоке."""

    search_finished = Signal(list)
    search_error = Signal(str)

    def __init__(self, query: str, character: str = ""):
        super().__init__()
        self.query = query
        self.character = character

    def run(self) -> None:
        try:
            mods = search_mods(self.query, self.character)
            self.search_finished.emit(mods)
        except Exception as exc:  # noqa: BLE001 — сообщение об ошибке уходит в UI
            self.search_error.emit(str(exc))


class GBDownloadWorker(QThread):
    """Скачивание последней версии мода с GameBanana в отдельном потоке."""

    download_finished = Signal(str, str, str)
    download_error = Signal(str)
    download_cancelled = Signal()
    progress_changed = Signal(int, int)  # (получено байт, всего байт; 0 — размер неизвестен)

    def __init__(self, mod_id: int, file_id: int | None = None):
        super().__init__()
        self.mod_id = mod_id
        self.file_id = file_id
        self._cancel_token = DownloadCancelToken()

    def cancel(self) -> None:
        """Прерывает скачивание (можно вызывать из GUI-потока)."""
        self._cancel_token.cancel()

    def _on_download_progress(self, received: int, total: int) -> None:
        """Пересылает прогресс скачивания в главное окно."""
        self.progress_changed.emit(received, total)

    def run(self) -> None:
        try:
            mod, gb_file, data = download_file_bytes(
                self.mod_id,
                self.file_id,
                progress_callback=self._on_download_progress,
                cancel_token=self._cancel_token,
            )
        except DownloadCancelled:
            self.download_cancelled.emit()
            return
        except Exception as exc:  # noqa: BLE001 — сообщение об ошибке уходит в UI
            self.download_error.emit(str(exc))
            return

        if self._cancel_token.cancelled:
            self.download_cancelled.emit()
            return

        staged_folder = None
        try:
            ext = Path(gb_file.name).suffix.lower()
            with tempfile.TemporaryDirectory() as tmp_dir:
                tmp = Path(tmp_dir)
                target = tmp / (gb_file.name or f"{mod.name}.vpk")
                target.write_bytes(data)
                if ext == ".vpk":
                    staged_folder = stage_vpk_file(target, mod.name)
                else:
                    count, err = extract_and_stage_archive(target)
                    if err or count == 0:
                        raise RuntimeError(
                            err or f"Не удалось найти VPK внутри архива «{gb_file.name}»"
                        )
        except Exception as exc:  # noqa: BLE001 — сообщение об ошибке уходит в UI
            self.download_error.emit(f"{mod.name}: {exc}")
            return

        staged = str(staged_folder) if staged_folder else ""
        self.download_finished.emit(mod.name, gb_file.name, staged)


class GBImageLoader(QThread):
    """Фоново загружает обложки модов (миниатюры или крупные изображения)."""

    image_loaded = Signal(int, object, bool)

    def __init__(self, tasks: list[tuple[int, str, bool]], parent=None):
        super().__init__(parent)
        self._tasks = list(tasks)
        self._stop_requested = False

    def stop(self) -> None:
        """Просит поток прекратить загрузку после текущего изображения."""
        self._stop_requested = True

    def run(self) -> None:
        for mod_id, url, large in self._tasks:
            if self._stop_requested:
                return
            if not url:
                continue
            try:
                data = download_image_bytes(url)
            except Exception:  # noqa: BLE001, S112 — без обложки поиск всё равно работает
                continue
            if self._stop_requested:
                return
            self.image_loaded.emit(mod_id, data, large)


class GBModInfoWorker(QThread):
    """Догружает статистику и список файлов выбранного мода."""

    info_ready = Signal(object)
    info_error = Signal(str)

    def __init__(self, mod_id: int, parent=None):
        super().__init__(parent)
        self.mod_id = int(mod_id)

    def run(self) -> None:
        try:
            mod = get_mod(self.mod_id)
            files = get_mod_files(self.mod_id)
        except Exception as exc:  # noqa: BLE001 — сообщение об ошибке уходит в UI
            self.info_error.emit(str(exc))
            return
        self.info_ready.emit(
            {
                "mod_id": self.mod_id,
                "downloads": mod.downloads,
                "likes": mod.likes,
                "date": mod.date,
                "files": files,
            }
        )


class GBPageFetchWorker(QThread):
    """Загружает одну страницу Mod/Index и отфильтрованные моды этой недели."""

    page_ready = Signal(list, dict)
    page_error = Signal(str)

    def __init__(self, page: int, parent=None):
        super().__init__(parent=parent)
        self.page = page

    def run(self) -> None:
        try:
            mods, meta = fetch_recent_mods_page(self.page)
            self.page_ready.emit(mods, meta)
        except Exception as exc:  # noqa: BLE001 — сообщение об ошибке уходит в UI
            self.page_error.emit(str(exc))


class GameBananaWindow(QDialog):
    """Окно-браузер модов GameBanana с обложками и карточкой мода."""

    def __init__(self, manager) -> None:
        super().__init__(parent=manager)
        self.setWindowTitle("GameBanana — браузер модов Deadlock")
        self.resize(1000, 680)
        self.setStyleSheet(load_stylesheet())
        self.manager = manager  # DeadlockModManager

        self.gb_mods: dict[int, GBMod] = {}
        self.gb_current_mod: GBMod | None = None
        self._items: dict[int, QListWidgetItem] = {}
        self._thumb_pixmaps: dict[int, QPixmap] = {}
        self._large_pixmaps: dict[int, QPixmap] = {}
        self._image_loader: GBImageLoader | None = None
        self._large_loader: GBImageLoader | None = None
        self._info_worker: GBModInfoWorker | None = None
        self._info_workers: list[GBModInfoWorker] = []
        self._placeholder_pixmap = self._make_placeholder_pixmap()

        # Популярные моды + пагинация (ленивая загрузка страниц)
        self._search_mods: list[GBMod] = []
        self._popular_mods: list[GBMod] = []
        self._current_page = 0
        self._page_size = 12
        self._mode = "popular"
        # Состояние ленивой загрузки популярных модов
        self._popular_next_page = 1
        self._popular_total_pages: int | None = None
        self._popular_loading = False
        self._popular_exhausted = False
        self._popular_page_worker: GBPageFetchWorker | None = None

        self._build_widgets()
        self._build_ui()
        self._set_mode("popular")
        self._start_popular_load()

    def showEvent(self, event) -> None:
        super().showEvent(event)
        apply_dark_titlebar(self)

    # ── Построение интерфейса ──────────────────────────────────────────────

    def _make_placeholder_pixmap(self) -> QPixmap:
        """Плейсхолдер в тон темы для модов без обложки."""
        pixmap = QPixmap(THUMB_SIZE)
        pixmap.fill(Qt.transparent)
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.Antialiasing)
        path = QPainterPath()
        path.addRoundedRect(QRectF(0, 0, THUMB_SIZE.width(), THUMB_SIZE.height()), 12, 12)
        painter.setClipPath(path)
        painter.fillPath(path, QColor("#282a2c"))
        painter.setPen(QColor("#5f6368"))
        painter.setFont(QFont("Segoe UI", 10))
        painter.drawText(pixmap.rect(), Qt.AlignmentFlag.AlignCenter, "GameBanana")
        painter.end()
        return pixmap

    @staticmethod
    def _rounded_pixmap(source: QPixmap, size: QSize, radius: int = 12) -> QPixmap:
        """Обрезает изображение по мягкому скруглению (как карточки Gemini)."""
        if source.isNull():
            return source
        scaled = source.scaled(
            size,
            Qt.AspectRatioMode.KeepAspectRatioByExpanding,
            Qt.TransformationMode.SmoothTransformation,
        )
        result = QPixmap(size)
        result.fill(Qt.transparent)
        painter = QPainter(result)
        painter.setRenderHint(QPainter.Antialiasing)
        path = QPainterPath()
        path.addRoundedRect(QRectF(0, 0, size.width(), size.height()), radius, radius)
        painter.setClipPath(path)
        x = (size.width() - scaled.width()) // 2
        y = (size.height() - scaled.height()) // 2
        painter.drawPixmap(x, y, scaled)
        painter.end()
        return result

    def _build_widgets(self) -> None:
        self.gb_search_input = QLineEdit()
        self.gb_search_input.setObjectName("searchField")
        self.gb_search_input.setPlaceholderText("Название мода Deadlock…")

        self.btn_gb_search = QPushButton("🔍 Найти")
        self.btn_gb_search.setObjectName("primaryBtn")
        self.btn_gb_search.setCursor(Qt.PointingHandCursor)

        self.gb_character_combo = QComboBox()
        self.gb_character_combo.setObjectName("characterFilter")
        self.gb_character_combo.setPlaceholderText("Персонаж…")
        self.gb_character_combo.setCursor(Qt.PointingHandCursor)
        self.gb_character_combo.addItem(GB_CHARACTER_ALL)
        for hero in HEROES:
            self.gb_character_combo.addItem(hero)

        self.gb_status = QLabel("Введите название мода и нажмите «Найти»")
        self.gb_status.setObjectName("statusLabel")
        self.gb_status.setWordWrap(True)

        self.gb_gallery = QListWidget()
        self.gb_gallery.setObjectName("gbGallery")
        self.gb_gallery.setViewMode(QListView.ViewMode.IconMode)
        self.gb_gallery.setIconSize(THUMB_SIZE)
        self.gb_gallery.setGridSize(QSize(196, 176))
        self.gb_gallery.setResizeMode(QListView.ResizeMode.Adjust)
        self.gb_gallery.setMovement(QListView.Movement.Static)
        self.gb_gallery.setWordWrap(True)
        self.gb_gallery.setSpacing(6)
        self.gb_gallery.setUniformItemSizes(True)
        self.gb_gallery.setVerticalScrollMode(QAbstractItemView.ScrollPerPixel)
        self.gb_gallery.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)

        self._gallery_effect = QGraphicsOpacityEffect(self.gb_gallery)
        self._gallery_effect.setOpacity(1.0)
        self.gb_gallery.setGraphicsEffect(self._gallery_effect)
        self._gallery_fade = QPropertyAnimation(self._gallery_effect, b"opacity", self)
        self._gallery_fade.setDuration(180)
        self._gallery_fade.setEasingCurve(QEasingCurve.OutCubic)

        self.btn_gb_close = QPushButton("✕ Закрыть")
        self.btn_gb_close.setObjectName("ghostBtn")
        self.btn_gb_close.setCursor(Qt.PointingHandCursor)

        # Пагинация (популярные моды и поиск) + кнопка «назад»
        self.btn_gb_back = QPushButton("← Назад")
        self.btn_gb_back.setObjectName("ghostBtn")
        self.btn_gb_back.setCursor(Qt.PointingHandCursor)
        self.btn_gb_back.setToolTip("Вернуться к популярным модам")

        self.btn_gb_prev = QPushButton("◀")
        self.btn_gb_prev.setObjectName("iconBtn")
        self.btn_gb_prev.setCursor(Qt.PointingHandCursor)
        self.btn_gb_prev.setToolTip("Предыдущая страница")
        self.btn_gb_next = QPushButton("▶")
        self.btn_gb_next.setObjectName("iconBtn")
        self.btn_gb_next.setCursor(Qt.PointingHandCursor)
        self.btn_gb_next.setToolTip("Следующая страница")
        self.gb_page_label = QLabel("")
        self.gb_page_label.setObjectName("mutedLabel")

        self.detail_panel = self._build_details_panel()

    def _build_details_panel(self) -> QFrame:
        panel = QFrame()
        panel.setObjectName("gbDetails")
        panel.setMinimumWidth(306)
        panel.setMaximumWidth(352)

        layout = QVBoxLayout(panel)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(10)

        self.detail_image = QLabel()
        self.detail_image.setObjectName("detailImage")
        self.detail_image.setFixedSize(DETAIL_IMAGE_SIZE)
        self.detail_image.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.detail_image.setText("Выберите мод")
        layout.addWidget(self.detail_image, 0, Qt.AlignmentFlag.AlignHCenter)

        self.detail_name = QLabel("Мод не выбран")
        self.detail_name.setObjectName("detailName")
        self.detail_name.setWordWrap(True)
        layout.addWidget(self.detail_name)

        self.detail_meta = QLabel("")
        self.detail_meta.setObjectName("detailMeta")
        self.detail_meta.setWordWrap(True)
        layout.addWidget(self.detail_meta)

        self.detail_stats = QLabel("")
        self.detail_stats.setObjectName("detailStats")
        layout.addWidget(self.detail_stats)

        self.detail_file = QLabel("")
        self.detail_file.setObjectName("detailFile")
        self.detail_file.setWordWrap(True)
        layout.addWidget(self.detail_file)

        layout.addStretch(1)

        self.btn_gb_download = QPushButton("⬇ Скачать последнюю версию")
        self.btn_gb_download.setObjectName("primaryBtn")
        self.btn_gb_download.setCursor(Qt.PointingHandCursor)
        self.btn_gb_download.setEnabled(False)
        layout.addWidget(self.btn_gb_download)

        self.gb_download_progress = QProgressBar()
        self.gb_download_progress.setObjectName("gbDownloadProgress")
        self.gb_download_progress.setFixedHeight(6)
        self.gb_download_progress.setTextVisible(False)
        self.gb_download_progress.setRange(0, 0)
        self.gb_download_progress.setVisible(False)
        layout.addWidget(self.gb_download_progress)

        self.btn_gb_cancel_download = QPushButton("✕ Прервать скачивание")
        self.btn_gb_cancel_download.setObjectName("cancelBtn")
        self.btn_gb_cancel_download.setCursor(Qt.PointingHandCursor)
        self.btn_gb_cancel_download.setVisible(False)
        layout.addWidget(self.btn_gb_cancel_download)

        self.btn_gb_open = QPushButton("🌐 Открыть на GameBanana")
        self.btn_gb_open.setObjectName("ghostBtn")
        self.btn_gb_open.setCursor(Qt.PointingHandCursor)
        self.btn_gb_open.setEnabled(False)
        layout.addWidget(self.btn_gb_open)

        return panel

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(22, 18, 22, 16)
        layout.setSpacing(14)

        title_row = QHBoxLayout()
        title_box = QVBoxLayout()
        title_box.setSpacing(3)
        title = QLabel('GameBanana <span style="color:#a8c7fa;">моды</span>')
        title.setObjectName("titleLabel")
        title_box.addWidget(title)
        subtitle = QLabel("Популярные моды этой недели и поиск по каталогу Deadlock")
        subtitle.setObjectName("subtitle")
        title_box.addWidget(subtitle)
        title_row.addLayout(title_box)
        title_row.addStretch(1)
        title_row.addWidget(self.btn_gb_close)
        layout.addLayout(title_row)

        search_row = QHBoxLayout()
        search_row.setSpacing(10)
        self.gb_search_input.setFixedHeight(46)
        self.gb_character_combo.setFixedHeight(46)
        self.btn_gb_search.setFixedHeight(46)
        search_row.addWidget(self.gb_search_input, 1)
        search_row.addWidget(self.gb_character_combo)
        search_row.addWidget(self.btn_gb_search)
        layout.addLayout(search_row)

        layout.addWidget(self.gb_status)

        content = QHBoxLayout()
        content.setSpacing(16)
        content.addWidget(self.gb_gallery, 1)
        content.addWidget(self.detail_panel)
        layout.addLayout(content, 1)

        self.gb_pag_container = QWidget()
        self.gb_pag_container.setObjectName("transparentPane")
        self.gb_pag_layout = QHBoxLayout(self.gb_pag_container)
        self.gb_pag_layout.setContentsMargins(0, 0, 0, 0)
        self.gb_pag_layout.setSpacing(8)
        self.gb_pag_layout.addWidget(self.btn_gb_back)
        self.gb_pag_layout.addStretch(1)
        self.gb_pag_layout.addWidget(self.btn_gb_prev)
        self.gb_pag_layout.addWidget(self.gb_page_label)
        self.gb_pag_layout.addWidget(self.btn_gb_next)
        layout.addWidget(self.gb_pag_container)

        self.btn_gb_search.clicked.connect(self._on_search)
        self.gb_search_input.returnPressed.connect(self._on_search)
        self.gb_gallery.itemClicked.connect(self._on_item_clicked)
        self.gb_gallery.itemDoubleClicked.connect(self._on_item_double_clicked)
        self.btn_gb_download.clicked.connect(self.start_download)
        self.btn_gb_cancel_download.clicked.connect(self._cancel_download)
        self.btn_gb_open.clicked.connect(self._open_profile)
        self.btn_gb_close.clicked.connect(self.close)
        self.btn_gb_prev.clicked.connect(self._prev_page)
        self.btn_gb_next.clicked.connect(self._next_page)
        self.btn_gb_back.clicked.connect(self._on_back_to_popular)


    # ── Вызывается из главного окна ────────────────────────────────────────

    def set_searching(self) -> None:
        """Показывает, что поиск выполняется."""
        self.btn_gb_search.setEnabled(False)
        self.gb_status.setText("⏳ Идёт поиск на GameBanana…")

    def show_busy(self, message: str) -> None:
        """Показывает служебное сообщение (например, «операция уже идёт»)."""
        self.gb_status.setText(message)

    def show_error(self, message: str) -> None:
        """Показывает ошибку поиска или скачивания прямо в окне."""
        self.gb_status.setText(f"❌ {message}")
        self.btn_gb_search.setEnabled(True)
        self.btn_gb_download.setEnabled(
            self.gb_current_mod is not None
            and not self.manager._worker_running("gb_download_worker")
        )

    def set_downloading(self) -> None:
        """Показывает, что идёт скачивание выбранного мода."""
        self.btn_gb_download.setEnabled(False)
        self._set_download_active(True)
        if self.gb_current_mod is not None:
            self.gb_status.setText(f"⬇ Скачивание «{self.gb_current_mod.name}»…")

    def _set_download_active(self, active: bool) -> None:
        """Показывает/скрывает прогресс и кнопку прерывания скачивания."""
        self.gb_download_progress.setVisible(active)
        self.btn_gb_cancel_download.setVisible(active)
        self.btn_gb_cancel_download.setEnabled(active)
        if active:
            self.gb_download_progress.setRange(0, 0)
            self.gb_download_progress.setValue(0)

    def _cancel_download(self) -> None:
        """Просит главное окно прервать текущее скачивание."""
        cancel = getattr(self.manager, "cancel_gb_download", None)
        if callable(cancel) and cancel():
            self.btn_gb_cancel_download.setEnabled(False)
            self.gb_status.setText("⏹ Прерываем скачивание…")

    def on_download_progress(self, received: int, total: int) -> None:
        """Обновляет прогресс-бар в карточке мода (total == 0 — размер неизвестен)."""
        if total > 0:
            self.gb_download_progress.setRange(0, total)
            self.gb_download_progress.setValue(min(received, total))
        else:
            self.gb_download_progress.setRange(0, 0)

    def on_download_finished(self, mod_name: str, file_name: str) -> None:
        """Сообщает об успешном скачивании и добавлении мода в библиотеку."""
        self._set_download_active(False)
        self.btn_gb_download.setEnabled(self.gb_current_mod is not None)
        self.gb_status.setText(
            f"✅ «{mod_name}» скачан и добавлен в библиотеку ({file_name})"
        )

    def on_download_error(self, message: str) -> None:
        """Сообщает об ошибке скачивания мода."""
        self._set_download_active(False)
        self.btn_gb_download.setEnabled(self.gb_current_mod is not None)
        self.gb_status.setText(f"❌ Не удалось скачать мод: {message}")

    def on_download_cancelled(self) -> None:
        """Сообщает, что скачивание прервано пользователем."""
        self._set_download_active(False)
        self.btn_gb_download.setEnabled(self.gb_current_mod is not None)
        self.gb_status.setText("⏹ Скачивание прервано")

    def set_results(self, mods: list[GBMod]) -> None:
        """Заполняет браузер результатами поиска (с пагинацией)."""
        self._set_mode("search")
        self._stop_image_loader()
        self._stop_large_loader()
        self.gb_mods = {mod.mod_id: mod for mod in mods}
        self._items.clear()
        self._search_mods = list(mods)
        self._current_page = 0
        self.btn_gb_search.setEnabled(True)
        self._render_page()

    # ── Популярные моды + пагинация (ленивая загрузка страниц) ─────────────

    def _start_popular_load(self) -> None:
        """Запускает ленивую загрузку популярных модов с первой страницы."""
        self._current_page = 0
        self._popular_mods.clear()
        self._popular_next_page = 1
        self._popular_total_pages = None
        self._popular_exhausted = False
        self.gb_status.setText("⏳ Подбираем популярные моды этой недели…")
        self.btn_gb_prev.setEnabled(False)
        self.btn_gb_next.setEnabled(True)
        self.gb_page_label.setText("")
        self._fetch_next_popular_page()

    def _fetch_next_popular_page(self) -> None:
        """Загружает следующую страницу Mod/Index в отдельном потоке."""
        if self._popular_loading or self._popular_exhausted:
            return
        if (
            self._popular_total_pages is not None
            and self._popular_next_page > self._popular_total_pages
        ):
            self._popular_exhausted = True
            return

        self._popular_loading = True
        self.gb_status.setText("⏳ Загрузка популярных модов…")
        worker = GBPageFetchWorker(self._popular_next_page, self)
        self._popular_page_worker = worker
        worker.page_ready.connect(self._on_popular_page_loaded)
        worker.page_error.connect(self._on_popular_page_error)
        worker.start()

    def _on_popular_page_loaded(self, mods: list[GBMod], meta: dict) -> None:
        """Обработка загруженной страницы Mod/Index."""
        self._popular_loading = False
        if self._popular_total_pages is None and isinstance(meta, dict):
            rc = meta.get("_nRecordCount")
            if isinstance(rc, int) and rc > 0:
                self._popular_total_pages = max(1, math.ceil(rc / POPULAR_SCAN_PERPAGE))
        if mods:
            self._popular_mods.extend(mods)
            self.gb_mods = {mod.mod_id: mod for mod in self._popular_mods}
        self._popular_next_page += 1
        if (
            self._popular_total_pages is not None
            and self._popular_next_page > self._popular_total_pages
        ):
            self._popular_exhausted = True
        self._render_page()

    def _on_popular_page_error(self, message: str) -> None:
        """Ошибка загрузки страницы Mod/Index."""
        self._popular_loading = False
        self.gb_status.setText(f"❌ Не удалось загрузить страницу: {message}")

    def _fill_gallery(self, page_mods: list[GBMod]) -> None:
        """Заполняет галерею обложками текущей страницы."""
        self._stop_image_loader()
        self.gb_gallery.clear()
        for mod in page_mods:
            item = QListWidgetItem(QIcon(self._placeholder_pixmap), mod.name)
            item.setData(Qt.ItemDataRole.UserRole, mod.mod_id)
            item.setToolTip(f"{mod.name}\n{mod.author or 'автор неизвестен'}")
            self.gb_gallery.addItem(item)
            self._items[mod.mod_id] = item

        if page_mods:
            self.gb_gallery.setCurrentRow(0)
            self._show_details(page_mods[0])
            self._start_image_loader(
                [(mod.mod_id, mod.image_url, False) for mod in page_mods if mod.image_url]
            )
            self._fade_in_gallery()

    def _fade_in_gallery(self) -> None:
        """Мягкое появление страницы (как при переключении разделов в Gemini)."""
        effect = getattr(self, "_gallery_effect", None)
        if effect is None:
            return
        self._gallery_fade.stop()
        self._gallery_fade.setStartValue(0.35)
        self._gallery_fade.setEndValue(1.0)
        self._gallery_fade.start()


    def _render_page(self) -> None:
        """Рисует текущую страницу в зависимости от режима (популярные / поиск)."""
        if self._mode == "search":
            total = len(self._search_mods)
            pages = max(1, (total + self._page_size - 1) // self._page_size)
            self._current_page = min(max(self._current_page, 0), pages - 1)
            start = self._current_page * self._page_size
            end = min(start + self._page_size, total)
            page_mods = self._search_mods[start:end]

            if pages > 1:
                label = f"Страница {self._current_page + 1} из {pages}"
            else:
                label = "Поиск"
            prev_enabled = self._current_page > 0
            next_enabled = self._current_page < pages - 1

            if total == 0:
                status_text = "Ничего не найдено. Попробуйте другое название."
            else:
                status_text = (
                    f"Найдено модов: {total} — нажмите на обложку, чтобы открыть карточку"
                )

        else:  # popular
            total_loaded = len(self._popular_mods)
            start = self._current_page * self._page_size
            end = min(start + self._page_size, total_loaded)
            page_mods = self._popular_mods[start:end]

            if self._popular_exhausted:
                total_known = max(1, (total_loaded + self._page_size - 1) // self._page_size)
                label = f"Страница {self._current_page + 1} из {total_known}"
                next_enabled = self._current_page < total_known - 1
            else:
                label = f"Страница {self._current_page + 1}"
                next_enabled = True
            prev_enabled = self._current_page > 0

            if self._popular_loading:
                status_text = "⏳ Загрузка популярных модов…"
            elif total_loaded == 0:
                status_text = "Пока нет популярных модов за эту неделю."
            else:
                status_text = f"Популярные моды этой недели — найдено {total_loaded}"

        self.gb_page_label.setText(label)
        self.btn_gb_prev.setEnabled(prev_enabled)
        self.btn_gb_next.setEnabled(next_enabled)
        self.gb_status.setText(status_text)

        # Синхронизируем словарь модов с текущим режимом, чтобы клик по обложке
        # всегда находил нужный мод (в т.ч. при возврате из поиска к популярным).
        if self._mode == "search":
            self.gb_mods = {m.mod_id: m for m in self._search_mods}
        else:
            self.gb_mods = {m.mod_id: m for m in self._popular_mods}

        self._fill_gallery(page_mods)

        # Если текущей страницы не хватает и можно загрузить ещё — подтягиваем.
        if self._mode == "popular":
            need_more = len(self._popular_mods) < (self._current_page + 1) * self._page_size
            if need_more and not self._popular_exhausted and not self._popular_loading:
                self.gb_status.setText("⏳ Загрузка популярных модов…")
                self._fetch_next_popular_page()

    def _prev_page(self) -> None:
        if self._current_page > 0:
            self._current_page -= 1
            self._render_page()

    def _next_page(self) -> None:
        if self._mode == "search":
            total = len(self._search_mods)
            pages = max(1, (total + self._page_size - 1) // self._page_size)
            if self._current_page >= pages - 1:
                return
        else:  # popular
            if self._popular_exhausted:
                total_loaded_pages = max(
                    1, (len(self._popular_mods) + self._page_size - 1) // self._page_size
                )
                if self._current_page >= total_loaded_pages - 1:
                    return
            else:
                full_pages = len(self._popular_mods) // self._page_size
                if self._current_page >= full_pages:
                    return

        self._current_page += 1
        self._render_page()

    def _set_mode(self, mode: str) -> None:
        """Переключает режим: популярные моды или результаты поиска."""
        self._mode = mode
        if mode == "popular":
            self.btn_gb_back.setVisible(False)
        else:
            self.btn_gb_back.setVisible(True)
        self.gb_pag_container.setVisible(True)

    def _on_back_to_popular(self) -> None:
        """Возврат из результатов поиска к популярным модам."""
        self._set_mode("popular")
        self._current_page = 0
        self._render_page()

    # ── Карточка мода ──────────────────────────────────────────────────────

    def _on_item_clicked(self, item: QListWidgetItem) -> None:
        mod_id = item.data(Qt.ItemDataRole.UserRole)
        if mod_id is None:
            return
        mod = self.gb_mods.get(int(mod_id))
        if mod is not None:
            self._show_details(mod)

    def _on_item_double_clicked(self, item: QListWidgetItem) -> None:
        self._on_item_clicked(item)
        self.start_download()

    def _show_details(self, mod: GBMod) -> None:
        """Показывает карточку мода: обложку, название, автора и файл."""
        self.gb_current_mod = mod
        self.detail_name.setText(mod.name)

        meta: list[str] = []
        if mod.author:
            meta.append(f"👤 {mod.author}")
        if mod.category:
            meta.append(f"🗂 {mod.category}")
        if mod.date:
            meta.append(f"📅 {self._format_date(mod.date)}")
        self.detail_meta.setText("   ·   ".join(meta) or "Информация загружается…")

        self.detail_stats.setText(self._format_stats(mod))
        self.detail_file.setText("📦 Файлы уточняются…")
        self.btn_gb_download.setEnabled(
            not self.manager._worker_running("gb_download_worker")
        )
        self.btn_gb_open.setEnabled(bool(mod.profile_url))

        pixmap = self._large_pixmaps.get(mod.mod_id) or self._thumb_pixmaps.get(mod.mod_id)
        if pixmap is not None:
            self._set_detail_pixmap(pixmap)
        else:
            self.detail_image.clear()
            self.detail_image.setText("Загрузка изображения…")

        if mod.image_large_url and mod.mod_id not in self._large_pixmaps:
            self._load_large_image(mod)

        self._load_mod_info(mod)

    def _set_detail_pixmap(self, pixmap: QPixmap) -> None:
        if pixmap.isNull():
            return
        self.detail_image.setPixmap(
            self._rounded_pixmap(pixmap, DETAIL_IMAGE_SIZE, 14)
        )

    @staticmethod
    def _format_stats(mod: GBMod) -> str:
        parts: list[str] = []
        if mod.downloads:
            parts.append(f"⬇ {mod.downloads:,}".replace(",", " "))
        if mod.likes:
            parts.append(f"❤ {mod.likes}")
        if mod.views:
            parts.append(f"👁 {mod.views:,}".replace(",", " "))
        return "   ·   ".join(parts)

    @staticmethod
    def _format_date(timestamp: int) -> str:
        try:
            return datetime.fromtimestamp(int(timestamp)).strftime("%d.%m.%Y")
        except (OverflowError, OSError, ValueError):
            return ""

    def _load_large_image(self, mod: GBMod) -> None:
        self._stop_large_loader()
        loader = GBImageLoader([(mod.mod_id, mod.image_large_url, True)], self)
        self._large_loader = loader
        loader.image_loaded.connect(
            lambda mod_id, data, large, ldr=loader: self._on_image_loaded(
                mod_id, data, large, ldr
            )
        )
        loader.start()

    def _load_mod_info(self, mod: GBMod) -> None:
        worker = self._info_worker
        if worker is not None and worker.isRunning() and worker.mod_id == mod.mod_id:
            return
        worker = GBModInfoWorker(mod.mod_id, self)
        self._info_worker = worker
        self._info_workers.append(worker)
        worker.finished.connect(lambda w=worker: self._on_info_worker_finished(w))
        worker.info_ready.connect(self._on_mod_info_ready)
        worker.info_error.connect(self._on_mod_info_error)
        worker.start()

    def _on_info_worker_finished(self, worker: GBModInfoWorker) -> None:
        if worker in self._info_workers:
            self._info_workers.remove(worker)

    def _on_mod_info_ready(self, info: object) -> None:
        if not isinstance(info, dict):
            return
        mod = self.gb_current_mod
        if mod is None or mod.mod_id != int(info.get("mod_id", -1)):
            return

        mod.downloads = int(info.get("downloads") or 0)
        mod.likes = int(info.get("likes") or 0) or mod.likes
        if info.get("date"):
            mod.date = int(info["date"])
        mod.files = list(info.get("files") or [])

        self.detail_stats.setText(self._format_stats(mod))

        latest = mod.latest_file
        if latest is None:
            self.detail_file.setText("⚠ У мода нет файлов для скачивания")
            self.btn_gb_download.setEnabled(False)
            return

        size = (
            f"{latest.size / (1024 * 1024):.1f} МБ" if latest.size else "размер неизвестен"
        )
        version = f" · v{latest.version}" if latest.version else ""
        self.detail_file.setText(f"📦 {latest.name} · {size}{version}")
        self.btn_gb_download.setEnabled(
            not self.manager._worker_running("gb_download_worker")
        )

    def _on_mod_info_error(self, message: str) -> None:
        if self.gb_current_mod is None:
            return
        self.detail_file.setText(f"⚠ Не удалось получить файлы: {message}")

    # ── Загрузка обложек ───────────────────────────────────────────────────

    def _start_image_loader(self, tasks: list[tuple[int, str, bool]]) -> None:
        if not tasks:
            return
        loader = GBImageLoader(tasks, self)
        self._image_loader = loader
        loader.image_loaded.connect(
            lambda mod_id, data, large, ldr=loader: self._on_image_loaded(
                mod_id, data, large, ldr
            )
        )
        loader.start()

    def _stop_image_loader(self, wait_forever: bool = False) -> None:
        loader = self._image_loader
        self._image_loader = None
        if loader is not None:
            loader.stop()
            if loader.isRunning():
                if wait_forever:
                    loader.wait()
                elif not loader.wait(1500):
                    loader.terminate()
                    loader.wait(500)

    def _stop_large_loader(self, wait_forever: bool = False) -> None:
        loader = self._large_loader
        self._large_loader = None
        if loader is not None:
            loader.stop()
            if loader.isRunning():
                if wait_forever:
                    loader.wait()
                elif not loader.wait(1500):
                    loader.terminate()
                    loader.wait(500)

    def _on_image_loaded(
        self,
        mod_id: int,
        data: bytes,
        large: bool,
        loader: GBImageLoader | None = None,
    ) -> None:
        if loader is not self._image_loader and loader is not self._large_loader:
            return

        pixmap = QPixmap()
        if not pixmap.loadFromData(data) or pixmap.isNull():
            return

        if large:
            self._large_pixmaps[mod_id] = pixmap
            if self.gb_current_mod is not None and self.gb_current_mod.mod_id == mod_id:
                self._set_detail_pixmap(pixmap)
            return

        self._thumb_pixmaps[mod_id] = pixmap
        item = self._items.get(mod_id)
        if item is not None:
            item.setIcon(QIcon(self._rounded_pixmap(pixmap, THUMB_SIZE, 12)))
        if self.gb_current_mod is not None and self.gb_current_mod.mod_id == mod_id:
            self._set_detail_pixmap(pixmap)

    # ── Действия пользователя ──────────────────────────────────────────────

    def _on_search(self) -> None:
        query = self.gb_search_input.text().strip()
        character = self.gb_character_combo.currentText()
        if character == GB_CHARACTER_ALL:
            character = ""
        if not query and not character:
            self.gb_status.setText("Введите название мода или выберите персонажа")
            return
        self.manager.start_gb_search(query, character)

    def start_download(self) -> None:
        """Скачивает последнюю версию выбранного мода."""
        mod = self.gb_current_mod
        if mod is None:
            self.gb_status.setText("Сначала выберите мод в браузере")
            return
        if self.manager.start_gb_download(mod.mod_id):
            self.btn_gb_download.setEnabled(False)
            self.gb_status.setText(f"⬇ Скачивание «{mod.name}»…")

    def _open_profile(self) -> None:
        mod = self.gb_current_mod
        if mod is None or not mod.profile_url:
            return
        webbrowser.open(mod.profile_url)

    # ── Служебное ──────────────────────────────────────────────────────────

    def _reset_details(self) -> None:
        self.gb_current_mod = None
        self.detail_image.clear()
        self.detail_image.setText("Выберите мод")
        self.detail_name.setText("Мод не выбран")
        self.detail_meta.setText("")
        self.detail_stats.setText("")
        self.detail_file.setText("")
        self.btn_gb_download.setEnabled(False)
        self.btn_gb_open.setEnabled(False)

    def shutdown(self) -> None:
        """Останавливает все фоновые потоки окна (вызывается при выходе из приложения)."""
        self._stop_image_loader()
        self._stop_large_loader()
        for worker in list(self._info_workers):
            _stop_worker(worker)
        self._info_workers.clear()
        _stop_worker(self._popular_page_worker)
        self._popular_page_worker = None

    def closeEvent(self, event) -> None:
        self._stop_image_loader()
        self._stop_large_loader()
        if self._popular_page_worker is not None and self._popular_page_worker.isRunning():
            self._popular_page_worker.wait(2000)
        super().closeEvent(event)
