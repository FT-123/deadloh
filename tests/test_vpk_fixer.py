from pathlib import Path
from types import SimpleNamespace

import pytest

from core import vpk_fixer


@pytest.fixture
def game(tmp_path):
    citadel = tmp_path / "citadel"
    citadel.mkdir()
    (citadel / "pak01_dir.vpk").write_bytes(b"BASE")
    return citadel


@pytest.fixture
def mod_folder(tmp_path):
    folder = tmp_path / "staging" / "mod"
    folder.mkdir(parents=True)
    (folder / "content.vpk").write_bytes(b"OLD")
    return folder


@pytest.fixture(autouse=True)
def tools(tmp_path, monkeypatch, game):
    exe = tmp_path / "vpkmerge.exe"
    exe.write_bytes(b"exe")
    monkeypatch.setattr(vpk_fixer, "get_vpkmerge_path", lambda: exe)
    monkeypatch.setattr(vpk_fixer, "get_base_pak", lambda path: game / "pak01_dir.vpk")
    return SimpleNamespace(exe=exe)


def run_result(stdout: str, code: int = 0):
    return SimpleNamespace(returncode=code, stdout=stdout, stderr=b"")


def test_check_detects_stale_from_summary(monkeypatch, mod_folder, game):
    monkeypatch.setattr(
        vpk_fixer.subprocess,
        "run",
        lambda *a, **k: run_result("1 of 3 mods ship stale models\n"),
    )

    is_stale, models, err = vpk_fixer.check_mod_stale(mod_folder / "content.vpk", game)

    assert (is_stale, err) == (True, None)
    assert models == []


def test_check_detects_stale_from_model_lines(monkeypatch, mod_folder, game):
    monkeypatch.setattr(
        vpk_fixer.subprocess,
        "run",
        lambda *a, **k: run_result("hero/haze/haze.vmdl_c stale: something\n"),
    )

    is_stale, models, err = vpk_fixer.check_mod_stale(mod_folder / "content.vpk", game)

    assert (is_stale, err) == (True, None)
    assert models == ["hero/haze/haze.vmdl_c"]


def test_check_clean_output(monkeypatch, mod_folder, game):
    monkeypatch.setattr(
        vpk_fixer.subprocess,
        "run",
        lambda *a, **k: run_result("All attachments are up to date\n"),
    )

    assert vpk_fixer.check_mod_stale(mod_folder / "content.vpk", game) == (False, [], None)


def test_check_nonzero_exit_without_stale_is_error(monkeypatch, mod_folder, game):
    monkeypatch.setattr(
        vpk_fixer.subprocess,
        "run",
        lambda *a, **k: run_result("unknown option\n", code=2),
    )

    is_stale, _models, err = vpk_fixer.check_mod_stale(mod_folder / "content.vpk", game)

    assert is_stale is False
    assert err and "кодом 2" in err


def test_check_reports_missing_vpkmerge(monkeypatch, mod_folder, game):
    monkeypatch.setattr(vpk_fixer, "get_vpkmerge_path", lambda: None)

    is_stale, models, err = vpk_fixer.check_mod_stale(mod_folder / "content.vpk", game)

    assert is_stale is False
    assert models == []
    assert err == "vpkmerge.exe не найден"


def test_check_reports_missing_base_pak(monkeypatch, mod_folder, game):
    monkeypatch.setattr(vpk_fixer, "get_base_pak", lambda path: None)

    is_stale, _, err = vpk_fixer.check_mod_stale(mod_folder / "content.vpk", game)

    assert is_stale is False
    assert err and "pak01_dir.vpk" in err


def test_heal_replaces_content_and_keeps_backup(monkeypatch, mod_folder, game):
    def fake_run(cmd, **kwargs):
        output = Path(cmd[cmd.index("--output") + 1])
        output.write_bytes(b"FIXED")
        return run_result("healed\n")

    monkeypatch.setattr(vpk_fixer.subprocess, "run", fake_run)
    monkeypatch.setattr(vpk_fixer, "check_mod_stale", lambda *a, **k: (False, [], None))

    ok, message = vpk_fixer.heal_mod_vpk(mod_folder, game)

    assert ok is True, message
    assert (mod_folder / "content.vpk").read_bytes() == b"FIXED"
    assert (mod_folder / "content.vpk.bak").read_bytes() == b"OLD"
    assert (mod_folder / "content.vpk.orig.bak").read_bytes() == b"OLD"
    assert not (mod_folder / "content_fixed.vpk").exists()


def test_second_heal_rotates_last_backup_and_keeps_original(monkeypatch, mod_folder, game):
    payloads = iter([b"FIXED-1", b"FIXED-2"])

    def fake_run(cmd, **kwargs):
        output = Path(cmd[cmd.index("--output") + 1])
        output.write_bytes(next(payloads))
        return run_result("healed\n")

    monkeypatch.setattr(vpk_fixer.subprocess, "run", fake_run)
    monkeypatch.setattr(vpk_fixer, "check_mod_stale", lambda *a, **k: (False, [], None))

    assert vpk_fixer.heal_mod_vpk(mod_folder, game)[0] is True
    assert (mod_folder / "content.vpk").read_bytes() == b"FIXED-1"

    assert vpk_fixer.heal_mod_vpk(mod_folder, game)[0] is True
    assert (mod_folder / "content.vpk").read_bytes() == b"FIXED-2"
    assert (mod_folder / "content.vpk.bak").read_bytes() == b"FIXED-1"
    assert (mod_folder / "content.vpk.orig.bak").read_bytes() == b"OLD"


def test_heal_fails_when_post_check_errors(monkeypatch, mod_folder, game):
    def fake_run(cmd, **kwargs):
        output = Path(cmd[cmd.index("--output") + 1])
        output.write_bytes(b"FIXED")
        return run_result("healed\n")

    monkeypatch.setattr(vpk_fixer.subprocess, "run", fake_run)
    monkeypatch.setattr(
        vpk_fixer,
        "check_mod_stale",
        lambda *a, **k: (False, [], "vpkmerge check упал"),
    )

    ok, message = vpk_fixer.heal_mod_vpk(mod_folder, game)

    assert ok is False
    assert "Не удалось проверить" in message
    assert (mod_folder / "content.vpk").read_bytes() == b"OLD"
    assert not (mod_folder / "content.vpk.bak").exists()
    assert not (mod_folder / "content_fixed.vpk").exists()


def test_heal_fails_when_still_stale(monkeypatch, mod_folder, game):
    def fake_run(cmd, **kwargs):
        output = Path(cmd[cmd.index("--output") + 1])
        output.write_bytes(b"FIXED")
        return run_result("healed\n")

    monkeypatch.setattr(vpk_fixer.subprocess, "run", fake_run)
    monkeypatch.setattr(vpk_fixer, "check_mod_stale", lambda *a, **k: (True, [], None))

    ok, message = vpk_fixer.heal_mod_vpk(mod_folder, game)

    assert ok is False
    assert "stale" in message
    assert (mod_folder / "content.vpk").read_bytes() == b"OLD"
    assert not (mod_folder / "content_fixed.vpk").exists()


def test_heal_fails_on_command_error(monkeypatch, mod_folder, game):
    monkeypatch.setattr(
        vpk_fixer.subprocess,
        "run",
        lambda *a, **k: run_result("boom\n", code=1),
    )

    ok, message = vpk_fixer.heal_mod_vpk(mod_folder, game)

    assert ok is False
    assert "код 1" in message
    assert (mod_folder / "content.vpk").read_bytes() == b"OLD"
    assert not (mod_folder / "content_fixed.vpk").exists()


def test_heal_requires_base_pak(monkeypatch, mod_folder, game):
    monkeypatch.setattr(vpk_fixer, "get_base_pak", lambda path: None)

    ok, message = vpk_fixer.heal_mod_vpk(mod_folder, game)

    assert ok is False
    assert "base pak" in message
    assert (mod_folder / "content.vpk").read_bytes() == b"OLD"
