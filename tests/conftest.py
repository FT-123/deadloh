"""Общая настройка pytest для GUI-тестов.

Некоторые окружения (IDE, WSL, другие Qt-приложения) задают глобальный
``QT_QPA_PLATFORM`` неверно — например, ``xcb`` или ``wayland``. На Windows
такого плагина нет, и Qt падает с диалогом «no Qt platform plugin could be
initialized». Принудительно включаем offscreen ДО создания QApplication:
тестам не нужен реальный дисплей, а значение из окружения не должно ломать
запуск.
"""

import os

os.environ["QT_QPA_PLATFORM"] = "offscreen"
