import re
from pathlib import Path

try:
    import winreg
except ImportError:
    winreg = None


def detect_deadlock_path() -> Path | None:
    """Ищет game/citadel через реестр Steam и библиотеки"""
    steam_path = None
    if winreg:
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Valve\Steam") as key:
                val, _ = winreg.QueryValueEx(key, "SteamPath")
                steam_path = Path(val)
        except Exception:
            pass

    candidates = [
        steam_path,
        Path(r"C:\Program Files (x86)\Steam"),
        Path(r"C:\Program Files\Steam"),
        Path.home() / ".steam" / "steam",
        Path.home() / ".local" / "share" / "Steam",
    ]

    steam_root = next((c for c in candidates if c and c.exists()), None)
    if not steam_root:
        return None

    library_folders = [steam_root]
    vdf_file = steam_root / "steamapps" / "libraryfolders.vdf"
    if vdf_file.exists():
        try:
            content = vdf_file.read_text(encoding="utf-8", errors="ignore")
            for m in re.findall(r'"path"\s+"([^"]+)"', content):
                p = Path(m.replace(r"\\", "\\"))
                if p.exists() and p not in library_folders:
                    library_folders.append(p)
        except Exception:
            pass

    for lib in library_folders:
        citadel_dir = lib / "steamapps" / "common" / "Deadlock" / "game" / "citadel"
        if citadel_dir.exists() and (citadel_dir / "gameinfo.gi").exists():
            return citadel_dir

    return None