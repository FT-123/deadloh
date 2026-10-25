"""Тесты изолированного workspace и защиты от выхода за его пределы."""


import pytest

from core.vpk_editor.errors import (
    UnsafeResourcePathError,
    WorkspaceError,
)
from core.vpk_editor.workspace import Workspace, is_safe_relative_path


def test_write_read_and_cleanup(tmp_path):
    workspace = Workspace(tmp_path)
    root = workspace.root

    target = workspace.write_bytes("sounds/hero/haze/ability3.vsnd_c", b"data")

    assert target == root / "sounds" / "hero" / "haze" / "ability3.vsnd_c"
    assert target.read_bytes() == b"data"
    assert workspace.read_bytes("sounds/hero/haze/ability3.vsnd_c") == b"data"
    assert workspace.file_count() == 1

    workspace.cleanup()
    assert not root.exists()
    workspace.cleanup()  # повторный вызов безопасен


def test_context_manager_removes_workspace(tmp_path):
    with Workspace(tmp_path) as workspace:
        root = workspace.root
        workspace.write_bytes("file.txt", b"x")
        assert root.exists()

    assert not root.exists()


@pytest.mark.parametrize(
    "path",
    [
        "../evil.txt",
        "../../Windows/System32/evil.dll",
        "/absolute/evil.txt",
        "C:/Windows/evil.txt",
        "sounds/../../evil.txt",
        "bad\x00name.txt",
        "CON.txt",
        "trailing.",
        "trailing ",
    ],
)
def test_unsafe_paths_are_rejected(tmp_path, path):
    workspace = Workspace(tmp_path)

    with pytest.raises(UnsafeResourcePathError):
        workspace.path_for(path)

    workspace.cleanup()


def test_written_file_stays_inside_workspace(tmp_path):
    workspace = Workspace(tmp_path)
    outside = tmp_path / "outside.txt"

    workspace.write_bytes("nested/file.txt", b"safe")

    assert (workspace.root / "nested" / "file.txt").is_file()
    assert not outside.exists()
    workspace.cleanup()


def test_safe_path_normalization(tmp_path):
    workspace = Workspace(tmp_path)

    assert workspace.path_for("sounds//hero/./haze/file.vsnd_c") == (
        workspace.root / "sounds" / "hero" / "haze" / "file.vsnd_c"
    )

    workspace.cleanup()


def test_missing_parent_is_reported(tmp_path):
    with pytest.raises(WorkspaceError):
        Workspace(tmp_path / "does-not-exist")


def test_write_after_cleanup_fails(tmp_path):
    workspace = Workspace(tmp_path)
    workspace.cleanup()

    with pytest.raises(WorkspaceError):
        workspace.write_bytes("file.txt", b"x")


def test_public_path_validator():
    assert is_safe_relative_path("sounds/hero/haze/file.vsnd_c") is True
    assert is_safe_relative_path("../evil") is False
    assert is_safe_relative_path("/absolute") is False
    assert is_safe_relative_path("C:/drive") is False
    assert is_safe_relative_path("") is False
