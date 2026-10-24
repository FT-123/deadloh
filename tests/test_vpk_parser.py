from types import SimpleNamespace

import pytest

from core import vpk_parser
from core.constants import clean_display_name


class FakePak:
    def __init__(self, entries):
        self.entries = entries

    def __iter__(self):
        return iter(self.entries)


def fake_vpk(entries):
    return SimpleNamespace(open=lambda path: FakePak(entries))


def test_hero_and_model_tags(tmp_path, monkeypatch):
    monkeypatch.setattr(
        vpk_parser,
        "vpk",
        fake_vpk(["models/hero/haze/haze_body.vmdl_c"]),
    )

    result = vpk_parser.analyze_vpk(tmp_path / "mod.vpk")

    assert result == {"heroes": ["Haze"], "types": ["Model"]}


def test_hero_alias_maps_to_display_name(tmp_path, monkeypatch):
    monkeypatch.setattr(
        vpk_parser,
        "vpk",
        fake_vpk(["models/hero/archer/body.vmdl_c"]),
    )

    result = vpk_parser.analyze_vpk(tmp_path / "mod.vpk")

    assert "Grey Talon" in result["heroes"]


def test_ui_tag(tmp_path, monkeypatch):
    monkeypatch.setattr(
        vpk_parser,
        "vpk",
        fake_vpk(["panorama/hud/hud.vxml_c"]),
    )

    assert vpk_parser.analyze_vpk(tmp_path / "mod.vpk")["types"] == ["UI"]


def test_texture_tag(tmp_path, monkeypatch):
    monkeypatch.setattr(
        vpk_parser,
        "vpk",
        fake_vpk(["materials/hero/haze/body.vmat_c"]),
    )

    assert vpk_parser.analyze_vpk(tmp_path / "mod.vpk")["types"] == ["Texture"]


def test_voice_tag(tmp_path, monkeypatch):
    monkeypatch.setattr(
        vpk_parser,
        "vpk",
        fake_vpk(["sounds/vo/haze/line.vsnd_c"]),
    )

    assert vpk_parser.analyze_vpk(tmp_path / "mod.vpk")["types"] == ["Voice"]


def test_sound_tag(tmp_path, monkeypatch):
    monkeypatch.setattr(
        vpk_parser,
        "vpk",
        fake_vpk(["sounds/weapons/shot.vsnd_c"]),
    )

    assert vpk_parser.analyze_vpk(tmp_path / "mod.vpk")["types"] == ["Sound"]


def test_vfx_tag(tmp_path, monkeypatch):
    monkeypatch.setattr(
        vpk_parser,
        "vpk",
        fake_vpk(["particles/hero/haze/trail.vpcf_c"]),
    )

    assert vpk_parser.analyze_vpk(tmp_path / "mod.vpk")["types"] == ["VFX"]


def test_types_are_returned_in_canonical_order(tmp_path, monkeypatch):
    entries = [
        "particles/x.vpcf_c",
        "sounds/vo/x.vsnd_c",
        "panorama/x.vxml_c",
        "models/x.vmdl_c",
    ]
    monkeypatch.setattr(vpk_parser, "vpk", fake_vpk(entries))

    types = vpk_parser.analyze_vpk(tmp_path / "mod.vpk")["types"]

    assert types == ["Model", "UI", "Voice", "VFX"]


def test_missing_vpk_module_returns_empty_tags(tmp_path, monkeypatch):
    monkeypatch.setattr(vpk_parser, "vpk", None)

    assert vpk_parser.analyze_vpk(tmp_path / "mod.vpk") == {"heroes": [], "types": []}


def test_broken_vpk_returns_empty_tags(tmp_path, monkeypatch):
    def boom(path):
        raise ValueError("bad vpk")

    monkeypatch.setattr(vpk_parser, "vpk", SimpleNamespace(open=boom))

    assert vpk_parser.analyze_vpk(tmp_path / "mod.vpk") == {"heroes": [], "types": []}


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("my_mod_86cdd", "My mod"),
        ("Hero-Name", "Hero name"),
        ("some.file_name", "Some file name"),
        ("  spaced   name  ", "Spaced name"),
    ],
)
def test_clean_display_name(raw, expected):
    assert clean_display_name(raw) == expected
