"""Тесты подготовки звуковой замены (без фиктивной компиляции Source 2)."""

from pathlib import Path

import pytest

pytest.importorskip("vpk")

from vpk_editor_fixtures import make_vpk

from core.vpk_editor.errors import (
    CompilationUnavailableError,
    UnsupportedResourceError,
)
from core.vpk_editor.resource_compiler import UnavailableResourceCompiler
from core.vpk_editor.sound_replacer import SoundReplacer
from core.vpk_editor.vpk_reader import VPKReader

SOUND = "sounds/hero/haze/ability3_cast.vsnd_c"
FILES = {
    SOUND: b"ORIGINAL-SOUND",
    "materials/hero/haze.vmat_c": b"MATERIAL",
}


class _FakeCompiler:
    """Тестовый mock компилятора. Не является поддержкой реальной компиляции."""

    def __init__(self):
        self.calls = []

    def compile_sound(self, source: Path, resource_path: str) -> bytes:
        self.calls.append((source, resource_path))
        return b"FAKE-COMPILED-VSND"


@pytest.fixture
def target(tmp_path):
    reader = VPKReader(make_vpk(tmp_path, FILES, version=2))
    return reader.get(SOUND)


def test_compiled_source_is_copied_verbatim(tmp_path, target):
    source = tmp_path / "custom.vsnd_c"
    source.write_bytes(b"CUSTOM-COMPILED")

    prepared = SoundReplacer(UnavailableResourceCompiler()).prepare(target, source)

    assert prepared.data == b"CUSTOM-COMPILED"
    assert prepared.compiled is False
    assert prepared.sha256


def test_wav_without_compiler_is_rejected(tmp_path, target):
    source = tmp_path / "custom.wav"
    source.write_bytes(b"RIFF....WAVE")

    with pytest.raises(CompilationUnavailableError, match="resourcecompiler"):
        SoundReplacer(UnavailableResourceCompiler()).prepare(target, source)


def test_wav_with_injected_compiler(tmp_path, target):
    source = tmp_path / "custom.wav"
    source.write_bytes(b"RIFF....WAVE")
    compiler = _FakeCompiler()

    prepared = SoundReplacer(compiler).prepare(target, source)

    assert prepared.data == b"FAKE-COMPILED-VSND"
    assert prepared.compiled is True
    assert compiler.calls == [(source.resolve(), SOUND)]


def test_unsupported_source_format(tmp_path, target):
    source = tmp_path / "custom.txt"
    source.write_bytes(b"text")

    with pytest.raises(UnsupportedResourceError, match="не поддерживается"):
        SoundReplacer(UnavailableResourceCompiler()).prepare(target, source)


def test_non_sound_target_is_rejected(tmp_path):
    reader = VPKReader(make_vpk(tmp_path, FILES, version=2))
    material = reader.get("materials/hero/haze.vmat_c")
    source = tmp_path / "custom.vsnd_c"
    source.write_bytes(b"DATA")

    with pytest.raises(UnsupportedResourceError, match="vsnd_c"):
        SoundReplacer(UnavailableResourceCompiler()).prepare(material, source)


def test_missing_source_file(target, tmp_path):
    with pytest.raises(UnsupportedResourceError, match="не найден"):
        SoundReplacer(UnavailableResourceCompiler()).prepare(
            target, tmp_path / "missing.vsnd_c"
        )


def test_empty_source_file(target, tmp_path):
    source = tmp_path / "empty.vsnd_c"
    source.write_bytes(b"")

    with pytest.raises(UnsupportedResourceError, match="пуст"):
        SoundReplacer(UnavailableResourceCompiler()).prepare(target, source)
