"""
ui/pack_panel.py

Renders the Packs section in the sidebar and the pack detail page
in the main content area.

Called from tkinter_ui.py — receives the content_frame and mode_manager
as arguments so it stays decoupled from the main app class.
"""

import tkinter as tk
from tkinter import ttk

# Match palette from tkinter_ui.py
BG       = "#080808"
SURFACE  = "#111111"
SURFACE2 = "#1a1a1a"
BORDER   = "#222222"
ACCENT   = "#2BC2F0"
ACCENT2  = "#da29e7"
ACCENT_DIM = "#1a7a9a"
TEXT     = "#f0f0f0"
TEXT_DIM = "#555555"
TEXT_MED = "#999999"
SAVE_CLR = "#34d399"
REC_CLR  = "#f87171"
FONT_MONO  = ("Courier New", 10)
FONT_LABEL = ("Courier New", 9)
FONT_TITLE = ("Courier New", 13, "bold")
FONT_BTN   = ("Courier New", 9, "bold")
FONT_NAV   = ("Courier New", 10)
FONT_BADGE = ("Courier New", 8, "bold")


def render_pack_page(content_frame: tk.Frame, pack, mode_manager, on_mode_change):
    """
    Renders the detail view for a single pack in the main content area.

    content_frame  : the scrollable Frame in the center of the UI
    pack           : GesturePack instance
    mode_manager   : ModeManager instance
    on_mode_change : callable() — tells the main app to refresh the UI
                     after the user switches mode
    """
    for w in content_frame.winfo_children():
        w.destroy()

    is_active = mode_manager.active_pack_id() == pack.id

    # ── Header ────────────────────────────────────────────────────────────────
    header = tk.Frame(content_frame, bg=BG)
    header.pack(fill="x", padx=24, pady=(20, 4))

    tk.Label(
        header,
        text=f"{pack.icon}  {pack.name}",
        bg=BG, fg=ACCENT2,
        font=("Courier New", 16, "bold"),
    ).pack(side="left")

    # Active badge
    if is_active:
        tk.Label(
            header,
            text="  ● ACTIVE",
            bg=BG, fg=SAVE_CLR,
            font=FONT_BADGE,
        ).pack(side="left", padx=(12, 0))

    # ── Description ───────────────────────────────────────────────────────────
    tk.Label(
        content_frame,
        text=pack.description,
        bg=BG, fg=TEXT_MED,
        font=FONT_LABEL,
        anchor="w",
    ).pack(anchor="w", padx=24, pady=(0, 8))

    # ── Mode toggle button ────────────────────────────────────────────────────
    btn_frame = tk.Frame(content_frame, bg=BG)
    btn_frame.pack(anchor="w", padx=24, pady=(0, 16))

    if is_active:
        btn_text = "● ACTIVE — Click to return to Default"
        btn_bg   = SAVE_CLR
        btn_fg   = BG
    else:
        btn_text = "Activate This Pack"
        btn_bg   = ACCENT2
        btn_fg   = BG

    def on_toggle():
        mode_manager.toggle_pack(pack.id)
        on_mode_change()  # tells main app to re-render sidebar + update mode label

    tk.Button(
        btn_frame,
        text=btn_text,
        command=on_toggle,
        bg=btn_bg, fg=btn_fg,
        font=FONT_BTN,
        relief="flat", padx=12, pady=5,
        cursor="hand2", bd=0,
    ).pack(side="left")

    if not is_active:
        tk.Label(
            btn_frame,
            text="  Gestures will use this pack instead of your app mappings",
            bg=BG, fg=TEXT_DIM, font=FONT_LABEL,
        ).pack(side="left", padx=(10, 0))

    # ── Separator + column headers ────────────────────────────────────────────
    tk.Frame(content_frame, bg=BORDER, height=1).pack(fill="x", padx=24, pady=(4, 0))

    hdr = tk.Frame(content_frame, bg=BG)
    hdr.pack(fill="x", padx=24, pady=(6, 2))
    for col, w in [("GESTURE", 18), ("ACTION", 24), ("DESCRIPTION", 36)]:
        tk.Label(
            hdr, text=col, bg=BG, fg=TEXT_DIM,
            font=FONT_BADGE, width=w, anchor="w",
        ).pack(side="left")

    tk.Frame(content_frame, bg=BORDER, height=1).pack(fill="x", padx=24, pady=(2, 4))

    # ── Gesture rows ──────────────────────────────────────────────────────────
    for i, (gesture_name, gdef) in enumerate(pack.gestures.items()):
        row_bg = SURFACE2 if i % 2 == 1 else SURFACE
        row = tk.Frame(content_frame, bg=row_bg)
        row.pack(fill="x", padx=24, pady=1)

        # Gesture name badge
        tk.Label(
            row,
            text=gesture_name,
            bg=ACCENT_DIM, fg=TEXT,
            font=FONT_BADGE, padx=6, pady=2,
            width=16, anchor="w",
        ).pack(side="left", padx=(8, 12), pady=6)

        # Action label
        tk.Label(
            row,
            text=gdef.get("label", ""),
            bg=row_bg, fg=ACCENT,
            font=FONT_MONO, width=22, anchor="w",
        ).pack(side="left", padx=(0, 8))

        # Description
        tk.Label(
            row,
            text=gdef.get("description", ""),
            bg=row_bg, fg=TEXT_MED,
            font=FONT_LABEL, anchor="w",
        ).pack(side="left")

    # ── hold_center note ──────────────────────────────────────────────────────
    tk.Frame(content_frame, bg=BORDER, height=1).pack(fill="x", padx=24, pady=(12, 0))
    tk.Label(
        content_frame,
        text="💡  hold_center always exits pack mode and returns to Default",
        bg=BG, fg=TEXT_DIM, font=FONT_LABEL,
    ).pack(anchor="w", padx=24, pady=8)


def render_pack_sidebar_section(
    sidebar: tk.Frame,
    mode_manager,
    nav_btns: dict,
    on_show_pack,
):
    """
    Adds a PACKS section to the bottom of the sidebar.

    sidebar       : the sidebar Frame
    mode_manager  : ModeManager instance
    nav_btns      : the existing nav_btns dict (we add pack buttons to it)
    on_show_pack  : callable(pack_id) — tells main app to render the pack page
    """
    # Separator
    tk.Frame(sidebar, bg=BORDER, height=1).pack(fill="x", pady=(8, 0))

    tk.Label(
        sidebar,
        text="  PACKS",
        bg=SURFACE, fg=TEXT_DIM,
        font=FONT_BADGE,
    ).pack(anchor="w", pady=(10, 4))

    for pack in mode_manager.all_packs():
        is_active = mode_manager.active_pack_id() == pack.id

        # Show active indicator in button text
        label = f"  {pack.icon} {pack.name}"
        if is_active:
            label += "  ●"

        b = tk.Button(
            sidebar,
            text=label,
            command=lambda pid=pack.id: on_show_pack(pid),
            bg=SURFACE if not is_active else ACCENT_DIM,
            fg=TEXT_MED if not is_active else TEXT,
            font=FONT_NAV,
            relief="flat", anchor="w",
            padx=4, pady=7,
            cursor="hand2", bd=0,
            highlightthickness=0,
            activebackground=SURFACE2,
            activeforeground=TEXT,
        )
        b.pack(fill="x")
        nav_btns[f"pack:{pack.id}"] = b

        def _enter(e, btn=b, active=is_active):
            if not active:
                btn.config(bg=SURFACE2, fg=TEXT)

        def _leave(e, btn=b, active=is_active):
            if not active:
                btn.config(bg=SURFACE, fg=TEXT_MED)

        b.bind("<Enter>", _enter)
        b.bind("<Leave>", _leave)