import json
import shutil
from pathlib import Path
from types import SimpleNamespace

import pytest

from core import deployer


@pytest.fixture
def env(tmp_path, monkeypatch):
    staging = tmp_path / "staging"
    staging.mkdir()
    state_file = tmp_path / "deployed_state.json"
    game = tmp_path / "citadel"
    addons = game / "addons"
    addons.mkdir(parents=True)

    monkeypatch.setattr(deployer, "STAGING_DIR", staging)
    monkeypatch.setattr(deployer, "DEPLOYED_STATE_PATH", state_file)

    return SimpleNamespace(staging=staging, state_file=state_file, game=game, addons=addons)


def add_mod(env, name: str, enabled: bool = True, payload: bytes | None = None) -> Path:
    folder = env.staging / name
    folder.mkdir()
    (folder / "content.vpk").write_bytes(payload or f"content-{name}".encode())
    (folder / "meta.json").write_text(
        json.dumps({"name": name, "enabled": enabled}, ensure_ascii=False),
        encoding="utf-8",
    )
    return folder


def read_state(env) -> dict:
    return json.loads(env.state_file.read_text(encoding="utf-8"))


def test_deploys_enabled_mods_in_sorted_order(env):
    add_mod(env, "b_mod")
    add_mod(env, "a_mod")
    add_mod(env, "off_mod", enabled=False)

    total, err = deployer.deploy_active_mods(env.game)

    assert err is None
    assert total == 2
    assert (env.addons / "pak01_dir.vpk").read_bytes() == b"content-a_mod"
    assert (env.addons / "pak02_dir.vpk").read_bytes() == b"content-b_mod"
    assert deployer.get_deployed_mods_set() == {"a_mod", "b_mod"}
    assert read_state(env)["files"] == {"a_mod": "pak01_dir.vpk", "b_mod": "pak02_dir.vpk"}


def test_foreign_pak_files_are_never_touched(env):
    foreign = env.addons / "pak01_dir.vpk"
    foreign.write_bytes(b"another-manager")
    add_mod(env, "a_mod")

    total, err = deployer.deploy_active_mods(env.game)

    assert err is None
    assert total == 1
    assert foreign.read_bytes() == b"another-manager"
    assert (env.addons / "pak02_dir.vpk").read_bytes() == b"content-a_mod"


def test_redeploy_removes_only_previous_own_files(env):
    add_mod(env, "a_mod")
    add_mod(env, "b_mod")
    deployer.deploy_active_mods(env.game)

    foreign = env.addons / "pak03_dir.vpk"
    foreign.write_bytes(b"foreign")
    (env.staging / "b_mod" / "meta.json").write_text(
        json.dumps({"name": "b_mod", "enabled": False}), encoding="utf-8"
    )

    total, err = deployer.deploy_active_mods(env.game)

    assert err is None
    assert total == 1
    assert sorted(p.name for p in env.addons.glob("*.vpk")) == [
        "pak01_dir.vpk",
        "pak03_dir.vpk",
    ]
    assert foreign.read_bytes() == b"foreign"
    assert deployer.get_deployed_mods_set() == {"a_mod"}


def test_disable_all_clears_only_owned_files(env):
    add_mod(env, "a_mod")
    deployer.deploy_active_mods(env.game)
    foreign = env.addons / "pak05_dir.vpk"
    foreign.write_bytes(b"foreign")

    (env.staging / "a_mod" / "meta.json").write_text(
        json.dumps({"name": "a_mod", "enabled": False}), encoding="utf-8"
    )
    total, err = deployer.deploy_active_mods(env.game)

    assert (total, err) == (0, None)
    assert sorted(p.name for p in env.addons.glob("*.vpk")) == ["pak05_dir.vpk"]
    assert read_state(env)["deployed"] == []


def test_legacy_state_without_files_mapping_is_migrated(env):
    # Старый формат: deployed без files, pak01..pakNN в порядке списка.
    (env.addons / "pak01_dir.vpk").write_bytes(b"old-a")
    (env.addons / "pak02_dir.vpk").write_bytes(b"old-b")
    foreign = env.addons / "pak03_dir.vpk"
    foreign.write_bytes(b"foreign")
    env.state_file.write_text(
        json.dumps({"deployed": ["old_a", "old_b"]}), encoding="utf-8"
    )
    add_mod(env, "new_a")

    total, err = deployer.deploy_active_mods(env.game)

    assert err is None
    assert total == 1
    assert sorted(p.name for p in env.addons.glob("*.vpk")) == [
        "pak01_dir.vpk",
        "pak03_dir.vpk",
    ]
    assert (env.addons / "pak01_dir.vpk").read_bytes() == b"content-new_a"
    assert foreign.read_bytes() == b"foreign"


def test_rollback_restores_previous_deployment_on_failure(env, monkeypatch):
    add_mod(env, "old_mod")
    deployer.deploy_active_mods(env.game)
    old_state = read_state(env)

    add_mod(env, "new_mod")
    real_install = deployer._install
    calls = {"n": 0}

    def flaky_install(src, target):
        calls["n"] += 1
        if calls["n"] == 2:
            raise OSError("disk full")
        real_install(src, target)

    monkeypatch.setattr(deployer, "_install", flaky_install)

    total, err = deployer.deploy_active_mods(env.game)

    assert total == 0
    assert err and "disk full" in err
    # Старый мод восстановлен, новый удалён, состояние не изменилось.
    files = sorted(p.name for p in env.addons.glob("*.vpk"))
    assert files == ["pak01_dir.vpk"]
    assert (env.addons / "pak01_dir.vpk").read_bytes() == b"content-old_mod"
    assert read_state(env) == old_state
    assert not list(env.addons.glob("*.dmm_tmp"))
    assert not list(env.addons.glob(".dmm_deploy_*"))


def test_corrupt_meta_is_skipped(env):
    folder = add_mod(env, "broken")
    (folder / "meta.json").write_text("{not json", encoding="utf-8")

    total, err = deployer.deploy_active_mods(env.game)

    assert (total, err) == (0, None)
    assert not list(env.addons.glob("*.vpk"))


def test_missing_staging_dir_is_not_an_error(env, monkeypatch):
    monkeypatch.setattr(deployer, "STAGING_DIR", env.staging / "absent")

    total, err = deployer.deploy_active_mods(env.game)

    assert (total, err) == (0, None)


def test_failed_delete_reports_game_running_and_restores(env, monkeypatch):
    add_mod(env, "a_mod")
    deployer.deploy_active_mods(env.game)

    def locked_unlink(self, *args, **kwargs):
        raise PermissionError("file is locked")

    monkeypatch.setattr(Path, "unlink", locked_unlink)

    total, err = deployer.deploy_active_mods(env.game)

    assert total == 0
    assert err and "Закройте игру" in err
    assert (env.addons / "pak01_dir.vpk").read_bytes() == b"content-a_mod"


def test_deployed_set_ignores_files_missing_from_addons(env):
    add_mod(env, "a_mod")
    add_mod(env, "b_mod")
    deployer.deploy_active_mods(env.game)

    assert deployer.get_deployed_mods_set(env.game) == {"a_mod", "b_mod"}

    (env.addons / "pak02_dir.vpk").unlink()

    assert deployer.get_deployed_mods_set(env.game) == {"a_mod"}


def test_deployed_set_is_empty_without_addons_dir(env):
    add_mod(env, "a_mod")
    deployer.deploy_active_mods(env.game)

    shutil.rmtree(env.addons)

    assert deployer.get_deployed_mods_set(env.game) == set()


def test_deployed_set_without_game_path_trusts_state(env):
    add_mod(env, "a_mod")
    deployer.deploy_active_mods(env.game)

    assert deployer.get_deployed_mods_set() == {"a_mod"}
