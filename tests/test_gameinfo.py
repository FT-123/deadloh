from pathlib import Path

import pytest

from core import gameinfo

UNPATCHED = (
    "// gameinfo.gi\r\n"
    "SearchPaths\r\n"
    "{\r\n"
    "\tGame\t\tcitadel\r\n"
    "\tGame\t\tcitadel/addons\r\n"
    "}\r\n"
    "// trailing comment\r\n"
)


def make_game(tmp_path: Path, content: str) -> Path:
    citadel = tmp_path / "citadel"
    citadel.mkdir()
    (citadel / "gameinfo.gi").write_bytes(content.encode("utf-8"))
    return citadel


def test_unpatched_order_detected_as_broken(tmp_path):
    game = make_game(tmp_path, UNPATCHED)
    assert gameinfo.is_gameinfo_patched(game) is False


def test_patched_order_detected(tmp_path):
    content = (
        "SearchPaths {\n"
        "\tGame citadel/addons\n"
        "\tGame citadel\n"
        "}\n"
    )
    game = make_game(tmp_path, content)
    assert gameinfo.is_gameinfo_patched(game) is True


def test_patch_creates_backup_and_patches(tmp_path):
    game = make_game(tmp_path, UNPATCHED)

    ok, message = gameinfo.patch_gameinfo_file(game)

    assert ok is True, message
    assert gameinfo.is_gameinfo_patched(game) is True
    assert (game / "gameinfo.gi.bak").read_bytes() == UNPATCHED.encode("utf-8")


def test_patch_preserves_crlf_line_endings(tmp_path):
    game = make_game(tmp_path, UNPATCHED)
    gameinfo.patch_gameinfo_file(game)

    result = (game / "gameinfo.gi").read_bytes().decode("utf-8")
    assert "\r\n" in result
    assert "\n" not in result.replace("\r\n", "")


def test_patch_keeps_original_backup_on_repeated_patch(tmp_path):
    game = make_game(tmp_path, UNPATCHED)
    gameinfo.patch_gameinfo_file(game)
    gameinfo.patch_gameinfo_file(game)

    assert (game / "gameinfo.gi.bak").read_bytes() == UNPATCHED.encode("utf-8")


def test_patch_refuses_ambiguous_file(tmp_path):
    content = (
        "SearchPaths { Game citadel }\n"
        "Other { SearchPaths { Game citadel/addons } }\n"
    )
    game = make_game(tmp_path, content)

    ok, message = gameinfo.patch_gameinfo_file(game)

    assert ok is False
    assert "однозначно" in message
    assert (game / "gameinfo.gi").read_bytes() == content.encode("utf-8")
    assert not (game / "gameinfo.gi.bak").exists()


def test_patch_refuses_missing_block(tmp_path):
    game = make_game(tmp_path, "// no search paths here\n")

    ok, message = gameinfo.patch_gameinfo_file(game)

    assert ok is False
    assert "SearchPaths" in message


def test_commented_searchpaths_is_ignored(tmp_path):
    content = (
        "// SearchPaths { Game citadel }\n"
        "SearchPaths\n"
        "{\n"
        "\tGame citadel\n"
        "}\n"
    )
    game = make_game(tmp_path, content)

    ok, _ = gameinfo.patch_gameinfo_file(game)

    assert ok is True
    assert gameinfo.is_gameinfo_patched(game) is True


def test_brace_inside_comment_does_not_break_matching(tmp_path):
    content = (
        "SearchPaths\n"
        "{\n"
        "\t// } этот символ не закрывает блок\n"
        "\tGame citadel\n"
        "}\n"
        "// после блока\n"
    )
    game = make_game(tmp_path, content)

    ok, message = gameinfo.patch_gameinfo_file(game)

    assert ok is True, message
    text = (game / "gameinfo.gi").read_text(encoding="utf-8")
    assert text.rstrip().endswith("// после блока")
    assert "Game" in text


def test_quoted_brace_inside_block_is_ignored(tmp_path):
    content = (
        "SearchPaths\n"
        "{\n"
        '\tGame "citadel}"\n'
        "}\n"
    )
    game = make_game(tmp_path, content)

    ok, message = gameinfo.patch_gameinfo_file(game)

    assert ok is True, message


def test_missing_gameinfo_returns_error(tmp_path):
    ok, message = gameinfo.patch_gameinfo_file(tmp_path)
    assert ok is False
    assert "не найден" in message


def test_unwritable_directory_returns_error_instead_of_raising(tmp_path, monkeypatch):
    game = make_game(tmp_path, UNPATCHED)

    def boom(*args, **kwargs):
        raise OSError("access denied")

    monkeypatch.setattr(gameinfo.os, "replace", boom)

    ok, message = gameinfo.patch_gameinfo_file(game)

    assert ok is False
    assert "Не удалось записать" in message
    assert (game / "gameinfo.gi").read_bytes() == UNPATCHED.encode("utf-8")


@pytest.mark.parametrize("content", ["", "SearchPaths"])
def test_empty_or_incomplete_file_is_not_patched(tmp_path, content):
    game = make_game(tmp_path, content)
    ok, _ = gameinfo.patch_gameinfo_file(game)
    assert ok is False
