import os
import sys
from pathlib import Path

from . import __version__ as VERSION  # noqa: F401  (one version for Windows and Linux)

APP_NAME = "HumanType"


def resource_dir() -> Path:
    """Where bundled files (assets) live: PyInstaller's unpack folder when built."""
    return Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent.parent))


def app_dir() -> Path:
    """Folder next to HumanType.exe when built, the project root while developing."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


def data_dir() -> Path:
    """Settings, history and logs. An installed copy may sit where the user cannot write
    (Program Files), so it keeps them in %LOCALAPPDATA%; while developing, in ./data."""
    if getattr(sys, "frozen", False):
        return Path(os.environ.get("LOCALAPPDATA") or Path.home()) / APP_NAME
    return app_dir() / "data"


DATA_DIR = data_dir()
IMAGES_DIR = DATA_DIR / "images"
LOG_FILE = DATA_DIR / "humantype.log"
ASSETS_DIR = resource_dir() / "assets"

DATA_DIR.mkdir(parents=True, exist_ok=True)
IMAGES_DIR.mkdir(parents=True, exist_ok=True)
