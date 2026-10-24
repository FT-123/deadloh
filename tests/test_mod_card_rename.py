"""Переименование мода: меняется только подпись, папка и VPK остаются целы."""

import json

import pytest

pytest.importorskip("PySide6")  # GUI-тесты пропускаются, если нет PySide6

from PySide6.QtWidgets import QInputDialog, QWidget

from ui.widgets import ModCardWidget


class _Manager(QWidget):
    """Минимальный менеджер-родитель для карточки мода."""

    def __init__(self):
        super().__init__()
        self.renamed: list[tuple[str, str]] = []
        self.messages: list[str] = []

    def notify(self, text: str):
        self.messages.append(text)

    def on_mod_renamed(self, folder_name: str, display_name: str):
        self.renamed.append((folder_name, display_name))


def _make_mod(tmp_path, meta: dict) -> tuple[str, dict]:
    folder_name = "haze_recolor"
    mod_dir = tmp_path / folder_name
    mod_dir.mkdir(parents=True)
    (mod_dir / "content.vpk").write_bytes(b"vpk-bytes")
    (mod_dir / "meta.json").write_text(
        json.dumps(meta, ensure_ascii=False), encoding="utf-8"
    )
    return folder_name, meta


def _mock_name(monkeypatch, value: str, accepted: bool = True):
    monkeypatch.setattr(
        QInputDialog, "getText", staticmethod(lambda *args, **kwargs: (value, accepted))
    )


def test_rename_changes_only_display_name(tmp_path, monkeypatch, qapp):
    meta = {
        "name": "Haze Recolor Pack",
        "tags_data": {"heroes": ["Haze"], "types": ["Texture"]},
        "enabled": True,
    }
    folder_name, _ = _make_mod(tmp_path, meta)
    monkeypatch.setattr("ui.widgets.STAGING_DIR", tmp_path)
    _mock_name(monkeypatch, "  Мой   Haze  ")

    manager = _Manager()
    card = ModCardWidget(folder_name, meta, False, manager)
    card.on_rename()

    saved = json.loads(
        (tmp_path / folder_name / "meta.json").read_text(encoding="utf-8")
    )
    assert saved["display_name"] == "Мой Haze"
    assert saved["name"] == "Haze Recolor Pack"
    assert saved["tags_data"] == {"heroes": ["Haze"], "types": ["Texture"]}
    # Папка и содержимое не тронуты — деплой в игру продолжит работать.
    assert (tmp_path / folder_name / "content.vpk").read_bytes() == b"vpk-bytes"
    assert manager.renamed == [(folder_name, "Мой Haze")]
    assert card.display_name == "Мой Haze"


def test_custom_display_name_used_on_build(tmp_path, monkeypatch, qapp):
    meta = {"name": "Haze Recolor Pack", "display_name": "Любимый Haze"}
    folder_name, _ = _make_mod(tmp_path, meta)
    monkeypatch.setattr("ui.widgets.STAGING_DIR", tmp_path)

    card = ModCardWidget(folder_name, meta, False, _Manager())
    assert card.display_name == "Любимый Haze"


def test_empty_rename_is_rejected(tmp_path, monkeypatch, qapp):
    meta = {"name": "Haze Recolor Pack"}
    folder_name, _ = _make_mod(tmp_path, meta)
    monkeypatch.setattr("ui.widgets.STAGING_DIR", tmp_path)
    _mock_name(monkeypatch, "   ")

    manager = _Manager()
    card = ModCardWidget(folder_name, meta, False, manager)
    card.on_rename()

    saved = json.loads(
        (tmp_path / folder_name / "meta.json").read_text(encoding="utf-8")
    )
    assert "display_name" not in saved
    assert manager.renamed == []
    assert manager.messages == ["Название не может быть пустым"]
