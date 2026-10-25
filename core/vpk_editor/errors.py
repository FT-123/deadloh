"""Доменные исключения редактора VPK.

Все ошибки модуля наследуются от :class:`VPKEditorError`, чтобы вызывающий
код (в том числе фоновая задача PySide6) мог отличить ошибки редактора от
посторонних исключений.
"""


class VPKEditorError(Exception):
    """Базовая ошибка редактора VPK."""


class VPKDependencyError(VPKEditorError):
    """Не установлена библиотека ``vpk``, необходимая для работы редактора."""


class InvalidSourceError(VPKEditorError):
    """Исходный VPK не найден, не является файлом или путь пуст."""


class VPKSyntaxError(VPKEditorError):
    """Файл не является корректным VPK или его индекс повреждён."""


class UnsupportedVPKError(VPKEditorError):
    """Формат VPK распознан, но не поддерживается редактором."""


class UnsafeResourcePathError(VPKEditorError):
    """Путь ресурса внутри VPK небезопасен (выход за пределы workspace)."""


class ResourceNotFoundError(VPKEditorError):
    """Запрошенный ресурс отсутствует в исходном VPK."""


class UnsupportedResourceError(VPKEditorError):
    """Ресурс или формат файла не поддерживается операцией."""


class UnknownHeroError(VPKEditorError):
    """Герой не найден в официальном ростере Deadlock."""


class CompilationUnavailableError(VPKEditorError):
    """Нет доступного компилятора Source 2 для выбранного аудиоформата."""


class OutputConflictError(VPKEditorError):
    """Выходной файл уже существует, а перезапись не разрешена."""


class SourceOverwriteError(VPKEditorError):
    """Сборка попыталась бы перезаписать исходный VPK."""


class OutputPathError(VPKEditorError):
    """Некорректный путь выходного VPK или его родительская папка."""


class WorkspaceError(VPKEditorError):
    """Ошибка изолированной рабочей папки."""


class ProjectClosedError(VPKEditorError):
    """Проект уже закрыт, а операция требует открытого workspace."""


class BuildVerificationError(VPKEditorError):
    """Собранный VPK не прошёл проверку чтением."""


class PackageWriteError(VPKEditorError):
    """Не удалось записать файл внутри рабочей папки."""
