import queue
import threading
import tkinter as tk
from tkinter import ttk, messagebox
from pynput import keyboard as pynput_kb
from engine.active_app import get_mapped_app

try:
    import engine.mappings as store
    import engine.macro_runner as macro_runner
    import engine.bluetooth_spp as bluetooth_spp
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
FONT_LABEL = ("Courier New", 9)
FONT_TITLE = ("Courier New", 13, "bold")
FONT_BTN   = ("Courier New", 9, "bold")


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
for _k in ("cmd_l", "cmd_r"):
    _v = getattr(pynput_kb.Key, _k, None)
    if _v:
        MODIFIER_KEYS.add(_v)


def mk_btn(parent, text, command, bg=ACCENT, fg=BG):
    return tk.Button(
        parent, text=text, command=command,
        bg=bg, fg=fg, font=FONT_BTN,
        relief="flat", padx=8, pady=4, cursor="hand2"
    )


# A single long-lived listener, started once at app boot. The Tk UI "arms" the
# recorder for the next chord; the listener thread pushes the captured chord
# onto a queue, and Tk polls the queue from the main loop. This avoids
# repeatedly creating/destroying CGEventTaps, which is what was causing the
# SIGTRAP on macOS.
class GlobalKeyRecorder:
    def __init__(self):
        self._armed = False
        self._lock = threading.Lock()
        self._chord: list[str] = []
        self._held: set = set()
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
            self._held = set()
            self._token = token

    def cancel(self):
        with self._lock:
            self._armed = False
            self._chord = []
            self._held = set()

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
            self._held = set()
        self.results.put((token, chord))


class GesturePuckApp:
    def __init__(self, root):
        self.root = root
        self.root.title("Gesture Puck")
        sw, sh = self.root.winfo_screenwidth(), self.root.winfo_screenheight()
        self.root.geometry(f"{sw}x{sh}")
        self.root.configure(bg=BG)

        self.mappings     = store.load()
        self.devices      = []
        self.connection   = None
        self.current_page = "Global"

        self._macro_vars: dict[str, tk.StringVar]  = {}
        self._label_vars: dict[str, tk.StringVar]  = {}
        self._entries:    dict[str, tk.Entry]      = {}
        self._next_token  = 1
        self._pending: dict[int, str] = {}

        self.recorder = GlobalKeyRecorder()

        self.status       = tk.StringVar(value="NOT CONNECTED")
        self.last_gesture = tk.StringVar(value="—")
        self.device_var   = tk.StringVar()

        self._build_ui()
        self._show_page("Global")
        self._scan_devices()
        self.root.after(50, self._poll_recorder)

    def _build_ui(self):
        top = tk.Frame(self.root, bg=BG)
        top.pack(fill="x", padx=20, pady=(10, 0))
        tk.Label(top, text="GESTURE PUCK", bg=BG, fg=ACCENT,
                 font=FONT_TITLE).pack(side="left")
        tk.Label(top, textvariable=self.status, bg=BG, fg=TEXT_DIM,
                 font=FONT_LABEL).pack(side="right")

        dev = tk.Frame(self.root, bg=SURFACE)
        dev.pack(fill="x", padx=20, pady=6)
        self.combo = ttk.Combobox(dev, textvariable=self.device_var, state="readonly")
        self.combo.pack(side="left", fill="x", expand=True, padx=(4, 4))
        mk_btn(dev, "SCAN",    self._scan_devices, bg=BORDER, fg=TEXT).pack(side="left", padx=2)
        mk_btn(dev, "CONNECT", self._connect).pack(side="left", padx=2)

        gf = tk.Frame(self.root, bg=BG)
        gf.pack(fill="x", padx=20, pady=(4, 8))
        tk.Label(gf, text="LAST GESTURE", bg=BG, fg=TEXT_DIM,
                 font=FONT_LABEL).pack(side="left")
        tk.Label(gf, textvariable=self.last_gesture, bg=BG, fg=ACCENT,
                 font=("Courier New", 14, "bold")).pack(side="left", padx=10)

        body = tk.Frame(self.root, bg=BG)
        body.pack(fill="both", expand=True)

        sidebar = tk.Frame(body, bg=SURFACE, width=180)
        sidebar.pack(side="left", fill="y", padx=(10, 0))
        sidebar.pack_propagate(False)

        self._nav_btns: dict[str, tk.Button] = {}
        for app in [
            "Global", "Figma", "Adobe Photoshop", "Blender",
            "Visual Studio Code", "Google Slides", "Notion", "Slack", "OBS Studio"
        ]:
            b = tk.Button(
                sidebar, text=app, command=lambda a=app: self._show_page(a),
                bg=SURFACE, fg=TEXT, font=FONT_BTN, relief="flat",
                anchor="w", padx=12, pady=6, cursor="hand2",
                bd=0, highlightthickness=0,
            )
            b.pack(fill="x", pady=1)
            self._nav_btns[app] = b

        content_outer = tk.Frame(body, bg=BG)
        content_outer.pack(side="left", fill="both", expand=True, padx=10)

        self.canvas = tk.Canvas(content_outer, bg=BG, highlightthickness=0)
        sb = ttk.Scrollbar(content_outer, orient="vertical", command=self.canvas.yview)
        self.canvas.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        self.canvas.pack(side="left", fill="both", expand=True)

        self.content_frame = tk.Frame(self.canvas, bg=BG)
        self._cwin = self.canvas.create_window((0, 0), window=self.content_frame, anchor="nw")

        self.content_frame.bind("<Configure>",
            lambda e: self.canvas.configure(scrollregion=self.canvas.bbox("all")))
        self.canvas.bind("<Configure>",
            lambda e: self.canvas.itemconfig(self._cwin, width=e.width))

    def _show_page(self, app_name):
        self.current_page = app_name
        for name, btn in self._nav_btns.items():
            btn.config(
                bg=ACCENT_DIM if name == app_name else SURFACE,
                fg=BG         if name == app_name else TEXT)
        self._render_page(app_name)

    def _render_page(self, app_name):
        for w in self.content_frame.winfo_children():
            w.destroy()
        self._macro_vars.clear()
        self._label_vars.clear()
        self._entries.clear()

        tk.Label(self.content_frame, text=f"{app_name}  —  Gesture Mappings",
                 bg=BG, fg=ACCENT, font=FONT_TITLE).pack(anchor="w", padx=10,
                 pady=(16, 8))

        hdr = tk.Frame(self.content_frame, bg=BG)
        hdr.pack(fill="x", padx=10, pady=(0, 4))
        for text, w in [("GESTURE", 14), ("LABEL", 20), ("MACRO", 20), ("ACTIONS", 22)]:
            tk.Label(hdr, text=text, bg=BG, fg=TEXT_DIM, font=FONT_LABEL,
                     width=w, anchor="w").pack(side="left")

        tk.Frame(self.content_frame, bg=BORDER, height=1).pack(fill="x", padx=10, pady=2)

        for gesture, data in self.mappings.get(app_name, {}).items():
            self._add_row(gesture, data.get("label", gesture), data.get("macro", ""))

        tk.Frame(self.content_frame, bg=BORDER, height=1).pack(fill="x", padx=10, pady=8)
        mk_btn(self.content_frame, "+ ADD GESTURE",
               lambda: self._add_new_gesture(app_name),
               bg=BORDER, fg=ACCENT).pack(anchor="w", padx=10, pady=4)

    def _add_row(self, gesture, label, macro):
        row = tk.Frame(self.content_frame, bg=SURFACE)
        row.pack(fill="x", padx=10, pady=2)

        tk.Label(row, text=gesture, bg=SURFACE, fg=ACCENT,
                 font=FONT_MONO, width=14, anchor="w").pack(side="left", padx=(8, 4))

        lvar = tk.StringVar(value=label)
        self._label_vars[gesture] = lvar
        tk.Entry(row, textvariable=lvar, bg=BG, fg=TEXT,
                 insertbackground=ACCENT, relief="flat", width=20,
                 font=FONT_MONO).pack(side="left", padx=4)

        mvar = tk.StringVar(value=macro)
        self._macro_vars[gesture] = mvar
        entry = tk.Entry(row, textvariable=mvar, bg=BG, fg=YELLOW,
                         insertbackground=ACCENT, relief="flat", width=20,
                         font=FONT_MONO)
        entry.pack(side="left", padx=4)
        self._entries[gesture] = entry

        mk_btn(row, "REC",
               lambda g=gesture: self._start_record(g),
               bg=BORDER, fg=YELLOW).pack(side="left", padx=2)
        mk_btn(row, "SAVE",
               lambda g=gesture, mv=mvar, lv=lvar: self._save(g, mv.get(), lv.get()),
               ).pack(side="left", padx=2)
        mk_btn(row, "✕",
               lambda g=gesture: self._delete(g),
               bg=BORDER, fg=RED).pack(side="left", padx=(2, 8))

    def _add_new_gesture(self, app_name):
        existing = self.mappings.get(app_name, {})
        i, name = len(existing) + 1, ""
        while not name or name in existing:
            name = f"Gesture {i}"; i += 1
        self.mappings.setdefault(app_name, {})[name] = {"label": name, "macro": ""}
        store.save(self.mappings)
        self._render_page(app_name)

    def _scan_devices(self):
        self.status.set("SCANNING...")
        def task():
            devs = bluetooth_spp.list_devices()
            self.root.after(0, lambda: self._update_devices(devs))
        threading.Thread(target=task, daemon=True).start()

    def _update_devices(self, devices):
        self.devices = devices
        names = [n for _, n in devices]
        self.combo["values"] = names
        if names:
            self.combo.current(0); self.status.set("DEVICES FOUND")
        else:
            self.status.set("NO DEVICES")

    def _connect(self):
        name = self.device_var.get()
        addr = next((a for a, n in self.devices if n == name), None)
        if not addr:
            messagebox.showerror("Error", "Select a valid device"); return
        def task():
            self.connection = bluetooth_spp.connect(addr, self._on_event)
            self.root.after(0, lambda: self.status.set("CONNECTED"))
        threading.Thread(target=task, daemon=True).start()

    from engine.active_app import get_mapped_app  # add to imports at top

    def _on_event(self, msg):
        print(f"[DEBUG] received: {repr(msg)}")  # remove once working
    
        if "_DOWN" in msg:
            gesture = msg.replace("_DOWN", "").strip()
            self.root.after(0, lambda: self.last_gesture.set(gesture))

            active_app = get_mapped_app()
            print(f"[DEBUG] active app: {active_app}, gesture: {gesture}")

        # Try app-specific first, fall back to Global
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


    def _start_record(self, gesture):
        # If another row is currently armed, repaint its entry back to normal.
        for tok, prev_g in list(self._pending.items()):
            prev_entry = self._entries.get(prev_g)
            prev_mvar  = self._macro_vars.get(prev_g)
            if prev_entry and prev_mvar:
                prev_entry.config(fg=YELLOW)
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
        entry.config(fg=RED)

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
                entry.config(fg=YELLOW)
        except queue.Empty:
            pass
        self.root.after(50, self._poll_recorder)

    def _save(self, gesture, macro, label):
        self.mappings.setdefault(self.current_page, {})[gesture] = {
            "label": label, "macro": macro}
        store.save(self.mappings)

    def _delete(self, gesture):
        if messagebox.askyesno("Delete", f"Delete '{gesture}'?"):
            self.mappings.get(self.current_page, {}).pop(gesture, None)
            store.save(self.mappings)
            self._render_page(self.current_page)

    def _ask_map(self, gesture):
        if not messagebox.askyesno("New Gesture",
                                   f"'{gesture}' has no mapping. Add one?"):
            return
        self.mappings.setdefault(self.current_page, {})[gesture] = {
            "label": gesture, "macro": ""}
        store.save(self.mappings)
        self._render_page(self.current_page)


def main():
    # Seed a default page so a fresh run shows something to test against.
    if not store.load():
        store.save({"Global": {"Tap": {"label": "Tap", "macro": ""}}})
    root = tk.Tk()
    GesturePuckApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()