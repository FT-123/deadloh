"""Тесты кеша 3D-моделей: ключ по mtime и очистка временных папок."""

import os
import time

from core import model_exporter


def test_model_cache_dir_is_stable_and_changes_with_mtime(tmp_path, monkeypatch):
    monkeypatch.setattr("core.model_exporter.tempfile.gettempdir", lambda: str(tmp_path))
    vpk = tmp_path / "mod.vpk"
    vpk.write_bytes(b"one")

    first = model_exporter.model_cache_dir(vpk)
    assert first == model_exporter.model_cache_dir(vpk)
    assert first.parent == tmp_path / model_exporter.MODEL_CACHE_ROOT_NAME
    assert first.name.startswith("model_")

    vpk.write_bytes(b"two")
    future = time.time() + 10
    os.utime(vpk, (future, future))
    assert model_exporter.model_cache_dir(vpk) != first


def test_clear_preview_caches_removes_only_dmm_vpk(tmp_path, monkeypatch):
    monkeypatch.setattr("core.model_exporter.tempfile.gettempdir", lambda: str(tmp_path))
    cache_dir = tmp_path / "dmm-vpk-preview-abc"
    cache_dir.mkdir()
    (cache_dir / "thumb.png").write_bytes(b"1234")
    keep = tmp_path / "other-app"
    keep.mkdir()
    (keep / "keep.txt").write_text("keep", encoding="utf-8")

    removed, freed = model_exporter.clear_preview_caches()

    assert removed == 1
    assert freed >= 4
    assert not cache_dir.exists()
    assert keep.exists()


def test_clear_preview_caches_skips_excluded_path(tmp_path, monkeypatch):
    monkeypatch.setattr("core.model_exporter.tempfile.gettempdir", lambda: str(tmp_path))
    workspace = tmp_path / "dmm-vpk-editor-open"
    workspace.mkdir()
    (workspace / "resource.bin").write_bytes(b"data")
    cache_dir = tmp_path / "dmm-vpk-model-old"
    cache_dir.mkdir()

    removed, _freed = model_exporter.clear_preview_caches(exclude=[workspace])

    assert removed == 1
    assert workspace.exists()
    assert not cache_dir.exists()
