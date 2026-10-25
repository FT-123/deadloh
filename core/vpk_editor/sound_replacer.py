"""Подготовка замены звукового ресурса.

Поддерживается два реальных сценария:

* источник уже скомпилирован (``.vsnd_c``) — байты заменяются напрямую;
* источник — обычный аудиофайл (``.wav``/``.mp3``/``.ogg``/``.flac``), и
  вызывающий код передал рабочий :class:`ResourceCompiler`.

Без компилятора ``.wav`` не превращается в ``.vsnd_c``: выбрасывается
:class:`CompilationUnavailableError`, никакой фиктивной компиляции нет.
"""

import hashlib
from dataclasses import dataclass
from pathlib import Path

from .errors import UnsupportedResourceError
from .models import AUDIO_SOURCE_SUFFIXES, ResourceInfo
from .resource_compiler import ResourceCompiler


@dataclass(frozen=True, slots=True)
class PreparedSound:
    """Байты будущего ресурса до записи в workspace."""

    resource_path: str
    source_path: Path
    data: bytes
    compiled: bool

    @property
    def sha256(self) -> str:
        return hashlib.sha256(self.data).hexdigest()


class SoundReplacer:
    """Готовит байты замены для одного звукового ресурса."""

    def __init__(self, compiler: ResourceCompiler):
        self._compiler = compiler

    def prepare(self, target: ResourceInfo, source_audio: str | Path) -> PreparedSound:
        if not target.is_compiled_sound:
            raise UnsupportedResourceError(
                "Заменять можно только скомпилированные звуки .vsnd_c; "
                f"выбран ресурс «{target.path}»"
            )

        source = Path(source_audio).expanduser()
        if not source.exists():
            raise UnsupportedResourceError(f"Аудиофайл не найден: {source}")
        if not source.is_file():
            raise UnsupportedResourceError(f"Источник не является файлом: {source}")
        source = source.resolve()

        suffix = source.suffix.lower()
        if suffix == ".vsnd_c":
            try:
                data = source.read_bytes()
            except OSError as exc:
                raise UnsupportedResourceError(
                    f"Не удалось прочитать звук «{source.name}»: {exc}"
                ) from exc
            if not data:
                raise UnsupportedResourceError(f"Файл звука пуст: {source.name}")
            return PreparedSound(
                resource_path=target.path,
                source_path=source,
                data=data,
                compiled=False,
            )

        if suffix in AUDIO_SOURCE_SUFFIXES:
            data = self._compiler.compile_sound(source, target.path)
            if not isinstance(data, bytes) or not data:
                raise UnsupportedResourceError(
                    "Компилятор вернул пустой или неверный результат для "
                    f"«{source.name}»"
                )
            return PreparedSound(
                resource_path=target.path,
                source_path=source,
                data=data,
                compiled=True,
            )

        supported = ", ".join(sorted({".vsnd_c", *AUDIO_SOURCE_SUFFIXES}))
        raise UnsupportedResourceError(
            f"Формат «{suffix or 'без расширения'}» не поддерживается. "
            f"Доступно: {supported}"
        )
