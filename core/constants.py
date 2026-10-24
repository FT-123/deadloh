import re

DEADLOCK_APP_ID = "1422450"
VALID_EXTS = {".vpk", ".zip", ".7z", ".rar"}

# Актуальный ростер: https://api.deadlock-api.com/v1/assets/heroes
# (player_selectable=true, проверено 2026-10-24).
# HERO_MAP — только официальные имена героев: ключ для тегов/UI, значение для отображения.
HERO_MAP = {
    "abrams": "Abrams",
    "apollo": "Apollo",
    "baba": "Baba",
    "bebop": "Bebop",
    "billy": "Billy",
    "calico": "Calico",
    "celeste": "Celeste",
    "deadman_danny": "Deadman Danny",
    "drifter": "Drifter",
    "dynamo": "Dynamo",
    "graves": "Graves",
    "grey_talon": "Grey Talon",
    "haze": "Haze",
    "holliday": "Holliday",
    "infernus": "Infernus",
    "ivy": "Ivy",
    "kelvin": "Kelvin",
    "lady_geist": "Lady Geist",
    "lash": "Lash",
    "mcginnis": "McGinnis",
    "mina": "Mina",
    "mirage": "Mirage",
    "mo_and_krill": "Mo & Krill",
    "nurse_harrow": "Nurse Harrow",
    "paige": "Paige",
    "paradox": "Paradox",
    "pocket": "Pocket",
    "rat_king": "Rat King",
    "rem": "Rem",
    "seven": "Seven",
    "shiv": "Shiv",
    "silver": "Silver",
    "sinclair": "Sinclair",
    "solomon": "Solomon",
    "the_doorman": "The Doorman",
    "venator": "Venator",
    "victor": "Victor",
    "vindicta": "Vindicta",
    "violet": "Violet",
    "viscous": "Viscous",
    "vyper": "Vyper",
    "warden": "Warden",
    "wraith": "Wraith",
    "yamato": "Yamato",
}

# Внутренние имена сущностей и папок ассетов → официальное имя.
# Используются только для распознавания героя по путям внутри VPK:
# в файлах модов встречаются legacy-папки (archer, tengu, geist и т.п.).
HERO_ALIASES = {
    "atlas": "Abrams", "bull": "Abrams",
    "fencer": "Apollo",
    "punkgoat": "Billy",
    "nano": "Calico",
    "unicorn": "Celeste",
    "deadpack": "Deadman Danny",
    "sumo": "Dynamo",
    "necro": "Graves",
    "archer": "Grey Talon", "orion": "Grey Talon",
    "astro": "Holliday",
    "inferno": "Infernus",
    "tengu": "Ivy",
    "ghost": "Lady Geist", "geist": "Lady Geist", "spectre": "Lady Geist",
    "forge": "McGinnis", "engineer": "McGinnis",
    "vampirebat": "Mina",
    "krill": "Mo & Krill", "digger": "Mo & Krill",
    "nurse": "Nurse Harrow",
    "bookworm": "Paige",
    "chrono": "Paradox",
    "synth": "Pocket",
    "ratking": "Rat King",
    "familiar": "Rem",
    "gigawatt": "Seven",
    "werewolf": "Silver",
    "magician": "Sinclair",
    "chessmaster": "Solomon",
    "doorman": "The Doorman",
    "priest": "Venator",
    "frank": "Victor",
    "hornet": "Vindicta",
    "artist": "Violet",
    "goo": "Viscous",
    "viper": "Vyper",
}

HEROES = sorted(set(HERO_MAP.values()))

SEARCH_PATHS_TEMPLATE = """\t\tSearchPaths
\t\t{
\t\t\t// These are optional language paths. They must be mounted first, which is why there are first in the list.
\t\t\t// *LANGUAGE* will be replaced with the actual language name. If not running a specific language, these paths will not be mounted
\t\t\t// These currently hold localized images containing text, so they need to follow the UI language, not the audio language.
\t\t\t// When we ship localized VO, it should go in a separate Game_AudioLanguage path (e.g. citadel_vo_*LANGUAGE*)
\t\t\tGame_UILanguage\t\tcitadel_*LANGUAGE*

\t\t\t// These are optional low-violence paths. They will only get mounted if you're in a low-violence mode.
\t\t\tGame_LowViolence\tcitadel_lv

\t\t\tGame\t\t\t\tcitadel/addons
\t\t\tMod\t\t\t\tcitadel
\t\t\tWrite\t\t\t\tcitadel
\t\t\tGame\t\t\t\tcitadel
\t\t\tWrite\t\t\t\tcore
\t\t\tMod\t\t\t\tcore
\t\t\tGame\t\t\t\tcore
\t\t}"""


def clean_display_name(raw_name: str) -> str:
    """Убирает хэши GameBanana (_86cdd) для UI"""
    name = re.sub(r"_[a-f0-9]{4,7}$", "", raw_name, flags=re.IGNORECASE)
    name = re.sub(r"[\-_.]", " ", name)
    return " ".join(name.split()).strip().capitalize()