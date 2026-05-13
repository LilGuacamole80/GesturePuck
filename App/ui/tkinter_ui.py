import queue
import threading
import tkinter as tk
from tkinter import ttk, messagebox
from pynput import keyboard as pynput_kb
from engine.active_app import get_mapped_app
import subprocess
import sys
import ctypes
from engine.active_app import get_mapped_app
from engine.gesture_engine import GestureEngine

try:
    import engine.mappings as store
    import engine.macro_runner as macro_runner
    #import engine.bluetooth_spp as bluetooth_spp
except ImportError:
    class _Store:
        _data: dict = {}
        @classmethod
        def load(cls): return cls._data
        @classmethod
        def save(cls, d): cls._data = d
    store = _Store

    class _MacroRunner:
        @staticmethod
        def run_macro(m): print(f"[macro] {m}")
    macro_runner = _MacroRunner

    class _BT:
        @staticmethod
        def list_devices(): return []
        @staticmethod
        def connect(addr, cb): return None
    bluetooth_spp = _BT


# ── PALETTE ────────────────────────────────────────────────────────────────────
BG          = "#080808"   # near-black background
SURFACE     = "#111111"   # card / sidebar surface
SURFACE2    = "#1a1a1a"   # slightly lighter surface (hover, row alt)
BORDER      = "#222222"   # hairline borders
ACCENT      = "#2BC2F0"   # cyan — primary accent
ACCENT2     = "#da29e7"   # purple — secondary accent
ACCENT_DIM  = "#1a7a9a"   # dimmed cyan for active nav
TEXT        = "#f0f0f0"   # primary text
TEXT_DIM    = "#555555"   # muted / label text
TEXT_MED    = "#999999"   # medium text
MACRO_CLR   = "#a78bfa"   # soft purple for macro values
REC_CLR     = "#f87171"   # soft red for recording state
SAVE_CLR    = "#34d399"   # soft green for save button
DEL_CLR     = "#f87171"   # soft red for delete

# ── FONTS ──────────────────────────────────────────────────────────────────────
FONT_MONO   = ("Courier New", 10)
FONT_LABEL  = ("Courier New", 9)
FONT_TITLE  = ("Courier New", 13, "bold")
FONT_BTN    = ("Courier New", 9, "bold")
FONT_NAV    = ("Courier New", 10)
FONT_BADGE  = ("Courier New", 8, "bold")


PYNPUT_KEY_NAMES = {
    pynput_kb.Key.ctrl:      "ctrl",
    pynput_kb.Key.ctrl_l:    "ctrl",
    pynput_kb.Key.ctrl_r:    "ctrl",
    pynput_kb.Key.shift:     "shift",
    pynput_kb.Key.shift_l:   "shift",
    pynput_kb.Key.shift_r:   "shift",
    pynput_kb.Key.alt:       "alt",
    pynput_kb.Key.alt_l:     "alt",
    pynput_kb.Key.alt_r:     "alt",
    pynput_kb.Key.cmd:       "cmd",
    pynput_kb.Key.enter:     "enter",
    pynput_kb.Key.space:     "space",
    pynput_kb.Key.tab:       "tab",
    pynput_kb.Key.esc:       "esc",
    pynput_kb.Key.backspace: "backspace",
    pynput_kb.Key.delete:    "delete",
    pynput_kb.Key.up:        "up",
    pynput_kb.Key.down:      "down",
    pynput_kb.Key.left:      "left",
    pynput_kb.Key.right:     "right",
    pynput_kb.Key.f1:  "f1",  pynput_kb.Key.f2:  "f2",  pynput_kb.Key.f3:  "f3",
    pynput_kb.Key.f4:  "f4",  pynput_kb.Key.f5:  "f5",  pynput_kb.Key.f6:  "f6",
    pynput_kb.Key.f7:  "f7",  pynput_kb.Key.f8:  "f8",  pynput_kb.Key.f9:  "f9",
    pynput_kb.Key.f10: "f10", pynput_kb.Key.f11: "f11", pynput_kb.Key.f12: "f12",
}

MODIFIER_KEYS = {
    pynput_kb.Key.ctrl,    pynput_kb.Key.ctrl_l,  pynput_kb.Key.ctrl_r,
    pynput_kb.Key.shift,   pynput_kb.Key.shift_l, pynput_kb.Key.shift_r,
    pynput_kb.Key.alt,     pynput_kb.Key.alt_l,   pynput_kb.Key.alt_r,
    pynput_kb.Key.cmd,
}

KNOWN_GESTURES = [
        "swipe_left", "swipe_right", "swipe_up", "swipe_down",
        "push", "pull", "hold_center"
    ]

for _k in ("cmd_l", "cmd_r"):
    _v = getattr(pynput_kb.Key, _k, None)
    if _v:
        MODIFIER_KEYS.add(_v)


# ── WIDGET HELPERS ─────────────────────────────────────────────────────────────
def mk_btn(parent, text, command, bg=ACCENT, fg=BG, width=None):
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


def mk_separator(parent, color=BORDER):
    return tk.Frame(parent, bg=color, height=1)


def mk_label(parent, text, fg=TEXT_DIM, font=FONT_LABEL, bg=BG, **kw):
    return tk.Label(parent, text=text, bg=bg, fg=fg, font=font, **kw)



def check_macos_permissions():
    """Checks and prompts for required macOS permissions on first launch"""
    if sys.platform != "darwin":
        return
    
    # Check if we have accessibility access
    result = subprocess.run(
        ["osascript", "-e", 
         'tell application "System Events" to get name of first process'],
        capture_output=True
    )
    
    if result.returncode != 0:
        # No accessibility permission — show instructions
        import tkinter.messagebox as mb
        mb.showwarning(
            "Permission Required",
            "GesturePuck needs Accessibility permission to simulate key presses.\n\n"
            "Please go to:\n"
            "System Settings → Privacy & Security → Accessibility\n\n"
            "Enable GesturePuck, then restart the app."
        )
        # Open the right settings page automatically
        subprocess.run([
            "open", 
            "x-apple.systempreferences:com.apple.preference.security?Privacy_Accessibility"
        ])

# ── KEY RECORDER ───────────────────────────────────────────────────────────────
class GlobalKeyRecorder:
    def __init__(self):
        self._armed = False
        self._lock  = threading.Lock()
        self._chord: list[str] = []
        self._held:  set       = set()
        self._token = 0
        self.results: queue.Queue = queue.Queue()
        self._listener = pynput_kb.Listener(
            on_press=self._on_press,
            on_release=self._on_release,
            suppress=False,
        )
        self._listener.daemon = True
        self._listener.start()

    def arm(self, token):
        with self._lock:
            self._armed = True
            self._chord = []
            self._held  = set()
            self._token = token

    def cancel(self):
        with self._lock:
            self._armed = False
            self._chord = []
            self._held  = set()

    def _canonical(self, key):
        if key in PYNPUT_KEY_NAMES:
            return PYNPUT_KEY_NAMES[key]
        ch = getattr(key, "char", None)
        if ch:
            return ch.lower()
        return str(key).replace("<", "").replace(">", "")

    def _on_press(self, key):
        with self._lock:
            if not self._armed:
                return
            name = self._canonical(key)
            if key not in self._held:
                self._held.add(key)
                if name not in self._chord:
                    self._chord.append(name)

    def _on_release(self, key):
        with self._lock:
            if not self._armed:
                return
            if key in MODIFIER_KEYS:
                return
            chord = "+".join(self._chord)
            token = self._token
            self._armed = False
            self._chord = []
            self._held  = set()
        self.results.put((token, chord))


# ── MAIN APP ───────────────────────────────────────────────────────────────────
class GesturePuckApp:
    def __init__(self, root):
        self.root = root
        self.root.title("GesturePuck")
        sw, sh = self.root.winfo_screenwidth(), self.root.winfo_screenheight()
        self.root.geometry(f"{sw}x{sh}")
        self.root.configure(bg=BG)

        self.mappings     = store.load()
        self.devices      = []
        self.connection   = None
        self.current_page = "Global"
        self.engine       = None          # ← add this

        self._macro_vars: dict[str, tk.StringVar] = {}
        self._label_vars: dict[str, tk.StringVar] = {}
        self._entries:    dict[str, tk.Entry]     = {}
        self._next_token  = 1
        self._pending: dict[int, str] = {}

        self.recorder     = GlobalKeyRecorder()
        self.status       = tk.StringVar(value="NOT CONNECTED")
        self.last_gesture = tk.StringVar(value="—")
        self.device_var   = tk.StringVar()

        self._build_ui()
        self._show_page("Global")
        self.root.after(50, self._poll_recorder)

        

    # ── BUILD ──────────────────────────────────────────────────────────────────
    def _build_ui(self):
        # ── TOP BAR ───────────────────────────────────────────────────────────
        topbar = tk.Frame(self.root, bg=SURFACE, height=52)
        topbar.pack(fill="x")
        topbar.pack_propagate(False)

        # logo dot + wordmark
        logo_area = tk.Frame(topbar, bg=SURFACE)
        logo_area.pack(side="left", padx=(20, 0))

        dot_canvas = tk.Canvas(logo_area, width=14, height=14,
                               bg=SURFACE, highlightthickness=0)
        dot_canvas.pack(side="left", pady=18)
        dot_canvas.create_oval(1, 1, 13, 13, outline=ACCENT, width=2)

        tk.Label(logo_area, text="  G E S T U R E P U C K", bg=SURFACE, fg=TEXT,
                 font=("Courier New", 12, "bold"),
                 ).pack(side="left")

        # status pill
        pill = tk.Frame(topbar, bg=SURFACE)
        pill.pack(side="right", padx=20)
        self._status_dot = tk.Canvas(pill, width=8, height=8,
                                     bg=SURFACE, highlightthickness=0)
        self._status_dot.pack(side="left", pady=20)
        self._status_dot.create_oval(0, 0, 8, 8, fill=TEXT_DIM, outline="")
        self._status_lbl = tk.Label(pill, textvariable=self.status,
                                    bg=SURFACE, fg=TEXT_DIM, font=FONT_LABEL)
        self._status_lbl.pack(side="left", padx=(4, 0))

        # ── DEVICE BAR ────────────────────────────────────────────────────────
        devrow = tk.Frame(self.root, bg=BG)
        devrow.pack(fill="x", padx=20, pady=8)

        mk_label(devrow, "PORT", fg=TEXT_DIM).pack(side="left", padx=(0, 8))
        self.port_var = tk.StringVar(value="/dev/cu.usbmodem2101")
        tk.Entry(devrow, textvariable=self.port_var, bg=SURFACE, fg=TEXT,
         insertbackground=ACCENT, relief="flat", width=24, font=FONT_MONO,
         highlightthickness=1, highlightbackground=BORDER,
         highlightcolor=ACCENT).pack(side="left", padx=(0, 8), ipady=3)

       # mk_btn(devrow, "CONNECT", self._connect, bg=ACCENT, fg=BG).pack(side="left", padx=(0, 8))
        mk_btn(devrow, "DEMO",    self._connect_demo, bg=SURFACE2, fg=TEXT_MED).pack(side="left")

        # gesture pill
        gf = tk.Frame(devrow, bg=BG)
        gf.pack(side="right")
        mk_label(gf, "LAST GESTURE", fg=TEXT_DIM).pack(side="left")
        self._gesture_badge = tk.Label(
            gf, textvariable=self.last_gesture,
            bg=SURFACE2, fg=ACCENT,
            font=("Courier New", 11, "bold"),
            padx=10, pady=2,
        )
        self._gesture_badge.pack(side="left", padx=(8, 0))

        mk_separator(self.root).pack(fill="x")

        # ── BODY ──────────────────────────────────────────────────────────────
        body = tk.Frame(self.root, bg=BG)
        body.pack(fill="both", expand=True)

        # sidebar
        sidebar = tk.Frame(body, bg=SURFACE, width=200)
        sidebar.pack(side="left", fill="y")
        sidebar.pack_propagate(False)

        mk_label(sidebar, "  APPS", fg=TEXT_DIM, font=FONT_BADGE,
                 bg=SURFACE).pack(anchor="w", pady=(16, 6))

        self._nav_btns: dict[str, tk.Button] = {}
        apps = [
            "Global", "Figma", "Adobe Photoshop", "Blender",
            "Visual Studio Code", "Google Slides", "Notion", "Slack", "OBS Studio"
        ]
        for app in apps:
            b = tk.Button(
                sidebar, text=f"  {app}",
                command=lambda a=app: self._show_page(a),
                bg=SURFACE, fg=TEXT_MED, font=FONT_NAV,
                relief="flat", anchor="w",
                padx=4, pady=7,
                cursor="hand2", bd=0,
                highlightthickness=0,
                activebackground=SURFACE2,
                activeforeground=TEXT,
            )
            b.pack(fill="x")
            self._nav_btns[app] = b

            # hover effect
            def _enter(e, btn=b):
                if btn.cget("bg") != ACCENT_DIM:
                    btn.config(bg=SURFACE2, fg=TEXT)

            def _leave(e, btn=b):
                if btn.cget("bg") != ACCENT_DIM:
                    btn.config(bg=SURFACE, fg=TEXT_MED)

            b.bind("<Enter>", _enter)
            b.bind("<Leave>", _leave)

        # right separator
        tk.Frame(body, bg=BORDER, width=1).pack(side="left", fill="y")

        # scrollable content
        content_outer = tk.Frame(body, bg=BG)
        content_outer.pack(side="left", fill="both", expand=True)

        self.canvas = tk.Canvas(content_outer, bg=BG, highlightthickness=0)
        sb = ttk.Scrollbar(content_outer, orient="vertical",
                           command=self.canvas.yview)
        self.canvas.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        self.canvas.pack(side="left", fill="both", expand=True)

        self.content_frame = tk.Frame(self.canvas, bg=BG)
        self._cwin = self.canvas.create_window(
            (0, 0), window=self.content_frame, anchor="nw")

        self.content_frame.bind(
            "<Configure>",
            lambda e: self.canvas.configure(
                scrollregion=self.canvas.bbox("all")))
        self.canvas.bind(
            "<Configure>",
            lambda e: self.canvas.itemconfig(self._cwin, width=e.width))

    # ── NAVIGATION ────────────────────────────────────────────────────────────
    def _show_page(self, app_name):
        self.current_page = app_name
        for name, btn in self._nav_btns.items():
            if name == app_name:
                btn.config(bg=ACCENT_DIM, fg=TEXT)
            else:
                btn.config(bg=SURFACE, fg=TEXT_MED)
        self._render_page(app_name)

    # ── RENDER ────────────────────────────────────────────────────────────────

    def _render_page(self, app_name):
        for w in self.content_frame.winfo_children():
            w.destroy()
        self._macro_vars.clear()
        self._label_vars.clear()
        self._entries.clear()

        header = tk.Frame(self.content_frame, bg=BG)
        header.pack(fill="x", padx=24, pady=(20, 4))
        tk.Label(header, text=app_name, bg=BG, fg=ACCENT,
             font=("Courier New", 16, "bold")).pack(side="left")
        tk.Label(header, text="  ·  Gesture Mappings", bg=BG,
             fg=TEXT_DIM, font=FONT_LABEL).pack(side="left")

        mk_separator(self.content_frame, BORDER).pack(fill="x", padx=24, pady=(8, 0))

        hdr = tk.Frame(self.content_frame, bg=BG)
        hdr.pack(fill="x", padx=24, pady=(6, 2))
        for col, w in [("GESTURE", 18), ("LABEL", 22), ("MACRO / SHORTCUT", 26)]:
            tk.Label(hdr, text=col, bg=BG, fg=TEXT_DIM,
                 font=FONT_BADGE, width=w, anchor="w").pack(side="left")
        tk.Label(hdr, text="ACTIONS", bg=BG, fg=TEXT_DIM,
             font=FONT_BADGE).pack(side="left")

        mk_separator(self.content_frame, BORDER).pack(fill="x", padx=24, pady=(2, 4))

        rows = self.mappings.get(app_name, {})
        for i, gesture in enumerate(KNOWN_GESTURES):
            data = rows.get(gesture, {"label": gesture, "macro": ""})
            self._add_row(gesture, data.get("label", gesture),
                      data.get("macro", ""), alt=(i % 2 == 1))


    def _add_row(self, gesture, label, macro, alt=False):
        row_bg = SURFACE2 if alt else SURFACE
        row = tk.Frame(self.content_frame, bg=row_bg)
        row.pack(fill="x", padx=24, pady=1)

        # gesture name badge
        badge = tk.Label(row, text=gesture, bg=ACCENT_DIM, fg=TEXT,
                         font=FONT_BADGE, padx=6, pady=2, width=16, anchor="w")
        badge.pack(side="left", padx=(8, 12), pady=6)

        # label entry
        lvar = tk.StringVar(value=label)
        self._label_vars[gesture] = lvar
        label_entry = tk.Entry(
            row, textvariable=lvar, bg=BG, fg=TEXT,
            insertbackground=ACCENT, relief="flat",
            width=20, font=FONT_MONO,
            highlightthickness=1,
            highlightbackground=BORDER,
            highlightcolor=ACCENT,
        )
        label_entry.pack(side="left", padx=(0, 8), ipady=3)

        # macro entry
        mvar = tk.StringVar(value=macro)
        self._macro_vars[gesture] = mvar
        entry = tk.Entry(
            row, textvariable=mvar, bg=BG, fg=MACRO_CLR,
            insertbackground=ACCENT, relief="flat",
            width=22, font=FONT_MONO,
            highlightthickness=1,
            highlightbackground=BORDER,
            highlightcolor=ACCENT,
        )
        entry.pack(side="left", padx=(0, 12), ipady=3)
        self._entries[gesture] = entry

        # action buttons
        mk_btn(row, "⏺  REC",
               lambda g=gesture: self._start_record(g),
               bg=SURFACE, fg=REC_CLR).pack(side="left", padx=(0, 4))

        mk_btn(row, "✓  SAVE",
               lambda g=gesture, mv=mvar, lv=lvar: self._save(
                   g, mv.get(), lv.get()),
               bg=SURFACE, fg=SAVE_CLR).pack(side="left", padx=(0, 4))

        mk_btn(row, "✕",
               lambda g=gesture: self._delete(g),
               bg=SURFACE, fg=DEL_CLR).pack(side="left", padx=(0, 8))

    # ── DEVICE / CONNECT ──────────────────────────────────────────────────────
    def _scan_devices(self):
        pass
        self._set_status("SCANNING…", TEXT_DIM)
        def task():
            devs = bluetooth_spp.list_devices()
            self.root.after(0, lambda: self._update_devices(devs))
        threading.Thread(target=task, daemon=True).start()

    def _update_devices(self, devices):
        pass
        self.devices = devices
        names = [n for _, n in devices]
        self.combo["values"] = names
        if names:
            self.combo.current(0)
            self._set_status("DEVICES FOUND", TEXT_MED)
        else:
            self._set_status("NO DEVICES", TEXT_DIM)

    def _connect(self):
        port = self.port_var.get().strip()
        if not port:
            messagebox.showerror("Error", "Enter a serial port")
            return
        self._start_engine(port=port, demo=False)

    def _connect_demo(self):
        self._start_engine(port=None, demo=True)

    def _start_engine(self, port, demo):
    # Stop any existing engine first
        if hasattr(self, "engine") and self.engine is not None:
            self.engine.stop()
            self.engine = None

        self._set_status("CONNECTING…", TEXT_DIM)

        def task():
            try:
                eng = GestureEngine(
                port=port or "",
                on_gesture=self._on_gesture_event,
                demo=demo,
                )
                eng.start()
                self.engine = eng
                self.root.after(0, lambda: self._set_status(
                "DEMO" if demo else "CONNECTED", ACCENT))
            except Exception as exc:
                self.root.after(0, lambda: self._set_status("ERROR", REC_CLR))
                self.root.after(0, lambda: messagebox.showerror(
                "Connection failed", str(exc)))
        threading.Thread(target=task, daemon=True).start()

    def _set_status(self, text, color):
        self.status.set(text)
        self._status_lbl.config(fg=color)
        dot_color = ACCENT if color == ACCENT else (
            REC_CLR if color == REC_CLR else TEXT_DIM)
        self._status_dot.delete("all")
        self._status_dot.create_oval(0, 0, 8, 8, fill=dot_color, outline="")

    # ── EVENTS ────────────────────────────────────────────────────────────────
    def _on_gesture_event(self, gesture_name: str, confidence: float):
    # Engine runs in a background thread, so use root.after() 
    # to safely touch the UI from the main thread
        self.root.after(0, lambda: self._handle_gesture(gesture_name, confidence))

    def _handle_gesture(self, gesture_name: str, confidence: float):
        self.last_gesture.set(f"{gesture_name} ({confidence:.0%})")
    
        active_app = get_mapped_app()
        macro = (
            self.mappings.get(active_app, {}).get(gesture_name, {}).get("macro")
            or
            self.mappings.get("Global", {}).get(gesture_name, {}).get("macro")
        )
        if macro:
            macro_runner.run_macro(macro)
       



    def _on_event(self, msg):
        pass
        print(f"[DEBUG] received: {repr(msg)}")
        if "_DOWN" in msg:
            gesture = msg.replace("_DOWN", "").strip()
            self.root.after(0, lambda: self.last_gesture.set(gesture))
            active_app = get_mapped_app()
            print(f"[DEBUG] active app: {active_app}, gesture: {gesture}")
            macro = (
                self.mappings.get(active_app, {}).get(gesture, {}).get("macro")
                or
                self.mappings.get("Global", {}).get(gesture, {}).get("macro")
            )
            print(f"[DEBUG] macro found: {repr(macro)}")
            if macro:
                macro_runner.run_macro(macro)
            else:
                self.root.after(0, lambda: self._ask_map(gesture))

    # ── RECORDING ─────────────────────────────────────────────────────────────
    def _start_record(self, gesture):
        for tok, prev_g in list(self._pending.items()):
            prev_entry = self._entries.get(prev_g)
            prev_mvar  = self._macro_vars.get(prev_g)
            if prev_entry and prev_mvar:
                prev_entry.config(fg=MACRO_CLR)
                if prev_mvar.get() == "recording…":
                    prev_mvar.set("")
            self._pending.pop(tok, None)

        self.recorder.cancel()

        token = self._next_token
        self._next_token += 1
        self._pending[token] = gesture

        mvar  = self._macro_vars[gesture]
        entry = self._entries[gesture]
        mvar.set("recording…")
        entry.config(fg=REC_CLR)
        self.recorder.arm(token)

    def _poll_recorder(self):
        try:
            while True:
                token, chord = self.recorder.results.get_nowait()
                gesture = self._pending.pop(token, None)
                if gesture is None:
                    continue
                mvar  = self._macro_vars.get(gesture)
                entry = self._entries.get(gesture)
                if mvar is None or entry is None:
                    continue
                mvar.set(chord)
                entry.config(fg=MACRO_CLR)
        except queue.Empty:
            pass
        self.root.after(50, self._poll_recorder)

    # ── CRUD ──────────────────────────────────────────────────────────────────
    def _save(self, gesture, macro, label):
        self.mappings.setdefault(self.current_page, {})[gesture] = {
            "label": label, "macro": macro}
        store.save(self.mappings)

    def _delete(self, gesture):
        if messagebox.askyesno("Delete", f"Delete mapping for '{gesture}'?"):
            self.mappings.get(self.current_page, {}).pop(gesture, None)
            store.save(self.mappings)
            self._render_page(self.current_page)

    def _ask_map(self, gesture):
        if not messagebox.askyesno(
                "New Gesture",
                f"'{gesture}' has no mapping.\nAdd one for this app?"):
            return
        self.mappings.setdefault(self.current_page, {})[gesture] = {
            "label": gesture, "macro": ""}
        store.save(self.mappings)
        self._render_page(self.current_page)

    def _add_new_gesture(self, app_name):
        pass
        existing = self.mappings.get(app_name, {})
        i, name = len(existing) + 1, ""
        while not name or name in existing:
            name = f"Gesture {i}"; i += 1
        self.mappings.setdefault(app_name, {})[name] = {
            "label": name, "macro": ""}
        store.save(self.mappings)
        self._render_page(app_name)


# ── ENTRY POINT ────────────────────────────────────────────────────────────────
def main():
    if not store.load():
        store.save({"Global": {"Tap": {"label": "Tap", "macro": ""}}})
    root = tk.Tk()
    GesturePuckApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()