import threading
import tkinter as tk
from tkinter import ttk, messagebox
from pynput import keyboard as pynput_kb

import engine.mappings as store
import engine.macro_runner as macro_runner
import engine.bluetooth_spp as bluetooth_spp


# ── THEME ─────────────────────────────────────────────────────────────────────
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


# ── KEY HELPERS ───────────────────────────────────────────────────────────────
PYNPUT_KEY_NAMES = {
    pynput_kb.Key.ctrl: "ctrl",
    pynput_kb.Key.ctrl_l: "ctrl",
    pynput_kb.Key.ctrl_r: "ctrl",
    pynput_kb.Key.shift: "shift",
    pynput_kb.Key.shift_l: "shift",
    pynput_kb.Key.shift_r: "shift",
    pynput_kb.Key.alt: "alt",
    pynput_kb.Key.alt_l: "alt",
    pynput_kb.Key.alt_r: "alt",
    pynput_kb.Key.cmd: "win",
    pynput_kb.Key.enter: "enter",
    pynput_kb.Key.space: "space",
    pynput_kb.Key.tab: "tab",
    pynput_kb.Key.esc: "esc",
}

MODIFIER_KEYS = {
    pynput_kb.Key.ctrl, pynput_kb.Key.ctrl_l, pynput_kb.Key.ctrl_r,
    pynput_kb.Key.shift, pynput_kb.Key.shift_l, pynput_kb.Key.shift_r,
    pynput_kb.Key.alt, pynput_kb.Key.alt_l, pynput_kb.Key.alt_r,
    pynput_kb.Key.cmd,
}


def mk_btn(parent, text, command, bg=ACCENT, fg=BG):
    return tk.Button(
        parent,
        text=text,
        command=command,
        bg=bg,
        fg=fg,
        font=FONT_BTN,
        relief="flat",
        padx=8,
        pady=4,
        cursor="hand2"
    )


# ── KEY RECORDER ──────────────────────────────────────────────────────────────
class KeyRecorder:
    def __init__(self, on_done):
        self.on_done = on_done
        self._held = set()
        self._chord = []
        self._listener = None
        self._done = False

    def start(self):
        self._held = set()
        self._chord = []
        self._done = False

        self._listener = pynput_kb.Listener(
            on_press=self._on_press,
            on_release=self._on_release,
            suppress=True
        )
        self._listener.start()

    def stop(self):
        if self._listener:
            self._listener.stop()

    def _key_name(self, key):
        return PYNPUT_KEY_NAMES.get(key, getattr(key, "char", str(key)))

    def _on_press(self, key):
        if self._done:
            return
        name = self._key_name(key)
        if name and name not in self._held:
            self._held.add(name)
            self._chord.append(name)

    def _on_release(self, key):
        if self._done:
            return
        if key not in MODIFIER_KEYS:
            self._done = True
            self._listener.stop()
            result = "+".join(self._chord)
            self.on_done(result)


# ── MAIN APP ──────────────────────────────────────────────────────────────────
class GesturePuckApp:
    def __init__(self, root):
        self.root = root
        self.root.title("Gesture Puck")
        screen_width = self.root.winfo_screenwidth()
        screen_height = self.root.winfo_screenheight()
        self.root.geometry(f"{screen_width}x{screen_height}")
        self.root.configure(bg=BG)

        # state
        self.mappings = store.load()
        self.devices = []
        self.connection = None
        self.current_page = "Global"

        self._macro_vars = {}
        self._label_vars = {}
        self._active_recorder = None

        # UI state
        self.status = tk.StringVar(value="NOT CONNECTED")
        self.last_gesture = tk.StringVar(value="—")
        self.device_var = tk.StringVar()

        self._build_ui()
        self._show_page("Global")
        self._scan_devices()

    # ── UI ────────────────────────────────────────────────────────────────────
    def _build_ui(self):
        # ── Top bar: title + status ──
        top = tk.Frame(self.root, bg=BG)
        top.pack(fill="x", padx=20, pady=(10, 0))

        tk.Label(top, text="GESTURE PUCK", bg=BG, fg=ACCENT,
                 font=FONT_TITLE).pack(side="left")
        tk.Label(top, textvariable=self.status,
                 bg=BG, fg=TEXT_DIM, font=FONT_LABEL).pack(side="right")

        # ── Device row ──
        dev_frame = tk.Frame(self.root, bg=SURFACE)
        dev_frame.pack(fill="x", padx=20, pady=6)

        self.combo = ttk.Combobox(dev_frame, textvariable=self.device_var, state="readonly")
        self.combo.pack(side="left", fill="x", expand=True, padx=(4, 4))
        mk_btn(dev_frame, "SCAN", self._scan_devices, bg=BORDER, fg=TEXT).pack(side="left", padx=2)
        mk_btn(dev_frame, "CONNECT", self._connect, bg=ACCENT).pack(side="left", padx=2)

        # ── Last gesture ──
        gesture_frame = tk.Frame(self.root, bg=BG)
        gesture_frame.pack(fill="x", padx=20, pady=(4, 8))
        tk.Label(gesture_frame, text="LAST GESTURE", bg=BG, fg=TEXT_DIM,
                 font=FONT_LABEL).pack(side="left")
        tk.Label(gesture_frame, textvariable=self.last_gesture,
                 bg=BG, fg=ACCENT, font=("Courier New", 14, "bold")).pack(side="left", padx=10)

        # ── Main body: sidebar + content ──
        body = tk.Frame(self.root, bg=BG)
        body.pack(fill="both", expand=True, padx=0, pady=0)

        # Sidebar
        self.sidebar = tk.Frame(body, bg=SURFACE, width=180)
        self.sidebar.pack(side="left", fill="y", padx=(10, 0), pady=0)
        self.sidebar.pack_propagate(False)

        apps = [
            "Global", "Figma", "Adobe Photoshop", "Blender",
            "Visual Studio Code", "Google Slides", "Notion", "Slack", "OBS Studio"
        ]

        self._nav_buttons = {}
        for app in apps:
            btn = tk.Button(
                self.sidebar,
                text=app,
                command=lambda a=app: self._show_page(a),
                bg=SURFACE,
                fg=TEXT,
                font=FONT_BTN,
                relief="flat",
                anchor="w",
                padx=12,
                pady=6,
                cursor="hand2",
                bd=0,
                highlightthickness=0,
            )
            btn.pack(fill="x", pady=1)
            self._nav_buttons[app] = btn

        # Content area with scrollable canvas
        content_outer = tk.Frame(body, bg=BG)
        content_outer.pack(side="left", fill="both", expand=True, padx=10, pady=0)

        self.canvas = tk.Canvas(content_outer, bg=BG, highlightthickness=0)
        scrollbar = ttk.Scrollbar(content_outer, orient="vertical", command=self.canvas.yview)
        self.canvas.configure(yscrollcommand=scrollbar.set)

        scrollbar.pack(side="right", fill="y")
        self.canvas.pack(side="left", fill="both", expand=True)

        self.content_frame = tk.Frame(self.canvas, bg=BG)
        self._canvas_window = self.canvas.create_window((0, 0), window=self.content_frame, anchor="nw")

        self.content_frame.bind("<Configure>", self._on_frame_configure)
        self.canvas.bind("<Configure>", self._on_canvas_configure)

    def _on_frame_configure(self, event):
        self.canvas.configure(scrollregion=self.canvas.bbox("all"))

    def _on_canvas_configure(self, event):
        self.canvas.itemconfig(self._canvas_window, width=event.width)

    # ── PAGE RENDERING ────────────────────────────────────────────────────────
    def _show_page(self, app_name):
        self.current_page = app_name

        # Highlight active nav button
        for name, btn in self._nav_buttons.items():
            if name == app_name:
                btn.config(bg=ACCENT_DIM, fg=BG)
            else:
                btn.config(bg=SURFACE, fg=TEXT)

        self._render_page(app_name)

    def _render_page(self, app_name):
        # Clear content
        for w in self.content_frame.winfo_children():
            w.destroy()

        self._macro_vars.clear()
        self._label_vars.clear()

        # Page title
        tk.Label(
            self.content_frame,
            text=f"{app_name}  —  Gesture Mappings",
            bg=BG, fg=ACCENT, font=FONT_TITLE
        ).pack(anchor="w", padx=10, pady=(16, 8))

        # Column headers
        header = tk.Frame(self.content_frame, bg=BG)
        header.pack(fill="x", padx=10, pady=(0, 4))
        for text, width in [("GESTURE", 14), ("LABEL", 20), ("MACRO", 20), ("ACTIONS", 22)]:
            tk.Label(header, text=text, bg=BG, fg=TEXT_DIM,
                     font=FONT_LABEL, width=width, anchor="w").pack(side="left")

        tk.Frame(self.content_frame, bg=BORDER, height=1).pack(fill="x", padx=10, pady=2)

        # Rows
        app_data = self.mappings.get(app_name, {})
        for gesture, data in app_data.items():
            self._add_row(gesture, data.get("label", gesture), data.get("macro", ""))

        # Add new gesture button
        tk.Frame(self.content_frame, bg=BORDER, height=1).pack(fill="x", padx=10, pady=8)
        mk_btn(
            self.content_frame, "+ ADD GESTURE",
            lambda: self._add_new_gesture(app_name),
            bg=BORDER, fg=ACCENT
        ).pack(anchor="w", padx=10, pady=4)

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

        mk_btn(row, "REC",
               lambda g=gesture, mv=mvar, e=entry: self._start_record(g, mv, e),
               bg=BORDER, fg=YELLOW).pack(side="left", padx=2)

        mk_btn(row, "SAVE",
               lambda g=gesture, mv=mvar, lv=lvar: self._save(g, mv.get(), lv.get())
               ).pack(side="left", padx=2)

        mk_btn(row, "✕",
               lambda g=gesture: self._delete(g),
               bg=BORDER, fg=RED).pack(side="left", padx=(2, 8))

    def _add_new_gesture(self, app_name):
        # Find next available gesture name
        existing = self.mappings.get(app_name, {})
        i = len(existing) + 1
        name = f"Gesture {i}"
        while name in existing:
            i += 1
            name = f"Gesture {i}"

        if app_name not in self.mappings:
            self.mappings[app_name] = {}
        self.mappings[app_name][name] = {"label": name, "macro": ""}
        store.save(self.mappings)
        self._render_page(app_name)

    # ── DEVICE LOGIC ──────────────────────────────────────────────────────────
    def _scan_devices(self):
        self.status.set("SCANNING...")

        def task():
            devices = bluetooth_spp.list_devices()
            self.root.after(0, lambda: self._update_devices(devices))

        threading.Thread(target=task, daemon=True).start()

    def _update_devices(self, devices):
        self.devices = devices
        names = [n for _, n in devices]
        self.combo["values"] = names

        if names:
            self.combo.current(0)
            self.status.set("DEVICES FOUND")
        else:
            self.status.set("NO DEVICES")

    def _connect(self):
        name = self.device_var.get()
        addr = next((a for a, n in self.devices if n == name), None)

        if not addr:
            messagebox.showerror("Error", "Select valid device")
            return

        def task():
            self.connection = bluetooth_spp.connect(addr, self._on_event)
            self.root.after(0, lambda: self.status.set("CONNECTED"))

        threading.Thread(target=task, daemon=True).start()

    # ── EVENTS ────────────────────────────────────────────────────────────────
    def _on_event(self, msg):
        if msg.endswith("_DOWN"):
            gesture = msg.replace("_DOWN", "")
            self.root.after(0, lambda: self.last_gesture.set(gesture))

            macro = (
                self.mappings
                .get(self.current_page, {})
                .get(gesture, {})
                .get("macro")
            )
            if macro:
                macro_runner.run_macro(macro)
            else:
                self.root.after(0, lambda: self._ask_map(gesture))

    # ── RECORDING ─────────────────────────────────────────────────────────────
    def _start_record(self, gesture, mvar, entry):
        if self._active_recorder:
            self._active_recorder.stop()

        mvar.set("recording...")
        entry.config(fg=YELLOW)

        def done(chord):
            self.root.after(0, lambda: self._finish_record(chord, mvar, entry))

        self._active_recorder = KeyRecorder(done)
        self._active_recorder.start()

    def _finish_record(self, chord, mvar, entry):
        mvar.set(chord)
        entry.config(fg=YELLOW)
        self._active_recorder = None

    # ── DATA ACTIONS ──────────────────────────────────────────────────────────
    def _save(self, gesture, macro, label):
        if self.current_page not in self.mappings:
            self.mappings[self.current_page] = {}

        self.mappings[self.current_page][gesture] = {
            "label": label,
            "macro": macro
        }
        store.save(self.mappings)

    def _delete(self, gesture):
        if messagebox.askyesno("Delete", f"Delete '{gesture}'?"):
            if self.current_page in self.mappings:
                self.mappings[self.current_page].pop(gesture, None)
            store.save(self.mappings)
            self._render_page(self.current_page)

    def _ask_map(self, gesture):
        if not messagebox.askyesno("New Gesture",
                                   f"'{gesture}' has no mapping. Add one?"):
            return

        if self.current_page not in self.mappings:
            self.mappings[self.current_page] = {}
        self.mappings[self.current_page][gesture] = {"label": gesture, "macro": ""}
        store.save(self.mappings)
        self._render_page(self.current_page)


# ── ENTRY ─────────────────────────────────────────────────────────────────────
def main():
    root = tk.Tk()
    app = GesturePuckApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()