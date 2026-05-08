import json
from pathlib import Path

STORAGE_PATH = Path(__file__).parent / "mappings.json"

DEFAULT_MAPPINGS = {
    "Global": {
        "Gesture 1": {
            "label": "Screenshot",
            "macro": "win+shift+s"
        }
    }
}


def load():
    if not STORAGE_PATH.exists():
        save(DEFAULT_MAPPINGS)
        return DEFAULT_MAPPINGS

    try:
        with open(STORAGE_PATH, "r", encoding="utf-8") as f:
            return json.load(f)["Global"]

    except Exception as e:
        print("Error loading mappings:", e)
        return DEFAULT_MAPPINGS


def save(mappings):
    with open(STORAGE_PATH, "w", encoding="utf-8") as f:
        json.dump(mappings, f, indent=4)


# GLOBAL RUNTIME DATA
mappings = load()