"""Подготовка превью ресурсов VPK: текстуры, 3D-модели и текстовые файлы.

Модуль не зависит от PySide6. Окно редактора VPK вызывает :func:`build_preview`
в фоновом потоке и показывает результат: путь к PNG/GLB или готовый текст.

Текстуры (``.vtex_c``) и модели (``.vmdl_c``) декомпилируются внешним
``Source2Viewer-CLI.exe`` из ``tools/``. Если бинарник не найден, функция
возвращает понятное сообщение, а не падает.
"""

import hashlib
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from .model_exporter import get_s2v_cli_path, resolve_gameinfo_path
from .vpk_editor import VPKReader

TEXTURE_SUFFIXES = frozenset({".vtex_c"})
MODEL_SUFFIXES = frozenset({".vmdl_c"})
TEXT_SUFFIXES = frozenset({
    ".txt",
    ".cfg",
    ".gi",
    ".kv3",
    ".json",
    ".xml",
    ".vts",
    ".vsc",
    ".css",
    ".js",
    ".lua",
    ".py",
    ".md",
    ".ini",
})
MAX_TEXT_BYTES = 256 * 1024

TEXTURE_TIMEOUT = 90
MODEL_TIMEOUT = 240

NO_WINDOW = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0

NO_PREVIEW_MESSAGE = "Предпросмотр для этого типа файлов пока недоступен."


@dataclass(frozen=True, slots=True)
class PreviewOutcome:
    """Результат подготовки превью.

    ``kind``: ``image`` (PNG на диске), ``model`` (GLB на диске),
    ``text`` (содержимое в ``text``) или ``none`` (причина в ``message``).
    """

    kind: str
    path: Path | None = None
    text: str = ""
    message: str = ""


def resource_suffix(resource_path: str) -> str:
    """Расширение ресурса в нижнем регистре (``.vtex_c``)."""
    return PurePosixPath(resource_path).suffix.lower()


def preview_kind(resource_path: str) -> str:
    """Ожидаемый вид превью по расширению ресурса."""
    suffix = resource_suffix(resource_path)
    if suffix in TEXTURE_SUFFIXES:
        return "image"
    if suffix in MODEL_SUFFIXES:
        return "model"
    if suffix in TEXT_SUFFIXES:
        return "text"
    return "none"


def pick_model_glb(directory: Path, resource_path: str) -> Path | None:
    """Выбирает GLB нужной модели: совпадение по имени, иначе самый большой."""
    if not directory.is_dir():
        return None
    candidates = sorted(
        path
        for path in directory.rglob("*.glb")
        if path.is_file() and path.stat().st_size > 1024
    )
    if not candidates:
        return None
    stem = PurePosixPath(resource_path).stem.lower()
    for path in candidates:
        if path.stem.lower() == stem:
            return path
    return max(candidates, key=lambda path: path.stat().st_size)


def build_preview(
    vpk_path: str | Path,
    resource_path: str,
    cache_dir: str | Path,
    *,
    workspace_dir: str | Path | None = None,
    game_path: str | Path | None = None,
) -> PreviewOutcome:
    """Готовит превью одного ресурса VPK.

    ``workspace_dir`` — рабочая копия проекта (если есть): из неё читаются
    уже заменённые текстуры и текстовые файлы. Модель всегда декомпилируется
    из исходного VPK, потому что ей нужны материалы и текстуры рядом.
    """
    source = Path(vpk_path)
    cache = Path(cache_dir)
    kind = preview_kind(resource_path)

    if kind == "image":
        return _texture_preview(source, resource_path, cache, workspace_dir)
    if kind == "model":
        return _model_preview(source, resource_path, cache, game_path)
    if kind == "text":
        return _text_preview(source, resource_path, workspace_dir)
    return PreviewOutcome("none", message=NO_PREVIEW_MESSAGE)


def _read_resource_bytes(
    vpk_path: Path,
    resource_path: str,
    workspace_dir: str | Path | None,
) -> bytes:
    """Читает ресурс из рабочей копии, если он там есть, иначе из VPK."""
    if workspace_dir is not None:
        candidate = Path(workspace_dir, *PurePosixPath(resource_path).parts)
        if candidate.is_file():
            return candidate.read_bytes()
    return VPKReader(vpk_path).read_resource(resource_path)


def _run_cli(cmd: list[str], timeout: int) -> tuple[bool, str]:
    """Запускает Source2Viewer-CLI без окна консоли."""
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            creationflags=NO_WINDOW,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return False, f"Декомпилятор не ответил за {timeout} секунд."
    except OSError as exc:
        return False, f"Не удалось запустить Source2Viewer-CLI: {exc}"

    if proc.returncode == 0:
        return True, ""
    detail = (proc.stderr or proc.stdout or "").strip()
    if len(detail) > 300:
        detail = detail[-300:]
    return False, detail or f"Source2Viewer-CLI завершился с кодом {proc.returncode}."


def _texture_preview(
    vpk_path: Path,
    resource_path: str,
    cache: Path,
    workspace_dir: str | Path | None,
) -> PreviewOutcome:
    """Декомпилирует ``.vtex_c`` в PNG (результат кешируется по содержимому)."""
    data = _read_resource_bytes(vpk_path, resource_path, workspace_dir)
    if not data:
        return PreviewOutcome("none", message="Ресурс пуст — превью недоступно.")

    key = hashlib.sha1(data).hexdigest()[:20]
    png_path = cache / f"{key}.png"
    if png_path.is_file() and png_path.stat().st_size:
        return PreviewOutcome("image", path=png_path)

    cli_path = get_s2v_cli_path()
    if cli_path is None:
        return PreviewOutcome(
            "none",
            message="Source2Viewer-CLI.exe не найден: превью текстур недоступно.",
        )

    cache.mkdir(parents=True, exist_ok=True)
    source_path = cache / f"{key}.vtex_c"
    source_path.write_bytes(data)
    ok, error = _run_cli(
        [str(cli_path), "-i", str(source_path), "-o", str(png_path), "-d"],
        TEXTURE_TIMEOUT,
    )
    if not ok or not png_path.is_file() or not png_path.stat().st_size:
        return PreviewOutcome(
            "none",
            message=f"Не удалось декомпилировать текстуру. {error}".strip(),
        )
    return PreviewOutcome("image", path=png_path)


def _model_preview(
    vpk_path: Path,
    resource_path: str,
    cache: Path,
    game_path: str | Path | None,
) -> PreviewOutcome:
    """Экспортирует конкретную ``.vmdl_c`` из VPK в GLB (с кешем)."""
    try:
        stamp = vpk_path.stat().st_mtime_ns
    except OSError as exc:
        return PreviewOutcome("none", message=f"VPK недоступен: {exc}")

    key = hashlib.sha1(
        f"{vpk_path.resolve()}|{stamp}|{resource_path}".encode()
    ).hexdigest()[:20]
    out_dir = cache / f"model_{key}"
    cached = pick_model_glb(out_dir, resource_path)
    if cached is not None:
        return PreviewOutcome("model", path=cached)

    cli_path = get_s2v_cli_path()
    if cli_path is None:
        return PreviewOutcome(
            "none",
            message="Source2Viewer-CLI.exe не найден: превью моделей недоступно.",
        )

    out_dir.mkdir(parents=True, exist_ok=True)
    cmd = [
        str(cli_path),
        "-i", str(vpk_path),
        "-o", str(out_dir.resolve()).rstrip("\\/") + "\\",
        "-d",
        "-e", "vmdl_c",
        "-f", resource_path,
        "--gltf_export_format", "glb",
        "--gltf_textures_adapt",
        "--gltf_export_materials",
    ]
    gameinfo = resolve_gameinfo_path(Path(game_path) if game_path else None)
    if gameinfo is not None:
        cmd.extend(["--game", str(gameinfo)])

    _, error = _run_cli(cmd, MODEL_TIMEOUT)
    glb_path = pick_model_glb(out_dir, resource_path)
    if glb_path is None:
        detail = error or "в архиве нет мешей для этой модели"
        return PreviewOutcome(
            "none", message=f"Не удалось собрать 3D-превью. {detail}".strip()
        )
    return PreviewOutcome("model", path=glb_path)


def _text_preview(
    vpk_path: Path,
    resource_path: str,
    workspace_dir: str | Path | None,
) -> PreviewOutcome:
    """Показывает небольшой текстовый ресурс (``.txt``, ``.cfg``, ``.kv3``…)."""
    data = _read_resource_bytes(vpk_path, resource_path, workspace_dir)
    text = _decode_text(data[:MAX_TEXT_BYTES])
    if text is None:
        return PreviewOutcome(
            "none", message="Файл не похож на текст — превью недоступно."
        )
    return PreviewOutcome("text", text=text)


def _decode_text(data: bytes) -> str | None:
    """Декодирует UTF-8/CP1251, если данные выглядят как текст."""
    if not data:
        return ""
    text: str | None = None
    for encoding in ("utf-8-sig", "cp1251"):
        try:
            text = data.decode(encoding)
            break
        except UnicodeDecodeError:
            continue
    if text is None:
        return None

    sample = text[:4096]
    printable = sum(
        1 for char in sample if char.isprintable() or char in "\r\n\t"
    )
    if printable / len(sample) < 0.95:
        return None
    return text
