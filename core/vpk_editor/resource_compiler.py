"""Адаптер компиляции Source 2 ресурсов.

Реальная компиляция ``.wav`` → ``.vsnd_c`` требует внешнего компилятора
(ValveResourceFormat/Source 2 resource compiler), которого нет в зависимостях
проекта. Модуль не скачивает и не подменяет такой инструмент: по умолчанию
любая попытка компиляции завершается :class:`CompilationUnavailableError`.

Будущая интеграция может передать в :class:`VPKEditor` собственную реализацию
протокола :class:`ResourceCompiler` — например, обёртку над лицензированным
``resourcecompiler.exe``. Тесты используют только тестовый mock и не выдают
его за поддержку реальной компиляции.
"""

from pathlib import Path
from typing import Protocol, runtime_checkable

from .errors import CompilationUnavailableError


@runtime_checkable
class ResourceCompiler(Protocol):
    """Интерфейс компилятора Source 2 ресурсов."""

    def compile_sound(self, source: Path, resource_path: str) -> bytes:
        """Компилирует аудиофайл в содержимое ``.vsnd_c``.

        ``resource_path`` — целевой путь внутри VPK (нужен компилятору для
        выбора параметров/кодека). Возвращает готовые байты ресурса.
        """


class UnavailableResourceCompiler:
    """Компилятор-заглушка: честно сообщает об отсутствии инструмента."""

    def compile_sound(self, source: Path, resource_path: str) -> bytes:
        raise CompilationUnavailableError(
            "Компиляция Source 2 звука недоступна: в проекте нет "
            "resourcecompiler/ValveResourceFormat. Замените ресурс уже "
            f"скомпилированным .vsnd_c или передайте свой ResourceCompiler "
            f"(цель: {resource_path}, источник: {source.name})."
        )
