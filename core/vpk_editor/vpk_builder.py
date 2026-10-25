"""Сборка нового VPK из изолированной рабочей папки.

Для записи используется та же библиотека ``vpk``, что и для чтения: она
создаёт корректный VPK v1/v2 (v2 — с MD5-блоками и checksums). Запись идёт
во временный файл рядом с целевым, затем проверяется чтением и атомарно
переименовывается. Исходный VPK никогда не перезаписывается.
"""

import os
import tempfile
from pathlib import Path

from .errors import (
    BuildVerificationError,
    OutputConflictError,
    OutputPathError,
    PackageWriteError,
    SourceOverwriteError,
    UnsupportedResourceError,
    UnsupportedVPKError,
    VPKDependencyError,
    VPKEditorError,
)
from .models import BuildResult
from .pathing import is_within, normalize_resource_path
from .vpk_reader import VPKReader

try:
    import vpk
except ImportError:  # pragma: no cover - зависит от окружения
    vpk = None

SUPPORTED_OUTPUT_VERSIONS = (1, 2)


def _same_path(first: Path, second: Path) -> bool:
    try:
        return os.path.samefile(first, second)
    except OSError:
        return os.path.normcase(str(first)) == os.path.normcase(str(second))


def _collect_workspace_files(root: Path) -> list[tuple[Path, str]]:
    """Собирает файлы workspace и проверяет их пригодность для VPK."""
    if not root.is_dir():
        raise OutputPathError(f"Рабочая папка не найдена: {root}")

    files: list[tuple[Path, str]] = []
    seen: dict[str, str] = {}
    for path in sorted(root.rglob("*")):
        if path.is_symlink():
            raise UnsupportedResourceError(
                f"Символические ссылки в workspace не поддерживаются: {path}"
            )
        if not path.is_file():
            continue
        relative = normalize_resource_path(path.relative_to(root).as_posix())
        key = relative.casefold()
        if key in seen:
            raise UnsupportedResourceError(
                "Ресурсы различаются только регистром и столкнутся в VPK: "
                f"{seen[key]!r} и {relative!r}"
            )
        seen[key] = relative
        if not path.suffix:
            raise UnsupportedResourceError(
                "Библиотека записи VPK не поддерживает файлы без расширения: "
                f"{relative!r}"
            )
        files.append((path, relative))
    return files


def build_vpk(
    workspace_root: str | Path,
    output_path: str | Path,
    *,
    source_path: str | Path | None = None,
    version: int = 2,
    overwrite: bool = False,
) -> BuildResult:
    """Собирает VPK из папки и возвращает результат с проверкой чтением."""
    if vpk is None:
        raise VPKDependencyError(
            "Для сборки VPK нужна библиотека 'vpk' (pip install vpk)"
        )
    if version not in SUPPORTED_OUTPUT_VERSIONS:
        raise UnsupportedVPKError(
            f"Поддерживаются выходные версии {SUPPORTED_OUTPUT_VERSIONS}, "
            f"запрошена v{version}"
        )

    root = Path(workspace_root).expanduser().resolve()
    output = Path(output_path).expanduser()
    try:
        output = output.resolve()
    except OSError as exc:
        raise OutputPathError(f"Некорректный путь выходного VPK: {output}") from exc

    if source_path is not None:
        source = Path(source_path).expanduser().resolve()
        if _same_path(source, output):
            raise SourceOverwriteError(
                "Сборка не может перезаписать исходный VPK. Укажите другой путь."
            )

    if is_within(root, output):
        raise OutputPathError(
            "Выходной VPK нельзя создавать внутри временной рабочей папки: "
            f"{output}"
        )

    parent = output.parent
    if not parent.is_dir():
        raise OutputPathError(f"Папка для выходного VPK не существует: {parent}")
    if output.exists():
        if not output.is_file():
            raise OutputPathError(f"Выходной путь занят не файлом: {output}")
        if not overwrite:
            raise OutputConflictError(
                f"Выходной VPK уже существует: {output}. "
                "Передайте overwrite=True для замены."
            )

    files = _collect_workspace_files(root)
    expected = {relative for _path, relative in files}

    handle, temp_name = tempfile.mkstemp(
        prefix=f".{output.stem}-", suffix=".tmp.vpk", dir=str(parent)
    )
    os.close(handle)
    temp_output = Path(temp_name)
    try:
        try:
            writer = vpk.new(str(root))
            writer.version = version
            writer.save(str(temp_output))
        except VPKEditorError:
            raise
        except Exception as exc:  # библиотека бросает разные типы
            raise PackageWriteError(
                f"Не удалось собрать VPK «{output.name}»: {exc}"
            ) from exc

        try:
            reader = VPKReader(temp_output)
            built = {resource.path for resource in reader.list_resources()}
            if reader.info.version != version:
                raise BuildVerificationError(
                    f"Ожидалась VPK v{version}, получена v{reader.info.version}"
                )
            if built != expected:
                missing = sorted(expected - built)
                extra = sorted(built - expected)
                raise BuildVerificationError(
                    f"Состав собранного VPK не совпал. Нет: {missing[:5]}; "
                    f"лишние: {extra[:5]}"
                )
            for relative in sorted(expected):
                for _chunk in reader.iter_chunks(relative):
                    pass
        except BuildVerificationError:
            raise
        except Exception as exc:  # проверка включает чтение и CRC
            raise BuildVerificationError(
                f"Собранный VPK не прошёл проверку чтением: {exc}"
            ) from exc

        if not overwrite and output.exists():
            raise OutputConflictError(
                f"Выходной VPK появился во время сборки: {output}"
            )
        try:
            os.replace(temp_output, output)
        except OSError as exc:
            raise PackageWriteError(
                f"Не удалось сохранить VPK «{output}»: {exc}"
            ) from exc
    finally:
        temp_output.unlink(missing_ok=True)

    return BuildResult(
        output_path=output,
        version=version,
        entry_count=len(expected),
        size=output.stat().st_size,
        verified=True,
    )
