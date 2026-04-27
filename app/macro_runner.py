"""
Parses and fires macro strings like "ctrl+shift+s", "cmd+tab", "f5".
Uses pynput — works on Windows, macOS, Linux.
"""

from pynput.keyboard import Controller, Key

keyboard = Controller()

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


def parse_key(token: str):
    token = token.strip().lower()
    if token in SPECIAL_KEYS:
        return SPECIAL_KEYS[token]
    if len(token) == 1:
        return token
    raise ValueError(f"Unknown key: '{token}' — check your macro string.")


def run_macro(macro: str) -> None:
    if not macro.strip():
        return
    tokens = [t.strip() for t in macro.split("+") if t.strip()]
    parsed = [parse_key(t) for t in tokens]
    for key in parsed:
        keyboard.press(key)
    for key in reversed(parsed):
        keyboard.release(key)