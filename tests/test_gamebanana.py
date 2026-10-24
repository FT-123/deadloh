import time

from core.gamebanana import (
    GameBananaError,
    GBFile,
    GBMod,
    download_file_bytes,
    get_mod,
    get_mod_files,
    get_popular_mods,
    search_mods,
)


def test_get_mod_parses_item_data(monkeypatch):
    def fake_gb_get(endpoint, params=None):
        assert endpoint == "Core/Item/Data"
        assert params["itemid"] == 123
        return {"name": "Gravity Gun", "downloads": 10, "likes": 2, "date": 1700000000}

    monkeypatch.setattr("core.gamebanana._gb_get", fake_gb_get)

    mod = get_mod(123)

    assert mod.mod_id == 123
    assert mod.name == "Gravity Gun"
    assert mod.downloads == 10
    assert mod.likes == 2
    assert mod.files == []


def test_get_mod_files_orders_latest_first(monkeypatch):
    def fake_gb_get(endpoint, params=None):
        if params and "Files().aFiles()" in (params.get("fields") or ""):
            return {
                "Files().aFiles()": {
                    "2": {"_idRow": 2, "_sFile": "b_mod.vpk", "_nFilesize": 100, "_tsDateAdded": 200, "_nDownloadCount": 5},
                    "1": {"_idRow": 1, "_sFile": "a_mod.vpk", "_nFilesize": 90, "_tsDateAdded": 100, "_nDownloadCount": 4},
                }
            }
        return {"name": "Gravity Gun", "downloads": 10, "likes": 2, "date": 1700000000}

    monkeypatch.setattr("core.gamebanana._gb_get", fake_gb_get)

    files = get_mod_files(123)

    assert [f.file_id for f in files] == [2, 1]
    assert files[0].name == "b_mod.vpk"
    assert files[0].download_url == "https://gamebanana.com/dl/2"
    assert files[0].size == 100


def test_search_mods_uses_search_api_with_deadlock_filter(monkeypatch):
    def fake_gb_get(endpoint, params=None, base_url=None):
        assert endpoint == "Util/Search/Results"
        assert params["_idGameRow"] == 20948
        assert params["_sSearchString"] == "gravity"
        assert base_url == "https://gamebanana.com/apiv11"
        return {
            "_aMetadata": {"_bIsComplete": True},
            "_aRecords": [
                {
                    "_idRow": 1,
                    "_sName": "Gravity Gun",
                    "_tsDateAdded": 1700000000,
                    "_nLikeCount": 7,
                    "_nViewCount": 100,
                    "_sProfileUrl": "https://gamebanana.com/mods/1",
                    "_aSubmitter": {"_sName": "author"},
                    "_aRootCategory": {"_sName": "Skins"},
                    "_aSubCategory": {"_sName": "Weapons"},
                    "_aPreviewMedia": {
                        "_aImages": [
                            {
                                "_sBaseUrl": "https://images.gamebanana.com/img/ss/mods",
                                "_sFile": "full.jpg",
                                "_sFile220": "220-90_thumb.jpg",
                                "_sFile530": "530-90_large.jpg",
                            }
                        ]
                    },
                }
            ],
        }

    monkeypatch.setattr("core.gamebanana._gb_get", fake_gb_get)

    mods = search_mods("gravity", max_pages=1)

    assert [m.mod_id for m in mods] == [1]
    mod = mods[0]
    assert mod.name == "Gravity Gun"
    assert mod.author == "author"
    assert mod.category == "Skins / Weapons"
    assert mod.likes == 7
    assert mod.views == 100
    assert mod.image_url == "https://images.gamebanana.com/img/ss/mods/220-90_thumb.jpg"
    assert mod.image_large_url == "https://images.gamebanana.com/img/ss/mods/530-90_large.jpg"
    assert mod.profile_url == "https://gamebanana.com/mods/1"


def test_search_mods_without_images_has_empty_urls(monkeypatch):
    def fake_gb_get(endpoint, params=None, base_url=None):
        return {
            "_aMetadata": {"_bIsComplete": True},
            "_aRecords": [{"_idRow": 2, "_sName": "No Art"}],
        }

    monkeypatch.setattr("core.gamebanana._gb_get", fake_gb_get)

    mods = search_mods("no art")

    assert mods[0].image_url == ""
    assert mods[0].image_large_url == ""


def test_download_file_bytes_sends_user_agent(monkeypatch):
    captured = {}

    class FakeResponse:
        def __init__(self):
            self._data = b"VPK-DATA"

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def read(self, size=None):
            if not self._data:
                return b""
            chunk = self._data[:size] if size else self._data
            self._data = self._data[len(chunk):]
            return chunk

    def fake_urlopen(request, timeout=None):
        captured["url"] = request.full_url
        captured["user_agent"] = request.get_header("User-agent")
        captured["referer"] = request.get_header("Referer")
        return FakeResponse()

    monkeypatch.setattr("core.gamebanana.urllib.request.urlopen", fake_urlopen)
    monkeypatch.setattr(
        "core.gamebanana.get_mod",
        lambda mod_id: GBMod(mod_id=mod_id, name="Test Mod", downloads=0),
    )
    monkeypatch.setattr(
        "core.gamebanana.get_mod_files",
        lambda mod_id: [
            GBFile(
                file_id=1,
                name="pak31_dir_1cc3e.zip",
                size=10,
                date_added=1,
                downloads=0,
                download_url="https://gamebanana.com/dl/1",
            )
        ],
    )

    mod, gb_file, data = download_file_bytes(42)

    assert mod.name == "Test Mod"
    assert gb_file.name == "pak31_dir_1cc3e.zip"
    assert data == b"VPK-DATA"
    assert captured["url"] == "https://gamebanana.com/dl/1"
    assert captured["user_agent"] == "DeadlockModManager/1.0"
    assert captured["referer"] == "https://gamebanana.com/"


def test_download_file_bytes_reports_progress(monkeypatch):
    progress_calls = []

    class FakeResponse:
        def __init__(self):
            self._data = b"A" * 200000
            self.headers = {"Content-Length": "200000"}

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def read(self, size=None):
            if not self._data:
                return b""
            chunk = self._data[:size] if size else self._data
            self._data = self._data[len(chunk):]
            return chunk

    def fake_urlopen(request, timeout=None):
        return FakeResponse()

    monkeypatch.setattr("core.gamebanana.urllib.request.urlopen", fake_urlopen)
    monkeypatch.setattr(
        "core.gamebanana.get_mod",
        lambda mod_id: GBMod(mod_id=mod_id, name="Test Mod", downloads=0),
    )
    monkeypatch.setattr(
        "core.gamebanana.get_mod_files",
        lambda mod_id: [
            GBFile(
                file_id=1,
                name="pak31_dir_1cc3e.zip",
                size=200000,
                date_added=1,
                downloads=0,
                download_url="https://gamebanana.com/dl/1",
            )
        ],
    )

    def on_progress(received, total):
        progress_calls.append((received, total))

    mod, gb_file, data = download_file_bytes(42, progress_callback=on_progress)

    assert len(data) == 200000
    assert progress_calls[-1][0] == 200000
    assert all(total == 200000 for _, total in progress_calls)
    receiveds = [received for received, _ in progress_calls]
    assert receiveds == sorted(receiveds)


def test_gamebanana_error_raised_on_bad_response(monkeypatch):
    def fake_gb_get(endpoint, params=None, base_url=None):
        raise GameBananaError("GameBanana: запрос не удался")

    monkeypatch.setattr("core.gamebanana._gb_get", fake_gb_get)

    try:
        search_mods("gravity")
    except GameBananaError:
        pass
    else:
        raise AssertionError("GameBananaError expected")


def test_get_popular_mods_filters_recent_and_ranks_by_popularity(monkeypatch):
    now = int(time.time())
    recent = now - 86400          # 1 день назад -> попадает в окно
    old = now - 30 * 86400        # 30 дней назад -> отбрасывается

    def rec(mod_id, name, likes, views, ts):
        return {
            "_idRow": mod_id,
            "_sName": name,
            "_tsDateAdded": ts,
            "_tsDateModified": ts,
            "_nLikeCount": likes,
            "_nViewCount": views,
            "_aSubmitter": {"_sName": "author"},
        }

    def fake_gb_get(endpoint, params=None, base_url=None):
        assert endpoint == "Mod/Index"
        assert params["_aFilters[Generic_Game]"] == 20948
        page = params["_nPage"]
        if page == 1:
            return {
                "_aMetadata": {"_nRecordCount": 60},
                "_aRecords": [rec(1, "Alpha", 5, 100, recent), rec(2, "Beta", 10, 50, old)],
            }
        return {
            "_aMetadata": {"_nRecordCount": 60},
            "_aRecords": [rec(3, "Gamma", 1, 200, recent), rec(4, "Delta", 0, 0, recent)],
        }

    monkeypatch.setattr("core.gamebanana._gb_get", fake_gb_get)

    mods = get_popular_mods(days=7)

    assert 2 not in [m.mod_id for m in mods]  # Beta старый -> не в списке
    assert {m.mod_id for m in mods} == {1, 3, 4}
    # Ранжирование по лайкам+просмотрам: Gamma(201) > Alpha(105) > Delta(0)
    assert [m.mod_id for m in mods] == [3, 1, 4]


def test_get_popular_mods_raises_on_api_error(monkeypatch):
    def fake_gb_get(endpoint, params=None, base_url=None):
        raise GameBananaError("GameBanana: запрос не удался")

    monkeypatch.setattr("core.gamebanana._gb_get", fake_gb_get)

    try:
        get_popular_mods()
    except GameBananaError:
        pass
    else:
        raise AssertionError("GameBananaError expected")


def test_search_mods_filters_by_character(monkeypatch):
    captured = {}

    def fake_gb_get(endpoint, params=None, base_url=None):
        assert endpoint == "Util/Search/Results"
        captured["params"] = params
        return {
            "_aMetadata": {"_bIsComplete": True},
            "_aRecords": [
                {"_idRow": 1, "_sName": "Haze Weapon", "_tsDateAdded": 1700000000}
            ],
        }

    monkeypatch.setattr("core.gamebanana._gb_get", fake_gb_get)

    mods = search_mods("weapon", character="Haze", max_pages=1)

    assert [m.mod_id for m in mods] == [1]
    # персонаж подмешивается в строку поиска (персонаж первичный)
    assert captured["params"]["_sSearchString"] == "Haze weapon"


def test_search_mods_character_only(monkeypatch):
    captured = {}

    def fake_gb_get(endpoint, params=None, base_url=None):
        assert endpoint == "Util/Search/Results"
        captured["params"] = params
        return {
            "_aMetadata": {"_bIsComplete": True},
            "_aRecords": [
                {"_idRow": 1, "_sName": "Haze", "_tsDateAdded": 1700000000}
            ],
        }

    monkeypatch.setattr("core.gamebanana._gb_get", fake_gb_get)

    mods = search_mods("", character="Haze", max_pages=1)

    assert [m.mod_id for m in mods] == [1]
    # без текста — строка поиска равна имени персонажа
    assert captured["params"]["_sSearchString"] == "Haze"


def test_search_mods_no_query_and_no_character_returns_empty(monkeypatch):
    def fake_gb_get(endpoint, params=None, base_url=None):
        raise AssertionError("запрос не должен уходить")

    monkeypatch.setattr("core.gamebanana._gb_get", fake_gb_get)

    mods = search_mods("", character="", max_pages=1)

    assert mods == []


def test_search_mods_existing_query_unchanged(monkeypatch):
    captured = {}

    def fake_gb_get(endpoint, params=None, base_url=None):
        assert endpoint == "Util/Search/Results"
        captured["params"] = params
        return {
            "_aMetadata": {"_bIsComplete": True},
            "_aRecords": [
                {"_idRow": 1, "_sName": "Gravity Gun", "_tsDateAdded": 1700000000}
            ],
        }

    monkeypatch.setattr("core.gamebanana._gb_get", fake_gb_get)

    mods = search_mods("gravity", max_pages=1)

    assert [m.mod_id for m in mods] == [1]
    # без персонажа строка поиска не меняется (назад-совместимость)
    assert captured["params"]["_sSearchString"] == "gravity"
