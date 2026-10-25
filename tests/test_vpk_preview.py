"""Тесты подготовки превью ресурсов VPK (текст, классификация, выбор GLB)."""

import pytest

pytest.importorskip("vpk")

from vpk_editor_fixtures import make_vpk

from core.vpk_preview import (
    NO_PREVIEW_MESSAGE,
    build_preview,
    pick_model_glb,
    preview_kind,
)


@pytest.fixture
def source(tmp_path):
    return make_vpk(
        tmp_path,
        {
            "scripts/readme.txt": b"hello preview\n",
            "scripts/legacy.cfg": "привет".encode("cp1251"),
            "materials/hero/haze.vmat_c": b"\x00\x01\x02",
            "models/heroes/haze/haze.vmdl_c": b"MODEL",
            "materials/hero/haze_ao_png.vtex_c": b"VTEX",
        },
        version=2,
        name="preview_mod.vpk",
    )


def test_preview_kind_by_suffix():
    assert preview_kind("materials/x.vtex_c") == "image"
    assert preview_kind("models/x.vmdl_c") == "model"
    assert preview_kind("scripts/x.cfg") == "text"
    assert preview_kind("scripts/x.txt") == "text"
    assert preview_kind("sounds/x.vsnd_c") == "none"
    assert preview_kind("materials/x.vmat_c") == "none"


def test_text_preview_reads_utf8(tmp_path, source):
    outcome = build_preview(source, "scripts/readme.txt", tmp_path / "cache")

    assert outcome.kind == "text"
    assert outcome.text.startswith("hello preview")


def test_text_preview_reads_cp1251(tmp_path, source):
    outcome = build_preview(source, "scripts/legacy.cfg", tmp_path / "cache")

    assert outcome.kind == "text"
    assert outcome.text == "привет"


def test_workspace_copy_overrides_source_for_text(tmp_path, source):
    workspace = tmp_path / "workspace"
    (workspace / "scripts").mkdir(parents=True)
    (workspace / "scripts" / "readme.txt").write_text(
        "edited in workspace", encoding="utf-8"
    )

    outcome = build_preview(
        source,
        "scripts/readme.txt",
        tmp_path / "cache",
        workspace_dir=workspace,
    )

    assert outcome.kind == "text"
    assert outcome.text == "edited in workspace"


def test_binary_text_suffix_is_rejected(tmp_path):
    pak = make_vpk(
        tmp_path,
        {"scripts/data.txt": b"\x00\x01\x02\x03" * 50},
        name="binary.vpk",
    )
    outcome = build_preview(pak, "scripts/data.txt", tmp_path / "cache")

    assert outcome.kind == "none"
    assert "текст" in outcome.message


def test_unsupported_suffix_returns_message(tmp_path, source):
    outcome = build_preview(
        source, "materials/hero/haze.vmat_c", tmp_path / "cache"
    )

    assert outcome.kind == "none"
    assert outcome.message == NO_PREVIEW_MESSAGE


def test_texture_without_cli_reports_message(tmp_path, source, monkeypatch):
    monkeypatch.setattr("core.vpk_preview.get_s2v_cli_path", lambda: None)

    outcome = build_preview(
        source, "materials/hero/haze_ao_png.vtex_c", tmp_path / "cache"
    )

    assert outcome.kind == "none"
    assert "Source2Viewer" in outcome.message


def test_model_without_cli_reports_message(tmp_path, source, monkeypatch):
    monkeypatch.setattr("core.vpk_preview.get_s2v_cli_path", lambda: None)

    outcome = build_preview(
        source, "models/heroes/haze/haze.vmdl_c", tmp_path / "cache"
    )

    assert outcome.kind == "none"
    assert "Source2Viewer" in outcome.message


def test_pick_model_glb_prefers_matching_stem(tmp_path):
    out = tmp_path / "model"
    out.mkdir()
    (out / "other.glb").write_bytes(b"x" * 4096)
    (out / "haze.glb").write_bytes(b"y" * 2048)

    chosen = pick_model_glb(out, "models/heroes/haze/haze.vmdl_c")

    assert chosen is not None
    assert chosen.name == "haze.glb"


def test_pick_model_glb_falls_back_to_largest(tmp_path):
    out = tmp_path / "model"
    out.mkdir()
    (out / "a.glb").write_bytes(b"x" * 2048)
    large = out / "b.glb"
    large.write_bytes(b"y" * 4096)

    assert pick_model_glb(out, "models/unknown.vmdl_c") == large


def test_pick_model_glb_empty_directory(tmp_path):
    assert pick_model_glb(tmp_path / "missing", "models/x.vmdl_c") is None
