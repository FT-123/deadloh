"""Интеграционные тесты окна GameBanana: поиск, пагинация, ленивая загрузка, «назад»."""

import pytest

pytest.importorskip("PySide6")  # GUI-тесты пропускаются, если нет PySide6

from PySide6.QtWidgets import QWidget

from core.gamebanana import GBMod
from ui.gamebanana_window import GameBananaWindow


class _MockManager(QWidget):
    """Минимальный менеджер-родитель для окна (в приложении это DeadlockModManager)."""

    def _worker_running(self, name: str) -> bool:
        return False


def make_manager() -> QWidget:
    return _MockManager()


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    """По умолчанию сетевой вызов возвращает пустую страницу — без реального запроса."""

    def fake_fetch(page, days=7):
        return [], {"_nRecordCount": 5126}

    monkeypatch.setattr("ui.gamebanana_window.fetch_recent_mods_page", fake_fetch)


def make_mods(n: int) -> list[GBMod]:
    """Список тестовых модов с заполненными обязательными полями."""
    return [GBMod(mod_id=i, name=f"mod {i}", downloads=0) for i in range(n)]


def make_shown_window(qapp) -> GameBananaWindow:
    """Создаёт окно и показывает его, чтобы isVisible() отражал реальное состояние."""
    window = GameBananaWindow(make_manager())
    window.show()
    return window


def wait_for_load(window: GameBananaWindow, qapp) -> None:
    """Докачивает события, пока не закончится текущая загрузка страницы."""
    # доводим до состояния «загрузка завершена» (каждый цикл — один QThread-сигнал)
    for _ in range(300):
        if not window._popular_loading:
            break
        qapp.processEvents()
    # финальный дренаж очереди событий на случай, если сигнал ещё в очереди
    for _ in range(10):
        qapp.processEvents()


def test_window_opens_in_popular_mode_without_crash(qapp):
    window = make_shown_window(qapp)
    wait_for_load(window, qapp)
    assert window._mode == "popular"
    assert window.gb_pag_container.isVisible() is True
    assert window.btn_gb_back.isVisible() is False
    window.close()


def test_set_results_switches_to_search_and_paginates(qapp):
    mods = make_mods(25)
    window = make_shown_window(qapp)
    wait_for_load(window, qapp)
    window.set_results(mods)

    assert window._mode == "search"
    assert len(window._search_mods) == 25
    assert window.btn_gb_back.isVisible() is True
    # page_size=12 -> 3 страницы: 12, 12, 1
    assert window.gb_page_label.text() == "Страница 1 из 3"
    assert window.btn_gb_prev.isEnabled() is False
    assert window.btn_gb_next.isEnabled() is True

    # переход на последнюю страницу
    window._next_page()
    assert window._current_page == 1
    assert window.gb_page_label.text() == "Страница 2 из 3"
    window._next_page()
    assert window._current_page == 2
    assert window.gb_page_label.text() == "Страница 3 из 3"
    # дальше не ходим
    window._next_page()
    assert window._current_page == 2

    # назад
    window._prev_page()
    assert window._current_page == 1
    window.close()


def test_set_results_empty(qapp):
    window = make_shown_window(qapp)
    wait_for_load(window, qapp)
    window.set_results([])
    assert window._mode == "search"
    assert window.gb_page_label.text() == "Поиск"
    assert window.btn_gb_prev.isEnabled() is False
    assert window.btn_gb_next.isEnabled() is False
    window.close()


def test_back_to_popular(qapp):
    window = make_shown_window(qapp)
    wait_for_load(window, qapp)

    mods = make_mods(25)
    window.set_results(mods)
    assert window._mode == "search"
    assert window.btn_gb_back.isVisible() is True

    window._on_back_to_popular()
    assert window._mode == "popular"
    assert window._current_page == 0
    assert window.btn_gb_back.isVisible() is False
    window.close()


def test_lazily_loaded_pages_render(qapp, monkeypatch):
    """Ленивая загрузка: каждая страница Mod/Index даёт несколько модов."""
    all_mods = make_mods(36)
    state = {"page": 0}

    def fake_fetch(page, days=7):
        state["page"] += 1
        chunk = all_mods[(state["page"] - 1) * 12 : state["page"] * 12]
        return chunk, {"_nRecordCount": 5126}

    monkeypatch.setattr("ui.gamebanana_window.fetch_recent_mods_page", fake_fetch)

    window = make_shown_window(qapp)
    # после первого fetch: page 1 заполнена (12 модов), label "Страница 1 из 1"
    wait_for_load(window, qapp)
    assert len(window._popular_mods) == 12
    # не exhausted (отсканировано 1 из 103 страниц) -> без общего числа
    assert window.gb_page_label.text() == "Страница 1"

    # next -> подтягивает следующую страницу Mod/Index (асинхронно)
    window._next_page()
    wait_for_load(window, qapp)
    assert len(window._popular_mods) == 24
    assert window.gb_page_label.text() == "Страница 2"

    window._next_page()
    wait_for_load(window, qapp)
    assert len(window._popular_mods) == 36
    assert window.gb_page_label.text() == "Страница 3"
    # total_pages = ceil(5126/50)=103, next_page=4 <= 103 -> не exhausted
    assert window._popular_exhausted is False
    window.close()


def test_popular_next_at_boundary_triggers_fetch(qapp, monkeypatch):
    """На границе загруженных данных «next» подтягивает ещё страницу."""
    all_mods = make_mods(24)
    state = {"page": 0}

    def fake_fetch(page, days=7):
        state["page"] += 1
        chunk = all_mods[(state["page"] - 1) * 12 : state["page"] * 12]
        return chunk, {"_nRecordCount": 5126}

    monkeypatch.setattr("ui.gamebanana_window.fetch_recent_mods_page", fake_fetch)

    window = make_shown_window(qapp)
    wait_for_load(window, qapp)
    assert len(window._popular_mods) == 12
    # page_size=12, full_pages=1 -> можно уйти только на страницу index 1
    window._next_page()
    wait_for_load(window, qapp)
    assert len(window._popular_mods) == 24
    # не exhausted -> без общего числа
    assert window.gb_page_label.text() == "Страница 2"
    window.close()


def test_popular_error_does_not_crash(qapp, monkeypatch):
    def fake_fetch(page, days=7):
        raise Exception("network down")

    monkeypatch.setattr("ui.gamebanana_window.fetch_recent_mods_page", fake_fetch)

    window = make_shown_window(qapp)
    wait_for_load(window, qapp)
    # ошибка загрузки страницы -> статус с ошибкой, окно не упало
    assert "Не удалось загрузить страницу" in window.gb_status.text()
    assert window._popular_loading is False
    window.close()


def test_search_pagination_clamps_current_page(qapp):
    """set_results сбрасывает current_page и рендерит первую страницу."""
    mods = make_mods(5)
    window = make_shown_window(qapp)
    wait_for_load(window, qapp)
    # искусственно сдвигаем current_page вперёд
    window._current_page = 99
    window.set_results(mods)
    assert window._current_page == 0
    assert window.gb_page_label.text() == "Поиск"
    window.close()


def test_back_to_popular_item_click_uses_correct_mod(qapp, monkeypatch):
    """Возврат из поиска к популярным не ломает клик по обложке (gb_mods синхронизирован)."""
    popular_mods = [GBMod(mod_id=100 + i, name=f"pop {i}", downloads=0) for i in range(12)]
    search_mods = [GBMod(mod_id=200 + i, name=f"srch {i}", downloads=0) for i in range(5)]
    state = {"page": 0}

    def fake_fetch(page, days=7):
        state["page"] += 1
        chunk = popular_mods[(state["page"] - 1) * 12 : state["page"] * 12]
        return chunk, {"_nRecordCount": 5126}

    monkeypatch.setattr("ui.gamebanana_window.fetch_recent_mods_page", fake_fetch)

    window = make_shown_window(qapp)
    wait_for_load(window, qapp)
    assert len(window._popular_mods) == 12

    # уходим в поиск: gb_mods заполняется поисковыми модами
    window.set_results(search_mods)
    assert window._mode == "search"
    assert set(window.gb_mods) == {200, 201, 202, 203, 204}

    # возвращаемся к популярным: страница уже закеширована, новый fetch не запускается
    window._on_back_to_popular()
    assert window._mode == "popular"
    # gb_mods должен быть обновлён на популярные моды
    assert set(window.gb_mods) == {100, 101, 102, 103, 104, 105, 106, 107, 108, 109, 110, 111}

    # клик по популярной обложке -> _show_details вызывается с правильным модом
    item = window.gb_gallery.item(0)
    assert item is not None
    window._on_item_clicked(item)
    assert window.gb_current_mod is not None
    assert window.gb_current_mod.mod_id == 100
    window.close()


def test_download_progress_and_cancel(qapp):
    """Прогресс скачивания виден в карточке, кнопка «Прервать» вызывает менеджер."""
    window = make_shown_window(qapp)
    wait_for_load(window, qapp)
    cancelled = []
    window.manager.cancel_gb_download = lambda: cancelled.append(True) or True

    window.set_downloading()
    assert window.gb_download_progress.isVisible() is True
    assert window.btn_gb_cancel_download.isVisible() is True

    window.on_download_progress(50, 100)
    assert window.gb_download_progress.maximum() == 100
    assert window.gb_download_progress.value() == 50

    # Неизвестный размер — бегущая полоса.
    window.on_download_progress(1, 0)
    assert window.gb_download_progress.maximum() == 0

    window._cancel_download()
    assert cancelled == [True]
    assert window.btn_gb_cancel_download.isEnabled() is False

    window.on_download_cancelled()
    assert window.gb_download_progress.isVisible() is False
    assert window.btn_gb_cancel_download.isVisible() is False
    window.close()
