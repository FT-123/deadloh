"""Изолированный редактор VPK для модов Deadlock (Source 2).

Публичная точка входа — :class:`VPKEditor`. Модуль не импортирует PySide6,
не изменяет исходные VPK, gameinfo.gi или состояние деплоя ModManager.
"""

from .api import VPKEditor, VPKProject
from .errors import (
    BuildVerificationError,
    CompilationUnavailableError,
    InvalidSourceError,
    OutputConflictError,
    OutputPathError,
    PackageWriteError,
    ProjectClosedError,
    ResourceNotFoundError,
    SourceOverwriteError,
    UnknownHeroError,
    UnsafeResourcePathError,
    UnsupportedResourceError,
    UnsupportedVPKError,
    VPKDependencyError,
    VPKEditorError,
    VPKSyntaxError,
    WorkspaceError,
)
from .models import (
    AUDIO_SOURCE_SUFFIXES,
    BuildResult,
    ReplacementResult,
    ResourceInfo,
    VPKArchiveInfo,
)
from .resource_compiler import ResourceCompiler, UnavailableResourceCompiler
from .vpk_reader import VPKReader

__all__ = [
    "AUDIO_SOURCE_SUFFIXES",
    "BuildResult",
    "BuildVerificationError",
    "CompilationUnavailableError",
    "InvalidSourceError",
    "OutputConflictError",
    "OutputPathError",
    "PackageWriteError",
    "ProjectClosedError",
    "ReplacementResult",
    "ResourceCompiler",
    "ResourceInfo",
    "ResourceNotFoundError",
    "SourceOverwriteError",
    "UnavailableResourceCompiler",
    "UnknownHeroError",
    "UnsafeResourcePathError",
    "UnsupportedResourceError",
    "UnsupportedVPKError",
    "VPKArchiveInfo",
    "VPKDependencyError",
    "VPKEditor",
    "VPKEditorError",
    "VPKProject",
    "VPKReader",
    "VPKSyntaxError",
    "WorkspaceError",
]

__version__ = "1.0.0"
