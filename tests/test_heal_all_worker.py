"""Тесты фоновой массовой починки модов (внешний vpkmerge подменяется)."""

import pytest

pytest.importorskip("PySide6")  # без PySide6 тест пропускается

from ui.main_window import HealAllWorker


def make_folder(root, name: str):
    folder = root / name
    folder.mkdir()
    (folder / "content.vpk").write_bytes(b"")
    return folder


def test_heal_all_worker_collects_progress_and_results(qapp, monkeypatch, tmp_path):
    folders = [make_folder(tmp_path, "mod_a"), make_folder(tmp_path, "mod_b")]
    calls: list[str] = []

    def fake_heal(folder, game_path):
        calls.append(folder.name)
        if folder.name == "mod_a":
            return True, "ok"
        return False, "vpkmerge error"

    monkeypatch.setattr("ui.main_window.heal_mod_vpk", fake_heal)

    worker = HealAllWorker(folders, tmp_path)
    progress: list[tuple[int, int, str]] = []
    finished: list[tuple] = []
    worker.progress_changed.connect(
        lambda current, total, name: progress.append((current, total, name))
    )
    worker.heal_all_finished.connect(lambda *args: finished.append(args))

    worker.start()
    worker.wait()
    qapp.processEvents()

    assert calls == ["mod_a", "mod_b"]
    assert [entry[0] for entry in progress] == [0, 1, 2]
    assert all(entry[1] == 2 for entry in progress)
    assert len(finished) == 1
    healed_count, healed, errors = finished[0]
    assert healed_count == 1
    assert healed == ["mod_a"]
    assert errors and "mod_b" in errors[0]


def test_heal_all_worker_handles_empty_list(qapp):
    worker = HealAllWorker([], None)
    finished: list[tuple] = []
    worker.heal_all_finished.connect(lambda *args: finished.append(args))

    worker.start()
    worker.wait()
    qapp.processEvents()

    assert finished == [(0, [], [])]
