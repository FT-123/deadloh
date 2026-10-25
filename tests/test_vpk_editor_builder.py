"""Тесты сборки VPK: v1/v2, проверка чтением, конфликты и атомарность."""


import pytest

pytest.importorskip("vpk")

import vpk
from vpk_editor_fixtures import make_vpk, write_tree

from core.vpk_editor.errors import (
    OutputConflictError,
    OutputPathError,
    SourceOverwriteError,
    UnsupportedResourceError,
    UnsupportedVPKError,
)
from core.vpk_editor.models import BuildResult
from core.vpk_editor.vpk_builder import build_vpk
from core.vpk_editor.vpk_reader import VPKReader

FILES = {
    "sounds/hero/haze/ability3_cast.vsnd_c": b"NEW-SOUND",
    "materials/hero/haze.vmat_c": b"MATERIAL",
}


@pytest.mark.parametrize("version", [1, 2])
def test_build_and_reread(tmp_path, version):
    workspace = write_tree(tmp_path / "workspace", FILES)
    output = tmp_path / f"built_v{version}.vpk"

    result = build_vpk(workspace, output, version=version)

    assert isinstance(result, BuildResult)
    assert result.verified is True
    assert result.version == version
    assert result.entry_count == 2
    assert output.is_file()

    reader = VPKReader(output)
    assert reader.info.version == version
    assert reader.read_resource("sounds/hero/haze/ability3_cast.vsnd_c") == b"NEW-SOUND"
    assert reader.read_resource("materials/hero/haze.vmat_c") == b"MATERIAL"

    external = vpk.open(str(output), read_header_only=False)
    assert set(external) == set(FILES)
    if version == 2:
        assert external.verify() is True


def test_empty_workspace_builds_header_only(tmp_path):
    workspace = tmp_path / "empty"
    workspace.mkdir()
    output = tmp_path / "empty.vpk"

    result = build_vpk(workspace, output)

    assert result.entry_count == 0
    assert VPKReader(output).info.entry_count == 0


def test_root_level_file_roundtrip(tmp_path):
    workspace = write_tree(
        tmp_path / "workspace",
        {"addoninfo.txt": b"ROOT-FILE"},
    )
    output = tmp_path / "root.vpk"

    build_vpk(workspace, output)

    assert VPKReader(output).read_resource("addoninfo.txt") == b"ROOT-FILE"


def test_existing_output_requires_overwrite(tmp_path):
    workspace = write_tree(tmp_path / "workspace", FILES)
    output = tmp_path / "out.vpk"
    output.write_bytes(b"KEEP")

    with pytest.raises(OutputConflictError):
        build_vpk(workspace, output)
    assert output.read_bytes() == b"KEEP"

    build_vpk(workspace, output, overwrite=True)
    assert output.read_bytes() != b"KEEP"


def test_source_archive_cannot_be_overwritten(tmp_path):
    source = make_vpk(tmp_path, FILES, version=2, name="mod.vpk")
    workspace = write_tree(tmp_path / "workspace", FILES)

    with pytest.raises(SourceOverwriteError):
        build_vpk(workspace, source, source_path=source)


def test_output_inside_workspace_is_rejected(tmp_path):
    workspace = write_tree(tmp_path / "workspace", FILES)

    with pytest.raises(OutputPathError):
        build_vpk(workspace, workspace / "out.vpk")


def test_missing_output_parent_is_rejected(tmp_path):
    workspace = write_tree(tmp_path / "workspace", FILES)

    with pytest.raises(OutputPathError):
        build_vpk(workspace, tmp_path / "missing" / "out.vpk")


def test_unsupported_output_version(tmp_path):
    workspace = write_tree(tmp_path / "workspace", FILES)

    with pytest.raises(UnsupportedVPKError):
        build_vpk(workspace, tmp_path / "out.vpk", version=3)


def test_extensionless_files_are_reported(tmp_path):
    workspace = write_tree(
        tmp_path / "workspace",
        {"sounds/hero/haze/ability3": b"no-extension"},
    )
    output = tmp_path / "out.vpk"

    with pytest.raises(UnsupportedResourceError, match="без расширения"):
        build_vpk(workspace, output)
    assert not output.exists()


def test_failed_build_preserves_existing_output_and_cleans_temp(tmp_path):
    workspace = write_tree(
        tmp_path / "workspace",
        {"sounds/hero/haze/ability3": b"no-extension"},
    )
    output = tmp_path / "out.vpk"
    output.write_bytes(b"ORIGINAL")

    with pytest.raises(UnsupportedResourceError):
        build_vpk(workspace, output, overwrite=True)

    assert output.read_bytes() == b"ORIGINAL"
    assert list(tmp_path.glob(".*.tmp.vpk")) == []


def test_case_collision_is_rejected(tmp_path):
    workspace = tmp_path / "workspace"
    write_tree(workspace, {"materials/Haze.vmat_c": b"A"})
    write_tree(workspace, {"materials/haze.vmat_c": b"a"})

    if len({path.name for path in (workspace / "materials").iterdir()}) == 1:
        pytest.skip("Файловая система не различает регистр")

    with pytest.raises(UnsupportedResourceError, match="регистр"):
        build_vpk(workspace, tmp_path / "out.vpk")
