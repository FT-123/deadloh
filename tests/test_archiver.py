import json
import zipfile

import pytest

from core import archiver


@pytest.fixture
def staging(tmp_path, monkeypatch):
    target = tmp_path / "staging"
    target.mkdir()
    monkeypatch.setattr(archiver, "STAGING_DIR", target)
    monkeypatch.setattr(
        archiver,
        "analyze_vpk",
        lambda path: {"heroes": ["Haze"], "types": ["Model"]},
    )
    return target


def test_stage_vpk_creates_folder_and_meta(tmp_path, staging):
    source = tmp_path / "cool_mod.vpk"
    source.write_bytes(b"VPK-DATA")

    archiver.stage_vpk_file(source, "cool_mod")

    folder = staging / "cool_mod"
    assert (folder / "content.vpk").read_bytes() == b"VPK-DATA"
    meta = json.loads((folder / "meta.json").read_text(encoding="utf-8"))
    assert meta["name"] == "cool_mod"
    assert meta["enabled"] is True
    assert meta["tags_data"] == {"heroes": ["Haze"], "types": ["Model"]}


def test_stage_vpk_sanitizes_folder_name(tmp_path, staging):
    source = tmp_path / "mod.vpk"
    source.write_bytes(b"x")

    archiver.stage_vpk_file(source, "my:mod?name")

    assert (staging / "mymodname" / "content.vpk").exists()


def test_reimport_replaces_same_folder_and_keeps_enabled(tmp_path, staging):
    first = tmp_path / "mod.vpk"
    first.write_bytes(b"v1")
    archiver.stage_vpk_file(first, "same")

    meta_file = staging / "same" / "meta.json"
    meta = json.loads(meta_file.read_text(encoding="utf-8"))
    meta["enabled"] = False
    meta_file.write_text(json.dumps(meta), encoding="utf-8")

    second = tmp_path / "mod2.vpk"
    second.write_bytes(b"v2")
    archiver.stage_vpk_file(second, "same")

    assert sorted(p.name for p in staging.iterdir()) == ["same"]
    assert (staging / "same" / "content.vpk").read_bytes() == b"v2"
    updated = json.loads(meta_file.read_text(encoding="utf-8"))
    assert updated["enabled"] is False
    assert updated["tags_data"] == {"heroes": ["Haze"], "types": ["Model"]}


def test_stage_does_not_touch_foreign_folder(tmp_path, staging):
    foreign = staging / "same"
    foreign.mkdir()
    (foreign / "keep.txt").write_text("user data", encoding="utf-8")
    source = tmp_path / "mod.vpk"
    source.write_bytes(b"x")

    archiver.stage_vpk_file(source, "same")

    assert (foreign / "keep.txt").read_text(encoding="utf-8") == "user data"
    assert (staging / "same_1" / "content.vpk").read_bytes() == b"x"


def test_different_mod_with_same_sanitized_name_gets_suffix(tmp_path, staging):
    first = tmp_path / "first.vpk"
    first.write_bytes(b"v1")
    archiver.stage_vpk_file(first, "same")

    second = tmp_path / "second.vpk"
    second.write_bytes(b"v2")
    archiver.stage_vpk_file(second, "same?")

    assert (staging / "same" / "content.vpk").read_bytes() == b"v1"
    assert (staging / "same_1" / "content.vpk").read_bytes() == b"v2"
    assert (staging / "same_1" / "meta.json").exists()


def test_empty_sanitized_name_falls_back_to_mod(tmp_path, staging):
    source = tmp_path / "mod.vpk"
    source.write_bytes(b"x")

    archiver.stage_vpk_file(source, "???")

    assert (staging / "mod" / "content.vpk").exists()


def test_zip_with_two_vpks_stages_both(tmp_path, staging):
    archive = tmp_path / "pack.zip"
    with zipfile.ZipFile(archive, "w") as z:
        z.writestr("one/a.vpk", b"a")
        z.writestr("two/b.vpk", b"b")

    count, err = archiver.extract_and_stage_archive(archive)

    assert (count, err) == (2, None)
    assert len(list(staging.iterdir())) == 2


def test_zip_without_vpk_returns_zero(tmp_path, staging):
    archive = tmp_path / "empty.zip"
    with zipfile.ZipFile(archive, "w") as z:
        z.writestr("readme.txt", b"no mods")

    count, err = archiver.extract_and_stage_archive(archive)

    assert (count, err) == (0, None)
    assert not list(staging.iterdir())


def test_corrupt_zip_returns_error_text(tmp_path, staging):
    archive = tmp_path / "broken.zip"
    archive.write_bytes(b"not a zip at all")

    count, err = archiver.extract_and_stage_archive(archive)

    assert count == 0
    assert err and "Ошибка распаковки" in err


def test_7z_without_py7zr_returns_actionable_error(tmp_path, staging, monkeypatch):
    monkeypatch.setattr(archiver, "py7zr", None)
    archive = tmp_path / "mod.7z"
    archive.write_bytes(b"7z")

    count, err = archiver.extract_and_stage_archive(archive)

    assert count == 0
    assert err and "py7zr" in err


def test_unsupported_extension_returns_error(tmp_path, staging):
    archive = tmp_path / "mod.txt"
    archive.write_text("nope", encoding="utf-8")

    count, err = archiver.extract_and_stage_archive(archive)

    assert count == 0
    assert err and "не поддерживается" in err


def test_rar_without_unpackers_returns_error(tmp_path, staging, monkeypatch):
    monkeypatch.setattr(archiver, "rarfile", None)

    def no_tar(*args, **kwargs):
        raise FileNotFoundError("tar is missing")

    monkeypatch.setattr(archiver.subprocess, "run", no_tar)
    archive = tmp_path / "mod.rar"
    archive.write_bytes(b"rar")

    count, err = archiver.extract_and_stage_archive(archive)

    assert count == 0
    assert err and "Не удалось распаковать .rar" in err
