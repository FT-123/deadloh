"""Проверка и установка обновлений приложения через GitHub Releases.

Модуль не зависит от PySide6 — вся логика чистая, её можно тестировать без GUI.

Канал релизов: GitHub Releases для репозитория (по умолчанию ``FT-123/deadloh``).
Для закрытого репозитория задайте токен через переменную окружения ``GITHUB_TOKEN``
или поле ``github_token`` в ``config.json``. Репозиторий можно переопределить полем
``release_repo`` в ``config.json``.

Установка (frozen-сборка) — двухфазная: скачиваем zip в AppData, пишем маркер
отложенного обновления и завершаем процесс; при следующем запуске ``main.py``
извлекает архив поверх папки установки и перезапускает приложение.
"""

import json
import os
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path

# Версия текущего сборки. Поднимайте при каждом релизе (соответствует git-тегу / GitHub Release).
APP_VERSION = "1.2"

DEFAULT_REPO = "FT-123/deadloh"
GITHUB_RELEASES_API = "https://api.github.com/repos/{repo}/releases/latest"
GITHUB_TOKEN_ENV = "GITHUB_TOKEN"
USER_AGENT = f"DeadlockModManager/{APP_VERSION}"
DOWNLOAD_TIMEOUT_SECONDS = 60

# Имя helper-скрипта, который ставит обновление после выхода приложения.
UPDATE_HELPER_NAME = "install_update.bat"
UPDATE_ERROR_EXTRACT = "UPDATE_EXTRACT_FAILED"
UPDATE_ERROR_HELPER = "UPDATE_HELPER_FAILED"


class _SafeRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Снимает Authorization при редиректе на другой хост (GitHub → CDN).

    Иначе токен приватного репозитория уходил бы на objects.githubusercontent.com.
    """

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        new_request = super().redirect_request(req, fp, code, msg, headers, newurl)
        if new_request is not None:
            old_host = urllib.parse.urlsplit(req.full_url).netloc
            new_host = urllib.parse.urlsplit(newurl).netloc
            if old_host != new_host:
                new_request.remove_header("Authorization")
        return new_request


@dataclass(frozen=True)
class ReleaseInfo:
    """Метаданные последнего релиза."""

    version: str
    name: str
    download_url: str
    asset_name: str = ""
    html_url: str = ""

    @property
    def is_zip(self) -> bool:
        return self.asset_name.lower().endswith(".zip")


@dataclass
class UpdateResult:
    """Результат проверки обновлений."""

    current_version: str
    latest_version: str | None = None
    release: ReleaseInfo | None = None
    is_newer: bool = False
    error: str | None = None

    @property
    def is_up_to_date(self) -> bool:
        return self.latest_version is not None and not self.is_newer


def _to_int(value, default=0):
    try:
        return int(str(value))
    except (TypeError, ValueError):
        return default


def compare_versions(a: str, b: str) -> int:
    """Сравнивает две версии. Возвращает -1 / 0 / 1 (a < b / == / >).

    Игнорирует префикс ``v`` и нечисловые суффиксы; сравнивает числовые
    компоненты по порядку, доводя короткие версии до длины длинных нулями.
    """

    def parts(v: str):
        out = []
        for chunk in str(v).strip().lstrip("vV").split("."):
            num = ""
            for ch in chunk:
                if ch.isdigit():
                    num += ch
                else:
                    break
            out.append(int(num) if num else 0)
        return out

    pa, pb = parts(a), parts(b)
    n = max(len(pa), len(pb))
    pa += [0] * (n - len(pa))
    pb += [0] * (n - len(pb))
    for x, y in zip(pa, pb):
        if x != y:
            return 1 if x > y else -1
    return 0


def _load_config() -> dict:
    """Читает config.json (опционально). Возвращает пустой словарь при ошибке."""
    try:
        from .paths import CONFIG_PATH

        if CONFIG_PATH.exists():
            data = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
    except Exception:  # noqa: BLE001, S110 — повреждённый config не должен ронять проверку обновлений
        pass
    return {}


def _read_token() -> str | None:
    token = os.environ.get(GITHUB_TOKEN_ENV) or ""
    if not token:
        token = str(_load_config().get("github_token") or "").strip()
    return token or None


def _read_repo() -> str:
    repo = str(_load_config().get("release_repo") or DEFAULT_REPO).strip()
    return repo or DEFAULT_REPO


def fetch_latest_release(repo: str | None = None, token: str | None = None):
    """Запрашивает последний GitHub Release.

    Возвращает ``(ReleaseInfo | None, ошибка_строка | None)``. Если релизов нет
    или API недоступен — ``None`` и текст ошибки для пользователя.
    """
    token = token or _read_token()
    url = GITHUB_RELEASES_API.format(repo=repo or _read_repo())
    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": USER_AGENT,
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"

    request = urllib.request.Request(url, headers=headers, method="GET")
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            data = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return None, "Не найдено ни одного релиза. Создайте GitHub Release с файлом сборки."
        return None, f"Ошибка API ({exc.code}): не удалось получить данные о последнем релизе."
    except Exception:  # noqa: BLE001 — сеть/API могут упасть
        return None, "Не удалось проверить обновления (проверьте подключение к интернету)."

    if not isinstance(data, dict):
        return None, "Некорректный ответ от GitHub."

    tag = str(data.get("tag_name") or "").strip()
    name = str(data.get("name") or tag).strip()
    html_url = str(data.get("html_url") or "")
    assets_raw = data.get("assets") or []
    if not isinstance(assets_raw, list):
        assets_raw = []

    def asset_size(asset: dict) -> int:
        return _to_int(asset.get("size"))

    zips = [a for a in assets_raw if isinstance(a, dict) and str(a.get("name", "")).lower().endswith(".zip")]
    chosen = None
    if zips:
        deadlock_zips = [z for z in zips if "deadlock" in str(z.get("name", "")).lower()]
        pool = deadlock_zips or zips
        chosen = max(pool, key=asset_size)
    if chosen is None and assets_raw:
        chosen = assets_raw[0]

    if isinstance(chosen, dict):
        download_url = str(chosen.get("browser_download_url") or html_url)
        asset_name = str(chosen.get("name") or "")
        return ReleaseInfo(
            version=tag, name=name, download_url=download_url, asset_name=asset_name, html_url=html_url
        ), None

    if tag and html_url:
        return ReleaseInfo(version=tag, name=name, download_url=html_url, asset_name="", html_url=html_url), None

    return None, "У релиза нет файлов для скачивания."


def check_for_update(repo: str | None = None, token: str | None = None) -> UpdateResult:
    """Проверяет наличие новой версии и возвращает :class:`UpdateResult`."""
    release, error = fetch_latest_release(repo=repo, token=token)
    if release is None:
        return UpdateResult(current_version=APP_VERSION, error=error or "Не удалось проверить обновления.")
    cmp_result = compare_versions(APP_VERSION, release.version)
    return UpdateResult(
        current_version=APP_VERSION,
        latest_version=release.version,
        release=release,
        is_newer=cmp_result < 0,
    )


# ── Отложенная установка (two-phase) ───────────────────────────────────────

def _pending_marker_path() -> Path:
    from .paths import APP_DATA_DIR

    return APP_DATA_DIR / "pending_update.json"


def write_pending_update(zip_path: str | Path, install_dir: str | Path, version: str) -> None:
    """Записывает маркер отложенного обновления."""
    marker = _pending_marker_path()
    marker.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "zip": str(zip_path),
        "install_dir": str(install_dir),
        "version": version,
    }
    tmp = marker.with_name(marker.name + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, marker)


def read_pending_update() -> dict | None:
    """Читает маркер отложенного обновления (или ``None``)."""
    marker = _pending_marker_path()
    if not marker.exists():
        return None
    try:
        data = json.loads(marker.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else None
    except Exception:  # noqa: BLE001 — маркер может быть повреждён
        return None


def clear_pending_update() -> None:
    marker = _pending_marker_path()
    if marker.exists():
        try:
            marker.unlink()
        except OSError:
            pass


def download_asset(
    url: str,
    dest: Path,
    progress_callback=None,
    token: str | None = None,
) -> bool:
    """Скачивает ``url`` в ``dest`` (атомарно через .tmp). Возвращает True при успехе.

    Для приватного репозитория токен берётся из окружения/конфига и
    добавляется в запрос; при редиректе на CDN заголовок снимается.
    """
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_name(dest.name + ".tmp")
    headers = {
        "Accept": "*/*",
        "User-Agent": USER_AGENT,
    }
    token = token or _read_token()
    if token:
        headers["Authorization"] = f"Bearer {token}"
    request = urllib.request.Request(url, headers=headers, method="GET")
    opener = urllib.request.build_opener(_SafeRedirectHandler)
    try:
        with opener.open(request, timeout=DOWNLOAD_TIMEOUT_SECONDS) as response:
            total = 0
            headers = getattr(response, "headers", None)
            if headers is not None:
                total = _to_int(headers.get("Content-Length"), 0)
            received = 0
            with open(tmp, "wb") as f:
                while True:
                    chunk = response.read(65536)
                    if not chunk:
                        break
                    f.write(chunk)
                    received += len(chunk)
                    if progress_callback is not None:
                        progress_callback(received, total)
    except Exception:  # noqa: BLE001 — сеть может упасть в любой момент
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass
        return False

    os.replace(tmp, dest)
    return True


# ── Helper-установка (Windows) ─────────────────────────────────────────────
#
# Работающий exe и загруженные DLL из _internal нельзя перезаписать, поэтому
# установку делает отдельный .bat: он ждёт выхода приложения, распаковывает
# zip через tar.exe и запускает новую версию.

def build_update_helper_script(
    *,
    zip_path: str | Path,
    install_dir: str | Path,
    exe_path: str | Path,
    pid: int,
    marker_path: str | Path | None = None,
    error_path: str | Path | None = None,
) -> str:
    """Собирает ASCII-only .bat для установки обновления после выхода приложения."""
    if marker_path is None:
        marker_path = _pending_marker_path()
    if error_path is None:
        from .paths import APP_DATA_DIR

        error_path = APP_DATA_DIR / "update_error.txt"
    lines = [
        "@echo off",
        "setlocal",
        f'set "APP_PID={int(pid)}"',
        f'set "ZIP_PATH={Path(zip_path)}"',
        f'set "INSTALL_DIR={Path(install_dir)}"',
        f'set "APP_EXE={Path(exe_path)}"',
        f'set "MARKER={Path(marker_path)}"',
        f'set "ERROR_FILE={Path(error_path)}"',
        "",
        ":wait",
        '>nul 2>&1 tasklist /FI "PID eq %APP_PID%" | find "%APP_PID%"',
        "if not errorlevel 1 (",
        "    >nul ping -n 2 127.0.0.1",
        "    goto wait",
        ")",
        ">nul ping -n 2 127.0.0.1",
        'if exist "%ERROR_FILE%" del "%ERROR_FILE%" >nul 2>&1',
        'tar -xf "%ZIP_PATH%" -C "%INSTALL_DIR%"',
        "if errorlevel 1 (",
        (
            "    powershell -NoProfile -ExecutionPolicy Bypass -Command \"Expand-Archive "
            "-LiteralPath '%ZIP_PATH%' -DestinationPath '%INSTALL_DIR%' -Force\""
        ),
        ")",
        "if errorlevel 1 (",
        f'    >"%ERROR_FILE%" echo {UPDATE_ERROR_EXTRACT}',
        ") else (",
        '    del "%ZIP_PATH%" >nul 2>&1',
        ")",
        'if exist "%MARKER%" del "%MARKER%" >nul 2>&1',
        'start "" "%APP_EXE%" --update-installed',
        '(goto) 2>nul & del "%~f0"',
        "",
    ]
    return "\r\n".join(lines)


def launch_update_helper(
    zip_path: str | Path,
    install_dir: str | Path,
    exe_path: str | Path,
    *,
    marker_path: str | Path | None = None,
    error_path: str | Path | None = None,
) -> Path:
    """Запускает helper, который поставит обновление после выхода текущего процесса."""
    if sys.platform != "win32":
        raise OSError("Автоустановка обновлений поддерживается только на Windows")
    if marker_path is None:
        marker_path = _pending_marker_path()
    if error_path is None:
        from .paths import APP_DATA_DIR

        error_path = APP_DATA_DIR / "update_error.txt"

    helper = Path(marker_path).parent / UPDATE_HELPER_NAME
    helper.parent.mkdir(parents=True, exist_ok=True)
    script = build_update_helper_script(
        zip_path=zip_path,
        install_dir=install_dir,
        exe_path=exe_path,
        pid=os.getpid(),
        marker_path=marker_path,
        error_path=error_path,
    )
    helper.write_text(script, encoding="ascii", errors="replace")

    flags = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
    subprocess.Popen(
        ["cmd.exe", "/c", str(helper)],
        creationflags=flags,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        cwd=str(helper.parent),
        close_fds=True,
    )
    return helper
