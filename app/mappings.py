import json
from pathlib import Path

STORAGE_PATH = Path(__file__).parent / "mappings.json"
DEFAULT_MAPPINGS = {"BTN1": "cmd+shift+s"}


def load() -> dict[str, str]:
    if not STORAGE_PATH.exists():
        save(DEFAULT_MAPPINGS)
        return dict(DEFAULT_MAPPINGS)
    try:
        with open(STORAGE_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        merged = dict(DEFAULT_MAPPINGS)
        merged.update(data)
        return merged
    except Exception:
        return dict(DEFAULT_MAPPINGS)


def save(mappings: dict[str, str]) -> None:
    with open(STORAGE_PATH, "w", encoding="utf-8") as f:
        json.dump(mappings, f, indent=4)