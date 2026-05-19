"""
ui/review_dialog.py

Provides show_review_banner() — a temporary status banner shown at the
top of the screen after a pack gesture chain completes.

Green banner = success
Red banner   = failure

Used by engine/packs.py after every git chain action.
"""

import tkinter as tk

# Match palette from tkinter_ui.py
BG       = "#080808"
SURFACE  = "#111111"
SURFACE2 = "#1a1a1a"
ACCENT   = "#2BC2F0"
ACCENT2  = "#da29e7"
TEXT     = "#f0f0f0"
TEXT_DIM = "#555555"
SAVE_CLR = "#34d399"
REC_CLR  = "#f87171"
FONT_MONO  = ("Courier New", 10)
FONT_TITLE = ("Courier New", 13, "bold")
FONT_BTN   = ("Courier New", 9, "bold")


def show_review_banner(root, message: str, is_active: bool) -> None:
    """
    Shows a temporary banner at the top of the screen.

    is_active=True  → green banner (success)
    is_active=False → red banner   (failure)

    HOW IT WORKS:
    - Creates a Toplevel with no title bar (overrideredirect)
    - Positions it at the top center of the screen
    - Stays on top of all windows
    - Auto-destroys after 3 seconds

    Called from background threads via root.after() in packs.py,
    so it is safe to call from any thread — Tkinter handles the rest.
    """
    def _show():
        try:
            banner = tk.Toplevel(root)
            banner.overrideredirect(True)  # no title bar
            banner.configure(bg=SAVE_CLR if is_active else REC_CLR)

            # Position at top center of screen
            sw = banner.winfo_screenwidth()
            banner.geometry(f"520x40+{(sw - 520) // 2}+20")

            # Stay on top of everything including VS Code / browser
            banner.attributes("-topmost", True)

            icon = "✓" if is_active else "✗"
            tk.Label(
                banner,
                text=f"  {icon}  {message}",
                bg=SAVE_CLR if is_active else REC_CLR,
                fg=BG,
                font=("Courier New", 11, "bold"),
            ).pack(expand=True)

            # Auto-dismiss after 3 seconds
            banner.after(3000, banner.destroy)
        except Exception as exc:
            # Banner is non-critical — never crash the app over it
            print(f"[review_dialog] banner error: {exc}")

    # Always run UI code on the main thread
    try:
        root.after(0, _show)
    except Exception as exc:
        print(f"[review_dialog] after error: {exc}")