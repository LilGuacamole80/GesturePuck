"""
pack_panel.py
─────────────
All pack-related UI widgets:
  · render_pack_sidebar_section  — "MY PACKS" section at the bottom of the sidebar
  · render_pack_page             — the detail page shown when a pack is clicked
  · render_import_button         — the "+ Import Pack" button shown in the sidebar
"""

from __future__ import annotations

import json
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from typing import Callable, Optional

# ── Colour / font constants (mirrors main app palette) ────────────────────────
BG          = "#080808"
SURFACE     = "#111111"
SURFACE2    = "#1a1a1a"
SURFACE3    = "#202020"
BORDER      = "#222222"
ACCENT      = "#2BC2F0"
ACCENT2     = "#da29e7"
ACCENT_DIM  = "#1a7a9a"
TEXT        = "#f0f0f0"
TEXT_DIM    = "#555555"
TEXT_MED    = "#999999"
MACRO_CLR   = "#a78bfa"
REC_CLR     = "#f87171"
SAVE_CLR    = "#34d399"
DEL_CLR     = "#f87171"

FONT_MONO   = ("Courier New", 10)
FONT_LABEL  = ("Courier New", 9)
FONT_TITLE  = ("Courier New", 13, "bold")
FONT_BTN    = ("Courier New", 9, "bold")
FONT_NAV    = ("Courier New", 10)
FONT_BADGE  = ("Courier New", 8, "bold")
FONT_SMALL  = ("Courier New", 8)


def _mk_btn(parent, text, command, bg=ACCENT, fg=BG, width=None):
    kw = dict(
        text=text, command=command,
        bg=bg, fg=fg, font=FONT_BTN,
        relief="flat", padx=8, pady=3,
        cursor="hand2", bd=0,
        highlightthickness=0,
        activebackground=bg,
        activeforeground=fg,
    )
    if width:
        kw["width"] = width
    btn = tk.Button(parent, **kw)

    def on_enter(e):
        try:
            r, g, b = btn.winfo_rgb(bg)
            lighter = "#{:02x}{:02x}{:02x}".format(
                min(255, r // 256 + 30),
                min(255, g // 256 + 30),
                min(255, b // 256 + 30),
            )
            btn.config(bg=lighter)
        except Exception:
            pass

    def on_leave(e):
        btn.config(bg=bg)

    btn.bind("<Enter>", on_enter)
    btn.bind("<Leave>", on_leave)
    return btn


def _sep(parent, color=BORDER):
    return tk.Frame(parent, bg=color, height=1)


def _lbl(parent, text, fg=TEXT_DIM, font=FONT_LABEL, bg=BG, **kw):
    return tk.Label(parent, text=text, bg=bg, fg=fg, font=font, **kw)


# ── Import helpers ─────────────────────────────────────────────────────────────
def _do_import(mode_manager, on_success: Callable, root: tk.Misc):
    """Open a file dialog and import the chosen .gpack."""
    path = filedialog.askopenfilename(
        parent=root,
        title="Import GesturePuck Pack",
        filetypes=[("GesturePuck Pack", "*.gpack"), ("JSON", "*.json"), ("All files", "*.*")],
    )
    if not path:
        return

    def task():
        try:
            pack = mode_manager.import_and_reload(path)
            root.after(0, lambda: on_success(pack))
        except Exception as exc:
            root.after(0, lambda: messagebox.showerror(
                "Import Failed",
                f"Could not import pack:\n{exc}",
                parent=root,
            ))

    threading.Thread(target=task, daemon=True).start()


# ── Sidebar section ───────────────────────────────────────────────────────────
def render_pack_sidebar_section(
    sidebar: tk.Frame,
    mode_manager,
    nav_btns: dict[str, tk.Button],
    *,
    on_show_pack: Callable[[str], None],
    on_import_done: Callable | None = None,
    root: tk.Misc | None = None,
):
    """
    Render the "MY PACKS" section at the bottom of the sidebar.
    Only shows packs that have been imported by the user.
    Always shows an '+ Import Pack' button.
    """
    # Clear any previously rendered pack widgets in the sidebar.
    for key in list(nav_btns.keys()):
        if key.startswith("pack:"):
            nav_btns.pop(key)

    packs = mode_manager.installed_packs()

    _sep(sidebar, BORDER).pack(fill="x", padx=8, pady=(12, 6))

    # Section header + import button on same row
    header_row = tk.Frame(sidebar, bg=SURFACE)
    header_row.pack(fill="x", padx=4, pady=(0, 4))

    _lbl(header_row, "  MY PACKS", fg=TEXT_DIM, font=FONT_BADGE,
         bg=SURFACE).pack(side="left")

    def _on_import():
        _root = root or sidebar.winfo_toplevel()
        def _success(pack):
            # Refresh the sidebar by re-rendering everything.
            # This is done by triggering on_import_done if provided.
            if on_import_done:
                on_import_done(pack)

        _do_import(mode_manager, _success, _root)

    import_btn = tk.Button(
        header_row,
        text="+ Import",
        command=_on_import,
        bg=SURFACE, fg=ACCENT,
        font=("Courier New", 8, "bold"),
        relief="flat", padx=4, pady=1,
        cursor="hand2", bd=0,
        highlightthickness=0,
        activebackground=SURFACE2,
        activeforeground=ACCENT,
    )
    import_btn.pack(side="right", padx=(0, 4))

    if not packs:
        _lbl(sidebar, "  No packs installed", fg=TEXT_DIM,
             font=FONT_SMALL, bg=SURFACE).pack(anchor="w", padx=4, pady=(2, 8))
        _lbl(sidebar, "  Download from shop →", fg=TEXT_DIM,
             font=FONT_SMALL, bg=SURFACE).pack(anchor="w", padx=4, pady=(0, 8))
        return

    for pack in packs:
        key = f"pack:{pack.id}"
        icon = pack.icon or "📦"
        b = tk.Button(
            sidebar,
            text=f"  {icon}  {pack.name}",
            command=lambda pid=pack.id: on_show_pack(pid),
            bg=SURFACE, fg=TEXT_MED, font=FONT_NAV,
            relief="flat", anchor="w",
            padx=4, pady=7,
            cursor="hand2", bd=0,
            highlightthickness=0,
            activebackground=SURFACE2,
            activeforeground=TEXT,
        )
        b.pack(fill="x")
        nav_btns[key] = b

        def _enter(e, btn=b):
            if btn.cget("bg") != ACCENT_DIM:
                btn.config(bg=SURFACE2, fg=TEXT)

        def _leave(e, btn=b):
            if btn.cget("bg") != ACCENT_DIM:
                btn.config(bg=SURFACE, fg=TEXT_MED)

        b.bind("<Enter>", _enter)
        b.bind("<Leave>", _leave)


# ── Pack detail page ──────────────────────────────────────────────────────────
def render_pack_page(
    content_frame: tk.Frame,
    pack,
    mode_manager,
    *,
    on_mode_change: Callable | None = None,
    on_remove: Callable[[str], None] | None = None,
):
    """
    Render the detail page for an installed pack inside content_frame.
    Shows pack info, modes, gesture mappings, and Activate / Remove buttons.
    """
    for w in content_frame.winfo_children():
        w.destroy()

    # ── Header ────────────────────────────────────────────────────────────────
    header = tk.Frame(content_frame, bg=BG)
    header.pack(fill="x", padx=24, pady=(20, 4))

    icon_lbl = tk.Label(header, text=pack.icon or "📦", bg=BG, fg=ACCENT,
                         font=("Courier New", 28))
    icon_lbl.pack(side="left", padx=(0, 12))

    title_col = tk.Frame(header, bg=BG)
    title_col.pack(side="left")
    tk.Label(title_col, text=pack.name, bg=BG, fg=ACCENT,
             font=("Courier New", 16, "bold")).pack(anchor="w")
    if pack.description:
        tk.Label(title_col, text=pack.description, bg=BG, fg=TEXT_MED,
                 font=FONT_LABEL, wraplength=500, justify="left").pack(anchor="w")
    if pack.author:
        tk.Label(title_col, text=f"by {pack.author}", bg=BG, fg=TEXT_DIM,
                 font=FONT_SMALL).pack(anchor="w")

    _sep(content_frame, BORDER).pack(fill="x", padx=24, pady=(12, 0))

    # ── Action buttons ────────────────────────────────────────────────────────
    action_row = tk.Frame(content_frame, bg=BG)
    action_row.pack(fill="x", padx=24, pady=(10, 6))

    active = mode_manager.active_pack()
    is_active = active is not None and active.id == pack.id
    activate_lbl = "⏹  DEACTIVATE" if is_active else "▶  ACTIVATE"
    activate_clr = REC_CLR if is_active else SAVE_CLR

    def _toggle_active():
        if mode_manager.active_pack() and mode_manager.active_pack().id == pack.id:
            mode_manager.deactivate()
        else:
            mode_manager.activate(pack.id)
        if on_mode_change:
            on_mode_change()

    _mk_btn(action_row, activate_lbl, _toggle_active,
            bg=activate_clr, fg=BG).pack(side="left", padx=(0, 8))

    def _remove():
        if messagebox.askyesno(
            "Remove Pack",
            f"Remove '{pack.name}'? The .gpack file will be deleted.\n"
            "You can re-import it at any time.",
            parent=content_frame.winfo_toplevel(),
        ):
            mode_manager.remove_pack(pack.id)
            if on_remove:
                on_remove(pack.id)

    _mk_btn(action_row, "✕  REMOVE", _remove,
            bg=SURFACE2, fg=DEL_CLR).pack(side="left")

    # ── Modes + gesture table ─────────────────────────────────────────────────
    _sep(content_frame, BORDER).pack(fill="x", padx=24, pady=(8, 0))

    for mode in pack.modes:
        mode_header = tk.Frame(content_frame, bg=BG)
        mode_header.pack(fill="x", padx=24, pady=(14, 4))

        mode_icon = mode.icon or ""
        tk.Label(mode_header, text=f"{mode_icon}  {mode.name}",
                 bg=BG, fg=TEXT, font=("Courier New", 11, "bold")).pack(side="left")

        tk.Label(mode_header, text="  MODE",
                 bg=BG, fg=TEXT_DIM, font=FONT_BADGE).pack(side="left")

        # Column headers
        hdr = tk.Frame(content_frame, bg=BG)
        hdr.pack(fill="x", padx=36, pady=(2, 2))
        for col, w in [("GESTURE", 20), ("LABEL", 24), ("MACRO / SHORTCUT", 28)]:
            tk.Label(hdr, text=col, bg=BG, fg=TEXT_DIM,
                     font=FONT_BADGE, width=w, anchor="w").pack(side="left")

        _sep(content_frame, BORDER).pack(fill="x", padx=36, pady=(0, 4))

        for i, (gesture, mapping) in enumerate(mode.gestures.items()):
            row_bg = SURFACE2 if i % 2 == 1 else SURFACE
            row = tk.Frame(content_frame, bg=row_bg)
            row.pack(fill="x", padx=24, pady=1)

            tk.Label(row, text=gesture, bg=ACCENT_DIM, fg=TEXT,
                     font=FONT_BADGE, padx=6, pady=2,
                     width=18, anchor="w").pack(side="left", padx=(8, 12), pady=6)

            label_text = mapping.get("label", gesture)
            tk.Label(row, text=label_text, bg=row_bg, fg=TEXT,
                     font=FONT_MONO, width=22, anchor="w").pack(side="left", padx=(0, 8))

            macro_text = mapping.get("macro", "—")
            tk.Label(row, text=macro_text or "—", bg=row_bg, fg=MACRO_CLR,
                     font=FONT_MONO, width=26, anchor="w").pack(side="left")

        _sep(content_frame, BORDER).pack(fill="x", padx=24, pady=(8, 0))

    # ── Footer note ───────────────────────────────────────────────────────────
    note = tk.Frame(content_frame, bg=BG)
    note.pack(fill="x", padx=24, pady=(12, 24))
    _lbl(note,
         "💡  Use hold_center gesture to exit a pack and return to default mode.",
         fg=TEXT_DIM, bg=BG, font=FONT_SMALL).pack(anchor="w")