"""Тесты чтения VPK: v1/v2, split-архивы, preload, CRC и ошибки."""

import pytest

pytest.importorskip("vpk")

from vpk_editor_fixtures import make_manual_v1, make_vpk

from core.vpk_editor.errors import (
    InvalidSourceError,
    ResourceNotFoundError,
    UnsafeResourcePathError,
    UnsupportedVPKError,
    VPKSyntaxError,
)
from core.vpk_editor.vpk_reader import VPKReader

FILES = {
    "sounds/hero/haze/ability3_cast.vsnd_c": b"SOUND-DATA",
    "materials/hero/haze.vmat_c": b"MATERIAL",
}


def test_missing_and_directory_sources(tmp_path):
    with pytest.raises(InvalidSourceError):
        VPKReader(tmp_path / "missing.vpk")
    with pytest.raises(InvalidSourceError):
        VPKReader(tmp_path)


def test_reads_manual_v1(tmp_path):
    source = make_manual_v1(tmp_path / "mod.vpk", FILES)

    reader = VPKReader(source)

    assert reader.info.version == 1
    assert reader.info.entry_count == 2
    assert [item.path for item in reader.list_resources()] == [
        "materials/hero/haze.vmat_c",
        "sounds/hero/haze/ability3_cast.vsnd_c",
    ]
    assert reader.read_resource("sounds/hero/haze/ability3_cast.vsnd_c") == b"SOUND-DATA"
    assert reader.get("sounds/hero/haze/ability3_cast.vsnd_c").suffix == ".vsnd_c"


@pytest.mark.parametrize("version", [1, 2])
def test_reads_written_archives(tmp_path, version):
    source = make_vpk(tmp_path, FILES, version=version, name=f"mod{version}.vpk")

    reader = VPKReader(source)

    assert reader.info.version == version
    assert reader.info.entry_count == 2
    assert reader.read_resource("materials/hero/haze.vmat_c") == b"MATERIAL"


def test_reads_preload_bytes(tmp_path):
    source = make_manual_v1(
        tmp_path / "mod.vpk",
        {"sounds/hero/haze/ability3_cast.vsnd_c": b"TAIL"},
        preloads={"sounds/hero/haze/ability3_cast.vsnd_c": b"HEAD-"},
    )

    reader = VPKReader(source)

    assert reader.read_resource("sounds/hero/haze/ability3_cast.vsnd_c") == b"HEAD-TAIL"


def test_reads_split_archive(tmp_path):
    source = make_manual_v1(
        tmp_path / "pak01_dir.vpk", FILES, archive_index=0
    )

    reader = VPKReader(source)

    assert reader.read_resource("materials/hero/haze.vmat_c") == b"MATERIAL"
    assert (tmp_path / "pak01_000.vpk").is_file()


def test_missing_split_archive_is_reported(tmp_path):
    source = make_manual_v1(
        tmp_path / "pak01_dir.vpk", FILES, archive_index=0
    )
    (tmp_path / "pak01_000.vpk").unlink()

    reader = VPKReader(source)

    with pytest.raises(UnsupportedVPKError, match="внешний архив"):
        reader.read_resource("materials/hero/haze.vmat_c")


def test_corrupt_magic_and_truncated_header(tmp_path):
    bad = tmp_path / "bad.vpk"
    bad.write_bytes(b"not a vpk at all")
    with pytest.raises(VPKSyntaxError):
        VPKReader(bad)

    truncated = tmp_path / "truncated.vpk"
    truncated.write_bytes(b"\x34\x12\xaa\x55\x01\x00\x00\x00")
    with pytest.raises(VPKSyntaxError):
        VPKReader(truncated)


def test_crc_mismatch_is_detected(tmp_path):
    source = make_manual_v1(
        tmp_path / "mod.vpk",
        {"materials/hero/haze.vmat_c": b"MATERIAL"},
    )
    payload = bytearray(source.read_bytes())
    payload[-1] ^= 0xFF
    source.write_bytes(payload)

    reader = VPKReader(source)

    with pytest.raises(VPKSyntaxError, match="CRC32"):
        reader.read_resource("materials/hero/haze.vmat_c")


def test_unsafe_archive_path_is_rejected(tmp_path):
    source = make_manual_v1(
        tmp_path / "mod.vpk",
        {"../evil.txt": b"evil"},
    )

    with pytest.raises(UnsafeResourcePathError):
        VPKReader(source)


def test_case_collision_is_rejected(tmp_path):
    source = make_manual_v1(
        tmp_path / "mod.vpk",
        {"materials/A.vmat_c": b"A", "materials/a.vmat_c": b"a"},
    )

    with pytest.raises(VPKSyntaxError, match="регистр"):
        VPKReader(source)


def test_chunked_copy(tmp_path):
    source = make_vpk(tmp_path, FILES, version=2)
    reader = VPKReader(source)

    chunks = list(reader.iter_chunks("materials/hero/haze.vmat_c", chunk_size=3))

    assert b"".join(chunks) == b"MATERIAL"
    assert all(0 < len(chunk) <= 3 for chunk in chunks)


def test_unknown_resource_raises(tmp_path):
    reader = VPKReader(make_vpk(tmp_path, FILES, version=2))

    with pytest.raises(ResourceNotFoundError, match="отсутствует"):
        reader.get("materials/hero/missing.vmat_c")
