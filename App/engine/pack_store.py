"""
pack_store.py
─────────────
Handles importing, persisting, and loading .gpack files.

A .gpack file is a JSON file with this shape:
{
    "gpack_version": 1,
    "id": "garageband",
    "name": "GarageBand",
    "icon": "🎸",
    "description": "Music production controls",
    "author": "GesturePuck",
    "modes": [
        {
            "id": "record",
            "name": "Record",
            "icon": "⏺",
            "gestures": {
                "swipe_left":  {"label": "Rewind",  "macro": "cmd+left"},
                "swipe_right": {"label": "Forward", "macro": "cmd+right"}
            }
        }
    ]
}

Imported packs are stored individually as JSON files under:
    <APP_DIR>/packs/<pack_id>.gpack
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

# ── Paths ──────────────────────────────────────────────────────────────────────
TMP_DIR   = Path("/tmp")
PACKS_DIR = TMP_DIR / "packs"


def _ensure_dir():
    PACKS_DIR.mkdir(parents=True, exist_ok=True)


# ── Validation ─────────────────────────────────────────────────────────────────
REQUIRED_TOP = {"id", "name", "modes"}
REQUIRED_MODE = {"id", "name", "gestures"}


class PackValidationError(ValueError):
    pass


def validate(data: dict) -> None:
    """Raise PackValidationError if the pack dict is malformed."""
    missing = REQUIRED_TOP - data.keys()
    if missing:
        raise PackValidationError(f"Pack missing required fields: {missing}")
    if not isinstance(data.get("modes"), list) or not data["modes"]:
        raise PackValidationError("Pack must have at least one mode")
    for i, mode in enumerate(data["modes"]):
        missing_m = REQUIRED_MODE - mode.keys()
        if missing_m:
            raise PackValidationError(f"Mode {i} missing fields: {missing_m}")
        if not isinstance(mode.get("gestures"), dict):
            raise PackValidationError(f"Mode '{mode.get('id')}' gestures must be a dict")


# ── Public API ──────────────────────────────────────────────────────────────────
def import_pack(source_path: str | Path) -> dict:
    """
    Copy a .gpack file into the app's packs directory.
    Returns the validated pack dict.
    Raises PackValidationError on bad format, ValueError on bad extension.
    """
    source_path = Path(source_path)
    if source_path.suffix.lower() not in {".gpack", ".json"}:
        raise ValueError(f"Expected a .gpack file, got: {source_path.suffix}")

    raw = source_path.read_text(encoding="utf-8")
    data = json.loads(raw)
    validate(data)

    _ensure_dir()
    dest = PACKS_DIR / f"{data['id']}.gpack"
    shutil.copy2(source_path, dest)
    return data


def import_pack_from_dict(data: dict) -> dict:
    """
    Import a pack from an already-parsed dict (e.g. drag-and-drop bytes).
    Validates and writes to disk.
    """
    validate(data)
    _ensure_dir()
    dest = PACKS_DIR / f"{data['id']}.gpack"
    dest.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    return data


def load_all_packs() -> list[dict]:
    """Return a list of all installed pack dicts, sorted by name."""
    _ensure_dir()
    packs = []
    for path in sorted(PACKS_DIR.glob("*.gpack")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            validate(data)
            packs.append(data)
        except Exception as exc:
            # Corrupt/invalid pack — skip with a warning instead of crashing.
            print(f"[pack_store] skipping {path.name}: {exc}")
    return packs


def remove_pack(pack_id: str) -> bool:
    """Delete a pack from disk. Returns True if it existed."""
    dest = PACKS_DIR / f"{pack_id}.gpack"
    if dest.exists():
        dest.unlink()
        return True
    return False


def pack_ids() -> set[str]:
    """Return the set of installed pack IDs."""
    _ensure_dir()
    return {p.stem for p in PACKS_DIR.glob("*.gpack")}


def get_pack(pack_id: str) -> dict | None:
    """Return a single pack dict by ID, or None if not installed."""
    dest = PACKS_DIR / f"{pack_id}.gpack"
    if not dest.exists():
        return None
    try:
        data = json.loads(dest.read_text(encoding="utf-8"))
        validate(data)
        return data
    except Exception:
        return None