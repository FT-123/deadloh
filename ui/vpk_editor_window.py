"""Окно редактора VPK: просмотр ресурсов, замена файлов и сборка новой копии.

Окно не трогает исходный архив: все замены пишутся во временную рабочую
копию, а сборка создаёт новый ``.vpk`` рядом с выбранным местом.
Долгие операции (индексация, замена, сборка, превью) выполняются в ``QThread``.
"""

import shutil
import tempfile
from pathlib import Path

from PySide6.QtCore import Qt, QThread, QTimer, QUrl, Signal
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSplitter,
    QStackedWidget,
    QVBoxLayout,
)

from core.paths import STAGING_DIR
from core.vpk_editor import (
    BuildResult,
    ReplacementResult,
    ResourceInfo,
    VPKEditor,
    VPKProject,
)
from core.vpk_preview import PreviewOutcome, build_preview, preview_kind

from .styles import apply_dark_titlebar, load_stylesheet

MAX_LISTED_RESOURCES = 4000
PREVIEW_DELAY_MS = 250


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


def _format_size(size: int) -> str:
    """Человекочитаемый размер файла."""
    value = float(max(size, 0))
    for unit in ("Б", "КБ", "МБ", "ГБ"):
        if value < 1024 or unit == "ГБ":
            return f"{value:.0f} {unit}" if unit == "Б" else f"{value:.1f} {unit}"
        value /= 1024
    return f"{value:.1f} ГБ"


class PreviewImageLabel(QLabel):
    """QLabel, который масштабирует картинку под себя без искажений."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._source: QPixmap | None = None
        self.setObjectName("previewImage")
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setMinimumHeight(150)

    def set_source(self, pixmap: QPixmap) -> None:
        """Запоминает исходную картинку и сразу вписывает её в виджет."""
        self._source = pixmap
        self._rescale()

    def clear_source(self) -> None:
        """Сбрасывает картинку (например, при закрытии проекта)."""
        self._source = None
        self.clear()

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._rescale()

    def _rescale(self) -> None:
        if self._source is None or self._source.isNull():
            return
        self.setPixmap(
            self._source.scaled(
                self.contentsRect().size(),
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
        )


class OpenVPKWorker(QThread):
    """Открывает VPK и строит индекс ресурсов в фоне."""

    opened = Signal(object)
    failed = Signal(str)

    def __init__(self, editor: VPKEditor, path: str | Path, parent=None):
        super().__init__(parent)
        self._editor = editor
        self._path = str(path)

    def run(self) -> None:
        try:
            project = self._editor.open_vpk(self._path)
            try:
                resources = project.list_resources()
            except Exception:
                project.close()
                raise
        except Exception as exc:  # noqa: BLE001 — сообщение об ошибке уходит в UI
            self.failed.emit(str(exc))
            return
        self.opened.emit({"project": project, "resources": resources})


class ReplaceWorker(QThread):
    """Заменяет один ресурс в рабочей копии проекта."""

    replaced = Signal(object)
    failed = Signal(str)

    def __init__(
        self,
        project: VPKProject,
        target: str,
        source: str | Path,
        parent=None,
    ):
        super().__init__(parent)
        self._project = project
        self._target = target
        self._source = Path(source)

    def run(self) -> None:
        try:
            if self._target.lower().endswith(".vsnd_c"):
                result = self._project.replace_sound(self._target, self._source)
            else:
                result = self._project.replace_resource(self._target, self._source)
        except Exception as exc:  # noqa: BLE001 — сообщение об ошибке уходит в UI
            self.failed.emit(str(exc))
            return
        self.replaced.emit(result)


class BuildWorker(QThread):
    """Собирает новый VPK из рабочей копии проекта."""

    built = Signal(object)
    failed = Signal(str)

    def __init__(
        self,
        project: VPKProject,
        output: str | Path,
        overwrite: bool,
        parent=None,
    ):
        super().__init__(parent)
        self._project = project
        self._output = Path(output)
        self._overwrite = overwrite

    def run(self) -> None:
        try:
            result = self._project.build(self._output, overwrite=self._overwrite)
        except Exception as exc:  # noqa: BLE001 — сообщение об ошибке уходит в UI
            self.failed.emit(str(exc))
            return
        self.built.emit(result)


class PreviewWorker(QThread):
    """Готовит превью ресурса в фоне: текстура, модель или текст."""

    preview_ready = Signal(int, object)  # seq, PreviewOutcome

    def __init__(
        self,
        seq: int,
        vpk_path: str | Path,
        resource_path: str,
        cache_dir: str | Path,
        *,
        workspace_dir: str | Path | None = None,
        game_path: str | Path | None = None,
        parent=None,
    ):
        super().__init__(parent)
        self._seq = int(seq)
        self._vpk_path = Path(vpk_path)
        self._resource_path = resource_path
        self._cache_dir = Path(cache_dir)
        self._workspace_dir = Path(workspace_dir) if workspace_dir else None
        self._game_path = Path(game_path) if game_path else None

    def run(self) -> None:
        try:
            outcome = build_preview(
                self._vpk_path,
                self._resource_path,
                self._cache_dir,
                workspace_dir=self._workspace_dir,
                game_path=self._game_path,
            )
        except Exception as exc:  # noqa: BLE001 — сообщение об ошибке уходит в UI
            outcome = PreviewOutcome(
                "none", message=f"Не удалось подготовить превью: {exc}"
            )
        self.preview_ready.emit(self._seq, outcome)


class VPKEditorWindow(QDialog):
    """Отдельное окно редактора VPK."""

    def __init__(self, manager) -> None:
        super().__init__(parent=manager)
        self.setWindowTitle("VPK — редактор модов Deadlock")
        self.resize(1060, 700)
        self.setStyleSheet(load_stylesheet())
        self.manager = manager  # DeadlockModManager

        self._editor = VPKEditor()
        self._project: VPKProject | None = None
        self._resources: list[ResourceInfo] = []
        self._by_path: dict[str, ResourceInfo] = {}
        self._modifications: dict[str, ReplacementResult] = {}
        self._busy = False
        self._last_dir = str(STAGING_DIR)

        self._open_worker: OpenVPKWorker | None = None
        self._replace_worker: ReplaceWorker | None = None
        self._build_worker: BuildWorker | None = None

        # Превью ресурсов: отдельный кеш на окно + один фоновый поток.
        self._preview_cache = Path(tempfile.mkdtemp(prefix="dmm-vpk-preview-"))
        self._preview_worker: PreviewWorker | None = None
        self._preview_seq = 0
        self._preview_pending = False
        self._preview_expanded = False
        self._splitter_sizes: list[int] | None = None
        self._model_view = None
        self._preview_timer = QTimer(self)
        self._preview_timer.setSingleShot(True)
        self._preview_timer.setInterval(PREVIEW_DELAY_MS)
        self._preview_timer.timeout.connect(self._start_preview)

        self._build_widgets()
        self._build_ui()
        self._refresh_actions()

    def showEvent(self, event) -> None:
        super().showEvent(event)
        apply_dark_titlebar(self)

    # ── Построение интерфейса ──────────────────────────────────────────────

    def _build_widgets(self) -> None:
        self.btn_open = QPushButton("📂 Открыть VPK…")
        self.btn_open.setObjectName("primaryBtn")
        self.btn_open.setCursor(Qt.PointingHandCursor)
        self.btn_open.setToolTip("Выбрать архив .vpk для просмотра и правки")

        self.btn_build = QPushButton("💾 Собрать VPK…")
        self.btn_build.setObjectName("ghostBtn")
        self.btn_build.setCursor(Qt.PointingHandCursor)
        self.btn_build.setToolTip(
            "Собрать новую копию архива с заменами. Исходный файл не изменяется."
        )

        self.btn_close = QPushButton("✕ Закрыть")
        self.btn_close.setObjectName("ghostBtn")
        self.btn_close.setCursor(Qt.PointingHandCursor)

        self.file_label = QLabel("Архив не открыт")
        self.file_label.setObjectName("mutedLabel")
        self.file_label.setTextInteractionFlags(Qt.TextSelectableByMouse)

        self.search_input = QLineEdit()
        self.search_input.setObjectName("searchField")
        self.search_input.setPlaceholderText("Путь ресурса внутри VPK…")
        self.search_input.setFixedHeight(44)
        self.search_input.setClearButtonEnabled(True)

        self.btn_sounds = QPushButton("🎵 Только звуки")
        self.btn_sounds.setObjectName("filterChip")
        self.btn_sounds.setCheckable(True)
        self.btn_sounds.setCursor(Qt.PointingHandCursor)
        self.btn_sounds.setToolTip("Показать только .vsnd_c и файлы из sounds/")

        self.count_label = QLabel("")
        self.count_label.setObjectName("mutedLabel")

        self.resource_list = QListWidget()
        self.resource_list.setObjectName("resourceList")
        self.resource_list.setSelectionMode(
            QAbstractItemView.SelectionMode.SingleSelection
        )
        self.resource_list.setVerticalScrollMode(
            QAbstractItemView.ScrollPerPixel
        )
        self.resource_list.setUniformItemSizes(True)

        self.detail_panel = self._build_details_panel()

        self.status_label = QLabel("Откройте .vpk, чтобы увидеть список ресурсов")
        self.status_label.setObjectName("statusLabel")
        self.status_label.setWordWrap(True)

    def _build_details_panel(self) -> QFrame:
        panel = QFrame()
        panel.setObjectName("editorPanel")
        panel.setMinimumWidth(320)

        layout = QVBoxLayout(panel)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(8)

        section = QLabel("РЕСУРС")
        section.setObjectName("sectionLabel")
        layout.addWidget(section)

        self.detail_path = QLabel("Ресурс не выбран")
        self.detail_path.setObjectName("detailName")
        self.detail_path.setWordWrap(True)
        self.detail_path.setTextInteractionFlags(Qt.TextSelectableByMouse)
        layout.addWidget(self.detail_path)

        self.detail_meta = QLabel("")
        self.detail_meta.setObjectName("detailMeta")
        self.detail_meta.setWordWrap(True)
        layout.addWidget(self.detail_meta)

        self.detail_stats = QLabel("")
        self.detail_stats.setObjectName("detailStats")
        self.detail_stats.setWordWrap(True)
        layout.addWidget(self.detail_stats)

        self.detail_replaced = QLabel("")
        self.detail_replaced.setObjectName("detailFile")
        self.detail_replaced.setWordWrap(True)
        layout.addWidget(self.detail_replaced)

        preview_row = QHBoxLayout()
        preview_row.setSpacing(6)
        preview_title = QLabel("ПРЕВЬЮ")
        preview_title.setObjectName("sectionLabel")
        preview_row.addWidget(preview_title)
        preview_row.addStretch(1)
        self.btn_preview_expand = QPushButton("⤢")
        self.btn_preview_expand.setObjectName("iconBtn")
        self.btn_preview_expand.setCursor(Qt.PointingHandCursor)
        self.btn_preview_expand.setFixedSize(28, 24)
        self.btn_preview_expand.setToolTip("Развернуть превью на всё окно")
        preview_row.addWidget(self.btn_preview_expand)
        layout.addLayout(preview_row)

        self.preview_stack = QStackedWidget()
        self.preview_stack.setMinimumHeight(170)

        self.preview_status = QLabel("Выберите ресурс — здесь появится превью.")
        self.preview_status.setObjectName("previewStatus")
        self.preview_status.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.preview_status.setWordWrap(True)

        self.preview_image = PreviewImageLabel()

        self.preview_text = QPlainTextEdit()
        self.preview_text.setObjectName("previewText")
        self.preview_text.setReadOnly(True)
        self.preview_text.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)

        self.preview_stack.addWidget(self.preview_status)
        self.preview_stack.addWidget(self.preview_image)
        self.preview_stack.addWidget(self.preview_text)
        layout.addWidget(self.preview_stack, 1)

        divider = QFrame()
        divider.setObjectName("gbDivider")
        layout.addWidget(divider)

        self.btn_replace = QPushButton("📄 Заменить файл…")
        self.btn_replace.setObjectName("primaryBtn")
        self.btn_replace.setCursor(Qt.PointingHandCursor)
        layout.addWidget(self.btn_replace)

        self.detail_hint = QLabel(
            "Звук меняется только на готовый .vsnd_c: компилятора Source 2 "
            "в приложении нет."
        )
        self.detail_hint.setObjectName("mutedLabel")
        self.detail_hint.setWordWrap(True)
        layout.addWidget(self.detail_hint)

        layout.addSpacing(6)
        changes_title = QLabel("ИЗМЕНЕНИЯ")
        changes_title.setObjectName("sectionLabel")
        layout.addWidget(changes_title)

        self.changes_label = QLabel("Изменений нет")
        self.changes_label.setObjectName("mutedLabel")
        self.changes_label.setWordWrap(True)
        self.changes_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        layout.addWidget(self.changes_label)

        return panel

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(22, 18, 22, 16)
        layout.setSpacing(14)

        title_row = QHBoxLayout()
        title_box = QVBoxLayout()
        title_box.setSpacing(3)
        title = QLabel('VPK <span style="color:#a8c7fa;">редактор</span>')
        title.setObjectName("titleLabel")
        title_box.addWidget(title)
        subtitle = QLabel(
            "Замена ресурсов и звуков внутри архива мода. Исходный VPK не изменяется."
        )
        subtitle.setObjectName("subtitle")
        title_box.addWidget(subtitle)
        title_row.addLayout(title_box)
        title_row.addStretch(1)
        title_row.addWidget(self.btn_close)
        layout.addLayout(title_row)

        open_row = QHBoxLayout()
        open_row.setSpacing(10)
        open_row.addWidget(self.btn_open)
        open_row.addWidget(self.file_label, 1)
        open_row.addWidget(self.btn_build)
        layout.addLayout(open_row)

        filter_row = QHBoxLayout()
        filter_row.setSpacing(10)
        filter_row.addWidget(self.search_input, 1)
        filter_row.addWidget(self.btn_sounds)
        filter_row.addWidget(self.count_label)
        layout.addLayout(filter_row)

        self.content_splitter = QSplitter(Qt.Orientation.Horizontal)
        self.content_splitter.setObjectName("contentSplitter")
        self.content_splitter.setChildrenCollapsible(False)
        self.content_splitter.addWidget(self.resource_list)
        self.content_splitter.addWidget(self.detail_panel)
        self.content_splitter.setStretchFactor(0, 1)
        self.content_splitter.setStretchFactor(1, 0)
        self.content_splitter.setSizes([660, 380])
        layout.addWidget(self.content_splitter, 1)

        layout.addWidget(self.status_label)

        self.btn_open.clicked.connect(lambda: self.choose_and_open())
        self.btn_build.clicked.connect(self.choose_and_build)
        self.btn_close.clicked.connect(self.close)
        self.btn_replace.clicked.connect(self.choose_and_replace)
        self.btn_preview_expand.clicked.connect(self._toggle_preview_expanded)
        self.search_input.textChanged.connect(self._populate_resources)
        self.btn_sounds.toggled.connect(self._populate_resources)
        self.resource_list.itemSelectionChanged.connect(self._on_selection_changed)

    # ── Состояние кнопок ───────────────────────────────────────────────────

    def _selected_resource(self) -> ResourceInfo | None:
        item = self.resource_list.currentItem()
        if item is None:
            return None
        return self._by_path.get(str(item.data(Qt.UserRole)))

    def _refresh_actions(self) -> None:
        """Включает кнопки по наличию проекта, выбора и фоновой операции."""
        has_project = self._project is not None
        has_selection = has_project and self.resource_list.currentItem() is not None
        self.btn_open.setEnabled(not self._busy)
        self.btn_build.setEnabled(
            not self._busy and has_project and bool(self._modifications)
        )
        self.btn_replace.setEnabled(not self._busy and has_selection)
        self.search_input.setEnabled(not self._busy and has_project)
        self.btn_sounds.setEnabled(not self._busy and has_project)
        self.resource_list.setEnabled(not self._busy and has_project)

    def _set_busy(self, busy: bool, message: str = "") -> None:
        self._busy = busy
        if message:
            self.status_label.setText(message)
        self._refresh_actions()

    # ── Открытие архива ────────────────────────────────────────────────────

    def choose_and_open(self, path: str | Path | None = None) -> None:
        """Открывает диалог выбора VPK либо сразу указанный файл."""
        if self._busy:
            return
        if path is None:
            selected, _ = QFileDialog.getOpenFileName(
                self,
                "Открыть VPK",
                self._last_dir,
                "VPK архивы (*.vpk);;Все файлы (*)",
            )
            if not selected:
                return
            path = selected
        self.start_open(Path(path))

    def start_open(self, path: str | Path) -> None:
        """Запускает индексацию архива в фоне."""
        if self._busy:
            return
        source = Path(path)
        self._set_busy(True, f"⏳ Открываю «{source.name}»…")
        worker = OpenVPKWorker(self._editor, source, self)
        self._open_worker = worker
        worker.opened.connect(self._on_opened)
        worker.failed.connect(self._on_failed)
        worker.finished.connect(self._on_open_finished)
        worker.start()

    def _on_opened(self, payload) -> None:
        project: VPKProject = payload["project"]
        self._close_project()
        self._project = project
        self._resources = list(payload["resources"])
        self._by_path = {resource.path: resource for resource in self._resources}
        self._modifications.clear()
        self._refresh_changes()

        info = project.info
        self._last_dir = str(info.path.parent)
        self.file_label.setText(
            f"{info.path.name} · {info.entry_count} ресурсов · VPK v{info.version}"
        )
        self.file_label.setToolTip(str(info.path))
        self._populate_resources()
        self.status_label.setText(
            f"✅ Открыт «{info.path.name}»: выберите ресурс слева для замены"
        )

    def _on_open_finished(self) -> None:
        worker = self._open_worker
        self._open_worker = None
        if worker is not None:
            worker.deleteLater()
        self._set_busy(False)

    def _on_failed(self, message: str) -> None:
        self.status_label.setText(f"❌ {message}")

    # ── Список ресурсов ────────────────────────────────────────────────────

    def _populate_resources(self, *_args) -> None:
        """Перестраивает список ресурсов по поиску и фильтру звуков."""
        query = self.search_input.text().strip().replace("\\", "/").casefold()
        sounds_only = self.btn_sounds.isChecked()

        visible = [
            resource
            for resource in self._resources
            if (not sounds_only or resource.is_sound)
            and (not query or query in resource.path.casefold())
        ]

        self.resource_list.blockSignals(True)
        self.resource_list.clear()
        for resource in visible[:MAX_LISTED_RESOURCES]:
            item = QListWidgetItem(resource.path)
            item.setData(Qt.UserRole, resource.path)
            item.setToolTip(f"{resource.path}\n{_format_size(resource.size)}")
            self.resource_list.addItem(item)
        self.resource_list.blockSignals(False)

        total = len(self._resources)
        if not total:
            self.count_label.setText("")
        elif len(visible) > MAX_LISTED_RESOURCES:
            self.count_label.setText(
                f"{len(visible)} из {total} · показаны первые {MAX_LISTED_RESOURCES}"
            )
        else:
            self.count_label.setText(f"{len(visible)} из {total}")

        self._on_selection_changed()

    def _on_selection_changed(self) -> None:
        resource = self._selected_resource()
        if resource is None:
            self.detail_path.setText("Ресурс не выбран")
            self.detail_meta.setText("")
            self.detail_stats.setText("")
            self.detail_replaced.setText("")
            self.btn_replace.setText("📄 Заменить файл…")
            self.detail_hint.setText(
                "Выберите ресурс в списке, чтобы заменить его содержимое."
            )
            self._schedule_preview(None)
            self._refresh_actions()
            return

        if resource.is_compiled_sound:
            kind = "Скомпилированный звук Source 2"
        elif resource.is_sound:
            kind = "Звуковой файл"
        else:
            kind = "Ресурс"
        self.detail_path.setText(resource.path)
        self.detail_meta.setText(
            f"{kind} · {resource.suffix or 'без расширения'}"
        )
        self.detail_stats.setText(
            f"Размер: {_format_size(resource.size)}   "
            f"CRC32: {resource.crc32:08x}"
        )

        result = self._modifications.get(resource.path)
        self.detail_replaced.setText(
            f"✅ Заменён в рабочей копии ({_format_size(result.size)})"
            if result is not None
            else ""
        )

        if resource.is_compiled_sound:
            self.btn_replace.setText("🎵 Заменить звук…")
            self.btn_replace.setToolTip(
                "Для .vsnd_c подойдёт только готовый скомпилированный файл"
            )
            self.detail_hint.setText(
                "Нужен готовый .vsnd_c: компилятора Source 2 в приложении нет, "
                "поэтому .wav/.mp3/.ogg/.flac не подойдут."
            )
        else:
            self.btn_replace.setText("📄 Заменить файл…")
            self.btn_replace.setToolTip(
                "Байты файла попадут в архив без интерпретации"
            )
            self.detail_hint.setText(
                "Содержимое копируется как есть — формат должен совпадать с исходным."
            )
        self._schedule_preview(resource)
        self._refresh_actions()

    # ── Превью ресурса ─────────────────────────────────────────────────────

    def _schedule_preview(self, resource: ResourceInfo | None) -> None:
        """Планирует превью с небольшой задержкой: быстрые клики не плодят потоки."""
        self._preview_seq += 1
        self._preview_timer.stop()

        if resource is None or self._project is None:
            self._show_preview_status("Выберите ресурс — здесь появится превью.")
            return

        if preview_kind(resource.path) == "none":
            if resource.is_sound:
                message = "Звук пока без превью: воспроизведение не поддерживается."
            else:
                message = (
                    "Превью для этого типа файлов пока нет.\n"
                    "Поддерживаются текстуры .vtex_c, модели .vmdl_c "
                    "и текстовые файлы."
                )
            self._show_preview_status(message)
            return

        self._show_preview_status("⏳ Готовлю превью…")
        self._preview_timer.start()

    def _start_preview(self) -> None:
        """Запускает фоновую подготовку превью выбранного ресурса."""
        resource = self._selected_resource()
        if resource is None or self._project is None:
            return
        if preview_kind(resource.path) == "none":
            return
        if self._preview_worker is not None and self._preview_worker.isRunning():
            self._preview_pending = True
            return

        self._preview_pending = False
        worker = PreviewWorker(
            self._preview_seq,
            self._project.source_path,
            resource.path,
            self._preview_cache,
            workspace_dir=self._project.workspace_path,
            game_path=getattr(self.manager, "game_path", None),
            parent=self,
        )
        self._preview_worker = worker
        worker.preview_ready.connect(self._on_preview_ready)
        worker.finished.connect(self._on_preview_finished)
        worker.start()

    def _on_preview_ready(self, seq: int, outcome: PreviewOutcome) -> None:
        if seq != self._preview_seq:
            return  # пользователь уже выбрал другой ресурс

        if outcome.kind == "image" and outcome.path is not None:
            pixmap = QPixmap(str(outcome.path))
            if pixmap.isNull():
                self._show_preview_status("Не удалось открыть изображение превью.")
                return
            self.preview_image.set_source(pixmap)
            self.preview_stack.setCurrentWidget(self.preview_image)
            return

        if outcome.kind == "model" and outcome.path is not None:
            self._show_model_preview(outcome.path)
            return

        if outcome.kind == "text":
            self.preview_text.setPlainText(outcome.text)
            self.preview_stack.setCurrentWidget(self.preview_text)
            return

        self._show_preview_status(outcome.message or "Превью недоступно.")

    def _on_preview_finished(self) -> None:
        worker = self._preview_worker
        self._preview_worker = None
        if worker is not None:
            worker.deleteLater()
        if self._preview_pending:
            self._preview_pending = False
            self._start_preview()

    def _show_preview_status(self, message: str) -> None:
        self.preview_status.setText(message)
        self.preview_stack.setCurrentWidget(self.preview_status)

    def _ensure_model_view(self):
        """Лениво создаёт WebEngine-вьювер для 3D-моделей."""
        if self._model_view is not None:
            return self._model_view
        try:
            from .model_viewer import create_model_viewer

            view = create_model_viewer(self)
        except Exception as exc:  # noqa: BLE001 — QtWebEngine может отсутствовать
            self._show_preview_status(f"3D-превью недоступно: {exc}")
            return None
        self._model_view = view
        self.preview_stack.addWidget(view)
        return view

    def _show_model_preview(self, glb_path: Path) -> None:
        view = self._ensure_model_view()
        if view is None:
            return
        from .model_viewer import model_viewer_html

        view.setHtml(
            model_viewer_html(glb_path),
            baseUrl=QUrl.fromLocalFile(str(glb_path.parent) + "/"),
        )
        self.preview_stack.setCurrentWidget(view)

    def _toggle_preview_expanded(self) -> None:
        """Разворачивает панель превью на всё окно и возвращает список обратно."""
        self._preview_expanded = not self._preview_expanded
        if self._preview_expanded:
            self._splitter_sizes = self.content_splitter.sizes()
            self.resource_list.setVisible(False)
            self.btn_preview_expand.setText("⤡")
            self.btn_preview_expand.setToolTip("Вернуть список ресурсов")
        else:
            self.resource_list.setVisible(True)
            if self._splitter_sizes:
                self.content_splitter.setSizes(self._splitter_sizes)
            self.btn_preview_expand.setText("⤢")
            self.btn_preview_expand.setToolTip("Развернуть превью на всё окно")

    def _clear_preview(self) -> None:
        """Сбрасывает превью в состояние «проект не открыт»."""
        self._preview_timer.stop()
        self._preview_seq += 1
        self.preview_image.clear_source()
        self.preview_text.clear()
        if self._model_view is not None:
            self._model_view.setHtml(
                "<html><body style='background:#121316;'></body></html>"
            )
        self._show_preview_status("Откройте .vpk и выберите ресурс.")

    # ── Замена ресурса ─────────────────────────────────────────────────────

    def choose_and_replace(self) -> None:
        """Спрашивает файл замены и запускает фоновую замену ресурса."""
        resource = self._selected_resource()
        if resource is None or self._project is None or self._busy:
            return
        if resource.is_compiled_sound:
            filters = "Скомпилированный звук Source 2 (*.vsnd_c);;Все файлы (*)"
        else:
            filters = "Все файлы (*)"
        selected, _ = QFileDialog.getOpenFileName(
            self, "Файл замены", self._last_dir, filters
        )
        if not selected:
            return
        self.start_replace(resource.path, selected)

    def start_replace(self, target: str, source: str | Path) -> None:
        """Запускает замену ресурса в рабочей копии проекта."""
        if self._project is None or self._busy:
            return
        self._set_busy(True, f"⏳ Заменяю «{target}»…")
        worker = ReplaceWorker(self._project, target, source, self)
        self._replace_worker = worker
        worker.replaced.connect(self._on_replaced)
        worker.failed.connect(self._on_failed)
        worker.finished.connect(self._on_replace_finished)
        worker.start()

    def _on_replaced(self, result: ReplacementResult) -> None:
        self._modifications[result.resource_path] = result
        self._refresh_changes()
        self.status_label.setText(
            f"✅ Заменён ресурс «{result.resource_path}» "
            f"({_format_size(result.size)})"
        )
        self._on_selection_changed()

    def _on_replace_finished(self) -> None:
        worker = self._replace_worker
        self._replace_worker = None
        if worker is not None:
            worker.deleteLater()
        self._set_busy(False)

    def _refresh_changes(self) -> None:
        """Показывает список замен в рабочей копии."""
        if not self._modifications:
            self.changes_label.setText("Изменений нет")
            self._refresh_actions()
            return
        paths = list(self._modifications)
        lines = [f"• {path}" for path in paths[:5]]
        if len(paths) > 5:
            lines.append(f"…и ещё {len(paths) - 5}")
        self.changes_label.setText("\n".join(lines))
        self._refresh_actions()

    # ── Сборка архива ──────────────────────────────────────────────────────

    def choose_and_build(self) -> None:
        """Спрашивает путь и запускает сборку нового VPK."""
        if self._project is None or self._busy:
            return
        source = Path(self._project.source_path)
        default = source.with_name(f"{source.stem}_edited.vpk")
        selected, _ = QFileDialog.getSaveFileName(
            self, "Собрать VPK", str(default), "VPK архивы (*.vpk)"
        )
        if not selected:
            return
        output = Path(selected)
        if output.suffix.lower() != ".vpk":
            output = output.with_suffix(".vpk")
        if output.resolve() == source.resolve():
            QMessageBox.warning(
                self,
                "Сборка VPK",
                "Нельзя перезаписать исходный архив. Выберите другое имя файла.",
            )
            return

        overwrite = False
        if output.exists():
            answer = QMessageBox.question(
                self,
                "Сборка VPK",
                f"Файл «{output.name}» уже существует. Перезаписать?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if answer != QMessageBox.StandardButton.Yes:
                return
            overwrite = True
        self.start_build(output, overwrite)

    def start_build(self, output: str | Path, overwrite: bool = False) -> None:
        """Запускает сборку нового архива в фоне."""
        if self._project is None or self._busy:
            return
        self._set_busy(True, f"⏳ Собираю «{Path(output).name}»…")
        worker = BuildWorker(self._project, output, overwrite, self)
        self._build_worker = worker
        worker.built.connect(self._on_built)
        worker.failed.connect(self._on_failed)
        worker.finished.connect(self._on_build_finished)
        worker.start()

    def _on_built(self, result: BuildResult) -> None:
        check = " · проверен чтением" if result.verified else ""
        self.status_label.setText(
            f"✅ Собрано: {result.output_path.name} · "
            f"{result.entry_count} ресурсов · {_format_size(result.size)}{check}"
        )
        self._last_dir = str(result.output_path.parent)
        QMessageBox.information(
            self,
            "Сборка VPK",
            f"Архив собран:\n{result.output_path}\n\n"
            f"Ресурсов: {result.entry_count}\n"
            f"Размер: {_format_size(result.size)}",
        )

    def _on_build_finished(self) -> None:
        worker = self._build_worker
        self._build_worker = None
        if worker is not None:
            worker.deleteLater()
        self._set_busy(False)

    # ── Завершение работы ──────────────────────────────────────────────────

    def _close_project(self) -> None:
        """Удаляет временную рабочую копию открытого проекта."""
        project = self._project
        self._project = None
        if project is not None:
            try:
                project.close()
            except Exception:  # noqa: BLE001, S110 — закрытие не должно мешать выходу
                pass

    def shutdown(self) -> None:
        """Останавливает фоновые потоки и освобождает рабочую копию и кеш превью."""
        for worker in (
            self._open_worker,
            self._replace_worker,
            self._build_worker,
            self._preview_worker,
        ):
            _stop_worker(worker)
        self._preview_worker = None
        self._preview_pending = False
        self._busy = False
        self._close_project()
        self._reset_project_ui()
        if self._model_view is not None:
            self._model_view.stop()
        shutil.rmtree(self._preview_cache, ignore_errors=True)

    def _reset_project_ui(self) -> None:
        """Приводит окно к состоянию «архив не открыт» после закрытия проекта."""
        self._resources = []
        self._by_path = {}
        self._modifications.clear()
        self.resource_list.blockSignals(True)
        self.resource_list.clear()
        self.resource_list.blockSignals(False)
        self.file_label.setText("Архив не открыт")
        self.file_label.setToolTip("")
        self.count_label.setText("")
        self._refresh_changes()
        self.status_label.setText("Откройте .vpk, чтобы увидеть список ресурсов")
        self._on_selection_changed()
        self._clear_preview()

    def closeEvent(self, event) -> None:
        self.shutdown()
        super().closeEvent(event)
