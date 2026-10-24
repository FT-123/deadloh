import re

DEADLOCK_APP_ID = "1422450"
VALID_EXTS = {".vpk", ".zip", ".7z", ".rar"}

HERO_MAP = {
    "abrams": "Abrams", "bebop": "Bebop", "dynamo": "Dynamo",
    "grey_talon": "Grey Talon", "archer": "Grey Talon", "haze": "Haze",
    "infernus": "Infernus", "inferno": "Infernus", "ivy": "Ivy", "tengu": "Ivy",
    "kelvin": "Kelvin", "lady_geist": "Lady Geist", "ghost": "Lady Geist",
    "lash": "Lash", "mcginnis": "McGinnis", "forge": "McGinnis",
    "mirage": "Mirage", "mo_and_krill": "Mo & Krill", "digger": "Mo & Krill",
    "paradox": "Paradox", "chrono": "Paradox", "pocket": "Pocket",
    "seven": "Seven", "gigawatt": "Seven", "shiv": "Shiv", "vampire": "Shiv",
    "vindicta": "Vindicta", "hornet": "Vindicta", "viscous": "Viscous", "goo": "Viscous",
    "warden": "Warden", "wraith": "Wraith", "yamato": "Yamato",
    # Тестовые и датамайненые персонажи
    "mina": "Mina", "doorman": "Doorman", "calico": "Calico",
    "holliday": "Holliday", "viper": "Viper", "magician": "Magician",
    "wrecker": "Wrecker", "rutger": "Rutger", "tokamak": "Tokamak",
    "slivko": "Slivko", "cadence": "Cadence", "fathom": "Fathom",
    "astro": "Astro", "boomer": "Boomer", "frank": "Frank", "swan": "Swan",
    "trapper": "Trapper", "drifter": "Drifter", "raven": "Raven",
    "remus": "Remus", "kali": "Kali", "nano": "Nano"
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