"""Типизированные модели редактора VPK."""

from dataclasses import dataclass
from pathlib import Path, PurePosixPath

COMPILED_SOUND_SUFFIXES = frozenset({".vsnd_c"})
AUDIO_SOURCE_SUFFIXES = frozenset({".wav", ".mp3", ".ogg", ".flac"})


@dataclass(frozen=True, slots=True)
class ResourceInfo:
    """Ресурс внутри VPK.

    ``size`` — полный размер (preload-часть + данные во внешнем архиве).
    ``crc32`` — контрольная сумма из индекса VPK.
    """

    path: str
    size: int
    crc32: int
    archive_index: int = 0x7FFF
    preload_length: int = 0

    @property
    def suffix(self) -> str:
        """Расширение в нижнем регистре (``.vsnd_c``)."""
        suffix = PurePosixPath(self.path).suffix
        return suffix.lower()

    @property
    def is_compiled_sound(self) -> bool:
        """Является ли ресурс скомпилированным звуком Source 2."""
        return self.suffix in COMPILED_SOUND_SUFFIXES

    @property
    def is_sound(self) -> bool:
        """Звуковой ресурс: скомпилированный звук или файл из ``sounds/``."""
        return self.is_compiled_sound or "/sounds/" in f"/{self.path.lower()}/"


@dataclass(frozen=True, slots=True)
class VPKArchiveInfo:
    """Сводная информация об открытом исходном архиве."""

    path: Path
    version: int
    entry_count: int
    header_length: int
    tree_length: int
    size: int


@dataclass(frozen=True, slots=True)
class ReplacementResult:
    """Результат замены одного ресурса в рабочей копии."""

    resource_path: str
    source_path: Path
    size: int
    sha256: str
    compiled: bool


@dataclass(frozen=True, slots=True)
class BuildResult:
    """Результат сборки нового VPK."""

    output_path: Path
    version: int
    entry_count: int
    size: int
    verified: bool
