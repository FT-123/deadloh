import re
from pathlib import Path

from .constants import HERO_ALIASES, HERO_MAP

try:
    import vpk
except ImportError:
    vpk = None

# Официальные имена + внутренние алиасы: в VPK встречаются legacy-папки.
_HERO_TAGS = tuple({**HERO_ALIASES, **HERO_MAP}.items())


def analyze_vpk(file_path: Path) -> dict:
    """Определяет героев и категории ассетов внутри .vpk"""
    if not vpk:
        return {"heroes": [], "types": []}

    try:
        pak = vpk.open(str(file_path))
    except Exception:
        return {"heroes": [], "types": []}

    detected_heroes = set()
    types = set()

    for entry in pak:
        low = entry.lower().replace("\\", "/")

        for key, display_name in _HERO_TAGS:
            if re.search(rf"(?:^|[_\/]){re.escape(key)}(?:[_\/]|\.|$)", low):
                detected_heroes.add(display_name)

        if "models/" in low or low.endswith((".vmdl_c", ".vmesh_c")):
            types.add("Model")
        if "panorama/" in low:
            types.add("UI")
        if "sounds/" in low or low.endswith(".vsnd_c"):
            types.add("Voice" if ("/vo/" in low or "voicelines" in low) else "Sound")
        if "particles/" in low or low.endswith(".vpcf_c"):
            types.add("VFX")
        if "materials/" in low and "Model" not in types and "UI" not in types:
            types.add("Texture")

    return {
        "heroes": sorted(list(detected_heroes)),
        "types": [t for t in ["Model", "Texture", "UI", "Sound", "Voice", "VFX"] if t in types],
    }