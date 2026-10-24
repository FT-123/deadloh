import os
import sys
from pathlib import Path

# Корень программы (папка с exe или корень проекта в dev).
# Используется для поиска бинарников и ресурсов.
if getattr(sys, "frozen", False):
    BASE_DIR = Path(sys.executable).resolve().parent
else:
    BASE_DIR = Path(__file__).resolve().parent.parent

# Папка данных приложения. В exe — в AppData (чтобы не засорять папку с файлом),
# при запуске из исходников — остаётся рядом с проектом, как раньше.
if getattr(sys, "frozen", False):
    APP_DATA_DIR = Path(os.environ.get("APPDATA") or Path.home() / "AppData" / "Roaming") / "DeadlockModManager"
else:
    APP_DATA_DIR = BASE_DIR

STAGING_DIR = APP_DATA_DIR / "staging"
CONFIG_PATH = APP_DATA_DIR / "config.json"
DEPLOYED_STATE_PATH = APP_DATA_DIR / "deployed_state.json"


def ensure_data_dir() -> None:
    """Создаёт папку данных приложения, если её ещё нет."""
    APP_DATA_DIR.mkdir(parents=True, exist_ok=True)
