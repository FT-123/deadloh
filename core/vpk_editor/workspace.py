"""Изолированная рабочая копия ресурсов VPK.

Workspace создаётся во временной папке ОС, никогда не указывает на игровую
директорию и удаляется после закрытия проекта. Все пути ресурсов проверяются
до записи, поэтому архив с ``..`` или абсолютными путями не может вывести
файлы за пределы рабочей папки.
"""

import os
import tempfile
from collections.abc import Iterable
from pathlib import Path

from .errors import PackageWriteError, WorkspaceError
from .pathing import normalize_resource_path, resolve_within


class Workspace:
    """Временная папка с ресурсами одного проекта."""

    def __init__(self, parent: str | Path | None = None, prefix: str = "dmm-vpk-editor-"):
        temp_parent = None
        if parent is not None:
            temp_parent = Path(parent).expanduser().resolve()
            if not temp_parent.is_dir():
                raise WorkspaceError(
                    f"Родительская папка workspace не существует: {temp_parent}"
                )
        try:
            self._temp = tempfile.TemporaryDirectory(
                prefix=prefix, dir=str(temp_parent) if temp_parent else None
            )
        except OSError as exc:
            raise WorkspaceError(f"Не удалось создать временную папку: {exc}") from exc

        self.root = Path(self._temp.name).resolve()
        self._closed = False

    @property
    def closed(self) -> bool:
        return self._closed

    def path_for(self, resource_path: str) -> Path:
        """Путь на диске для ресурса; выход за пределы workspace запрещён."""
        self._ensure_open()
        return resolve_within(self.root, resource_path)

    def write_bytes(self, resource_path: str, data: bytes) -> Path:
        """Атомарно записывает байты в файл рабочей копии."""
        self._ensure_open()
        target = self.path_for(resource_path)
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            handle, temp_name = tempfile.mkstemp(
                prefix=f".{target.name}.", suffix=".tmp", dir=str(target.parent)
            )
            try:
                with os.fdopen(handle, "wb") as stream:
                    stream.write(data)
                    stream.flush()
                    os.fsync(stream.fileno())
                os.replace(temp_name, target)
            finally:
                Path(temp_name).unlink(missing_ok=True)
        except OSError as exc:
            raise PackageWriteError(
                f"Не удалось записать ресурс «{resource_path}»: {exc}"
            ) from exc
        return target

    def write_chunks(self, resource_path: str, chunks: Iterable[bytes]) -> Path:
        """Атомарно записывает поток чанков (например, из VPKReader)."""
        self._ensure_open()
        target = self.path_for(resource_path)
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            handle, temp_name = tempfile.mkstemp(
                prefix=f".{target.name}.", suffix=".tmp", dir=str(target.parent)
            )
            try:
                with os.fdopen(handle, "wb") as stream:
                    for chunk in chunks:
                        stream.write(chunk)
                    stream.flush()
                    os.fsync(stream.fileno())
                os.replace(temp_name, target)
            finally:
                Path(temp_name).unlink(missing_ok=True)
        except OSError as exc:
            raise PackageWriteError(
                f"Не удалось записать ресурс «{resource_path}»: {exc}"
            ) from exc
        return target

    def read_bytes(self, resource_path: str) -> bytes:
        """Читает файл рабочей копии."""
        self._ensure_open()
        try:
            return self.path_for(resource_path).read_bytes()
        except OSError as exc:
            raise WorkspaceError(
                f"Не удалось прочитать ресурс «{resource_path}»: {exc}"
            ) from exc

    def file_count(self) -> int:
        """Число обычных файлов в рабочей копии."""
        self._ensure_open()
        return sum(1 for path in self.root.rglob("*") if path.is_file())

    def cleanup(self) -> None:
        """Удаляет временную папку; повторный вызов безопасен."""
        if self._closed:
            return
        self._closed = True
        self._temp.cleanup()

    def _ensure_open(self) -> None:
        if self._closed:
            raise WorkspaceError("Рабочая папка уже удалена")

    def __enter__(self) -> "Workspace":  # noqa: PYI034 - Self требует Python 3.11
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        self.cleanup()

    def __del__(self) -> None:  # pragma: no cover - страховка от утечки temp
        if getattr(self, "_closed", True):
            return
        try:
            self.cleanup()
        except Exception:  # noqa: BLE001, S110 - во время shutdown уже неважно
            pass


def is_safe_relative_path(value: str) -> bool:
    """Публичная проверка пути без создания файлов (удобно для UI-валидации)."""
    try:
        normalize_resource_path(value)
    except Exception:  # noqa: BLE001 - любая ошибка означает «небезопасно»
        return False
    return True
