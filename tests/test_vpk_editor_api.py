"""Интеграционные тесты публичного API редактора VPK."""

import hashlib
from pathlib import Path

import pytest

pytest.importorskip("vpk")

from vpk_editor_fixtures import make_manual_v1, make_vpk

from core.vpk_editor import (
    CompilationUnavailableError,
    InvalidSourceError,
    OutputConflictError,
    ProjectClosedError,
    ResourceNotFoundError,
    SourceOverwriteError,
    UnknownHeroError,
    UnsafeResourcePathError,
    UnsupportedResourceError,
    VPKEditor,
    VPKSyntaxError,
)

HAZE_SOUND = "sounds/hero/haze/ability3_cast.vsnd_c"
FILES = {
    HAZE_SOUND: b"HAZE-ABILITY-3",
    "sounds/hero/haze/ability2_cast.vsnd_c": b"HAZE-ABILITY-2",
    "sounds/hero/haze/vo/line.vsnd_c": b"VOICE",
    "sounds/hero/archer/ability3_cast.vsnd_c": b"GREY-TALON-ABILITY-3",
    "sounds/hero/geist/ability3_cast.vsnd_c": b"GEIST-ABILITY-3",
    "materials/hero/haze.vmat_c": b"MATERIAL",
}


@pytest.fixture
def source(tmp_path):
    return make_vpk(tmp_path, FILES, version=2, name="haze_mod.vpk")


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_editor_initialization():
    editor = VPKEditor()

    assert editor.compiler is not None


def test_open_missing_and_corrupt(tmp_path):
    editor = VPKEditor()
    with pytest.raises(InvalidSourceError):
        editor.open_vpk(tmp_path / "missing.vpk")

    broken = tmp_path / "broken.vpk"
    broken.write_bytes(b"broken")
    with pytest.raises(VPKSyntaxError):
        editor.open_vpk(broken)


def test_list_resources_and_filters(source):
    with VPKEditor().open_vpk(source) as project:
        assert len(project.list_resources()) == len(FILES)
        assert [item.path for item in project.list_resources(suffix="vsnd_c")] == sorted(
            path for path in FILES if path.endswith(".vsnd_c")
        )
        assert {item.path for item in project.list_resources(prefix="sounds/hero/haze")} == {
            HAZE_SOUND,
            "sounds/hero/haze/ability2_cast.vsnd_c",
            "sounds/hero/haze/vo/line.vsnd_c",
        }
        assert any(
            item.path == "materials/hero/haze.vmat_c"
            for item in project.list_resources(sounds_only=False)
        )
        assert all(
            item.is_sound for item in project.list_resources(sounds_only=True)
        )


def test_hero_and_ability_search(source):
    with VPKEditor().open_vpk(source) as project:
        assert [item.path for item in project.find_ability_sounds("Haze")] == [HAZE_SOUND]
        assert [
            item.path for item in project.find_ability_sounds("grey_talon", ability=3)
        ] == ["sounds/hero/archer/ability3_cast.vsnd_c"]
        assert [
            item.path for item in project.find_ability_sounds("Lady Geist", ability=3)
        ] == ["sounds/hero/geist/ability3_cast.vsnd_c"]
        assert [item.path for item in project.find_hero_sounds("Haze")] == [
            "sounds/hero/haze/ability2_cast.vsnd_c",
            HAZE_SOUND,
            "sounds/hero/haze/vo/line.vsnd_c",
        ]

        with pytest.raises(UnknownHeroError):
            project.find_ability_sounds("Not-A-Hero")


def test_tags_reuse_existing_analyzer(source):
    with VPKEditor().open_vpk(source) as project:
        tags = project.tags

    assert "Haze" in tags["heroes"]
    assert "Sound" in tags["types"]


def test_replace_build_and_preserve_source(tmp_path, source):
    before = _sha256(source)
    replacement = tmp_path / "replacement.vsnd_c"
    replacement.write_bytes(b"REPLACED-SOUND")
    output = tmp_path / "edited.vpk"

    with VPKEditor().open_vpk(source) as project:
        assert project.workspace_path is None
        result = project.replace_sound(HAZE_SOUND, replacement)
        assert result.compiled is False
        assert result.resource_path == HAZE_SOUND
        assert result.size == len(b"REPLACED-SOUND")
        assert project.workspace_path is not None
        assert project.workspace_path.is_dir()

        built = project.build(output)
        assert built.verified is True
        assert built.entry_count == len(FILES)

    assert _sha256(source) == before
    assert output.is_file()

    with VPKEditor().open_vpk(output) as edited:
        assert edited.read_resource(HAZE_SOUND) == b"REPLACED-SOUND"
        assert edited.read_resource("sounds/hero/haze/ability2_cast.vsnd_c") == (
            b"HAZE-ABILITY-2"
        )


def test_replace_sound_without_compiler_keeps_workspace_absent(tmp_path, source):
    wave = tmp_path / "custom.wav"
    wave.write_bytes(b"RIFF....WAVE")

    with VPKEditor().open_vpk(source) as project:
        with pytest.raises(CompilationUnavailableError):
            project.replace_sound(HAZE_SOUND, wave)
        assert project.workspace_path is None


def test_replace_sound_with_mock_compiler(tmp_path, source):
    class FakeCompiler:
        def compile_sound(self, source_path: Path, resource_path: str) -> bytes:
            assert resource_path == HAZE_SOUND
            return b"MOCK-COMPILED"

    wave = tmp_path / "custom.wav"
    wave.write_bytes(b"RIFF....WAVE")
    output = tmp_path / "compiled.vpk"

    editor = VPKEditor(compiler=FakeCompiler())
    with editor.open_vpk(source) as project:
        result = project.replace_sound(HAZE_SOUND, wave)
        assert result.compiled is True
        project.build(output)

    with VPKEditor().open_vpk(output) as edited:
        assert edited.read_resource(HAZE_SOUND) == b"MOCK-COMPILED"


def test_generic_resource_replacement(tmp_path, source):
    material = tmp_path / "custom.vmat_c"
    material.write_bytes(b"NEW-MATERIAL")
    output = tmp_path / "material.vpk"

    with VPKEditor().open_vpk(source) as project:
        project.replace_resource("materials/hero/haze.vmat_c", material)
        project.build(output)

    with VPKEditor().open_vpk(output) as edited:
        assert edited.read_resource("materials/hero/haze.vmat_c") == b"NEW-MATERIAL"


def test_missing_target_and_bad_source(tmp_path, source):
    replacement = tmp_path / "replacement.vsnd_c"
    replacement.write_bytes(b"X")

    with VPKEditor().open_vpk(source) as project:
        with pytest.raises(ResourceNotFoundError):
            project.replace_sound("sounds/hero/haze/missing.vsnd_c", replacement)
        with pytest.raises(UnsupportedResourceError):
            project.replace_sound(HAZE_SOUND, tmp_path / "missing.vsnd_c")


def test_build_conflict_and_source_overwrite(tmp_path, source):
    output = tmp_path / "existing.vpk"
    output.write_bytes(b"KEEP")

    with VPKEditor().open_vpk(source) as project:
        with pytest.raises(OutputConflictError):
            project.build(output)
        assert output.read_bytes() == b"KEEP"

        with pytest.raises(SourceOverwriteError):
            project.build(source)

        project.build(output, overwrite=True)

    assert output.read_bytes() != b"KEEP"


def test_close_removes_workspace_and_blocks_operations(tmp_path, source):
    replacement = tmp_path / "replacement.vsnd_c"
    replacement.write_bytes(b"X")
    project = VPKEditor().open_vpk(source)
    project.replace_sound(HAZE_SOUND, replacement)
    workspace = project.workspace_path
    assert workspace is not None and workspace.exists()

    project.close()

    assert not workspace.exists()
    with pytest.raises(ProjectClosedError):
        project.list_resources()
    with pytest.raises(ProjectClosedError):
        project.build(tmp_path / "out.vpk")
    project.close()  # идемпотентно


def test_context_manager_closes_project(tmp_path, source):
    replacement = tmp_path / "replacement.vsnd_c"
    replacement.write_bytes(b"X")

    with VPKEditor().open_vpk(source) as project:
        project.replace_sound(HAZE_SOUND, replacement)
        workspace = project.workspace_path

    assert workspace is not None and not workspace.exists()


def test_open_rejects_unsafe_archive_entries(tmp_path):
    malicious = make_manual_v1(
        tmp_path / "malicious.vpk",
        {"../escape.txt": b"evil"},
    )
    outside = tmp_path.parent / "escape.txt"
    if outside.exists():
        outside.unlink()

    with pytest.raises(UnsafeResourcePathError):
        VPKEditor().open_vpk(malicious)

    assert not outside.exists()


def test_failed_build_preserves_source_and_output(tmp_path, source):
    before = _sha256(source)
    replacement = tmp_path / "replacement.vsnd_c"
    replacement.write_bytes(b"NEW")
    output = tmp_path / "existing.vpk"
    output.write_bytes(b"ORIGINAL")

    with VPKEditor().open_vpk(source) as project:
        project.replace_sound(HAZE_SOUND, replacement)
        workspace = project.workspace_path
        assert workspace is not None
        (workspace / "broken-extensionless").write_bytes(b"bad")

        with pytest.raises(UnsupportedResourceError):
            project.build(output, overwrite=True)

    assert output.read_bytes() == b"ORIGINAL"
    assert _sha256(source) == before
