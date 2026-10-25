"""Тесты модуля self-update: сравнение версий и проверка обновлений."""

import pytest

from core.updater import (
    APP_VERSION,
    ReleaseInfo,
    check_for_update,
    compare_versions,
)


class _FakeHttpError(Exception):
    """Имитация ошибки HTTP в тестах."""


@pytest.mark.parametrize(
    "a,b,expected",
    [
        ("1.2", "1.2", 0),
        ("1.2", "1.3", -1),
        ("1.3", "1.2", 1),
        ("v1.2", "1.2", 0),
        ("1.0", "1.10", -1),
        ("1.10", "1.9", 1),
        ("2", "2.0", 0),
        ("2.0", "3", -1),
        ("1.2.0", "1.2", 0),
        ("1.2.1", "1.2.0", 1),
    ],
)
def test_compare_versions(a, b, expected):
    assert compare_versions(a, b) == expected


def test_check_for_update_up_to_date(monkeypatch):
    monkeypatch.setattr(
        "core.updater.fetch_latest_release",
        lambda repo=None, token=None: (ReleaseInfo("1.2", "v1.2", "https://x/a.zip"), None),
    )
    result = check_for_update()

    assert result.current_version == APP_VERSION
    assert result.latest_version == "1.2"
    assert result.is_newer is False
    assert result.is_up_to_date is True


def test_check_for_update_newer(monkeypatch):
    monkeypatch.setattr(
        "core.updater.fetch_latest_release",
        lambda repo=None, token=None: (ReleaseInfo("2.0", "v2.0", "https://x/b.zip"), None),
    )
    result = check_for_update()

    assert result.latest_version == "2.0"
    assert result.is_newer is True
    assert result.release.download_url == "https://x/b.zip"


def test_check_for_update_error(monkeypatch):
    monkeypatch.setattr(
        "core.updater.fetch_latest_release",
        lambda repo=None, token=None: (None, "Не найдено ни одного релиза."),
    )
    result = check_for_update()

    assert result.latest_version is None
    assert result.is_newer is False
    assert result.error == "Не найдено ни одного релиза."


def test_fetch_latest_release_prefers_deadlock_zip(monkeypatch):
    """Выбор ассета: zip с 'deadlock' в имени важнее, затем самый большой."""

    def fake_urlopen(request, timeout=15):
        import json as _json

        class FakeResp:
            def __init__(self):
                self.headers = {"Content-Length": "0"}

            def read(self):
                return (
                    _json.dumps(
                        {
                            "tag_name": "2.0",
                            "name": "v2.0",
                            "html_url": "https://github.com/FT-123/deadloh/releases/tag/v2.0",
                            "assets": [
                                {"name": "other.exe", "browser_download_url": "https://x/o.exe", "size": 999},
                                {"name": "deadlock_2.0.zip", "browser_download_url": "https://x/d.zip", "size": 100},
                                {"name": "big_deadlock.zip", "browser_download_url": "https://x/big.zip", "size": 5000},
                            ],
                        }
                    ).encode("utf-8")
                )

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

        return FakeResp()

    import core.updater as _up

    monkeypatch.setattr(_up.urllib.request, "urlopen", fake_urlopen)

    release, error = _up.fetch_latest_release(repo="FT-123/deadloh")
    assert error is None
    # среди deadlock-zip выбирается самый большой (основная сборка)
    assert release.asset_name == "big_deadlock.zip"
    assert release.is_zip is True


def test_fetch_latest_release_no_releases(monkeypatch):
    """404 → None и понятная ошибка."""

    def fake_urlopen(request, timeout=15):
        raise _FakeHttpError("http 404")

    import core.updater as _up

    monkeypatch.setattr(_up.urllib.request, "urlopen", fake_urlopen)

    release, error = _up.fetch_latest_release(repo="FT-123/deadloh")
    assert release is None
    assert error is not None


def test_fetch_latest_release_no_assets(monkeypatch):
    """Релиз без файлов → download_url = html_url."""

    def fake_urlopen(request, timeout=15):
        import json as _json

        class FakeResp:
            def __init__(self):
                self.headers = {"Content-Length": "0"}

            def read(self):
                return (
                    _json.dumps(
                        {
                            "tag_name": "2.0",
                            "name": "v2.0",
                            "html_url": "https://github.com/FT-123/deadloh/releases/tag/v2.0",
                            "assets": [],
                        }
                    ).encode("utf-8")
                )

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

        return FakeResp()

    import core.updater as _up

    monkeypatch.setattr(_up.urllib.request, "urlopen", fake_urlopen)

    release, error = _up.fetch_latest_release(repo="FT-123/deadloh")
    assert error is None
    assert release.asset_name == ""
    assert release.is_zip is False
    assert release.download_url == "https://github.com/FT-123/deadloh/releases/tag/v2.0"
