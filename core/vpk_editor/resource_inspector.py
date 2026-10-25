"""Инспекция ресурсов VPK: герои, типы файлов, звуки способностей.

Эвристики намеренно консервативны: они помогают найти кандидатов по путям
внутри мода, но не утверждают, что ресурс действительно относится к
способности. Точный выбор остаётся за вызывающим кодом/пользователем.
"""

import re
from collections.abc import Iterable

from core.constants import HERO_ALIASES, HERO_MAP, HEROES

from .errors import UnknownHeroError
from .models import ResourceInfo

_COMPILED_HERO_MAP = {**HERO_ALIASES, **HERO_MAP}


def resolve_hero(value: str) -> str:
    """Возвращает официальное отображаемое имя героя.

    Принимает официальное имя (``Lady Geist``), ключ карты (``lady_geist``)
    или внутренний алиас (``geist``, ``spectre``).
    """
    needle = " ".join(str(value).split())
    if not needle:
        raise UnknownHeroError("Не указан герой")

    folded = needle.casefold()
    for name in HEROES:
        if name.casefold() == folded:
            return name

    for key, name in _COMPILED_HERO_MAP.items():
        if key.casefold() == folded:
            return name

    normalized = re.sub(r"[^a-z0-9]+", "_", folded).strip("_")
    for name in HEROES:
        candidate = re.sub(r"[^a-z0-9]+", "_", name.casefold()).strip("_")
        if candidate == normalized:
            return name

    raise UnknownHeroError(f"Герой не найден в ростере Deadlock: {value!r}")


def hero_tokens(hero: str) -> tuple[str, frozenset[str]]:
    """Официальное имя героя и набор токенов для поиска по путям VPK."""
    display = resolve_hero(hero)
    tokens = {
        key.casefold()
        for key, name in _COMPILED_HERO_MAP.items()
        if name == display
    }
    tokens.add(re.sub(r"[^a-z0-9]+", "_", display.casefold()).strip("_"))
    return display, frozenset(token for token in tokens if token)


def _token_pattern(token: str) -> re.Pattern[str]:
    return re.compile(rf"(?:^|[/_.\-]){re.escape(token)}(?:[/_.\-]|$)")


def path_has_hero(path: str, tokens: Iterable[str]) -> bool:
    """Есть ли в пути хотя бы один токен героя (по границам сегментов)."""
    lowered = path.casefold()
    return any(_token_pattern(token).search(lowered) for token in tokens)


def ability_pattern(ability: int) -> re.Pattern[str]:
    """Шаблон для номера способности: ability3, ability_3, ability-03, ability_three."""
    if ability < 1:
        raise ValueError("Номер способности должен быть положительным")
    words = {1: "one", 2: "two", 3: "three"}
    number = str(ability)
    alternatives = [rf"0*{re.escape(number)}"]
    word = words.get(ability)
    if word:
        alternatives.append(word)
    joined = "|".join(alternatives)
    return re.compile(
        rf"(?:^|[/_.\-])(?:ability|ab)[_\-]?(?:{joined})(?:[/_.\-]|$)",
        re.IGNORECASE,
    )


def is_sound_resource(resource: ResourceInfo) -> bool:
    """Является ли ресурс звуковым файлом."""
    return resource.is_sound


def find_hero_sounds(
    resources: Iterable[ResourceInfo],
    hero: str,
) -> list[ResourceInfo]:
    """Все звуковые ресурсы, в пути которых встречается имя героя."""
    _display, tokens = hero_tokens(hero)
    result = [
        resource
        for resource in resources
        if resource.is_sound and path_has_hero(resource.path, tokens)
    ]
    return sorted(result, key=lambda item: item.path)


def find_ability_sounds(
    resources: Iterable[ResourceInfo],
    hero: str,
    ability: int = 3,
) -> list[ResourceInfo]:
    """Звуковые ресурсы героя, похожие на ресурсы указанной способности."""
    _display, tokens = hero_tokens(hero)
    pattern = ability_pattern(ability)
    result = []
    for resource in resources:
        if not resource.is_sound:
            continue
        if not path_has_hero(resource.path, tokens):
            continue
        if pattern.search(resource.path):
            result.append(resource)
    return sorted(result, key=lambda item: item.path)
