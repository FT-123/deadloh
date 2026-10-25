"""Интеграционные тесты окна редактора VPK: открытие, фильтр, замена, сборка."""

import time

import pytest

pytest.importorskip("PySide6")  # GUI-тесты пропускаются, если нет PySide6

from PySide6.QtWidgets import QWidget
from vpk_editor_fixtures import make_vpk

from core.vpk_editor import VPKEditor
from ui.vpk_editor_window import VPKEditorWindow

HAZE_SOUND = "sounds/hero/haze/ability3_cast.vsnd_c"
FILES = {
    HAZE_SOUND: b"HAZE-ABILITY-3",
    "materials/hero/haze.vmat_c": b"MATERIAL",
    "scripts/readme.txt": b"hello preview\n",
}


class _MockManager(QWidget):
    """Минимальный менеджер-родитель окна (в приложении — DeadlockModManager)."""


def make_window(qapp) -> VPKEditorWindow:
    """Создаёт и показывает окно, чтобы isVisible()/isEnabled() были реальными."""
    window = VPKEditorWindow(_MockManager())
    window.show()
    return window


def wait_for(qapp, predicate, attempts: int = 400) -> bool:
    """Докачивает события Qt, пока условие не выполнится (каждый цикл — сигнал потока)."""
    for _ in range(attempts):
        if predicate():
            return True
        qapp.processEvents()
        time.sleep(0.005)
    return predicate()


@pytest.fixture
def source(tmp_path):
    return make_vpk(tmp_path, FILES, version=2, name="haze_mod.vpk")


def test_window_starts_without_project(qapp):
    window = make_window(qapp)

    assert window.btn_open.isEnabled() is True
    assert window.btn_build.isEnabled() is False
    assert window.btn_replace.isEnabled() is False
    assert window.resource_list.count() == 0
    assert window.file_label.text() == "Архив не открыт"
    window.close()


def test_open_lists_resources_and_filters(qapp, source):
    window = make_window(qapp)
    window.start_open(source)

    assert wait_for(qapp, lambda: window._project is not None)
    assert wait_for(qapp, lambda: window._busy is False)
    assert window.resource_list.count() == len(FILES)
    assert source.name in window.file_label.text()

    # Поиск по пути ресурса.
    window.search_input.setText("vsnd")
    assert window.resource_list.count() == 1
    window.search_input.setText("")
    assert window.resource_list.count() == len(FILES)

    # Фильтр «только звуки» + подпись кнопки для .vsnd_c.
    window.btn_sounds.setChecked(True)
    assert window.resource_list.count() == 1
    window.resource_list.setCurrentRow(0)
    assert window.btn_replace.isEnabled() is True
    assert "звук" in window.btn_replace.text().lower()
    window.btn_sounds.setChecked(False)
    window.close()


def test_replace_and_build_never_touches_source(qapp, source, tmp_path, monkeypatch):
    monkeypatch.setattr(
        "ui.vpk_editor_window.QMessageBox.information", lambda *args, **kwargs: None
    )
    original = source.read_bytes()
    window = make_window(qapp)

    window.start_open(source)
    assert wait_for(qapp, lambda: window._project is not None)
    assert wait_for(qapp, lambda: window._busy is False)

    replacement = tmp_path / "custom.vsnd_c"
    replacement.write_bytes(b"CUSTOM-SOUND")
    window.start_replace(HAZE_SOUND, replacement)
    assert wait_for(qapp, lambda: HAZE_SOUND in window._modifications)
    assert window._modifications[HAZE_SOUND].size == len(b"CUSTOM-SOUND")

    output = tmp_path / "edited.vpk"
    window.start_build(output)
    assert wait_for(qapp, lambda: output.exists() and window._busy is False)

    # Исходный архив не тронут, а собранный содержит замену.
    assert source.read_bytes() == original
    with VPKEditor().open_vpk(output) as project:
        assert project.read_resource(HAZE_SOUND) == b"CUSTOM-SOUND"
    assert "Собрано" in window.status_label.text()
    window.close()


def test_shutdown_resets_window_state(qapp, source):
    window = make_window(qapp)
    window.start_open(source)
    assert wait_for(qapp, lambda: window._project is not None)

    window.shutdown()

    assert window._project is None
    assert window.resource_list.count() == 0
    assert window.file_label.text() == "Архив не открыт"
    assert window.btn_open.isEnabled() is True
    assert window.btn_build.isEnabled() is False
    window.close()


def test_invalid_source_reports_error(qapp, tmp_path):
    window = make_window(qapp)
    window.start_open(tmp_path / "missing.vpk")

    assert wait_for(qapp, lambda: window.status_label.text().startswith("❌"))
    assert wait_for(qapp, lambda: window._busy is False)
    assert window._project is None
    assert window.btn_open.isEnabled() is True
    window.close()


def open_and_wait(qapp, source):
    """Открывает архив и дожидается, пока окно перестанет быть занятым."""
    window = make_window(qapp)
    window.start_open(source)
    assert wait_for(qapp, lambda: window._project is not None)
    assert wait_for(qapp, lambda: window._busy is False)
    return window


def select_resource(window, fragment: str) -> None:
    """Фильтрует список по фрагменту пути и выбирает первый результат."""
    window.search_input.setText(fragment)
    assert window.resource_list.count() > 0
    window.resource_list.setCurrentRow(0)


def test_text_resource_gets_text_preview(qapp, source):
    window = open_and_wait(qapp, source)

    select_resource(window, "readme.txt")

    assert wait_for(qapp, lambda: window.preview_text.toPlainText().startswith("hello"))
    assert window.preview_stack.currentWidget() is window.preview_text
    window.close()


def test_unsupported_resource_shows_message(qapp, source):
    window = open_and_wait(qapp, source)

    select_resource(window, "haze.vmat_c")

    assert window.preview_stack.currentWidget() is window.preview_status
    assert "Превью" in window.preview_status.text()
    window.close()


def test_preview_expand_toggles_resource_list(qapp, source):
    window = open_and_wait(qapp, source)

    assert window.resource_list.isVisible() is True
    window.btn_preview_expand.click()
    assert window._preview_expanded is True
    assert window.resource_list.isVisible() is False

    window.btn_preview_expand.click()
    assert window._preview_expanded is False
    assert window.resource_list.isVisible() is True
    window.close()
