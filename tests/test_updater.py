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


def test_fetch_latest_release_public_does_not_send_token(monkeypatch):
    import core.updater as _up

    captured = {}

    class FakeResp:
        def __init__(self):
            self.headers = {"Content-Length": "0"}

        def read(self):
            return b'{"tag_name":"2.0","name":"v2.0","html_url":"https://x","assets":[]}'

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    def fake_urlopen(request, timeout=15):
        captured["auth"] = request.get_header("Authorization")
        return FakeResp()

    monkeypatch.setenv("GITHUB_TOKEN", "secret")
    monkeypatch.setattr(_up.urllib.request, "urlopen", fake_urlopen)

    _release, error = _up.fetch_latest_release(repo="owner/repo")
    assert error is None
    # Публичный репозиторий: токен не нужен и не отправляется.
    assert captured["auth"] is None


def test_fetch_latest_release_retries_with_env_token(monkeypatch):
    import io

    import core.updater as _up

    calls = []

    class FakeResp:
        def __init__(self):
            self.headers = {"Content-Length": "0"}

        def read(self):
            return b'{"tag_name":"2.0","name":"v2.0","html_url":"https://x","assets":[]}'

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    def fake_urlopen(request, timeout=15):
        calls.append(request.get_header("Authorization"))
        if len(calls) == 1:
            raise _up.urllib.error.HTTPError(
                request.full_url, 404, "Not Found", {}, io.BytesIO(b"")
            )
        return FakeResp()

    monkeypatch.setenv("GITHUB_TOKEN", "secret")
    monkeypatch.setattr(_up.urllib.request, "urlopen", fake_urlopen)

    _release, error = _up.fetch_latest_release(repo="owner/private")
    assert error is None
    assert calls == [None, "Bearer secret"]


def test_download_asset_public_does_not_send_token(tmp_path, monkeypatch):
    import core.updater as _up

    captured = {}

    class FakeResponse:
        def __init__(self):
            self._data = b"payload"
            self.headers = {"Content-Length": str(len(self._data))}

        def read(self, size=65536):
            chunk, self._data = self._data[:size], self._data[size:]
            return chunk

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    class FakeOpener:
        def open(self, request, timeout=None):
            captured["auth"] = request.get_header("Authorization")
            return FakeResponse()

    monkeypatch.setenv("GITHUB_TOKEN", "secret")
    monkeypatch.setattr(
        _up.urllib.request, "build_opener", lambda *args, **kwargs: FakeOpener()
    )

    dest = tmp_path / "update.zip"
    assert _up.download_asset("https://example.invalid/update.zip", dest) is True
    assert dest.read_bytes() == b"payload"
    assert captured["auth"] is None


def test_download_asset_retries_with_token(tmp_path, monkeypatch):
    import io

    import core.updater as _up

    calls = []

    class FakeResponse:
        def __init__(self):
            self._data = b"payload"
            self.headers = {"Content-Length": str(len(self._data))}

        def read(self, size=65536):
            chunk, self._data = self._data[:size], self._data[size:]
            return chunk

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    class FakeOpener:
        def open(self, request, timeout=None):
            calls.append(request.get_header("Authorization"))
            if len(calls) == 1:
                raise _up.urllib.error.HTTPError(
                    request.full_url, 404, "Not Found", {}, io.BytesIO(b"")
                )
            return FakeResponse()

    monkeypatch.setenv("GITHUB_TOKEN", "secret")
    monkeypatch.setattr(
        _up.urllib.request, "build_opener", lambda *args, **kwargs: FakeOpener()
    )

    dest = tmp_path / "update.zip"
    assert _up.download_asset("https://example.invalid/update.zip", dest) is True
    assert dest.read_bytes() == b"payload"
    assert calls == [None, "Bearer secret"]


def test_update_helper_script_is_ascii_and_extracts_after_exit(tmp_path):
    from core.updater import build_update_helper_script

    script = build_update_helper_script(
        zip_path=tmp_path / "update.zip",
        install_dir=tmp_path / "app",
        exe_path=tmp_path / "app" / "DeadlockModManager.exe",
        pid=1234,
        marker_path=tmp_path / "pending_update.json",
        error_path=tmp_path / "update_error.txt",
    )

    script.encode("ascii")  # helper должен быть ASCII-only для cmd
    assert 'set "APP_PID=1234"' in script
    assert "tasklist" in script
    assert "goto app_exited" in script
    assert "tar -xf" in script
    assert "powershell" in script
    assert "--update-installed" in script
