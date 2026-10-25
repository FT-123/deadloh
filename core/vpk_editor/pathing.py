"""Безопасная работа с путями ресурсов внутри VPK и workspace.

VPK хранит пути в POSIX-нотации (``/``). На Windows те же имена могут
трактоваться иначе (регистр, хвостовые точки/пробелы, зарезервированные
устройства), поэтому все имена нормализуются и проверяются до записи на диск.
"""

from pathlib import Path

from .errors import UnsafeResourcePathError

# Зарезервированные имена Windows: файл CON.txt или NUL.vpk создать нельзя.
_WINDOWS_RESERVED = {
    "CON",
    "PRN",
    "AUX",
    "NUL",
    *(f"COM{i}" for i in range(1, 10)),
    *(f"LPT{i}" for i in range(1, 10)),
}


def normalize_resource_path(value: str) -> str:
    """Возвращает безопасный относительный POSIX-путь.

    Отклоняет абсолютные пути, ``..``, буквы диска, NUL, управляющие символы
    и имена, которые Windows не сможет создать без искажений.
    """
    if not isinstance(value, str):
        raise UnsafeResourcePathError(f"Путь ресурса должен быть строкой: {value!r}")

    text = value.replace("\\", "/")
    if not text.strip():
        raise UnsafeResourcePathError("Пустой путь ресурса")
    if "\x00" in text:
        raise UnsafeResourcePathError(f"NUL-символ в пути ресурса: {value!r}")
    if text.startswith(("/", "//")):
        raise UnsafeResourcePathError(f"Абсолютный путь ресурса запрещён: {value!r}")
    if ":" in text:
        raise UnsafeResourcePathError(
            f"Буква диска или поток NTFS в пути ресурса запрещены: {value!r}"
        )

    parts: list[str] = []
    for part in text.split("/"):
        if part in ("", "."):
            continue
        if part == "..":
            raise UnsafeResourcePathError(
                f"Выход за пределы рабочей папки запрещён: {value!r}"
            )
        if part != part.strip():
            raise UnsafeResourcePathError(
                f"Пробелы в начале или конце имени запрещены: {value!r}"
            )
        if part.endswith((" ", ".")):
            raise UnsafeResourcePathError(
                f"Имя не должно оканчиваться пробелом или точкой: {value!r}"
            )
        if any(ord(char) < 32 for char in part):
            raise UnsafeResourcePathError(
                f"Управляющие символы в пути ресурса запрещены: {value!r}"
            )
        if part.split(".")[0].upper() in _WINDOWS_RESERVED:
            raise UnsafeResourcePathError(
                f"Зарезервированное имя Windows в пути ресурса: {value!r}"
            )
        parts.append(part)

    if not parts:
        raise UnsafeResourcePathError(f"Путь ресурса не содержит имён: {value!r}")
    return "/".join(parts)


def resource_key(value: str) -> str:
    """Ключ для поиска ресурса без учёта регистра (Windows-семантика)."""
    return normalize_resource_path(value).casefold()


def resolve_within(root: Path, value: str) -> Path:
    """Преобразует путь ресурса в путь на диске, не выпуская его из ``root``."""
    root_resolved = root.resolve()
    relative = normalize_resource_path(value)
    target = (root_resolved / Path(*relative.split("/"))).resolve()
    try:
        target.relative_to(root_resolved)
    except ValueError as exc:
        raise UnsafeResourcePathError(
            f"Путь ресурса выходит за пределы рабочей папки: {value!r}"
        ) from exc
    return target


def is_within(root: Path, candidate: Path) -> bool:
    """Проверяет, находится ли ``candidate`` внутри ``root``."""
    try:
        candidate.resolve().relative_to(root.resolve())
    except ValueError:
        return False
    return True
