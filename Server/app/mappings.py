import json
from pathlib import Path

STORAGE_PATH = Path(__file__).parent / "mappings.json"

# Each entry: { "label": "Screenshot", "macro": "win+shift+s" }
DEFAULT_MAPPINGS: dict[str, dict] = {
    "Gesture 1": {"label": "Gesture 1", "macro": "win+shift+s"},
    "Gesture 2": {"label": "Gesture 2", "macro": "ctrl+c"},
}


def load() -> dict[str, dict]:
    if not STORAGE_PATH.exists():
        save(DEFAULT_MAPPINGS)
        return dict(DEFAULT_MAPPINGS)
    try:
        with open(STORAGE_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        migrated = {}
        for k, v in data.items():
            if isinstance(v, str):
                migrated[k] = {"label": k, "macro": v}
            else:
                migrated[k] = v
        merged = dict(DEFAULT_MAPPINGS)
        merged.update(migrated)
        return merged
    except Exception:
        return dict(DEFAULT_MAPPINGS)


def save(mappings: dict[str, dict]) -> None:
    with open(STORAGE_PATH, "w", encoding="utf-8") as f:
        json.dump(mappings, f, indent=4)