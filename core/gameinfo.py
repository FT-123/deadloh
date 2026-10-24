import os
import re
import shutil
from pathlib import Path

from .constants import SEARCH_PATHS_TEMPLATE

BLOCK_KEYWORD = "SearchPaths"
BACKUP_NAME = "gameinfo.gi.bak"
TMP_NAME = "gameinfo.gi.dmm_tmp"


def _find_keyword_positions(content: str, keyword: str) -> list[int]:
    """Позиции keyword вне строк и комментариев."""
    positions = []
    i = 0
    size = len(content)
    in_string = False

    while i < size:
        ch = content[i]

        if in_string:
            if ch == "\\":
                i += 2
                continue
            if ch == '"':
                in_string = False
            i += 1
            continue

        if ch == '"':
            in_string = True
            i += 1
            continue

        if content.startswith("//", i):
            newline = content.find("\n", i)
            if newline == -1:
                break
            i = newline + 1
            continue

        if content.startswith("/*", i):
            end = content.find("*/", i + 2)
            if end == -1:
                break
            i = end + 2
            continue

        if content.startswith(keyword, i):
            before = content[i - 1] if i > 0 else ""
            after = content[i + len(keyword)] if i + len(keyword) < size else ""
            if not (before.isalnum() or before == "_") and not (after.isalnum() or after == "_"):
                positions.append(i)

        i += 1

    return positions


def _match_brace(content: str, open_pos: int) -> int | None:
    """Индекс сразу после парной '}' с учётом строк и комментариев."""
    depth = 0
    i = open_pos
    size = len(content)
    in_string = False

    while i < size:
        ch = content[i]

        if in_string:
            if ch == "\\":
                i += 2
                continue
            if ch == '"':
                in_string = False
            i += 1
            continue

        if ch == '"':
            in_string = True
            i += 1
            continue

        if content.startswith("//", i):
            newline = content.find("\n", i)
            if newline == -1:
                return None
            i = newline + 1
            continue

        if content.startswith("/*", i):
            end = content.find("*/", i + 2)
            if end == -1:
                return None
            i = end + 2
            continue

        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return i + 1

        i += 1

    return None


def _find_searchpaths_block(content: str) -> tuple[int, int] | None:
    """Возвращает (начало, конец) единственного блока SearchPaths.

    Если блоков несколько (в том числе закомментированных) — None:
    патчить неоднозначный файл опаснее, чем отказаться.
    """
    blocks = []
    for pos in _find_keyword_positions(content, BLOCK_KEYWORD):
        i = pos + len(BLOCK_KEYWORD)
        while i < len(content) and content[i].isspace():
            i += 1
        if i >= len(content) or content[i] != "{":
            continue
        end = _match_brace(content, i)
        if end is not None:
            blocks.append((pos, end))

    return blocks[0] if len(blocks) == 1 else None


def is_gameinfo_patched(game_path: Path | None) -> bool:
    """Проверяет правильный порядок SearchPaths (addons выше оригинального citadel)"""
    if not game_path:
        return False
    gi = game_path / "gameinfo.gi"
    if not gi.exists():
        return False

    try:
        with open(gi, "r", encoding="utf-8", errors="ignore", newline="") as f:
            content = f.read()
    except OSError:
        return False

    block = _find_searchpaths_block(content)
    if not block:
        return False

    start, end = block
    section = content[start:end]
    if not re.search(r"Game\s+citadel/addons", section):
        return False

    pos_addons = section.find("citadel/addons")
    match_citadel = re.search(r"Game\s+citadel(?![/_])", section)
    return not (match_citadel and pos_addons > match_citadel.start())


def patch_gameinfo_file(game_path: Path) -> tuple[bool, str]:
    """Заменяет SearchPaths на рабочий шаблон с бэкапом и атомарной записью."""
    gi = game_path / "gameinfo.gi"
    if not gi.exists():
        return False, "Файл gameinfo.gi не найден."

    try:
        with open(gi, "r", encoding="utf-8", errors="ignore", newline="") as f:
            content = f.read()
    except OSError as e:
        return False, f"Не удалось прочитать gameinfo.gi: {e}"

    block = _find_searchpaths_block(content)
    if not block:
        return False, "Не удалось однозначно найти блок SearchPaths в gameinfo.gi."

    start, end = block
    newline = "\r\n" if "\r\n" in content else "\n"
    template = SEARCH_PATHS_TEMPLATE
    if newline != "\n":
        template = template.replace("\n", newline)

    new_content = content[:start] + template + content[end:]
    backup = gi.with_name(BACKUP_NAME)
    tmp = gi.with_name(TMP_NAME)

    try:
        if not backup.exists():
            shutil.copy2(gi, backup)

        with open(tmp, "w", encoding="utf-8", errors="ignore", newline="") as f:
            f.write(new_content)
        os.replace(tmp, gi)
    except OSError as e:
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass
        return False, f"Не удалось записать gameinfo.gi: {e}"

    return True, "gameinfo.gi успешно пропатчен."
