"""
Parses and fires macro strings like "ctrl+shift+s", "cmd+tab", "f5".
Uses pynput — works on Windows, macOS, Linux.
"""

import sys
import subprocess
import time
from pathlib import Path

from pynput.keyboard import Controller, Key

keyboard = Controller()

if sys.platform == "darwin":
    NAMED_MACROS = {
        "screenshot": "cmd+shift+3",
        "screenshot_full": "cmd+shift+3",
        "screenshot_area": "cmd+shift+4",
        "screenshot_menu": "cmd+shift+5",
    }
elif sys.platform == "win32":
    NAMED_MACROS = {
        "screenshot": "win+shift+s",
        "screenshot_full": "print_screen",
        "screenshot_area": "win+shift+s",
    }
else:
    NAMED_MACROS = {
        "screenshot": "print_screen",
        "screenshot_full": "print_screen",
    }

SPECIAL_KEYS: dict[str, Key] = {
    "ctrl": Key.ctrl, "control": Key.ctrl,
    "shift": Key.shift,
    "alt": Key.alt, "option": Key.alt,
    "cmd": Key.cmd, "command": Key.cmd, "win": Key.cmd, "super": Key.cmd,
    "enter": Key.enter, "return": Key.enter,
    "space": Key.space,
    "tab": Key.tab,
    "esc": Key.esc, "escape": Key.esc,
    "backspace": Key.backspace,
    "delete": Key.delete, "del": Key.delete,
    "home": Key.home, "end": Key.end,
    "pageup": Key.page_up, "pagedown": Key.page_down,
    "left": Key.left, "right": Key.right, "up": Key.up, "down": Key.down,

    **{f"f{n}": getattr(Key, f"f{n}") for n in range(1, 21)},
}

_print_screen_key = getattr(Key, "print_screen", None)
if _print_screen_key is not None:
    SPECIAL_KEYS.update({
        "printscreen": _print_screen_key,
        "print_screen": _print_screen_key,
        "prtsc": _print_screen_key,
    })


def _mac_screenshot() -> str:
    dest = Path.home() / "Desktop" / f"GesturePuck-Screenshot-{time.strftime('%Y%m%d-%H%M%S')}.png"
    subprocess.run(["screencapture", "-x", str(dest)], check=True)
    return f"screenshot:{dest}"


def parse_key(token: str):
    token = token.strip().lower()
    if token in SPECIAL_KEYS:
        return SPECIAL_KEYS[token]
    if len(token) == 1:
        return token
    raise ValueError(f"Unknown key: '{token}' — check your macro string.")


def run_macro(macro: str) -> str:
    macro = macro.strip()
    if not macro:
        return "empty"
    macro_lower = macro.lower()
    if sys.platform == "darwin" and macro_lower in {
        "screenshot",
        "screenshot_full",
        "cmd+shift+3",
        "command+shift+3",
    }:
        return _mac_screenshot()
    macro = NAMED_MACROS.get(macro.lower(), macro)
    tokens = [t.strip() for t in macro.split("+") if t.strip()]
    parsed = [parse_key(t) for t in tokens]
    for key in parsed:
        keyboard.press(key)
    for key in reversed(parsed):
        keyboard.release(key)
    return "hotkey:" + "+".join(tokens)
