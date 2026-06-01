"""
packs.py
─────────
Runtime pack/mode management.

Packs are NOT bundled — they are loaded from disk via pack_store.
A pack is only available after the user imports its .gpack file.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Callable, Optional

try:
    from engine import pack_store
except ImportError:
    from . import pack_store


# ── Data model ─────────────────────────────────────────────────────────────────
@dataclass
class GestureMode:
    id: str
    name: str
    icon: str
    gestures: dict[str, dict]   # gesture_name -> {"label": ..., "macro": ...}


@dataclass
class GesturePack:
    id: str
    name: str
    icon: str
    description: str
    author: str
    modes: list[GestureMode]
    _active_mode_idx: int = field(default=0, repr=False)

    @property
    def active_mode(self) -> GestureMode:
        return self.modes[self._active_mode_idx]

    def cycle_mode(self) -> GestureMode:
        self._active_mode_idx = (self._active_mode_idx + 1) % len(self.modes)
        return self.active_mode

    def reset(self):
        self._active_mode_idx = 0


def _pack_from_dict(data: dict) -> GesturePack:
    modes = [
        GestureMode(
            id=m["id"],
            name=m["name"],
            icon=m.get("icon", ""),
            gestures=m.get("gestures", {}),
        )
        for m in data["modes"]
    ]
    return GesturePack(
        id=data["id"],
        name=data["name"],
        icon=data.get("icon", "📦"),
        description=data.get("description", ""),
        author=data.get("author", ""),
        modes=modes,
    )


# ── Manager ───────────────────────────────────────────────────────────────────
class ModeManager:
    """
    Manages the active pack and dispatches gesture macros for that pack.

    Usage pattern:
        manager = ModeManager()
        manager.reload()                        # load from disk
        handled = manager.handle("swipe_left", root)   # returns True if pack consumed it
    """

    # hold_center activates / deactivates a pack; subsequent gestures run pack macros.
    ACTIVATE_GESTURE = "hold_center"

    def __init__(self):
        self._packs: dict[str, GesturePack] = {}
        self._active_id: str | None = None
        self._on_change: list[Callable] = []
        self.reload()

    # ── Pack loading ──────────────────────────────────────────────────────────
    def reload(self):
        """Re-read all installed packs from disk."""
        raw_packs = pack_store.load_all_packs()
        current_ids = set(self._packs.keys())
        new_ids = {p["id"] for p in raw_packs}

        # Remove packs that were uninstalled.
        for gone in current_ids - new_ids:
            self._packs.pop(gone, None)
            if self._active_id == gone:
                self._active_id = None

        # Add / update packs.
        for data in raw_packs:
            pid = data["id"]
            if pid not in self._packs:
                self._packs[pid] = _pack_from_dict(data)
            # (preserve active-mode state for already-loaded packs)

    def import_and_reload(self, source_path) -> GesturePack:
        """Import a .gpack file then reload. Returns the new GesturePack."""
        data = pack_store.import_pack(source_path)
        self.reload()
        return self._packs[data["id"]]

    def import_dict_and_reload(self, data: dict) -> GesturePack:
        pack_store.import_pack_from_dict(data)
        self.reload()
        return self._packs[data["id"]]

    def remove_pack(self, pack_id: str) -> bool:
        removed = pack_store.remove_pack(pack_id)
        if removed:
            self._packs.pop(pack_id, None)
            if self._active_id == pack_id:
                self._active_id = None
        return removed

    # ── Active pack ───────────────────────────────────────────────────────────
    def active_pack(self) -> Optional[GesturePack]:
        return self._packs.get(self._active_id) if self._active_id else None

    def activate(self, pack_id: str):
        if pack_id in self._packs:
            self._active_id = pack_id
            self._packs[pack_id].reset()

    def deactivate(self):
        self._active_id = None

    def installed_packs(self) -> list[GesturePack]:
        return list(self._packs.values())

    # ── Gesture dispatch ──────────────────────────────────────────────────────
    def handle(self, gesture_name: str, root=None) -> bool:
        """
        Attempt to handle a gesture in the currently active pack.
        Returns True if the pack consumed it (caller should not also run default macros).
        """
        # hold_center toggles the active pack off (returns to default mode).
        if gesture_name == self.ACTIVATE_GESTURE and self._active_id:
            self.deactivate()
            return True

        pack = self.active_pack()
        if pack is None:
            return False

        mode = pack.active_mode
        mapping = mode.gestures.get(gesture_name)
        if mapping is None:
            return False

        macro = mapping.get("macro", "")
        if macro:
            try:
                import engine.macro_runner as macro_runner
                macro_runner.run_macro(macro)
            except ImportError:
                print(f"[packs] run macro: {macro}")

        return True