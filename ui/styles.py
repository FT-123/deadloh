"""Палитра, загрузка QSS и вспомогательные функции оформления.

Тема вдохновлена минималистичным интерфейсом Gemini: глубокий тёмный фон,
мягкие скруглённые поверхности, приглушённый текст и голубой акцент.
"""

import ctypes
import sys
from pathlib import Path

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import (
    QColor,
    QIcon,
    QLinearGradient,
    QPainter,
    QPainterPath,
    QPixmap,
)
from PySide6.QtWidgets import QWidget

# ── Палитра ────────────────────────────────────────────────────────────────
COLORS = {
    "bg": "#131314",
    "surface": "#1e1f20",
    "surface_alt": "#282a2c",
    "surface_hover": "#333537",
    "border": "#303134",
    "border_strong": "#444746",
    "text": "#e3e3e3",
    "text_muted": "#9aa0a6",
    "text_dim": "#5f6368",
    "accent": "#a8c7fa",
    "accent_hover": "#c5dbfd",
    "on_accent": "#062e6f",
    "success": "#81c995",
    "warning": "#fdd663",
    "danger": "#f28b82",
    "violet": "#c4a7f0",
}

# Фирменный градиент Google/Gemini: синий → фиолетовый → розовый.
GRADIENT_STOPS = ("#4796e3", "#9177c7", "#ca6673")


def load_stylesheet() -> str:
    """Загружает стили из файла ui/style.qss."""
    qss_path = Path(__file__).resolve().parent / "style.qss"
    if qss_path.exists():
        return qss_path.read_text(encoding="utf-8")
    return ""


def refresh_style(widget: QWidget) -> None:
    """Переприменяет QSS после смены динамического свойства (state, tagKind…)."""
    style = widget.style()
    if style is not None:
        style.unpolish(widget)
        style.polish(widget)
    widget.update()


def apply_dark_titlebar(widget: QWidget):
    """Окрашивает заголовок Windows в цвет фона приложения #131314."""
    if sys.platform != "win32":
        return
    try:
        hwnd = int(widget.winId())
        for attr in (20, 19):
            val = ctypes.c_int(1)
            ctypes.windll.dwmapi.DwmSetWindowAttribute(
                hwnd, attr, ctypes.byref(val), ctypes.sizeof(val)
            )
        bg_color = ctypes.c_int(0x00141313)  # BGR #131314
        ctypes.windll.dwmapi.DwmSetWindowAttribute(
            hwnd, 35, ctypes.byref(bg_color), ctypes.sizeof(bg_color)
        )
    except Exception:
        pass


def create_app_icon() -> QIcon:
    """Иконка приложения: градиентный квадрат с «искрой» Gemini."""
    size = 64
    pix = QPixmap(size, size)
    pix.fill(Qt.transparent)

    p = QPainter(pix)
    p.setRenderHint(QPainter.Antialiasing)

    grad = QLinearGradient(0, 0, size, size)
    grad.setColorAt(0.0, QColor(GRADIENT_STOPS[0]))
    grad.setColorAt(0.5, QColor(GRADIENT_STOPS[1]))
    grad.setColorAt(1.0, QColor(GRADIENT_STOPS[2]))
    p.setBrush(grad)
    p.setPen(Qt.NoPen)
    p.drawRoundedRect(QRectF(2, 2, size - 4, size - 4), 16, 16)

    # Четырёхлучевая «искра».
    cx, cy = size / 2, size / 2
    long_r, short_r = size * 0.34, size * 0.10
    path = QPainterPath()
    path.moveTo(cx, cy - long_r)
    path.quadTo(cx + short_r * 0.4, cy - short_r * 0.4, cx + long_r, cy)
    path.quadTo(cx + short_r * 0.4, cy + short_r * 0.4, cx, cy + long_r)
    path.quadTo(cx - short_r * 0.4, cy + short_r * 0.4, cx - long_r, cy)
    path.quadTo(cx - short_r * 0.4, cy - short_r * 0.4, cx, cy - long_r)
    path.closeSubpath()
    p.setBrush(QColor("#ffffff"))
    p.drawPath(path)
    p.end()
    return QIcon(pix)
