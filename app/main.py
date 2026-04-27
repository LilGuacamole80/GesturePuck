"""
Gesture Puck — desktop companion (BLE version)
No pairing needed — just flash, run, and connect.
"""

import platform
import threading
import tkinter as tk
from tkinter import ttk, messagebox

import mappings as store
import macro_runner
import bluetooth_spp

OS = platform.system()

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


def mk_btn(parent, text, command, bg=ACCENT, fg=BG, **kw):
    return tk.Button(parent, text=text, command=command,
                     bg=bg, fg=fg, activebackground=ACCENT_DIM,
                     activeforeground=fg, font=FONT_BTN,
                     relief="flat", cursor="hand2", padx=10, pady=5, **kw)


class GesturePuckApp:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title("Gesture Puck")
        self.root.configure(bg=BG)
        self.root.resizable(False, False)
        self.root.geometry("480x560")

        self.mappings     = store.load()
        self.connection   = None
        self.status_text  = tk.StringVar(value="NOT CONNECTED")
        self.last_gesture = tk.StringVar(value="—")
        self._macro_vars: dict[str, tk.StringVar] = {}

        self._build_ui()
        # Auto-scan on launch
        self._scan()

    def _build_ui(self):
        # title bar
        bar = tk.Frame(self.root, bg=BG, pady=16)
        bar.pack(fill="x", padx=20)
        tk.Label(bar, text="◉ GESTURE PUCK", font=FONT_TITLE,
                 bg=BG, fg=ACCENT).pack(side="left")
        self._dot = tk.Label(bar, text="●", font=("Courier New", 14),
                             bg=BG, fg=RED)
        self._dot.pack(side="right")

        # scan / connect panel
        conn = tk.Frame(self.root, bg=SURFACE, padx=16, pady=14)
        conn.pack(fill="x", padx=20)
        tk.Label(conn, text="NEARBY DEVICES", font=FONT_LABEL,
                 bg=SURFACE, fg=TEXT_DIM).pack(anchor="w")

        row = tk.Frame(conn, bg=SURFACE)
        row.pack(fill="x", pady=(4, 0))
        self.device_var = tk.StringVar()
        self.combo = ttk.Combobox(row, textvariable=self.device_var,
                                  font=FONT_MONO, width=30)
        self._style_combo()
        self.combo.pack(side="left", fill="x", expand=True)
        mk_btn(row, "↺", self._scan, bg=BORDER, fg=TEXT).pack(side="left", padx=(6, 0))
        mk_btn(conn, "CONNECT", self._connect).pack(fill="x", pady=(10, 0))

        # status
        tk.Label(self.root, textvariable=self.status_text, font=FONT_LABEL,
                 bg=BG, fg=TEXT_DIM).pack(anchor="w", padx=20, pady=6)

        # last gesture
        gf = tk.Frame(self.root, bg=SURFACE, padx=16, pady=14)
        gf.pack(fill="x", padx=20, pady=(0, 4))
        tk.Label(gf, text="LAST GESTURE", font=FONT_LABEL,
                 bg=SURFACE, fg=TEXT_DIM).pack(anchor="w")
        tk.Label(gf, textvariable=self.last_gesture,
                 font=("Courier New", 22, "bold"),
                 bg=SURFACE, fg=ACCENT).pack(anchor="w", pady=(4, 0))

        # mappings
        outer = tk.Frame(self.root, bg=BG)
        outer.pack(fill="both", expand=True, padx=20, pady=(4, 16))
        tk.Label(outer, text="GESTURE → MACRO MAPPINGS", font=FONT_LABEL,
                 bg=BG, fg=TEXT_DIM).pack(anchor="w", pady=(0, 6))
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

    # ── scanning ──────────────────────────────────────────────────────────────

    def _scan(self):
        self._set_status("SCANNING...", YELLOW)
        self.combo["values"] = []

        def do_scan():
            ports = bluetooth_spp.list_ports()
            self.root.after(0, lambda: self._on_scan_done(ports))

        threading.Thread(target=do_scan, daemon=True).start()

    def _on_scan_done(self, ports: list[str]):
        if ports:
            self.combo["values"] = ports
            self.device_var.set(ports[0])
            self._set_status(f"FOUND {len(ports)} DEVICE(S)", ACCENT)
        else:
            self.combo["values"] = ["(no devices found)"]
            self._set_status("NO DEVICES FOUND — IS ESP32 ON?", RED)

    # ── connection ────────────────────────────────────────────────────────────

    def _connect(self):
        address = self.device_var.get().strip()
        if not address or "no devices" in address:
            messagebox.showwarning("No Device", "Scan for devices first.")
            return

        if self.connection:
            self.connection.close()
            self.connection = None

        self._set_status(f"CONNECTING  {address}", YELLOW)

        def do_connect():
            try:
                conn = bluetooth_spp.connect(address, self._on_event_threadsafe)
                self.connection = conn
                self.root.after(0, lambda: self._set_status(f"CONNECTED  {address}", ACCENT))
            except Exception as e:
                self.root.after(0, lambda: messagebox.showerror("Connection Failed", str(e)))
                self.root.after(0, lambda: self._set_status("CONNECTION FAILED", RED))

        threading.Thread(target=do_connect, daemon=True).start()

    def _set_status(self, text: str, color: str):
        self.status_text.set(text)
        self._dot.config(fg=color)

    # ── event handling ────────────────────────────────────────────────────────

    def _on_event_threadsafe(self, line: str):
        """Called from BLE thread — marshal to UI thread."""
        self.root.after(0, lambda: self._on_event(line))

    def _on_event(self, line: str):
        if line == "PING":
            return
        if line.endswith("_DOWN"):
            gesture = line.replace("_DOWN", "")
            self.last_gesture.set(gesture)
            macro = self.mappings.get(gesture, "")
            if macro:
                try:
                    macro_runner.run_macro(macro)
                except Exception as e:
                    messagebox.showerror("Macro Error", str(e))
            else:
                self._prompt_map(gesture)

    # ── mapping rows ──────────────────────────────────────────────────────────

    def _rebuild_rows(self):
        for w in self.map_frame.winfo_children():
            w.destroy()
        self._macro_vars.clear()
        for gesture, macro in self.mappings.items():
            self._add_row(gesture, macro)

    def _add_row(self, gesture: str, macro: str):
        row = tk.Frame(self.map_frame, bg=SURFACE, padx=12, pady=10)
        row.pack(fill="x", pady=(0, 4))
        tk.Label(row, text=gesture, font=("Courier New", 11, "bold"),
                 bg=SURFACE, fg=ACCENT, width=8, anchor="w").pack(side="left")
        tk.Label(row, text="→", font=FONT_MONO,
                 bg=SURFACE, fg=TEXT_DIM).pack(side="left", padx=8)
        var = tk.StringVar(value=macro)
        self._macro_vars[gesture] = var
        tk.Entry(row, textvariable=var, font=FONT_MONO,
                 bg=BG, fg=TEXT, insertbackground=ACCENT,
                 relief="flat", bd=0, width=18).pack(side="left", fill="x", expand=True)
        mk_btn(row, "SAVE",
               lambda g=gesture, v=var: self._save(g, v.get())).pack(side="left", padx=(8, 0))
        mk_btn(row, "✕",
               lambda g=gesture: self._delete(g),
               bg=BORDER, fg=RED).pack(side="left", padx=(4, 0))

    def _save(self, gesture: str, macro: str):
        self.mappings[gesture] = macro
        store.save(self.mappings)
        old = self.status_text.get()
        self.status_text.set(f"✓  saved  {gesture}")
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
        win.geometry("320x160")
        win.grab_set()
        tk.Label(win, text="GESTURE ID  (e.g. BTN2)", font=FONT_LABEL,
                 bg=BG, fg=TEXT_DIM).pack(anchor="w", padx=20, pady=(18, 4))
        g_var = tk.StringVar()
        tk.Entry(win, textvariable=g_var, font=FONT_MONO, bg=SURFACE,
                 fg=TEXT, insertbackground=ACCENT, relief="flat", bd=0).pack(fill="x", padx=20)
        tk.Label(win, text="MACRO  (e.g. ctrl+shift+s)", font=FONT_LABEL,
                 bg=BG, fg=TEXT_DIM).pack(anchor="w", padx=20, pady=(12, 4))
        m_var = tk.StringVar()
        tk.Entry(win, textvariable=m_var, font=FONT_MONO, bg=SURFACE,
                 fg=TEXT, insertbackground=ACCENT, relief="flat", bd=0).pack(fill="x", padx=20)

        def confirm():
            g, m = g_var.get().strip(), m_var.get().strip()
            if not g or not m:
                messagebox.showwarning("Empty", "Fill in both fields.", parent=win)
                return
            self.mappings[g] = m
            store.save(self.mappings)
            self._rebuild_rows()
            win.destroy()

        mk_btn(win, "ADD", confirm).pack(pady=14)

    def _prompt_map(self, gesture: str):
        if not messagebox.askyesno("New Gesture",
                                   f"'{gesture}' has no macro.\nMap it now?"):
            return
        win = tk.Toplevel(self.root)
        win.title(f"Map {gesture}")
        win.configure(bg=BG)
        win.geometry("320x120")
        win.grab_set()
        tk.Label(win, text=f"MACRO for {gesture}", font=FONT_LABEL,
                 bg=BG, fg=TEXT_DIM).pack(anchor="w", padx=20, pady=(18, 4))
        m_var = tk.StringVar()
        tk.Entry(win, textvariable=m_var, font=FONT_MONO, bg=SURFACE,
                 fg=TEXT, insertbackground=ACCENT, relief="flat", bd=0).pack(fill="x", padx=20)

        def confirm():
            m = m_var.get().strip()
            if not m:
                return
            self._save(gesture, m)
            self._rebuild_rows()
            win.destroy()

        mk_btn(win, "SAVE", confirm).pack(pady=12)


def main():
    root = tk.Tk()
    GesturePuckApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()