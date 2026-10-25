"""Чтение структуры VPK и доступ к ресурсам.

Используется уже существующая зависимость ``vpk`` (ValvePython/vpk 1.4.0):
она читает VPK v1 и v2, включая preload-данные и внешние архивы. Поверх неё
добавлены проверки путей, контроль CRC32 и единый потокобезопасный интерфейс
без открытых дескрипторов файлов.
"""

from binascii import crc32
from collections.abc import Iterator
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
from typing import BinaryIO

from .errors import (
    InvalidSourceError,
    ResourceNotFoundError,
    UnsafeResourcePathError,
    UnsupportedVPKError,
    VPKDependencyError,
    VPKSyntaxError,
)
from .models import ResourceInfo, VPKArchiveInfo
from .pathing import normalize_resource_path

try:
    import vpk
except ImportError:  # pragma: no cover - зависит от окружения
    vpk = None

DEFAULT_CHUNK_SIZE = 1024 * 1024
EMBEDDED_ARCHIVE_INDEX = 0x7FFF


@dataclass(frozen=True, slots=True)
class _Entry:
    info: ResourceInfo
    preload: bytes
    offset: int
    file_length: int
    external: bool


class VPKReader:
    """Индекс одного VPK и потоковое чтение его ресурсов."""

    def __init__(self, source: str | Path):
        if vpk is None:
            raise VPKDependencyError(
                "Для работы редактора VPK нужна библиотека 'vpk' (pip install vpk)"
            )

        raw_source = Path(source).expanduser()
        if not raw_source.exists():
            raise InvalidSourceError(f"VPK не найден: {raw_source}")
        if not raw_source.is_file():
            raise InvalidSourceError(f"Путь не является файлом VPK: {raw_source}")
        self.source_path = raw_source.resolve()
        self._entries: dict[str, _Entry] = {}
        self._by_key: dict[str, str] = {}
        self._external_cache: dict[int, Path] = {}

        try:
            pak = vpk.open(str(self.source_path), read_header_only=False)
            version = int(pak.version)
            if version not in (1, 2):
                raise UnsupportedVPKError(
                    f"Поддерживаются VPK v1 и v2, найден v{version}"
                )
            raw_entries = list(pak.read_index_iter())
            header_length = int(pak.header_length)
            tree_length = int(pak.tree_length)
        except UnsupportedVPKError:
            raise
        except Exception as exc:  # библиотека бросает разные типы
            raise VPKSyntaxError(
                f"Не удалось прочитать VPK «{self.source_path.name}»: {exc}"
            ) from exc

        for raw_path, metadata in raw_entries:
            preload, checksum, preload_length, archive_index, offset, file_length = metadata
            try:
                path = normalize_resource_path(str(raw_path))
            except Exception as exc:  # путь из недоверенного архива
                raise UnsafeResourcePathError(
                    f"Небезопасный путь ресурса в VPK: {raw_path!r}: {exc}"
                ) from exc

            key = path.casefold()
            if key in self._by_key:
                raise VPKSyntaxError(
                    f"VPK содержит ресурсы, различающиеся только регистром: "
                    f"{path!r} и {self._by_key[key]!r}"
                )

            preload_bytes = bytes(preload or b"")
            if preload_length != len(preload_bytes):
                raise VPKSyntaxError(
                    f"Некорректная preload-часть ресурса {path!r}: "
                    f"заявлено {preload_length}, получено {len(preload_bytes)}"
                )

            external = archive_index != EMBEDDED_ARCHIVE_INDEX
            info = ResourceInfo(
                path=path,
                size=preload_length + int(file_length),
                crc32=int(checksum),
                archive_index=int(archive_index),
                preload_length=preload_length,
            )
            self._by_key[key] = path
            self._entries[path] = _Entry(
                info=info,
                preload=preload_bytes,
                offset=int(offset),
                file_length=int(file_length),
                external=external,
            )

        self.info = VPKArchiveInfo(
            path=self.source_path,
            version=version,
            entry_count=len(self._entries),
            header_length=header_length,
            tree_length=tree_length,
            size=self.source_path.stat().st_size,
        )

    def __len__(self) -> int:
        return len(self._entries)

    def list_resources(self) -> list[ResourceInfo]:
        """Все ресурсы, отсортированные по пути."""
        return [self._entries[path].info for path in sorted(self._entries)]

    def get(self, resource_path: str) -> ResourceInfo:
        """Метаданные ресурса; регистр пути не важен."""
        return self._entry(resource_path).info

    def _entry(self, resource_path: str) -> _Entry:
        try:
            normalized = normalize_resource_path(resource_path)
        except Exception as exc:  # нормализация уже даёт доменную ошибку
            raise ResourceNotFoundError(
                f"Некорректный путь ресурса {resource_path!r}: {exc}"
            ) from exc
        exact = self._entries.get(normalized)
        if exact is not None:
            return exact
        fallback = self._by_key.get(normalized.casefold())
        if fallback is None:
            raise ResourceNotFoundError(
                f"Ресурс отсутствует в VPK: {resource_path!r}"
            )
        return self._entries[fallback]

    def _external_archive_path(self, archive_index: int) -> Path:
        cached = self._external_cache.get(archive_index)
        if cached is not None:
            return cached

        stem = self.source_path.stem
        base = stem[:-4] if stem.lower().endswith("_dir") else stem
        candidates = [self.source_path.with_name(f"{base}_{archive_index:03d}.vpk")]
        if base != stem:
            candidates.append(
                self.source_path.with_name(f"{stem}_{archive_index:03d}.vpk")
            )
        for candidate in candidates:
            if candidate.is_file():
                resolved = candidate.resolve()
                self._external_cache[archive_index] = resolved
                return resolved
        raise UnsupportedVPKError(
            f"VPK использует внешний архив №{archive_index}, но файл не найден: "
            + ", ".join(candidate.name for candidate in candidates)
        )

    def iter_chunks(
        self,
        resource_path: str,
        chunk_size: int = DEFAULT_CHUNK_SIZE,
    ) -> Iterator[bytes]:
        """Потоково отдаёт содержимое ресурса и проверяет CRC32 в конце."""
        if chunk_size <= 0:
            raise ValueError("chunk_size должен быть положительным")
        entry = self._entry(resource_path)
        checksum = 0

        for start in range(0, len(entry.preload), chunk_size):
            chunk = entry.preload[start : start + chunk_size]
            checksum = crc32(chunk, checksum)
            yield chunk

        if entry.file_length:
            data_path = (
                self._external_archive_path(entry.info.archive_index)
                if entry.external
                else self.source_path
            )
            try:
                with data_path.open("rb") as handle:
                    handle.seek(entry.offset)
                    remaining = entry.file_length
                    while remaining > 0:
                        chunk = handle.read(min(chunk_size, remaining))
                        if not chunk:
                            raise VPKSyntaxError(
                                f"Данные ресурса {entry.info.path!r} обрезаны: "
                                f"не хватает {remaining} байт"
                            )
                        remaining -= len(chunk)
                        checksum = crc32(chunk, checksum)
                        yield chunk
            except OSError as exc:
                raise VPKSyntaxError(
                    f"Не удалось прочитать данные ресурса «{entry.info.path}»: {exc}"
                ) from exc

        if (checksum & 0xFFFFFFFF) != entry.info.crc32:
            raise VPKSyntaxError(
                f"CRC32 не совпала для ресурса «{entry.info.path}»: "
                f"ожидалось {entry.info.crc32:08x}, получено {checksum & 0xFFFFFFFF:08x}"
            )

    def copy_resource(
        self,
        resource_path: str,
        destination: BinaryIO,
        chunk_size: int = DEFAULT_CHUNK_SIZE,
    ) -> int:
        """Копирует ресурс в открытый бинарный поток, возвращает число байт."""
        written = 0
        for chunk in self.iter_chunks(resource_path, chunk_size=chunk_size):
            destination.write(chunk)
            written += len(chunk)
        return written

    def read_resource(
        self,
        resource_path: str,
        chunk_size: int = DEFAULT_CHUNK_SIZE,
    ) -> bytes:
        """Читает ресурс целиком в память (для небольших файлов)."""
        buffer = BytesIO()
        self.copy_resource(resource_path, buffer, chunk_size=chunk_size)
        return buffer.getvalue()
