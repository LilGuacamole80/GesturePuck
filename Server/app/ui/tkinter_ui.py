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
        print("LOADED MAPPINGS:")
        print(self.mappings)
        self.devices = []
        self.connection = None
        self.recorder = None

        self._macro_vars = {}
        self._label_vars = {}
        self._active_recorder = None

        # UI state
        self.status = tk.StringVar(value="NOT CONNECTED")
        self.last_gesture = tk.StringVar(value="—")
        self.device_var = tk.StringVar()
        #page container
        self.page_container = tk.Frame(self.root, bg=BG)
        self.page_container.pack(fill="both", expand=True)

        self._create_pages()

        self._build_ui()
        self._show_page("Global")
        self._scan_devices()
        
        

    # ── UI ────────────────────────────────────────────────────────────────────
    def _build_ui(self):
        top = tk.Frame(self.root, bg=BG)
        top.pack(fill="x", padx=20, pady=10)

        tk.Label(top, text="GESTURE PUCK", bg=BG, fg=ACCENT,
                 font=FONT_TITLE).pack(side="left")

        tk.Label(top, textvariable=self.status,
                 bg=BG, fg=TEXT_DIM).pack(side="right")

        frame = tk.Frame(self.root, bg=SURFACE)
        frame.pack(fill="x", padx=20, pady=10)

        self.combo = ttk.Combobox(frame, textvariable=self.device_var, state="readonly")
        self.combo.pack(side="left", fill="x", expand=True)

        mk_btn(frame, "SCAN", self._scan_devices, bg=BORDER, fg=TEXT).pack(side="left")
        mk_btn(frame, "CONNECT", self._connect, bg=ACCENT).pack(side="left")

        tk.Label(self.root, text="LAST GESTURE", bg=BG, fg=TEXT_DIM).pack()
        tk.Label(self.root, textvariable=self.last_gesture,
                 bg=BG, fg=ACCENT, font=("Courier", 18)).pack()
        mk_btn(self.root, "Figma", lambda: self._show_page("Figma"), bg=ACCENT).pack()
        mk_btn(self.root, "Adobe Photoshop", lambda: self._show_page("Adobe Photoshop"), bg=ACCENT).pack()
        mk_btn(self.root, "Blender", lambda: self._show_page("Blender"), bg=ACCENT).pack()
        mk_btn(self.root, "Visual Studio Code", lambda: self._show_page("Visual Studio Code"), bg=ACCENT).pack()
        mk_btn(self.root, "Google Slides", lambda: self._show_page("Google Slides"), bg=ACCENT).pack()
        mk_btn(self.root, "Notion", lambda: self._show_page("Notion"), bg=ACCENT).pack()
        mk_btn(self.root, "Slack", lambda: self._show_page("Slack"), bg=ACCENT).pack()
        mk_btn(self.root, "OBS Studio", lambda: self._show_page("OBS Studio"), bg=ACCENT).pack()

        

    def _create_pages(self):
        self.pages = {}

        apps = [
            "Global",
            "Figma",
            "Adobe Photoshop",
            "Blender",
            "Visual Studio Code",
            "Google Slides",
            "Notion",
            "Slack",
            "OBS Studio"
        ]

        for app in apps:
            frame = tk.Frame(self.page_container, bg=BG)

            label = tk.Label(
                frame,
                text=f"{app} Gestures",
                bg=BG,
                fg=ACCENT,
                font=FONT_TITLE
            )
            label.pack(pady=20)

            self.pages[app] = frame

    def _render_page(self, app_name):
        frame = self.pages[app_name]

    # clear old content
        for w in frame.winfo_children():
            w.destroy()

        tk.Label(
            frame,
            text=f"{app_name} Gestures",
            bg=BG,
            fg=ACCENT,
            font=FONT_TITLE
        ).pack(pady=20)

        app_data = self.mappings.get(app_name, {})

        for gesture, data in app_data.items():
            row = tk.Frame(frame, bg=SURFACE)
            row.pack(fill="x", pady=2, padx=20)

            tk.Label(
                row,
                text=gesture,
                bg=SURFACE,
                fg=ACCENT,
                width=10
            ).pack(side="left")

            tk.Label(
                row,
                text=data.get("label", ""),
                bg=SURFACE,
                fg=TEXT,
                width=20
            ).pack(side="left")

            tk.Label(
                row,
                text=data.get("macro", ""),
                bg=SURFACE,
                fg=YELLOW,
                width=20
            ).pack(side="left")

            print("CURRENT PAGE:", app_name)
            print("APP DATA:", app_data)

    def _show_page(self, app_name):
        self.current_page = app_name

        for page in self.pages.values():
            page.pack_forget()

        self.pages[app_name].pack(fill="both", expand=True)

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
            self.last_gesture.set(gesture)

            macro = (
                self.mappings
                .get(self.current_page, {})
                .get(gesture, {})
                .get("macro")
            )
            if macro:
                macro_runner.run_macro(macro)
            else:
                self._ask_map(gesture)

    # ── MAPPINGS UI ───────────────────────────────────────────────────────────
    def _render_mappings(self):
        for w in self.map_frame.winfo_children():
            w.destroy()

        self._macro_vars.clear()
        self._label_vars.clear()

        for gesture, data in self.mappings.items():
            self._add_row(
                gesture,
                data.get("label", gesture),
                data.get("macro", "")
            )

    def _add_row(self, gesture, label, macro):
        row = tk.Frame(self.map_frame, bg=SURFACE)
        row.pack(fill="x", pady=2)

        tk.Label(row, text=gesture, bg=SURFACE, fg=ACCENT, width=8)\
            .pack(side="left")

        lvar = tk.StringVar(value=label)
        self._label_vars[gesture] = lvar
        tk.Entry(row, textvariable=lvar, bg=BG, fg=TEXT,
                 insertbackground=ACCENT, relief="flat", width=12)\
            .pack(side="left", padx=5)

        mvar = tk.StringVar(value=macro)
        self._macro_vars[gesture] = mvar
        entry = tk.Entry(row, textvariable=mvar, bg=BG, fg=ACCENT,
                         insertbackground=ACCENT, relief="flat", width=16)
        entry.pack(side="left", padx=5)

        mk_btn(row, "REC",
               lambda g=gesture, mv=mvar, e=entry: self._start_record(g, mv, e),
               bg=BORDER, fg=YELLOW).pack(side="left", padx=2)

        mk_btn(row, "SAVE",
               lambda g=gesture, mv=mvar, lv=lvar:
               self._save(g, mv.get(), lv.get())
               ).pack(side="left", padx=2)

        mk_btn(row, "X",
               lambda g=gesture: self._delete(g),
               bg=BORDER, fg=RED).pack(side="left", padx=2)

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
        entry.config(fg=ACCENT)
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
        if messagebox.askyesno("Delete", f"Delete {gesture}?"):
            self.mappings.pop(gesture, None)
            store.save(self.mappings)
            self._render_mappings()

    def _ask_map(self, gesture):
        if not messagebox.askyesno("New Gesture",
                                  f"{gesture} has no mapping. Add one?"):
            return

        self.mappings[gesture] = {"label": gesture, "macro": ""}
        store.save(self.mappings)
        self._render_mappings()



# ── ENTRY ─────────────────────────────────────────────────────────────────────
def main():
    root = tk.Tk()
    app = GesturePuckApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()