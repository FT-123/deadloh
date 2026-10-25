"""Публичный API редактора VPK.

Модуль не зависит от PySide6 и может вызываться из фонового потока (QThread):
он не использует глобального состояния, не открывает QWidget и создаёт
отдельную временную папку на каждый проект.

Пример::

    from core.vpk_editor import VPKEditor

    editor = VPKEditor()
    with editor.open_vpk("mod.vpk") as project:
        sounds = project.find_ability_sounds("Haze", ability=3)
        project.replace_sound(sounds[0].path, "custom.vsnd_c")
        project.build("mod_edited.vpk")
"""

import hashlib
from pathlib import Path

from core.vpk_parser import analyze_vpk

from .errors import (
    ProjectClosedError,
    UnsupportedResourceError,
    WorkspaceError,
)
from .models import BuildResult, ReplacementResult, ResourceInfo
from .resource_compiler import ResourceCompiler, UnavailableResourceCompiler
from .resource_inspector import find_ability_sounds, find_hero_sounds
from .sound_replacer import SoundReplacer
from .vpk_builder import build_vpk
from .vpk_reader import VPKReader
from .workspace import Workspace


class VPKProject:
    """Открытый VPK: индекс, изолированная рабочая копия и сборка."""

    def __init__(
        self,
        source: str | Path,
        *,
        compiler: ResourceCompiler,
        workspace_parent: str | Path | None,
    ):
        self._reader = VPKReader(source)
        self._compiler = compiler
        self._workspace_parent = workspace_parent
        self._workspace: Workspace | None = None
        self._sound_replacer = SoundReplacer(compiler)
        self._modifications: dict[str, ReplacementResult] = {}
        self._tags: dict | None = None
        self._closed = False

    @property
    def source_path(self) -> Path:
        """Путь к исходному VPK (только чтение)."""
        return self._reader.source_path

    @property
    def info(self):
        """Сводная информация об исходном архиве."""
        return self._reader.info

    @property
    def workspace_path(self) -> Path | None:
        """Временная папка проекта или ``None``, пока она не создана."""
        return self._workspace.root if self._workspace is not None else None

    @property
    def modifications(self) -> tuple[ReplacementResult, ...]:
        """Уже выполненные замены (в порядке вызова)."""
        return tuple(self._modifications.values())

    @property
    def tags(self) -> dict:
        """Теги существующего ``analyze_vpk`` (герои/типы), без изменения архива."""
        self._ensure_open()
        if self._tags is None:
            self._tags = analyze_vpk(self.source_path)
        return dict(self._tags)

    def list_resources(
        self,
        *,
        prefix: str | None = None,
        suffix: str | None = None,
        sounds_only: bool = False,
    ) -> list[ResourceInfo]:
        """Список ресурсов с необязательными фильтрами по пути и расширению."""
        self._ensure_open()
        resources = self._reader.list_resources()

        if prefix is not None:
            normalized = prefix.replace("\\", "/").strip().strip("/").casefold()
            resources = [
                resource
                for resource in resources
                if resource.path.casefold().startswith(normalized)
            ]
        if suffix is not None:
            normalized_suffix = suffix.lower()
            if not normalized_suffix.startswith("."):
                normalized_suffix = f".{normalized_suffix}"
            resources = [
                resource
                for resource in resources
                if resource.suffix == normalized_suffix
            ]
        if sounds_only:
            resources = [resource for resource in resources if resource.is_sound]
        return resources

    def find_hero_sounds(self, hero: str) -> list[ResourceInfo]:
        """Все звуковые ресурсы героя (по эвристике путей)."""
        self._ensure_open()
        return find_hero_sounds(self._reader.list_resources(), hero)

    def find_ability_sounds(
        self, hero: str, ability: int = 3
    ) -> list[ResourceInfo]:
        """Звуковые ресурсы героя, похожие на ресурсы указанной способности."""
        self._ensure_open()
        return find_ability_sounds(
            self._reader.list_resources(), hero, ability=ability
        )

    def read_resource(self, resource_path: str) -> bytes:
        """Читает ресурс исходного VPK (с проверкой CRC32)."""
        self._ensure_open()
        return self._reader.read_resource(resource_path)

    def replace_sound(
        self,
        target_resource: str,
        source_audio: str | Path,
    ) -> ReplacementResult:
        """Заменяет существующий ``.vsnd_c`` в рабочей копии.

        Исходный VPK не меняется. Если ``source_audio`` — WAV/MP3/OGG/FLAC,
        требуется переданный в :class:`VPKEditor` компилятор Source 2.
        """
        self._ensure_open()
        target = self._reader.get(target_resource)
        prepared = self._sound_replacer.prepare(target, source_audio)
        self._ensure_workspace()
        workspace = self._require_workspace()
        workspace.write_bytes(prepared.resource_path, prepared.data)
        result = ReplacementResult(
            resource_path=prepared.resource_path,
            source_path=prepared.source_path,
            size=len(prepared.data),
            sha256=prepared.sha256,
            compiled=prepared.compiled,
        )
        self._modifications[prepared.resource_path] = result
        return result

    def replace_resource(
        self,
        target_resource: str,
        source_file: str | Path,
    ) -> ReplacementResult:
        """Заменяет любой существующий ресурс байтами из файла.

        Это низкоуровневая операция: содержимое не интерпретируется и не
        компилируется. Для звуков предпочтите :meth:`replace_sound`.
        """
        self._ensure_open()
        target = self._reader.get(target_resource)
        source = Path(source_file).expanduser()
        if not source.exists():
            raise UnsupportedResourceError(f"Файл замены не найден: {source}")
        if not source.is_file():
            raise UnsupportedResourceError(f"Источник не является файлом: {source}")
        try:
            data = source.read_bytes()
        except OSError as exc:
            raise UnsupportedResourceError(
                f"Не удалось прочитать файл «{source}»: {exc}"
            ) from exc
        if not data:
            raise UnsupportedResourceError(f"Файл замены пуст: {source}")

        self._ensure_workspace()
        workspace = self._require_workspace()
        workspace.write_bytes(target.path, data)
        result = ReplacementResult(
            resource_path=target.path,
            source_path=source.resolve(),
            size=len(data),
            sha256=hashlib.sha256(data).hexdigest(),
            compiled=False,
        )
        self._modifications[target.path] = result
        return result

    def build(
        self,
        output_path: str | Path,
        *,
        overwrite: bool = False,
        version: int = 2,
    ) -> BuildResult:
        """Собирает новый VPK, никогда не перезаписывая исходный."""
        self._ensure_open()
        self._ensure_workspace()
        return build_vpk(
            self._require_workspace().root,
            output_path,
            source_path=self._reader.source_path,
            version=version,
            overwrite=overwrite,
        )

    def _require_workspace(self) -> Workspace:
        if self._workspace is None:
            raise WorkspaceError("Рабочая папка проекта не создана")
        return self._workspace

    def _ensure_workspace(self) -> None:
        if self._workspace is not None:
            return
        workspace = Workspace(self._workspace_parent)
        try:
            for resource in self._reader.list_resources():
                workspace.write_chunks(
                    resource.path, self._reader.iter_chunks(resource.path)
                )
        except Exception:
            workspace.cleanup()
            raise
        self._workspace = workspace

    def _ensure_open(self) -> None:
        if self._closed:
            raise ProjectClosedError("Проект VPK уже закрыт")

    def close(self) -> None:
        """Удаляет временную копию; исходный архив не затрагивается."""
        if self._closed:
            return
        self._closed = True
        if self._workspace is not None:
            self._workspace.cleanup()
            self._workspace = None

    def __enter__(self) -> "VPKProject":  # noqa: PYI034 - Self требует Python 3.11
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        self.close()

    def __del__(self) -> None:  # pragma: no cover - страховка от утечки temp
        try:
            self.close()
        except Exception:  # noqa: BLE001, S110 - во время shutdown уже неважно
            pass


class VPKEditor:
    """Точка входа редактора VPK."""

    def __init__(
        self,
        *,
        compiler: ResourceCompiler | None = None,
        workspace_parent: str | Path | None = None,
    ):
        self._compiler = compiler or UnavailableResourceCompiler()
        self._workspace_parent = (
            Path(workspace_parent) if workspace_parent is not None else None
        )

    @property
    def compiler(self) -> ResourceCompiler:
        """Компилятор, который будет использован для аудиоформатов."""
        return self._compiler

    def open_vpk(self, source: str | Path) -> VPKProject:
        """Открывает VPK для чтения и редактирования.

        Создаёт только индекс; временная рабочая копия появляется при первой
        операции, которой она нужна.
        """
        return VPKProject(
            source,
            compiler=self._compiler,
            workspace_parent=self._workspace_parent,
        )
