import json
import os
import sys
from pathlib import Path


def _get_storage_path() -> Path:
    """
    Returns the correct OS-specific folder for persistent user data.
    Windows → C:/Users/<user>/AppData/Roaming/GesturePuck/
    macOS   → /Users/<user>/Library/Application Support/GesturePuck/
    Linux   → /home/<user>/.config/GesturePuck/
    """
    if sys.platform == "win32":
        base = Path(os.environ.get("APPDATA", Path.home()))
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support"
    else:
        base = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))

    folder = base / "GesturePuck"
    folder.mkdir(parents=True, exist_ok=True)
    return folder / "mappings.json"


STORAGE_PATH = _get_storage_path()

DEFAULT_MAPPINGS = {
    "Global": {
        "Gesture 1": {
            "label": "Screenshot",
            "macro": "win+shift+s"
        }
    }
}


def load() -> dict:
    if not STORAGE_PATH.exists():
        save(DEFAULT_MAPPINGS)
        return DEFAULT_MAPPINGS

    try:
        with open(STORAGE_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
            # Handle both old broken format and correct format
            if "Global" in data:
                return data          # ← return the WHOLE dict, not just ["Global"]
            return DEFAULT_MAPPINGS
    except Exception as e:
        print("Error loading mappings:", e)
        return DEFAULT_MAPPINGS


def save(mappings: dict) -> None:
    """Always saves the full mappings dict {app_name: {gesture: {...}}}"""
    with open(STORAGE_PATH, "w", encoding="utf-8") as f:
        json.dump(mappings, f, indent=4)