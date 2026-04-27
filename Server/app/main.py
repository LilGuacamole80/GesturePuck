"""
Gesture Puck — desktop companion
- Shows device name instead of MAC in dropdown
- Custom label per gesture
- Record to capture macro
- Save and delete gestures
"""

import threading
import tkinter as tk
from tkinter import ttk, messagebox
from pynput import keyboard as pynput_kb

import mappings as store
import macro_runner as macro_runner
import bluetooth_spp as bluetooth_spp

# ── theme ─────────────────────────────────────────────────────────────────────
BG         = "#0f0f0f"
SURFACE    = "#1a1a1a"
BORDER     = "#2a2a2a"
ACCENT     = "#00e5a0"
ACCENT_DIM = "#009966"
TEXT       = "#f0f0f0"
TEXT_DIM   = "#888888"
RED        = "#ff4f4f"
YELLOW     = "#f0c040"

FONT_MONO  = ("Courier New", 10)
FONT_LABEL = ("Courier New",  9)
FONT_TITLE = ("Courier New", 13, "bold")
FONT_BTN   = ("Courier New",  9, "bold")

# pynput special key → readable string
PYNPUT_KEY_NAMES = {
    pynput_kb.Key.ctrl:       "ctrl",
    pynput_kb.Key.ctrl_l:     "ctrl",
    pynput_kb.Key.ctrl_r:     "ctrl",
    pynput_kb.Key.shift:      "shift",
    pynput_kb.Key.shift_l:    "shift",
    pynput_kb.Key.shift_r:    "shift",
    pynput_kb.Key.alt:        "alt",
    pynput_kb.Key.alt_l:      "alt",
    pynput_kb.Key.alt_r:      "alt",
    pynput_kb.Key.cmd:        "win",
    pynput_kb.Key.cmd_l:      "win",
    pynput_kb.Key.cmd_r:      "win",
    pynput_kb.Key.enter:      "enter",
    pynput_kb.Key.space:      "space",
    pynput_kb.Key.tab:        "tab",
    pynput_kb.Key.esc:        "esc",
    pynput_kb.Key.backspace:  "backspace",
    pynput_kb.Key.delete:     "delete",
    pynput_kb.Key.home:       "home",
    pynput_kb.Key.end:        "end",
    pynput_kb.Key.page_up:    "pageup",
    pynput_kb.Key.page_down:  "pagedown",
    pynput_kb.Key.left:       "left",
    pynput_kb.Key.right:      "right",
    pynput_kb.Key.up:         "up",
    pynput_kb.Key.down:       "down",
    **{getattr(pynput_kb.Key, f"f{n}"): f"f{n}" for n in range(1, 21)},
}

MODIFIER_KEYS = {
    pynput_kb.Key.ctrl,   pynput_kb.Key.ctrl_l,  pynput_kb.Key.ctrl_r,
    pynput_kb.Key.shift,  pynput_kb.Key.shift_l, pynput_kb.Key.shift_r,
    pynput_kb.Key.alt,    pynput_kb.Key.alt_l,   pynput_kb.Key.alt_r,
    pynput_kb.Key.cmd,    pynput_kb.Key.cmd_l,   pynput_kb.Key.cmd_r,
}


def mk_btn(parent, text, command, bg=ACCENT, fg=BG, **kw):
    return tk.Button(parent, text=text, command=command,
                     bg=bg, fg=fg, activebackground=ACCENT_DIM,
                     activeforeground=fg, font=FONT_BTN,
                     relief="flat", cursor="hand2", padx=10, pady=5, **kw)


# ── key recorder ──────────────────────────────────────────────────────────────

class KeyRecorder:
    """
    Records a key chord (e.g. ctrl+shift+s) from actual keypresses.
    Captures all keys held, finalises when non-modifier key is released.
    """
    def __init__(self, on_done):
        self.on_done    = on_done
        self._held      = set()
        self._chord     = []
        self._listener  = None
        self._done      = False

    def start(self):
        self._held   = set()
        self._chord  = []
        self._done   = False
        self._listener = pynput_kb.Listener(
            on_press=self._on_press,
            on_release=self._on_release,
            suppress = True)
        self._listener.start()

    def stop(self):
        if self._listener:
            self._listener.stop()

    def _key_name(self, key) -> str | None:
        if key in PYNPUT_KEY_NAMES:
            return PYNPUT_KEY_NAMES[key]
        try:
            return key.char.lower() if key.char else None
        except AttributeError:
            return str(key).replace("Key.", "")

    def _on_press(self, key):
        if self._done:
            return
        name = self._key_name(key)
        if name and name not in self._held:
            self._held.add(name)
            if name not in self._chord:
                self._chord.append(name)

    def _on_release(self, key):
        if self._done:
            return
        if key not in MODIFIER_KEYS:
            self._done = True
            self._listener.stop()
            # Sort: modifiers first, then the trigger key
            modifiers = [k for k in self._chord
                         if k in ("ctrl","shift","alt","win","cmd")]
            others    = [k for k in self._chord
                         if k not in ("ctrl","shift","alt","win","cmd")]
            result = "+".join(modifiers + others)
            self.on_done(result)


# ── main app ──────────────────────────────────────────────────────────────────

class GesturePuckApp:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title("Gesture Puck")
        self.root.configure(bg=BG)
        self.root.resizable(False, False)
        self.root.geometry("500x580")

        self.mappings       = store.load()
        self.connection     = None
        self.status_text    = tk.StringVar(value="NOT CONNECTED")
        self.last_gesture   = tk.StringVar(value="—")
        self._devices: list[tuple[str, str]] = []   # (address, name)
        self._macro_vars:  dict[str, tk.StringVar] = {}
        self._label_vars:  dict[str, tk.StringVar] = {}
        self._active_recorder: KeyRecorder | None  = None

        self._build_ui()
        self._scan()

    # ── UI ────────────────────────────────────────────────────────────────────

    def _build_ui(self):
        bar = tk.Frame(self.root, bg=BG, pady=16)
        bar.pack(fill="x", padx=20)
        tk.Label(bar, text="◉ GESTURE PUCK", font=FONT_TITLE,
                 bg=BG, fg=ACCENT).pack(side="left")
        self._dot = tk.Label(bar, text="●", font=("Courier New", 14),
                             bg=BG, fg=RED)
        self._dot.pack(side="right")

        # connection panel
        conn = tk.Frame(self.root, bg=SURFACE, padx=16, pady=14)
        conn.pack(fill="x", padx=20)
        tk.Label(conn, text="NEARBY DEVICES", font=FONT_LABEL,
                 bg=SURFACE, fg=TEXT_DIM).pack(anchor="w")

        row = tk.Frame(conn, bg=SURFACE)
        row.pack(fill="x", pady=(4, 0))
        self.device_var = tk.StringVar()
        self.combo = ttk.Combobox(row, textvariable=self.device_var,
                                  font=FONT_MONO, width=28, state="readonly")
        self._style_combo()
        self.combo.pack(side="left", fill="x", expand=True)
        mk_btn(row, "↺", self._scan, bg=BORDER, fg=TEXT).pack(side="left", padx=(6, 0))
        mk_btn(conn, "CONNECT", self._connect).pack(fill="x", pady=(10, 0))

        tk.Label(self.root, textvariable=self.status_text, font=FONT_LABEL,
                 bg=BG, fg=TEXT_DIM).pack(anchor="w", padx=20, pady=6)

        # last gesture
        gf = tk.Frame(self.root, bg=SURFACE, padx=16, pady=14)
        gf.pack(fill="x", padx=20, pady=(0, 4))
        tk.Label(gf, text="LAST GESTURE", font=FONT_LABEL,
                 bg=SURFACE, fg=TEXT_DIM).pack(anchor="w")
        tk.Label(gf, textvariable=self.last_gesture,
                 font=("Courier New", 20, "bold"),
                 bg=SURFACE, fg=ACCENT).pack(anchor="w", pady=(4, 0))

        # mappings
        outer = tk.Frame(self.root, bg=BG)
        outer.pack(fill="both", expand=True, padx=20, pady=(4, 16))

        # column headers
        hdr = tk.Frame(outer, bg=BG)
        hdr.pack(fill="x", pady=(0, 4))
        tk.Label(hdr, text="GESTURE", font=FONT_LABEL, bg=BG,
                 fg=TEXT_DIM, width=8, anchor="w").pack(side="left")
        tk.Label(hdr, text="LABEL", font=FONT_LABEL, bg=BG,
                 fg=TEXT_DIM, width=12, anchor="w").pack(side="left", padx=(8, 0))
        tk.Label(hdr, text="MACRO", font=FONT_LABEL, bg=BG,
                 fg=TEXT_DIM).pack(side="left", padx=(8, 0))

        self.map_frame = tk.Frame(outer, bg=BG)
        self.map_frame.pack(fill="both", expand=True)
        self._rebuild_rows()
        mk_btn(outer, "+ ADD GESTURE", self._add_gesture,
               bg=BORDER, fg=TEXT).pack(fill="x", pady=(8, 0))

    def _style_combo(self):
        s = ttk.Style()
        s.theme_use("default")
        s.configure("TCombobox", fieldbackground=SURFACE, background=SURFACE,
                    foreground=TEXT, selectbackground=ACCENT,
                    selectforeground=BG, bordercolor=BORDER, arrowcolor=ACCENT)

    # ── mapping rows ──────────────────────────────────────────────────────────

    def _rebuild_rows(self):
        for w in self.map_frame.winfo_children():
            w.destroy()
        self._macro_vars.clear()
        self._label_vars.clear()
        for gesture, data in self.mappings.items():
            self._add_row(gesture, data.get("label", gesture), data.get("macro", ""))

    def _add_row(self, gesture: str, label: str, macro: str):
        row = tk.Frame(self.map_frame, bg=SURFACE, padx=10, pady=8)
        row.pack(fill="x", pady=(0, 4))

        # gesture id (fixed)
        tk.Label(row, text=gesture, font=("Courier New", 9, "bold"),
                 bg=SURFACE, fg=ACCENT, width=6, anchor="w").pack(side="left")

        # editable label
        lvar = tk.StringVar(value=label)
        self._label_vars[gesture] = lvar
        tk.Entry(row, textvariable=lvar, font=FONT_MONO,
                 bg=BG, fg=TEXT, insertbackground=ACCENT,
                 relief="flat", bd=0, width=11).pack(side="left", padx=(6, 0))

        tk.Label(row, text="→", font=FONT_MONO,
                 bg=SURFACE, fg=TEXT_DIM).pack(side="left", padx=6)

        # macro display
        mvar = tk.StringVar(value=macro)
        self._macro_vars[gesture] = mvar
        macro_entry = tk.Entry(row, textvariable=mvar, font=FONT_MONO,
                               bg=BG, fg=ACCENT, insertbackground=ACCENT,
                               relief="flat", bd=0, width=14)
        macro_entry.pack(side="left")

        # record button
        rec_btn = mk_btn(row, "⏺ REC",
                         lambda g=gesture, mv=mvar, e=macro_entry: self._start_record(g, mv, e),
                         bg=BORDER, fg=YELLOW)
        rec_btn.pack(side="left", padx=(6, 0))

        # save button
        mk_btn(row, "SAVE",
               lambda g=gesture, mv=mvar, lv=lvar: self._save(g, mv.get(), lv.get())
               ).pack(side="left", padx=(4, 0))

        # delete button
        mk_btn(row, "✕",
               lambda g=gesture: self._delete(g),
               bg=BORDER, fg=RED).pack(side="left", padx=(4, 0))

    # ── key recording ─────────────────────────────────────────────────────────

    def _start_record(self, gesture: str, mvar: tk.StringVar, entry: tk.Entry):
        # Stop any existing recorder
        if self._active_recorder:
            self._active_recorder.stop()

        mvar.set("● recording...")
        entry.config(fg=YELLOW)

        def on_done(chord: str):
            self.root.after(0, lambda: self._finish_record(chord, mvar, entry))

        self._active_recorder = KeyRecorder(on_done)
        self._active_recorder.start()

    def _finish_record(self, chord: str, mvar: tk.StringVar, entry: tk.Entry):
        mvar.set(chord)
        entry.config(fg=ACCENT)
        self._active_recorder = None

    # ── scanning ──────────────────────────────────────────────────────────────

    def _scan(self):
        self._set_status("SCANNING...", YELLOW)
        self._devices = []
        self.combo["values"] = []

        def do_scan():
            devices = bluetooth_spp.list_devices()
            self.root.after(0, lambda: self._on_scan_done(devices))

        threading.Thread(target=do_scan, daemon=True).start()

    def _on_scan_done(self, devices: list[tuple[str, str]]):
        self._devices = devices
        if devices:
            # Show names in dropdown, not MACs
            names = [name for _, name in devices]
            self.combo["values"] = names
            self.combo.current(0)
            self._set_status(f"FOUND {len(devices)} DEVICE(S)", ACCENT)
        else:
            self.combo["values"] = ["(no devices found)"]
            self._set_status("NO DEVICES FOUND — IS ESP32 ON?", RED)

    # ── connection ────────────────────────────────────────────────────────────

    def _connect(self):
        selected_name = self.device_var.get().strip()
        if not selected_name or "no devices" in selected_name:
            messagebox.showwarning("No Device", "Scan for devices first.")
            return

        # Look up address from name
        address = next((a for a, n in self._devices if n == selected_name), None)
        if not address:
            messagebox.showerror("Error", "Device not found — rescan.")
            return

        if self.connection:
            self.connection.close()
            self.connection = None

        self._set_status(f"CONNECTING  {selected_name}...", YELLOW)

        def do_connect():
            try:
                conn = bluetooth_spp.connect(address, self._on_event_threadsafe)
                self.connection = conn
                self.root.after(0, lambda: self._set_status(
                    f"CONNECTED  {selected_name}", ACCENT))
            except Exception as e:
                self.root.after(0, lambda: messagebox.showerror("Connection Failed", str(e)))
                self.root.after(0, lambda: self._set_status("CONNECTION FAILED", RED))

        threading.Thread(target=do_connect, daemon=True).start()

    def _set_status(self, text: str, color: str):
        self.status_text.set(text)
        self._dot.config(fg=color)

    # ── event handling ────────────────────────────────────────────────────────

    def _on_event_threadsafe(self, line: str):
        self.root.after(0, lambda: self._on_event(line))

    def _on_event(self, line: str):
        if line == "PING":
            return
        if line.endswith("_DOWN"):
            gesture = line.replace("_DOWN", "")
            label   = self.mappings.get(gesture, {}).get("label", gesture)
            self.last_gesture.set(f"{gesture}  ({label})")
            macro = self.mappings.get(gesture, {}).get("macro", "")
            if macro:
                try:
                    macro_runner.run_macro(macro)
                except Exception as e:
                    messagebox.showerror("Macro Error", str(e))
            else:
                self._prompt_map(gesture)

    # ── mapping actions ───────────────────────────────────────────────────────

    def _save(self, gesture: str, macro: str, label: str):
        self.mappings[gesture] = {"label": label, "macro": macro}
        store.save(self.mappings)
        old = self.status_text.get()
        self.status_text.set(f"✓  saved  {label}")
        self.root.after(1500, lambda: self.status_text.set(old))

    def _delete(self, gesture: str):
        if messagebox.askyesno("Delete", f"Delete mapping for {gesture}?"):
            self.mappings.pop(gesture, None)
            store.save(self.mappings)
            self._rebuild_rows()

    def _add_gesture(self):
        win = tk.Toplevel(self.root)
        win.title("Add Gesture")
        win.configure(bg=BG)
        win.geometry("320x230")
        win.grab_set()

        tk.Label(win, text="GESTURE ID  (e.g. BTN3)", font=FONT_LABEL,
                    bg=BG, fg=TEXT_DIM).pack(anchor="w", padx=20, pady=(10, 2))
        g_var = tk.StringVar()
        tk.Entry(win, textvariable=g_var, font=FONT_MONO, bg=SURFACE,
                    fg=TEXT, insertbackground=ACCENT,
                    relief="flat", bd=0).pack(fill="x", padx=20)

        tk.Label(win, text="LABEL  (e.g. Screenshot)", font=FONT_LABEL,
                    bg=BG, fg=TEXT_DIM).pack(anchor="w", padx=20, pady=(10, 2))
        l_var = tk.StringVar()
        tk.Entry(win, textvariable=l_var, font=FONT_MONO, bg=SURFACE,
                    fg=TEXT, insertbackground=ACCENT,
                    relief="flat", bd=0).pack(fill="x", padx=20)

        tk.Label(win, text="MACRO  — type or press REC", font=FONT_LABEL,
                    bg=BG, fg=TEXT_DIM).pack(anchor="w", padx=20, pady=(10, 2))

        m_var = tk.StringVar()
        m_entry = tk.Entry(win, textvariable=m_var, font=FONT_MONO, bg=SURFACE,
                            fg=TEXT, insertbackground=ACCENT,
                            relief="flat", bd=0)
        m_entry.pack(fill="x", padx=20)

        btn_row = tk.Frame(win, bg=BG)
        btn_row.pack(pady=10)

        mk_btn(btn_row, "⏺ REC",
                lambda: self._start_record("_add_popup", m_var, m_entry),
                bg=BORDER, fg=YELLOW).pack(side="left", padx=4)

        def confirm():
            g = g_var.get().strip()
            l = l_var.get().strip() or g
            m = m_var.get().strip()
            if not g or not m:
                messagebox.showwarning("Empty", "Gesture ID and macro are required.", parent=win)
                return
            if "recording" in m:
                messagebox.showwarning("Still Recording", "Finish recording first.", parent=win)
                return
            self.mappings[g] = {"label": l, "macro": m}
            store.save(self.mappings)
            self._rebuild_rows()
            win.destroy()

        mk_btn(btn_row, "ADD", confirm).pack(side="left", padx=4)

    def _prompt_map(self, gesture: str):
        if not messagebox.askyesno("New Gesture",
                                   f"'{gesture}' has no macro.\nMap it now?"):
            return
        win = tk.Toplevel(self.root)
        win.title(f"Map {gesture}")
        win.configure(bg=BG)
        win.geometry("320x160")
        win.grab_set()

        tk.Label(win, text="LABEL", font=FONT_LABEL,
                 bg=BG, fg=TEXT_DIM).pack(anchor="w", padx=20, pady=(16, 2))
        l_var = tk.StringVar(value=gesture)
        tk.Entry(win, textvariable=l_var, font=FONT_MONO, bg=SURFACE,
                 fg=TEXT, insertbackground=ACCENT, relief="flat", bd=0).pack(fill="x", padx=20)

        tk.Label(win, text="MACRO  (press REC or type)", font=FONT_LABEL,
                 bg=BG, fg=TEXT_DIM).pack(anchor="w", padx=20, pady=(10, 2))
        m_var = tk.StringVar()
        m_entry = tk.Entry(win, textvariable=m_var, font=FONT_MONO, bg=SURFACE,
                           fg=TEXT, insertbackground=ACCENT, relief="flat", bd=0)
        m_entry.pack(fill="x", padx=20)

        btn_row = tk.Frame(win, bg=BG)
        btn_row.pack(pady=10)
        mk_btn(btn_row, "⏺ REC",
               lambda: self._start_record(gesture, m_var, m_entry),
               bg=BORDER, fg=YELLOW).pack(side="left", padx=4)

        def confirm():
            m = m_var.get().strip()
            if not m or "recording" in m:
                return
            self._save(gesture, m, l_var.get().strip() or gesture)
            self._rebuild_rows()
            win.destroy()

        mk_btn(btn_row, "SAVE", confirm).pack(side="left", padx=4)


def main():
    root = tk.Tk()
    GesturePuckApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()