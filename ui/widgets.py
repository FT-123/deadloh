"""Переиспользуемые виджеты в стилистике Gemini.

Содержит анимированный переключатель, карточку мода, шапку с «живым»
градиентом, карточки статистики, всплывающее уведомление и список с
поддержкой drag&drop.
"""

import json
import math
import shutil

from PySide6.QtCore import (
    Property,
    QEasingCurve,
    QPointF,
    QPropertyAnimation,
    QRectF,
    Qt,
    QTimer,
    QVariantAnimation,
)
from PySide6.QtGui import (
    QColor,
    QFont,
    QFontMetrics,
    QLinearGradient,
    QPainter,
    QPainterPath,
    QPen,
    QRadialGradient,
)
from PySide6.QtWidgets import (
    QAbstractButton,
    QAbstractItemView,
    QFrame,
    QGraphicsOpacityEffect,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QListWidget,
    QMessageBox,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from core.constants import clean_display_name
from core.jsonio import write_json_atomic
from core.paths import STAGING_DIR

from .styles import GRADIENT_STOPS, apply_dark_titlebar, refresh_style


def _lerp_color(a: QColor, b: QColor, t: float) -> QColor:
    """Линейная интерполяция двух цветов (t от 0 до 1)."""
    t = min(max(t, 0.0), 1.0)
    return QColor(
        int(a.red() + (b.red() - a.red()) * t),
        int(a.green() + (b.green() - a.green()) * t),
        int(a.blue() + (b.blue() - a.blue()) * t),
        int(a.alpha() + (b.alpha() - a.alpha()) * t),
    )


# ── Переключатель ─────────────────────────────────────────────────────────


class ToggleSwitch(QAbstractButton):
    """Плавный переключатель в стиле Material/Gemini."""

    TRACK_OFF = QColor("#444746")
    TRACK_ON = QColor("#a8c7fa")
    KNOB_OFF = QColor("#c4c7c5")
    KNOB_ON = QColor("#062e6f")

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setCheckable(True)
        self.setFixedSize(46, 26)
        self.setCursor(Qt.PointingHandCursor)
        self._position = 0.0

        self._anim = QPropertyAnimation(self, b"position", self)
        self._anim.setDuration(180)
        self._anim.setEasingCurve(QEasingCurve.OutCubic)

    def hitButton(self, pos):
        return self.rect().contains(pos)

    def _get_position(self):
        return self._position

    def _set_position(self, pos):
        self._position = pos
        self.update()

    position = Property(float, _get_position, _set_position)

    def setChecked(self, checked):
        super().setChecked(checked)
        self._anim.stop()
        self._position = 1.0 if checked else 0.0
        self.update()

    def nextCheckState(self):
        new_state = not self.isChecked()
        self._anim.stop()
        self._anim.setStartValue(self._position)
        self._anim.setEndValue(1.0 if new_state else 0.0)
        self._anim.start()
        super().nextCheckState()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        w, h = self.width(), self.height()
        radius = h / 2

        track = _lerp_color(self.TRACK_OFF, self.TRACK_ON, self._position)
        p.setPen(Qt.NoPen)
        p.setBrush(track)
        p.drawRoundedRect(QRectF(0, 0, w, h), radius, radius)

        knob_d = h - 6
        knob_x = 3 + (w - knob_d - 6) * self._position
        knob = _lerp_color(self.KNOB_OFF, self.KNOB_ON, self._position)
        p.setBrush(knob)
        p.drawEllipse(QRectF(knob_x, 3, knob_d, knob_d))
        p.end()


# ── Живая графика ─────────────────────────────────────────────────────────


class SparkleMark(QWidget):
    """Градиентная «искра» Gemini с лёгким дыханием."""

    def __init__(self, size: int = 38, parent=None):
        super().__init__(parent)
        self.setFixedSize(size, size)
        self._phase = 0.0
        self._timer = QTimer(self)
        self._timer.setInterval(60)
        self._timer.timeout.connect(self._tick)

    def _tick(self):
        self._phase = (self._phase + 0.04) % (2 * math.pi)
        self.update()

    def showEvent(self, event):
        super().showEvent(event)
        self._timer.start()

    def hideEvent(self, event):
        self._timer.stop()
        super().hideEvent(event)

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        w, h = self.width(), self.height()
        cx, cy = w / 2, h / 2
        pulse = 1.0 + 0.05 * math.sin(self._phase * 1.4)
        long_r = w * 0.42 * pulse
        short_r = w * 0.13 * pulse

        grad = QLinearGradient(0, 0, w, h)
        grad.setColorAt(0.0, QColor(GRADIENT_STOPS[0]))
        grad.setColorAt(0.5, QColor(GRADIENT_STOPS[1]))
        grad.setColorAt(1.0, QColor(GRADIENT_STOPS[2]))

        path = QPainterPath()
        path.moveTo(cx, cy - long_r)
        path.quadTo(cx + short_r * 0.45, cy - short_r * 0.45, cx + long_r, cy)
        path.quadTo(cx + short_r * 0.45, cy + short_r * 0.45, cx, cy + long_r)
        path.quadTo(cx - short_r * 0.45, cy + short_r * 0.45, cx - long_r, cy)
        path.quadTo(cx - short_r * 0.45, cy - short_r * 0.45, cx, cy - long_r)
        path.closeSubpath()

        p.setPen(Qt.NoPen)
        p.setBrush(grad)
        p.drawPath(path)
        p.end()


class AuroraHeader(QFrame):
    """Шапка с медленно переливающимся градиентным свечением."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("auroraHeader")
        self.setMinimumHeight(104)
        self._phase = 0.0
        self._timer = QTimer(self)
        self._timer.setInterval(45)
        self._timer.timeout.connect(self._tick)

    def _tick(self):
        self._phase = (self._phase + 0.022) % (2 * math.pi)
        self.update()

    def showEvent(self, event):
        super().showEvent(event)
        self._timer.start()

    def hideEvent(self, event):
        self._timer.stop()
        super().hideEvent(event)

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        rect = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        radius = 22

        path = QPainterPath()
        path.addRoundedRect(rect, radius, radius)
        p.setClipPath(path)
        p.fillPath(path, QColor("#18191b"))

        w, h = self.width(), self.height()
        blobs = (
            (0.12 + 0.06 * math.sin(self._phase), 0.15, GRADIENT_STOPS[0], 46, 0.95),
            (0.55 + 0.08 * math.sin(self._phase * 0.7 + 1.2), -0.25, GRADIENT_STOPS[1], 40, 0.8),
            (0.93 + 0.04 * math.cos(self._phase * 1.1), 0.75, GRADIENT_STOPS[2], 34, 0.7),
        )
        for fx, fy, color, alpha, scale in blobs:
            center = QPointF(w * fx, h * fy)
            radial = QRadialGradient(center, max(w, h) * 0.55 * scale)
            c = QColor(color)
            c.setAlpha(alpha)
            radial.setColorAt(0.0, c)
            transparent = QColor(color)
            transparent.setAlpha(0)
            radial.setColorAt(1.0, transparent)
            p.setBrush(radial)
            p.setPen(Qt.NoPen)
            p.drawRect(rect)

        p.setClipping(False)
        p.setBrush(Qt.NoBrush)
        p.setPen(QPen(QColor("#2a2c2f"), 1))
        p.drawRoundedRect(rect, radius, radius)
        p.end()


class StatBadge(QFrame):
    """Компактная карточка статистики с анимированным числом."""

    def __init__(self, caption: str, dot_color: str = "#9aa0a6", parent=None):
        super().__init__(parent)
        self.setObjectName("statBadge")
        self._value = 0

        layout = QHBoxLayout(self)
        layout.setContentsMargins(14, 8, 16, 8)
        layout.setSpacing(8)

        self.lbl_dot = QLabel("●")
        self.lbl_dot.setObjectName("statDot")
        self.lbl_dot.setStyleSheet(f"color: {dot_color};")
        layout.addWidget(self.lbl_dot)

        text_box = QVBoxLayout()
        text_box.setSpacing(0)
        self.lbl_value = QLabel("0")
        self.lbl_value.setObjectName("statValue")
        text_box.addWidget(self.lbl_value)
        self.lbl_caption = QLabel(caption)
        self.lbl_caption.setObjectName("statCaption")
        text_box.addWidget(self.lbl_caption)
        layout.addLayout(text_box)

        self._anim = QVariantAnimation(self)
        self._anim.setDuration(320)
        self._anim.setEasingCurve(QEasingCurve.OutCubic)
        self._anim.valueChanged.connect(self._on_anim_value)

    def _on_anim_value(self, value):
        self._value = int(value)
        self.lbl_value.setText(str(self._value))

    def set_value(self, value: int, animate: bool = True):
        value = int(value)
        if value == self._value:
            return
        self._anim.stop()
        if not animate:
            self._on_anim_value(value)
            return
        self._anim.setStartValue(self._value)
        self._anim.setEndValue(value)
        self._anim.start()


# ── Уведомления ───────────────────────────────────────────────────────────


class Toast(QLabel):
    """Всплывающая плашка с плавным появлением и исчезновением."""

    def __init__(self, parent: QWidget):
        super().__init__(parent)
        self.setObjectName("toast")
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self.setGraphicsEffect(self._make_effect())

        self._anim = QPropertyAnimation(self._effect, b"opacity", self)
        self._anim.setDuration(180)
        self._anim.setEasingCurve(QEasingCurve.OutCubic)
        self._anim.finished.connect(self._on_anim_finished)

        self._hide_timer = QTimer(self)
        self._hide_timer.setSingleShot(True)
        self._hide_timer.timeout.connect(self._fade_out)
        self.hide()

    def _make_effect(self) -> QGraphicsOpacityEffect:
        self._effect = QGraphicsOpacityEffect(self)
        self._effect.setOpacity(0.0)
        return self._effect

    def _on_anim_finished(self):
        if self._effect.opacity() <= 0.01:
            self.hide()

    def reposition(self):
        parent = self.parentWidget()
        if parent is None:
            return
        self.adjustSize()
        pw, ph = parent.width(), parent.height()
        x = max(8, (pw - self.width()) // 2)
        y = max(8, ph - self.height() - 86)
        self.move(x, y)

    def show_message(self, text: str, msec: int = 2600):
        self.setText(text)
        self.reposition()
        self.raise_()
        self.show()
        self._anim.stop()
        self._anim.setStartValue(self._effect.opacity())
        self._anim.setEndValue(1.0)
        self._anim.start()
        self._hide_timer.start(max(900, msec))

    def _fade_out(self):
        self._anim.stop()
        self._anim.setStartValue(self._effect.opacity())
        self._anim.setEndValue(0.0)
        self._anim.start()


# ── Карточка мода ─────────────────────────────────────────────────────────


class ElidedLabel(QLabel):
    """QLabel, который аккуратно обрезает длинный текст многоточием."""

    def __init__(self, text: str = "", parent=None):
        super().__init__(text, parent)
        self._full_text = text
        self.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)

    def set_full_text(self, text: str):
        self._full_text = text
        self._apply_elided()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._apply_elided()

    def _apply_elided(self):
        fm = self.fontMetrics()
        super().setText(
            fm.elidedText(self._full_text, Qt.TextElideMode.ElideRight, max(10, self.width()))
        )


class ModCardWidget(QFrame):
    """Карточка мода: переключатель, название, теги, статус и действия."""

    RADIUS = 18

    def __init__(self, folder_name: str, meta: dict, is_deployed: bool, parent_manager):
        super().__init__()
        self.folder_name = folder_name
        self.manager = parent_manager
        self.is_deployed = is_deployed
        self._hover = 0.0
        self._intro_timer = None
        self._intro_anim = None

        if not isinstance(meta, dict):
            meta = {}
        name = meta.get("name")
        if not isinstance(name, str) or not name.strip():
            name = folder_name
        # Пользовательское название хранится отдельно: папка мода и content.vpk
        # остаются нетронутыми, поэтому деплой и игра продолжают работать.
        custom_name = meta.get("display_name")
        if isinstance(custom_name, str) and custom_name.strip():
            display_name = custom_name.strip()
        else:
            display_name = clean_display_name(name)
        self.raw_name = name
        self.display_name = display_name
        self.enabled = meta.get("enabled", True)
        if not isinstance(self.enabled, bool):
            self.enabled = True
        is_stale = meta.get("is_stale", False)
        if not isinstance(is_stale, bool):
            is_stale = False

        layout = QHBoxLayout(self)
        layout.setContentsMargins(16, 11, 12, 11)
        layout.setSpacing(14)

        # 1. Переключатель
        self.switch = ToggleSwitch()
        self.switch.setChecked(self.enabled)
        self.switch.toggled.connect(self.on_toggle)
        layout.addWidget(self.switch, 0, Qt.AlignmentFlag.AlignVCenter)

        # 2. Название
        title_box = QVBoxLayout()
        title_box.setSpacing(2)
        self.lbl_title = ElidedLabel(display_name)
        self.lbl_title.setStyleSheet("font-size: 14px; font-weight: 600; color: #e3e3e3;")
        self.lbl_title.setToolTip(display_name)
        title_box.addWidget(self.lbl_title)

        self.lbl_sub = QLabel(f"Архив: {name}")
        self.lbl_sub.setStyleSheet("font-size: 11px; color: #5f6368;")
        title_box.addWidget(self.lbl_sub)
        layout.addLayout(title_box, stretch=1)

        # 3. Починка (только для сломанных модов)
        self.btn_fix = QPushButton("🔧   Починить")
        self.btn_fix.setObjectName("fixBtn")
        self.btn_fix.setToolTip(
            "В моде устарели модели (баг камеры после обновления). "
            "Нажмите для починки через vpkmerge."
        )
        self.btn_fix.setCursor(Qt.PointingHandCursor)
        self.btn_fix.clicked.connect(self.on_fix)
        layout.addWidget(self.btn_fix, 0, Qt.AlignmentFlag.AlignVCenter)
        self.btn_fix.setVisible(is_stale)

        # 4. Статус
        self.lbl_status = QLabel()
        self.lbl_status.setObjectName("statusPill")
        layout.addWidget(self.lbl_status, 0, Qt.AlignmentFlag.AlignVCenter)
        self.update_status_ui(self.enabled)

        # 5. Теги
        self.tags_container = QWidget()
        tags_box = QHBoxLayout(self.tags_container)
        tags_box.setContentsMargins(0, 0, 0, 0)
        tags_box.setSpacing(6)
        self._tags_widgets: list[QLabel] = []

        tags_data = meta.get("tags_data")
        if not isinstance(tags_data, dict):
            tags_data = {}
        heroes = tags_data.get("heroes")
        heroes = heroes if isinstance(heroes, list) else []
        types = tags_data.get("types")
        types = types if isinstance(types, list) else []

        # Не больше трёх тегов — карточка должна оставаться лёгкой и читаемой.
        for hero in heroes[:3]:
            tags_box.addWidget(self._make_tag(str(hero), "hero"))
        for kind in types[: max(0, 3 - len(heroes[:3]))]:
            tags_box.addWidget(self._make_tag(str(kind), "type"))

        layout.addWidget(self.tags_container, 0, Qt.AlignmentFlag.AlignVCenter)
        self.tags_container.setVisible(bool(heroes or types))

        # 5.5. Кнопка предпросмотра
        self.btn_preview = QPushButton("👁")
        self.btn_preview.setObjectName("iconBtn")
        self.btn_preview.setToolTip("Предпросмотр мода")
        self.btn_preview.setCursor(Qt.PointingHandCursor)
        self.btn_preview.setFixedSize(32, 32)
        self.btn_preview.clicked.connect(self.on_preview)
        layout.addWidget(self.btn_preview, 0, Qt.AlignmentFlag.AlignVCenter)

		# 6. Переименование (меняет только подпись в менеджере)
        btn_rename = QPushButton("✏")
        btn_rename.setObjectName("renameBtn")
        btn_rename.setToolTip(
            "Переименовать мод в менеджере. Папка мода, content.vpk и файлы в игре "
            "остаются прежними — деплой не ломается."
        )
        btn_rename.setCursor(Qt.PointingHandCursor)
        btn_rename.clicked.connect(self.on_rename)
        layout.addWidget(btn_rename, 0, Qt.AlignmentFlag.AlignVCenter)

        # 7. Удаление
        btn_del = QPushButton("✕")
        btn_del.setObjectName("deleteBtn")
        btn_del.setToolTip("Удалить мод")
        btn_del.setCursor(Qt.PointingHandCursor)
        btn_del.clicked.connect(self.on_delete)
        layout.addWidget(btn_del, 0, Qt.AlignmentFlag.AlignVCenter)

        self._hover_anim = QPropertyAnimation(self, b"hoverProgress", self)
        self._hover_anim.setDuration(170)
        self._hover_anim.setEasingCurve(QEasingCurve.OutCubic)

    # ── Отрисовка ─────────────────────────────────────────────────────────

    def _make_tag(self, text: str, kind: str) -> QLabel:
        pill = QLabel(text)
        pill.setObjectName("tagPill")
        pill.setProperty("tagKind", kind)
        self._tags_widgets.append(pill)
        return pill

    def _get_hover(self) -> float:
        return self._hover

    def _set_hover(self, value: float):
        self._hover = value
        self.update()

    hoverProgress = Property(float, _get_hover, _set_hover)

    def enterEvent(self, event):
        super().enterEvent(event)
        self._animate_hover(1.0)

    def leaveEvent(self, event):
        super().leaveEvent(event)
        self._animate_hover(0.0)

    def _animate_hover(self, target: float):
        self._hover_anim.stop()
        self._hover_anim.setStartValue(self._hover)
        self._hover_anim.setEndValue(target)
        self._hover_anim.start()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        rect = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)

        base = _lerp_color(QColor("#1e1f20"), QColor("#282a2c"), self._hover)
        path = QPainterPath()
        path.addRoundedRect(rect, self.RADIUS, self.RADIUS)
        p.setPen(Qt.NoPen)
        p.setBrush(base)
        p.drawPath(path)

        # Мягкое свечение в правом верхнем углу при наведении.
        if self._hover > 0.01:
            glow = QRadialGradient(QPointF(rect.right() - 40, rect.top() + 10), 190)
            c = QColor("#a8c7fa")
            c.setAlpha(int(26 * self._hover))
            glow.setColorAt(0.0, c)
            clear = QColor("#a8c7fa")
            clear.setAlpha(0)
            glow.setColorAt(1.0, clear)
            p.setBrush(glow)
            p.drawPath(path)

        if self.enabled and self.is_deployed:
            border = _lerp_color(QColor(129, 201, 149, 90), QColor("#a8c7fa"), self._hover)
        else:
            border = _lerp_color(QColor("#303134"), QColor("#5f6368"), self._hover)

        p.setBrush(Qt.NoBrush)
        p.setPen(QPen(border, 1))
        p.drawPath(path)
        p.end()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        width = self.width()
        if hasattr(self, "lbl_sub"):
            self.lbl_sub.setVisible(width >= 560)
        if hasattr(self, "tags_container"):
            self.tags_container.setVisible(bool(self._tags_widgets) and width >= 880)

    def play_intro(self, delay_ms: int = 0):
        """Плавное появление карточки (эффект прозрачности)."""
        effect = QGraphicsOpacityEffect(self)
        effect.setOpacity(0.0)
        self.setGraphicsEffect(effect)

        anim = QPropertyAnimation(effect, b"opacity", self)
        anim.setDuration(260)
        anim.setStartValue(0.0)
        anim.setEndValue(1.0)
        anim.setEasingCurve(QEasingCurve.OutCubic)
        anim.finished.connect(self._schedule_intro_cleanup)
        self._intro_anim = anim

        if delay_ms <= 0:
            anim.start()
            return
        timer = QTimer(self)
        timer.setSingleShot(True)
        timer.timeout.connect(anim.start)
        timer.start(delay_ms)
        self._intro_timer = timer

    def _schedule_intro_cleanup(self):
        # Снимаем эффект вне обработки сигнала анимации, чтобы не удалить
        # объект, который ещё участвует в доставке сигнала.
        timer = QTimer(self)
        timer.setSingleShot(True)
        timer.timeout.connect(self._clear_intro_effect)
        timer.start(30)

    def _clear_intro_effect(self):
        if self.graphicsEffect() is not None:
            self.setGraphicsEffect(None)
        self._intro_anim = None

    # ── Логика ────────────────────────────────────────────────────────────

    def on_preview(self):
        """Вызывает открытие/закрытие панели в главном окне."""
        if hasattr(self.manager, "toggle_preview"):
            self.manager.toggle_preview(self.folder_name)

    def on_fix(self):
        """Вызывает процедуру лечения через менеджер."""
        self.manager.fix_mod_card(self.folder_name, self)

    def update_status_ui(self, enabled: bool):
        if enabled and self.is_deployed:
            text, state = "В игре", "deployed"
        elif enabled and not self.is_deployed:
            text, state = "Ожидает применения", "pending"
        elif not enabled and self.is_deployed:
            text, state = "Будет удалён из игры", "removing"
        else:
            text, state = "Выключен", "off"
        self.lbl_status.setText(text)
        self.lbl_status.setProperty("state", state)
        refresh_style(self.lbl_status)

    def update_card_appearance(self, enabled: bool):
        self.enabled = enabled
        self.update()

    def on_toggle(self, checked):
        meta_file = STAGING_DIR / self.folder_name / "meta.json"
        if meta_file.exists():
            try:
                meta = json.loads(meta_file.read_text(encoding="utf-8"))
                meta["enabled"] = checked
                write_json_atomic(meta_file, meta)
            except Exception:
                pass
        self.update_status_ui(checked)
        self.update_card_appearance(checked)
        self.manager.update_mod_counter()

    def on_rename(self):
        """Меняет только отображаемое название в meta.json (папка и VPK целы)."""
        new_name, accepted = QInputDialog.getText(
            self,
            "Переименование мода",
            "Название в менеджере (папка мода и VPK не меняются):",
            text=self.display_name,
        )
        if not accepted:
            return

        new_name = " ".join(str(new_name).split())
        if not new_name:
            self.manager.notify("Название не может быть пустым")
            return
        if len(new_name) > 80:
            new_name = new_name[:80].rstrip()
        if new_name == self.display_name:
            return

        meta_file = STAGING_DIR / self.folder_name / "meta.json"
        try:
            meta = json.loads(meta_file.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            self.manager.notify("Не удалось прочитать meta.json мода")
            return
        if not isinstance(meta, dict):
            meta = {}

        # Если вернули автоматическое имя — убираем пользовательское поле.
        if new_name == clean_display_name(self.raw_name):
            meta.pop("display_name", None)
        else:
            meta["display_name"] = new_name

        try:
            write_json_atomic(meta_file, meta)
        except OSError:
            self.manager.notify("Не удалось сохранить новое название")
            return

        self.display_name = new_name
        self.lbl_title.set_full_text(new_name)
        self.lbl_title.setToolTip(new_name)
        handler = getattr(self.manager, "on_mod_renamed", None)
        if callable(handler):
            handler(self.folder_name, new_name)
        self.manager.notify("Мод переименован")

    def on_delete(self):
        msg = QMessageBox(self)
        msg.setWindowTitle("Удаление")
        msg.setText(f"Удалить мод «{self.display_name}» из библиотеки?")
        msg.setStandardButtons(QMessageBox.Yes | QMessageBox.No)
        apply_dark_titlebar(msg)

        if msg.exec() == QMessageBox.Yes:
            shutil.rmtree(STAGING_DIR / self.folder_name, ignore_errors=True)
            if self.is_deployed:
                started = self.manager.deploy_mods(silent=True)
                if started:
                    self.manager.notify("Мод удалён — обновляю игру…")
                else:
                    self.manager.notify(
                        "Мод удалён. Нажмите «Применить», чтобы убрать его из игры."
                    )
            else:
                self.manager.refresh_list()
                self.manager.notify("Мод удалён из библиотеки")


# ── Список с drag&drop ────────────────────────────────────────────────────


class DropListWidget(QListWidget):
    """Список модов с подсказкой в пустом состоянии и подсветкой при перетаскивании."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.setObjectName("modList")
        self.setAcceptDrops(True)
        self.setDragDropMode(QAbstractItemView.DropOnly)
        self.setVerticalScrollMode(QAbstractItemView.ScrollPerPixel)
        self._drag_active = False

    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
            self._drag_active = True
            self.viewport().update()

    def dragLeaveEvent(self, event):
        self._drag_active = False
        self.viewport().update()
        super().dragLeaveEvent(event)

    def dragMoveEvent(self, event):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dropEvent(self, event):
        self._drag_active = False
        self.viewport().update()
        if event.mimeData().hasUrls():
            urls = event.mimeData().urls()
            event.acceptProposedAction()
            QTimer.singleShot(0, lambda: self.window().start_import_worker(urls))

    def paintEvent(self, event):
        super().paintEvent(event)
        if self.count() > 0 and not self._drag_active:
            return

        p = QPainter(self.viewport())
        p.setRenderHint(QPainter.Antialiasing)
        rect = QRectF(self.viewport().rect()).adjusted(10, 10, -10, -10)

        if self._drag_active:
            fill = QColor("#a8c7fa")
            fill.setAlpha(18)
            p.setBrush(fill)
            pen = QPen(QColor("#a8c7fa"), 1.4, Qt.DashLine)
            p.setPen(pen)
            p.drawRoundedRect(rect, 22, 22)
            p.setPen(QColor("#e3e3e3"))
            p.setFont(QFont(self.font().family(), 12, QFont.DemiBold))
            p.drawText(rect, Qt.AlignCenter, "Отпустите файлы, чтобы добавить моды")
            p.end()
            return

        p.setBrush(QColor(30, 31, 32, 90))
        p.setPen(QPen(QColor("#303134"), 1))
        p.drawRoundedRect(rect, 22, 22)

        center = rect.center()
        title_font = QFont(self.font().family(), 14, QFont.DemiBold)
        p.setFont(title_font)
        p.setPen(QColor("#e3e3e3"))
        p.drawText(
            QRectF(rect.left(), center.y() - 44, rect.width(), 30),
            Qt.AlignCenter,
            "Пока пусто",
        )

        hint_font = QFont(self.font().family(), 11)
        p.setFont(hint_font)
        p.setPen(QColor("#9aa0a6"))
        p.drawText(
            QRectF(rect.left(), center.y() - 8, rect.width(), 24),
            Qt.AlignCenter,
            "Перетащите сюда .vpk, .zip, .7z или .rar",
        )

        p.setPen(QColor("#5f6368"))
        fm = QFontMetrics(hint_font)
        support = "поддерживаются архивы с модами Deadlock"
        p.drawText(
            QRectF(rect.left(), center.y() + 24, rect.width(), 22),
            Qt.AlignCenter,
            support if fm.horizontalAdvance(support) < rect.width() - 40 else "",
        )
        p.end()
