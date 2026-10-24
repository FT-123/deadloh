"""Клиент GameBanana API: поиск модов и скачивание последней версии.

GameBanana (https://gamebanana.com) — каталог модов для Deadlock
(Steam app 1422450, внутри игры gameid 20948).

Поиск идёт через публичный поисковый API v11
(https://gamebanana.com/apiv11/Util/Search/Results): один запрос отдаёт имя,
обложку и автора мода, а фильтр **_idGameRow=20948** ограничивает выдачу
только Deadlock. Статистика (загрузки) и список файлов догружаются отдельно
через https://api.gamebanana.com/Core/Item/Data.
"""

import concurrent.futures
import json
import math
import time
import urllib.parse
import urllib.request
from dataclasses import asdict, dataclass, field
from typing import Any

GAMEBANANA_BASE_URL = "https://api.gamebanana.com"
GAMEBANANA_API11_URL = "https://gamebanana.com/apiv11"
GAMEBANANA_DOWNLOAD_BASE_URL = "https://gamebanana.com"
DEADLOCK_GAME_ID = 20948
ITEM_TYPE_MOD = "Mod"

GB_FIELDS = "name,downloads,likes,date"
GB_FILES_FIELD = "Files().aFiles()"
GB_SEARCH_ENDPOINT = "Util/Search/Results"
GB_FLAGS = "JSON_UNESCAPED_SLASHES"
GB_FORMAT = "json"
GB_USER_AGENT = "DeadlockModManager/1.0"

GB_MAX_PAGES = 20
GB_TIMEOUT_SECONDS = 30
GB_DOWNLOAD_TIMEOUT_SECONDS = 60

POPULAR_WEEK_DAYS = 7
POPULAR_SCAN_PERPAGE = 50
POPULAR_CONCURRENCY = 8


class GameBananaError(Exception):
    """Ошибка обращения к GameBanana API или неожиданный формат ответа."""


@dataclass
class GBFile:
    """Файл мода внутри GameBanana."""

    file_id: int
    name: str
    size: int
    date_added: int
    downloads: int
    download_url: str
    md5: str | None = None
    is_archived: bool = False
    has_contents: bool = False
    version: str | None = None
    description: str | None = None


@dataclass
class GBMod:
    """Мод, найденный в каталоге GameBanana."""

    mod_id: int
    name: str
    downloads: int
    likes: int = 0
    date: int = 0
    files: list[GBFile] = field(default_factory=list)
    image_url: str = ""
    image_large_url: str = ""
    author: str = ""
    category: str = ""
    version: str = ""
    views: int = 0
    profile_url: str = ""

    @property
    def latest_file(self) -> GBFile | None:
        """Файл последней версии мода (самый свежий первый в списке)."""
        return self.files[0] if self.files else None


def _to_int(value: Any, default: int = 0) -> int:
    try:
        return int(str(value))
    except (TypeError, ValueError):
        return default


def _gb_get(
    endpoint: str,
    params: dict[str, Any] | None = None,
    base_url: str = GAMEBANANA_BASE_URL,
) -> Any:
    url = f"{base_url}/{endpoint.strip('/')}"
    if params:
        url = f"{url}?{urllib.parse.urlencode(params)}"
    request = urllib.request.Request(
        url,
        headers={"Accept": "application/json", "User-Agent": GB_USER_AGENT},
        method="GET",
    )
    try:
        with urllib.request.urlopen(request, timeout=GB_TIMEOUT_SECONDS) as response:
            raw = response.read()
    except Exception as exc:
        raise GameBananaError(f"GameBanana: запрос к «{endpoint}» не удался — {exc}") from exc
    try:
        return json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as exc:
        raise GameBananaError("GameBanana: получен некорректный JSON-ответ") from exc


def _get_mod_data(mod_id: int, fields: str) -> dict[str, Any]:
    data = _gb_get(
        "Core/Item/Data",
        {
            "itemtype": ITEM_TYPE_MOD,
            "itemid": int(mod_id),
            "fields": fields,
            "return_keys": 1,
            "flags": GB_FLAGS,
            "format": GB_FORMAT,
        },
    )
    if not isinstance(data, dict):
        raise GameBananaError(f"GameBanana: неожиданный ответ для мода {mod_id}")
    return data


def get_mod(mod_id: int) -> GBMod:
    """Возвращает базовую информацию о моде по его GameBanana ID."""
    data = _get_mod_data(mod_id, GB_FIELDS)
    name = str(data.get("name") or f"Мод {mod_id}")
    return GBMod(
        mod_id=int(mod_id),
        name=name,
        downloads=_to_int(data.get("downloads")),
        likes=_to_int(data.get("likes")),
        date=_to_int(data.get("date")),
    )


def get_mod_files(mod_id: int) -> list[GBFile]:
    """Возвращает файлы мода, отсортированные с последней версии первой."""
    data = _get_mod_data(mod_id, GB_FILES_FIELD)
    files_raw = data.get(GB_FILES_FIELD) if isinstance(data, dict) else None

    entries: list[dict[str, Any]] = []
    if isinstance(files_raw, dict):
        entries = [value for _, value in sorted(files_raw.items(), key=lambda kv: str(kv[0])) if isinstance(value, dict)]
    elif isinstance(files_raw, list):
        entries = [value for value in files_raw if isinstance(value, dict)]

    files: list[GBFile] = []
    for entry in entries:
        file_id = _to_int(entry.get("_idRow"))
        name = str(entry.get("_sFile") or f"file_{file_id}")
        files.append(
            GBFile(
                file_id=file_id,
                name=name,
                size=_to_int(entry.get("_nFilesize")),
                date_added=_to_int(entry.get("_tsDateAdded")),
                downloads=_to_int(entry.get("_nDownloadCount")),
                download_url=str(
                    entry.get("_sDownloadUrl") or f"{GAMEBANANA_DOWNLOAD_BASE_URL}/dl/{file_id}"
                ),
                md5=entry.get("_sMd5Checksum"),
                is_archived=bool(entry.get("_bIsArchived")),
                has_contents=bool(entry.get("_bHasContents")),
                version=entry.get("_sVersion"),
                description=entry.get("_sDescription"),
            )
        )
    files.sort(key=lambda f: f.date_added, reverse=True)
    return files


def _image_urls(media: Any) -> tuple[str, str]:
    """Возвращает (миниатюра, крупное изображение) из блока ``_aPreviewMedia``."""
    if not isinstance(media, dict):
        return "", ""
    images = media.get("_aImages")
    if not isinstance(images, list) or not images or not isinstance(images[0], dict):
        return "", ""
    image = images[0]
    base = str(image.get("_sBaseUrl") or "").rstrip("/")

    def build(*keys: str) -> str:
        for key in keys:
            file_name = str(image.get(key) or "").strip()
            if file_name:
                return f"{base}/{file_name}" if base else file_name
        return ""

    thumbnail = build("_sFile220", "_sFile100", "_sFile")
    large = build("_sFile530", "_sFile", "_sFile220")
    return thumbnail, large


def _mod_from_search_record(record: dict[str, Any]) -> GBMod | None:
    """Преобразует запись поисковой выдачи GameBanana в :class:`GBMod`."""
    mod_id = _to_int(record.get("_idRow"))
    if mod_id <= 0:
        return None

    thumbnail, large = _image_urls(record.get("_aPreviewMedia"))

    submitter = record.get("_aSubmitter")
    author = str(submitter.get("_sName") or "") if isinstance(submitter, dict) else ""

    root_category = record.get("_aRootCategory")
    sub_category = record.get("_aSubCategory")
    root_name = str(root_category.get("_sName") or "") if isinstance(root_category, dict) else ""
    sub_name = str(sub_category.get("_sName") or "") if isinstance(sub_category, dict) else ""
    category = " / ".join(part for part in (root_name, sub_name) if part)

    return GBMod(
        mod_id=mod_id,
        name=str(record.get("_sName") or f"Мод {mod_id}"),
        downloads=0,
        likes=_to_int(record.get("_nLikeCount")),
        date=_to_int(record.get("_tsDateAdded")),
        image_url=thumbnail,
        image_large_url=large,
        author=author,
        category=category,
        version=str(record.get("_sVersion") or ""),
        views=_to_int(record.get("_nViewCount")),
        profile_url=str(
            record.get("_sProfileUrl") or f"{GAMEBANANA_DOWNLOAD_BASE_URL}/mods/{mod_id}"
        ),
    )


def search_mods(
    query: str,
    character: str = "",
    max_pages: int = GB_MAX_PAGES,
) -> list[GBMod]:
    """Ищет моды Deadlock по названию и/или игровому персонажу через поисковый API GameBanana.

    ``character`` — отображаемое имя персонажа (например, «Haze»). Если задано,
    оно подмешивается в строку поиска, чтобы выдача ограничивалась модами этого
    героя; если не задано — поиск идёт только по ``query``.
    """
    normalized = " ".join(str(query).split()).strip()
    character_norm = " ".join(str(character).split()).strip()

    # Строка поиска: персонаж первичный, затем текст запроса.
    if character_norm and normalized:
        search_string = f"{character_norm} {normalized}"
    else:
        search_string = character_norm or normalized
    if not search_string:
        return []

    found: list[GBMod] = []
    for page in range(1, max_pages + 1):
        data = _gb_get(
            GB_SEARCH_ENDPOINT,
            {
                "_sSearchString": search_string,
                "_sModelName": ITEM_TYPE_MOD,
                "_idGameRow": DEADLOCK_GAME_ID,
                "_nPage": page,
            },
            base_url=GAMEBANANA_API11_URL,
        )
        if not isinstance(data, dict):
            raise GameBananaError(f"GameBanana: неожиданный ответ поиска на странице {page}")

        records = data.get("_aRecords")
        if not isinstance(records, list) or not records:
            break

        for record in records:
            if not isinstance(record, dict):
                continue
            mod = _mod_from_search_record(record)
            if mod is None:
                continue
            found.append(mod)

        metadata = data.get("_aMetadata")
        if isinstance(metadata, dict) and metadata.get("_bIsComplete"):
            break
    return found


def _fetch_mod_index_page(page: int, perpage: int = POPULAR_SCAN_PERPAGE):
    """Один запрос к Mod/Index для Deadlock. Возвращает (записи, метаданные)."""
    data = _gb_get(
        "Mod/Index",
        {
            "_aFilters[Generic_Game]": DEADLOCK_GAME_ID,
            "_nPage": page,
            "_nPerpage": perpage,
        },
        base_url=GAMEBANANA_API11_URL,
    )
    if not isinstance(data, dict):
        raise GameBananaError(f"GameBanana: неожиданный ответ Mod/Index на странице {page}")
    records = data.get("_aRecords") or []
    meta = data.get("_aMetadata") or {}
    return [r for r in records if isinstance(r, dict)], meta


def fetch_recent_mods_page(page: int, days: int = POPULAR_WEEK_DAYS) -> tuple[list[GBMod], dict]:
    """Одна страница Mod/Index, отфильтрованная по последним ``days`` дням.

    Возвращает (список :class:`GBMod` за эту страницу, метаданные страницы).
    Мод попадает в список, если он добавлен или изменён в заданном окне.
    """
    now = int(time.time())
    cutoff = now - days * 86400
    records, meta = _fetch_mod_index_page(page)
    mods: list[GBMod] = []
    for record in records:
        if (
            _to_int(record.get("_tsDateAdded")) >= cutoff
            or _to_int(record.get("_tsDateModified")) >= cutoff
        ):
            mod = _mod_from_search_record(record)
            if mod is not None:
                mods.append(mod)
    return mods, meta


def get_popular_mods(days: int = POPULAR_WEEK_DAYS) -> list[GBMod]:
    """Собирает моды Deadlock за последние ``days`` дней и ранжирует по популярности.

    Мод включается, если он добавлен или изменён в заданном окне.
    Ранжирование — по сумме лайков и просмотров (в Mod/Index нет счётчика скачиваний).
    """
    now = int(time.time())
    cutoff = now - days * 86400

    first_records, first_meta = _fetch_mod_index_page(1)
    record_count = first_meta.get("_nRecordCount")
    if isinstance(record_count, int) and record_count > 0:
        total_pages = max(1, math.ceil(record_count / POPULAR_SCAN_PERPAGE))
    else:
        total_pages = 200

    all_records = list(first_records)

    def _fetch_page(page: int):
        try:
            records, _meta = _fetch_mod_index_page(page)
            return records
        except GameBananaError:
            return []

    if total_pages > 1:
        with concurrent.futures.ThreadPoolExecutor(max_workers=POPULAR_CONCURRENCY) as executor:
            for page in range(2, total_pages + 1):
                all_records.extend(_fetch_page(page))

    cutoff_ok = [
        r
        for r in all_records
        if _to_int(r.get("_tsDateAdded")) >= cutoff or _to_int(r.get("_tsDateModified")) >= cutoff
    ]

    mods: list[GBMod] = []
    for record in cutoff_ok:
        mod = _mod_from_search_record(record)
        if mod is not None:
            mods.append(mod)

    mods.sort(key=lambda m: (-(m.likes + m.views), m.name))
    return mods


def download_file_bytes(
    mod_id: int,
    file_id: int | None = None,
    progress_callback=None,
) -> tuple[GBMod, GBFile, bytes]:
    """Скачивает последнюю версию мода. Возвращает (мод, файл, содержимое).

    ``progress_callback`` — необязательная функция ``(received: int, total: int)``,
    вызываемая после каждой части скачанного файла; ``total == 0``, если сервер
    не сообщил размер через Content-Length.
    """
    mod = get_mod(mod_id)
    files = get_mod_files(mod_id)
    chosen: GBFile | None = None
    if file_id is not None:
        chosen = next((f for f in files if f.file_id == file_id), None)
    if chosen is None and files:
        chosen = files[0]
    if chosen is None:
        raise GameBananaError(f"GameBanana: у мода {mod_id} не найдено файлов")
    try:
        request = urllib.request.Request(
            chosen.download_url,
            headers={
                "Accept": "*/*",
                "User-Agent": GB_USER_AGENT,
                "Referer": f"{GAMEBANANA_DOWNLOAD_BASE_URL}/",
            },
            method="GET",
        )
        with urllib.request.urlopen(request, timeout=GB_DOWNLOAD_TIMEOUT_SECONDS) as response:
            total = 0
            headers = getattr(response, "headers", None)
            if headers is not None:
                total = _to_int(headers.get("Content-Length"), 0)
            data_parts: list[bytes] = []
            received = 0
            while True:
                chunk = response.read(65536)
                if not chunk:
                    break
                data_parts.append(chunk)
                received += len(chunk)
                if progress_callback is not None:
                    progress_callback(received, total)
            data = b"".join(data_parts)
    except Exception as exc:
        raise GameBananaError(f"GameBanana: не удалось скачать файл «{chosen.name}» — {exc}") from exc
    return mod, chosen, data


def download_image_bytes(url: str) -> bytes:
    """Скачивает обложку или миниатюру мода с сервера изображений GameBanana."""
    if not url:
        raise GameBananaError("GameBanana: у мода нет изображения")
    request = urllib.request.Request(
        url,
        headers={
            "Accept": "image/*",
            "User-Agent": GB_USER_AGENT,
            "Referer": f"{GAMEBANANA_DOWNLOAD_BASE_URL}/",
        },
        method="GET",
    )
    try:
        with urllib.request.urlopen(request, timeout=GB_TIMEOUT_SECONDS) as response:
            data = response.read()
    except Exception as exc:
        raise GameBananaError(f"GameBanana: не удалось загрузить изображение — {exc}") from exc
    if not data:
        raise GameBananaError("GameBanana: получено пустое изображение")
    return data
