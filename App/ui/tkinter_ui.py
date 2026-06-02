import glob
import queue
import subprocess
import sys
import threading
import time
import traceback
import tkinter as tk
from pathlib import Path
from tkinter import ttk, messagebox

import numpy as np
from pynput import keyboard as pynput_kb

from engine.active_app import get_mapped_app
from engine.gesture_engine import GestureEngine
from lidar_gesture_studio import (
    DualFramePacket,
    DualSensorSerialFrameSource,
    FusedSignalPipeline,
    SerialDebugLogger,
    StrokeGestureDetector,
    build_arg_parser,
    configure_runtime_args,
    default_sensor_calibration_path,
    load_sensor_calibration,
    parse_frame_line,
    resolve_serial_debug_path,
)
from calibrate_sensors import (
    TARGETS as SENSOR_CALIBRATION_TARGETS,
    fit_sensor_affines,
    read_visible_sensor_samples,
    save_calibration,
    summarize_sensor_samples,
)
from engine.packs import ModeManager
from ui.pack_panel import render_pack_page, render_pack_sidebar_section
from engine import pack_store


APP_DIR = Path(__file__).resolve().parents[1]


def resolve_ui_log_path(value):
    if value in (None, "", "auto"):
        stamp = time.strftime("%Y%m%d_%H%M%S")
        return APP_DIR / "logs" / f"tkinter_ui_{stamp}.log"
    if str(value).lower() in {"off", "none", "false", "0"}:
        return None
    path = Path(value).expanduser()
    if path.suffix:
        return path
    stamp = time.strftime("%Y%m%d_%H%M%S")
    return path / f"tkinter_ui_{stamp}.log"


class UiLogger:
    def __init__(self, path=None, *, mirror_stderr=True):
        self.path = Path(path).expanduser() if path else None
        self.mirror_stderr = mirror_stderr
        self._lock = threading.Lock()
        self._fh = None
        if self.path is not None:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self._fh = self.path.open("a", encoding="utf-8")
        if self.path is not None:
            self.log("log_open", str(self.path))

    def _timestamp(self):
        now = time.time()
        base = time.strftime("%H:%M:%S", time.localtime(now))
        return f"{base}.{int((now % 1.0) * 1000):03d}"

    def log(self, event, message=""):
        line = f"[{self._timestamp()}] {event}: {message}"
        with self._lock:
            if self.mirror_stderr:
                print(line, file=sys.stderr, flush=True)
            if self._fh is not None:
                self._fh.write(line + "\n")
                self._fh.flush()

    def exception(self, event, exc):
        self.log(event, f"{type(exc).__name__}: {exc}\n{traceback.format_exc()}")

    def close(self):
        with self._lock:
            if self._fh is not None:
                self._fh.close()
                self._fh = None

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
        def connect(addr, cb, on_status=None): return None
    bluetooth_spp = _BT


# ── PALETTE ────────────────────────────────────────────────────────────────────
BG          = "#080808"
SURFACE     = "#111111"
SURFACE2    = "#1a1a1a"
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

TK_KEY_NAMES = {
    "control_l": "ctrl",
    "control_r": "ctrl",
    "shift_l": "shift",
    "shift_r": "shift",
    "alt_l": "alt",
    "alt_r": "alt",
    "option_l": "alt",
    "option_r": "alt",
    "meta_l": "cmd",
    "meta_r": "cmd",
    "command": "cmd",
    "super_l": "cmd",
    "super_r": "cmd",
    "win_l": "cmd",
    "win_r": "cmd",
    "windows_l": "cmd",
    "windows_r": "cmd",
    "return": "enter",
    "escape": "esc",
    "backspace": "backspace",
    "delete": "delete",
    "space": "space",
    "tab": "tab",
    "up": "up",
    "down": "down",
    "left": "left",
    "right": "right",
}

MODIFIER_NAMES = {"ctrl", "shift", "alt", "cmd"}

KNOWN_GESTURES = [
        "swipe_left", "swipe_right", "swipe_up", "swipe_down",
        "push", "pull", "hold_center"
    ]

for _k in ("cmd_l", "cmd_r", "win_l", "win_r"):
    _v = getattr(pynput_kb.Key, _k, None)
    if _v:
        MODIFIER_KEYS.add(_v)
        PYNPUT_KEY_NAMES[_v] = "cmd"


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


def list_serial_devices():
    """Return [(port, display_label), ...] for USB serial devices.

    Uses pyserial when available so Windows shows COM descriptions, then
    falls back to glob patterns for macOS/Linux.
    """
    devices = []
    try:
        from serial.tools import list_ports
        for p in list_ports.comports():
            port = getattr(p, "device", "") or ""
            desc = getattr(p, "description", "") or ""
            hwid = getattr(p, "hwid", "") or ""
            if not port:
                continue
            detail = desc if desc and desc != "n/a" else hwid
            label = f"{port} · {detail}" if detail else port
            devices.append((port, label))
    except Exception:
        pass

    if not devices:
        patterns = [
            "/dev/cu.usbmodem*", "/dev/cu.usbserial*", "/dev/tty.usbmodem*", "/dev/tty.usbserial*",
            "/dev/ttyACM*", "/dev/ttyUSB*", "COM*",
        ]
        seen = set()
        for pattern in patterns:
            try:
                for port in sorted(glob.glob(pattern)):
                    if port not in seen:
                        seen.add(port)
                        devices.append((port, port))
            except Exception:
                pass
    return devices


def default_serial_port():
    devices = list_serial_devices()
    if devices:
        # Prefer COM5 on your Windows setup when it exists; otherwise use first detected port.
        for port, _label in devices:
            if port.upper() == "COM5":
                return port
        return devices[0][0]
    if sys.platform == "darwin":
        return "/dev/cu.usbserial-0001"
    return ""




def _unique_display_labels(items, *, prefer_name=False):
    """Return [(raw_value, display_label), ...] with clean labels and no addresses/details.

    For BLE, display only the advertised name when available. Duplicate names get
    a small number suffix so the hidden address mapping still stays unique.
    For serial, display only the actual port name.
    """
    counts = {}
    result = []
    for raw, name in items:
        base = (name or raw or "Unknown").strip() if prefer_name else (raw or name or "Unknown").strip()
        if not base:
            base = "Unknown"
        counts[base] = counts.get(base, 0) + 1
        label = base if counts[base] == 1 else f"{base} ({counts[base]})"
        result.append((raw, label))
    return result

def check_macos_permissions(logger=None):
    """Checks and prompts for required macOS permissions on first launch"""
    if sys.platform != "darwin":
        return
    if logger is not None:
        logger.log("permissions_check", "checking macOS Accessibility")
    try:
        result = subprocess.run(
            ["osascript", "-e",
             'tell application "System Events" to get name of first process'],
            capture_output=True
        )
    except Exception as exc:
        if logger is not None:
            logger.exception("permissions_check_error", exc)
        return

    if logger is not None:
        stderr = result.stderr.decode("utf-8", errors="replace").strip()
        logger.log("permissions_check", f"returncode={result.returncode} stderr={stderr!r}")

    if result.returncode != 0:
        import tkinter.messagebox as mb
        mb.showwarning(
            "Permission Required",
            "GesturePuck needs Accessibility permission to simulate key presses.\n"
            "Keyboard recording may also need Input Monitoring permission.\n\n"
            "Please go to:\n"
            "System Settings → Privacy & Security\n\n"
            "Enable GesturePuck or Terminal/Python under Accessibility and Input Monitoring, then restart the app."
        )
        subprocess.run([
            "open",
            "x-apple.systempreferences:com.apple.preference.security?Privacy_Accessibility"
        ])

# ── KEY RECORDER ───────────────────────────────────────────────────────────────
class GlobalKeyRecorder:
    """
    Records one keyboard shortcut at a time.

    The listener is created ONCE at __init__ and runs for the entire lifetime
    of the process (suppress=False). This avoids the macOS crash caused by
    constructing/starting a pynput Listener from a Tk button callback, and
    also avoids the deadlock caused by calling listener.stop() from inside the
    listener's own callback thread.

    Trade-off: because suppress=False, the key pressed during recording will
    also fire in whatever app currently has focus. This is acceptable — it is
    the same behaviour as the previous stable fix.
    """
    def __init__(self, logger=None):
        self.logger = logger
        self._armed = False
        self._lock  = threading.Lock()
        self._chord: list[str] = []
        self._held:  set       = set()
        self._token = 0
        self.results: queue.Queue = queue.Queue()
        self.errors:  queue.Queue = queue.Queue()

        # Single long-lived listener — started here, never restarted.
        self._listener = None
        self._log("recorder_listener", "starting permanent pynput keyboard listener (suppress=False)")
        try:
            self._listener = pynput_kb.Listener(
                on_press=self._on_press,
                on_release=self._on_release,
                suppress=False,
            )
            self._listener.daemon = True
            self._listener.start()
            self._log("recorder_listener", f"started running={self.running}")
        except Exception as exc:
            self._listener = None
            self.errors.put(str(exc))
            self._log_exception("recorder_listener_error", exc)

    def _log(self, event, message):
        if self.logger is not None:
            self.logger.log(event, message)

    def _log_exception(self, event, exc):
        if self.logger is not None:
            self.logger.exception(event, exc)

    @property
    def running(self):
        try:
            return bool(self._listener and self._listener.running)
        except Exception:
            return False

    def arm(self, token):
        """Mark the recorder as ready to capture the next keypress."""
        with self._lock:
            self._armed = True
            self._chord = []
            self._held  = set()
            self._token = token
        self._log("recorder_arm", f"token={token} running={self.running}")

    def cancel(self):
        """Disarm without stopping the listener."""
        with self._lock:
            self._armed = False
            self._chord = []
            self._held  = set()
        self._log("recorder_cancel", "global recorder cancelled")

    def _canonical(self, key):
        if key in PYNPUT_KEY_NAMES:
            return PYNPUT_KEY_NAMES[key]
        ch = getattr(key, "char", None)
        if ch:
            return ch.lower()
        raw = str(key).replace("<", "").replace(">", "")
        raw = raw.replace("Key.", "").lower()
        if raw in {"cmd_l", "cmd_r", "win_l", "win_r", "super_l", "super_r"}:
            return "cmd"
        if raw in {"shift_l", "shift_r"}:
            return "shift"
        if raw in {"ctrl_l", "ctrl_r", "control_l", "control_r"}:
            return "ctrl"
        if raw in {"alt_l", "alt_r", "option_l", "option_r"}:
            return "alt"
        return raw

    def _on_press(self, key):
        try:
            with self._lock:
                if not self._armed:
                    return
                name = self._canonical(key)
                if key not in self._held:
                    self._held.add(key)
                    if name not in self._chord:
                        self._chord.append(name)
                token = self._token
                chord = "+".join(self._chord)
            self._log("recorder_key_press", f"token={token} key={name} chord={chord}")
        except Exception as exc:
            self.errors.put(str(exc))
            self._log_exception("recorder_key_press_error", exc)

    def _on_release(self, key):
        try:
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
            self._log("recorder_result", f"token={token} chord={chord} source=global")
            self.results.put((token, chord, "global"))
            # Listener keeps running — do NOT call stop() here.
        except Exception as exc:
            self.errors.put(str(exc))
            self._log_exception("recorder_key_release_error", exc)


# ── BLE SCAN DIALOG ────────────────────────────────────────────────────────────
class BleScanDialog(tk.Toplevel):
    """
    Modal dialog that scans for BLE devices and lets the user pick one.
    On confirm it writes the chosen address into port_var with the
    special prefix  "BLE:"  so the engine knows to use BLE instead of serial.
    """
    def __init__(self, parent, port_var, logger=None):
        super().__init__(parent)
        self.port_var = port_var
        self.logger   = logger
        self._devices: list[tuple[str, str]] = []   # (address, name)

        self.title("Scan for BLE Devices")
        self.configure(bg=BG)
        self.resizable(False, False)
        self.grab_set()

        # ── header
        tk.Label(self, text="BLE DEVICE SCANNER", bg=BG, fg=ACCENT,
                 font=FONT_TITLE).pack(pady=(16, 4))
        tk.Label(self, text="GesturePuck devices shown first",
                 bg=BG, fg=TEXT_DIM, font=FONT_LABEL).pack()

        mk_separator(self, BORDER).pack(fill="x", padx=16, pady=8)

        # ── listbox
        list_frame = tk.Frame(self, bg=BG)
        list_frame.pack(fill="both", expand=True, padx=16)

        self._lb = tk.Listbox(
            list_frame, bg=SURFACE, fg=TEXT, selectbackground=ACCENT_DIM,
            selectforeground=TEXT, font=FONT_MONO, width=52, height=10,
            relief="flat", highlightthickness=1,
            highlightbackground=BORDER, highlightcolor=ACCENT,
            activestyle="none",
        )
        lb_sb = ttk.Scrollbar(list_frame, orient="vertical",
                              command=self._lb.yview)
        self._lb.configure(yscrollcommand=lb_sb.set)
        lb_sb.pack(side="right", fill="y")
        self._lb.pack(side="left", fill="both", expand=True)

        # ── status label
        self._scan_status = tk.StringVar(value="Press SCAN to search for devices")
        tk.Label(self, textvariable=self._scan_status, bg=BG, fg=TEXT_MED,
                 font=FONT_LABEL).pack(pady=(6, 2))

        # ── buttons
        btn_row = tk.Frame(self, bg=BG)
        btn_row.pack(pady=(4, 16))
        mk_btn(btn_row, "⟳  SCAN",   self._do_scan,   bg=ACCENT,   fg=BG    ).pack(side="left", padx=4)
        mk_btn(btn_row, "✓  SELECT", self._do_select, bg=SAVE_CLR, fg=BG    ).pack(side="left", padx=4)
        mk_btn(btn_row, "✕  CANCEL", self.destroy,    bg=SURFACE,  fg=DEL_CLR).pack(side="left", padx=4)

        self.geometry(f"+{parent.winfo_rootx()+80}+{parent.winfo_rooty()+80}")

    def _do_scan(self):
        self._scan_status.set("Scanning… (5 s)")
        self._lb.delete(0, tk.END)
        self._devices = []
        if self.logger:
            self.logger.log("ble_scan", "starting BLE scan")

        def task():
            devs = bluetooth_spp.list_devices()
            self.after(0, lambda: self._populate(devs))

        threading.Thread(target=task, daemon=True).start()

    def _populate(self, devices: list[tuple[str, str]]):
        self._devices = devices
        self._lb.delete(0, tk.END)
        if not devices:
            self._scan_status.set("No devices found")
            if self.logger:
                self.logger.log("ble_scan", "no devices found")
            return
        display_rows = _unique_display_labels(devices, prefer_name=True)
        for (addr, name), (_raw, label) in zip(devices, display_rows):
            marker = "★ " if "GesturePuck" in (name or "") else "  "
            self._lb.insert(tk.END, f"{marker}{label}")
        # auto-select first GesturePuck
        for i, (_, name) in enumerate(devices):
            if "GesturePuck" in name:
                self._lb.selection_set(i)
                self._lb.see(i)
                break
        self._scan_status.set(f"{len(devices)} device(s) found")
        if self.logger:
            self.logger.log("ble_scan", f"found {len(devices)} device(s)")

    def _do_select(self):
        sel = self._lb.curselection()
        if not sel:
            messagebox.showwarning("No Selection", "Select a device first.", parent=self)
            return
        addr, name = self._devices[sel[0]]
        # Write "BLE:<address>" into the port field so the engine uses BLE
        self.port_var.set(f"BLE:{addr}")
        if self.logger:
            self.logger.log("ble_select", f"selected name={name!r} addr={addr!r}")
        self.destroy()


class SensorCalibrationDialog(tk.Toplevel):
    """Collect five known finger positions and save the sensor fusion calibration."""

    OPEN_TIMEOUT_S = 8.0

    def __init__(
        self,
        parent,
        *,
        port: str,
        baud: int,
        serial_debug: bool,
        serial_debug_log: str | None,
        serial_debug_bytes: bool,
        logger=None,
        on_done=None,
        on_cancel=None,
    ):
        super().__init__(parent)
        self.parent = parent
        self.port = port
        self.baud = baud
        self.serial_debug = serial_debug
        self.serial_debug_log = serial_debug_log
        self.serial_debug_bytes = serial_debug_bytes
        self.logger = logger
        self.on_done = on_done
        self.on_cancel = on_cancel
        self.output_path = default_sensor_calibration_path()
        self.targets = list(SENSOR_CALIBRATION_TARGETS)
        self.index = 0
        self.captures = {}
        self.source = None
        self.pipeline = None
        self.busy = True
        self.completed = False
        self.closed = False
        self._events: queue.Queue = queue.Queue()
        self._open_started_at = time.time()
        self._open_finished = False

        self.title("Sensor Calibration")
        self.configure(bg=BG)
        self.resizable(False, False)
        self.transient(parent)
        self.grab_set()
        self.protocol("WM_DELETE_WINDOW", self._cancel)
        self.bind("<Return>", lambda _event: self._sample_current())

        frame = tk.Frame(self, bg=BG)
        frame.pack(fill="both", expand=True, padx=20, pady=18)

        tk.Label(
            frame,
            text="SENSOR CALIBRATION",
            bg=BG,
            fg=ACCENT,
            font=FONT_TITLE,
        ).pack(anchor="w")
        tk.Label(
            frame,
            text=f"Direct USB: {port}",
            bg=BG,
            fg=TEXT_DIM,
            font=FONT_LABEL,
        ).pack(anchor="w", pady=(2, 8))

        mk_separator(frame, BORDER).pack(fill="x", pady=(0, 12))

        self.target_var = tk.StringVar(value="Opening puck stream…")
        self.instruction_var = tk.StringVar(
            value="Keep the puck connected. The app will pause macros while calibration runs."
        )
        self.result_var = tk.StringVar(value="")

        tk.Label(
            frame,
            textvariable=self.target_var,
            bg=BG,
            fg=TEXT,
            font=("Courier New", 15, "bold"),
            anchor="w",
            width=46,
        ).pack(anchor="w")
        tk.Label(
            frame,
            textvariable=self.instruction_var,
            bg=BG,
            fg=TEXT_MED,
            font=FONT_LABEL,
            anchor="w",
            justify="left",
            wraplength=520,
        ).pack(anchor="w", pady=(8, 10))

        self.progress_var = tk.StringVar(value="")
        tk.Label(
            frame,
            textvariable=self.progress_var,
            bg=BG,
            fg=TEXT_DIM,
            font=FONT_LABEL,
            anchor="w",
        ).pack(anchor="w", pady=(0, 8))

        tk.Label(
            frame,
            textvariable=self.result_var,
            bg=SURFACE,
            fg=TEXT_MED,
            font=FONT_MONO,
            anchor="nw",
            justify="left",
            width=62,
            height=8,
            padx=10,
            pady=8,
        ).pack(fill="x")

        btn_row = tk.Frame(frame, bg=BG)
        btn_row.pack(fill="x", pady=(14, 0))
        self.sample_btn = mk_btn(
            btn_row,
            "SAMPLE (ENTER)",
            self._sample_current,
            bg=ACCENT,
            fg=BG,
            width=16,
        )
        self.sample_btn.pack(side="left", padx=(0, 8))
        mk_btn(btn_row, "CANCEL", self._cancel, bg=SURFACE2, fg=REC_CLR, width=10).pack(side="left")

        self.geometry(f"+{parent.winfo_rootx()+120}+{parent.winfo_rooty()+110}")
        self._set_sample_enabled(False)
        self.progress_var.set("Opening serial reader…")
        threading.Thread(target=self._open_source, daemon=True).start()
        self.after(50, self._poll_events)

    def _log(self, event, message=""):
        if self.logger is not None:
            self.logger.log(event, message)

    def _log_exception(self, event, exc):
        if self.logger is not None:
            self.logger.exception(event, exc)

    def _set_sample_enabled(self, enabled: bool):
        state = "normal" if enabled else "disabled"
        self.sample_btn.config(state=state)

    def _post_event(self, kind: str, *payload):
        if not self.closed:
            self._events.put((kind, *payload))

    def _poll_events(self):
        if self.closed:
            return
        try:
            while True:
                event = self._events.get_nowait()
                kind = event[0]
                if kind == "ready":
                    self._ready_for_target()
                elif kind == "fail":
                    self._fail(str(event[1]))
                elif kind == "sample_done":
                    _kind, name, summary, frames_seen = event
                    self._sample_done(name, summary, frames_seen)
                elif kind == "sample_failed":
                    _kind, name, message = event
                    self._sample_failed(name, message)
        except queue.Empty:
            pass

        if (
            not self.closed
            and not self._open_finished
            and time.time() - self._open_started_at > self.OPEN_TIMEOUT_S
        ):
            self._fail(
                f"Timed out opening {self.port}. The port may still be held by another process "
                "or the previous detector connection may not have released it yet. Close Serial Monitor "
                "and try SENSOR CAL again."
            )
            return
        self.after(50, self._poll_events)

    def _runtime_args(self):
        args = build_arg_parser().parse_args([])
        args.demo = False
        args.dual = True
        args.port = self.port
        args.baud = self.baud
        args.serial_debug = self.serial_debug
        args.serial_debug_log = self.serial_debug_log
        args.serial_debug_bytes = self.serial_debug_bytes
        args.sensor_calibration = "off"
        args.fusion_mode = "best-track"
        args.min_quality = 0.18
        args.min_component_cells = 2
        configure_runtime_args(args)
        return args

    def _open_source(self):
        try:
            self._log("sensor_calibration_open", f"port={self.port!r} baud={self.baud}")
            serial_debug_path = resolve_serial_debug_path(self.serial_debug_log)
            serial_debug = SerialDebugLogger(
                enabled=self.serial_debug,
                path=serial_debug_path,
                log_reads=self.serial_debug_bytes,
            )
            source = DualSensorSerialFrameSource(self.port, self.baud, serial_debug=serial_debug)
            pipeline = FusedSignalPipeline(self._runtime_args())
            self.source = source
            self.pipeline = pipeline
            source.start()
            self._open_finished = True
            if self.closed:
                self._cleanup_source()
                return
            self._post_event("ready")
        except Exception as exc:
            self._open_finished = True
            self._log_exception("sensor_calibration_open_error", exc)
            message = f"Could not open {self.port}: {exc}"
            self._post_event("fail", message)

    def _ready_for_target(self):
        if self.closed:
            return
        self.busy = False
        self._set_sample_enabled(True)
        self._show_target()

    def _show_target(self):
        if self.index >= len(self.targets):
            self._finish()
            return
        name, description, _common = self.targets[self.index]
        self.target_var.set(f"{self.index + 1}/5 · {name.upper()}")
        self.instruction_var.set(
            f"Move one finger/card {description}. Press Enter or SAMPLE immediately while holding it still."
        )
        self.progress_var.set(f"Saving to {self.output_path}")

    def _sample_current(self):
        if self.busy or self.closed or self.source is None or self.pipeline is None:
            return
        if self.index >= len(self.targets):
            return
        name, _description, _common = self.targets[self.index]
        self.busy = True
        self._set_sample_enabled(False)
        self.target_var.set(f"Sampling {name.upper()}…")
        self.instruction_var.set("Hold position until this point finishes.")
        self.progress_var.set("")
        threading.Thread(target=self._sample_worker, daemon=True).start()

    def _sample_worker(self):
        name, _description, _common = self.targets[self.index]
        try:
            source = self.source
            pipeline = self.pipeline
            if source is None or pipeline is None or self.closed:
                return
            while source.read_latest() is not None:
                pass
            pipeline.start_calibration()
            samples, frames_seen = read_visible_sensor_samples(
                source,
                pipeline,
                sample_seconds=1.25,
                warmup_seconds=0.12,
                min_quality=0.18,
            )
            summary = summarize_sensor_samples(samples, min_samples=3)
            self._post_event("sample_done", name, summary, frames_seen)
        except Exception as exc:
            self._log_exception("sensor_calibration_sample_error", exc)
            message = str(exc)
            self._post_event("sample_failed", name, message)

    def _sample_done(self, name, summary, frames_seen):
        if self.closed:
            return
        if not summary:
            self.busy = False
            self._set_sample_enabled(True)
            self.target_var.set(f"Retry {name.upper()}")
            self.instruction_var.set(
                "No stable hand blob was captured. Move into position and press Enter immediately while holding still."
            )
            self.progress_var.set(f"Frames read: {frames_seen}")
            return

        self.captures[name] = summary
        lines = [self.result_var.get().strip()] if self.result_var.get().strip() else []
        sensor_bits = []
        for sensor_id in sorted(summary):
            item = summary[sensor_id]
            sensor_bits.append(
                f"ToF#{sensor_id} x={item['x']:.2f} y={item['y']:.2f} q={item['quality']:.2f} n={int(item['n'])}"
            )
        lines.append(f"{name}: " + "; ".join(sensor_bits))
        self.result_var.set("\n".join(lines[-6:]))
        self.index += 1
        self.busy = False
        self._set_sample_enabled(True)
        self._show_target()

    def _sample_failed(self, name, message):
        if self.closed:
            return
        self.busy = False
        self._set_sample_enabled(True)
        self.target_var.set(f"Retry {name.upper()}")
        self.instruction_var.set(str(message))

    def _finish(self):
        self.busy = True
        self._set_sample_enabled(False)
        self.target_var.set("Fitting calibration…")
        self.instruction_var.set("Writing sensor_calibration.json and restarting detection.")

        try:
            sensors = fit_sensor_affines(self.captures)
            if not sensors:
                raise RuntimeError("No sensor had at least three usable calibration points.")
            save_calibration(self.output_path, self.captures, sensors)
        except Exception as exc:
            self._log_exception("sensor_calibration_fit_error", exc)
            self._fail(str(exc))
            return

        self.completed = True
        self._cleanup_source()
        self.result_var.set(
            "\n".join(
                f"ToF#{sensor_id}: rms={info['rms_error_cells']} cells weight={info['sensor_weight']}"
                for sensor_id, info in sorted(sensors.items(), key=lambda item: int(item[0]))
            )
        )
        self.target_var.set("Calibration saved")
        self.instruction_var.set("Detection will now restart with calibrated weighted fusion for macros.")
        self.progress_var.set(str(self.output_path))
        self.after(900, lambda: self._done(sensors))

    def _done(self, sensors):
        if self.on_done is not None:
            self.on_done(sensors, self.output_path)
        self.closed = True
        self.destroy()

    def _fail(self, message):
        if self.closed:
            return
        self.completed = False
        self._cleanup_source()
        messagebox.showerror("Sensor calibration failed", message, parent=self)
        self._cancel()

    def _cleanup_source(self):
        source = self.source
        self.source = None
        self.pipeline = None
        if source is not None:
            try:
                source.close()
            except Exception as exc:
                self._log_exception("sensor_calibration_close_error", exc)

    def _cancel(self):
        if self.closed:
            return
        self.closed = True
        self._cleanup_source()
        if not self.completed and self.on_cancel is not None:
            self.on_cancel()
        self.destroy()


# ── MAIN APP ───────────────────────────────────────────────────────────────────
class GesturePuckApp:
    def __init__(
        self,
        root,
        *,
        default_port=None,
        auto_connect=False,
        demo=False,
        baud=115200,
        serial_debug=False,
        serial_debug_log=None,
        serial_debug_bytes=False,
        logger=None,
    ):
        self.logger = logger or UiLogger(resolve_ui_log_path("auto"))
        self.logger.log(
            "app_init",
            f"default_port={default_port!r} auto_connect={auto_connect} demo={demo} baud={baud}",
        )
        self.root = root
        self.root.title("GesturePuck")
        sw, sh = self.root.winfo_screenwidth(), self.root.winfo_screenheight()
        self.root.geometry(f"{sw}x{sh}")
        self.root.configure(bg=BG)
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

        self.mappings     = store.load()
        self.mode_manager = ModeManager()
        self.devices      = []
        self.connection   = None        # BLE connection handle (if connected via BLE)
        self.current_page = "Global"
        self.engine       = None
        self.default_port = default_port or default_serial_port()
        self.baud = baud
        self.serial_debug = serial_debug
        self.serial_debug_log = serial_debug_log
        self.serial_debug_bytes = serial_debug_bytes

        # BLE UART receives the same text stream as Serial Monitor.
        # These fields let BLE feed FRAME lines into the same classifier path.
        self._ble_pipeline = None
        self._ble_detector = None
        self._ble_frames_seen = 0
        self._ble_lines_seen = 0
        self._ble_prox_seen = 0
        self._ble_last_proximity = 0
        self._ble_last_hand_present = False
        self._ble_connected = False
        self._ble_last_visible = False
        self._ble_last_quality = 0.0
        self._ble_last_measurement_status = ""
        self._ble_is_calibrating = False
        self._ble_pending_frames = {}
        self._ble_seen_frame3 = False
        self._serial_is_calibrating = False
        self._ready_token = 0
        self._ble_state_lock = threading.RLock()

        self._macro_vars: dict[str, tk.StringVar] = {}
        self._label_vars: dict[str, tk.StringVar] = {}
        self._entries:    dict[str, tk.Entry]     = {}
        self._next_token  = 1
        self._pending: dict[int, str] = {}
        self._ui_events: queue.Queue = queue.Queue()
        self._connect_generation = 0
        self._local_record_token = None
        self._local_chord: list[str] = []
        self._local_held: set[str] = set()
        self._sensor_calibration_dialog = None
        self._sensor_calibration_pending = False

        # GlobalKeyRecorder starts its permanent listener here, at app boot,
        # on the main thread — safe on macOS.
        self.recorder     = GlobalKeyRecorder(self.logger)
        self.status       = tk.StringVar(value="NOT CONNECTED")
        self.last_gesture = tk.StringVar(value="—")
        self.device_var   = tk.StringVar(value=self.default_port)
        self.connection_type_var = tk.StringVar(value="Direct")
        self._device_display_to_value: dict[str, str] = {}
        self._serial_calibrating_until = 0.0

        self._build_ui()
        self._on_connection_type_changed()
        self._refresh_calibration_hint()
        self.root.bind_all("<KeyPress>", self._on_local_key_press, add="+")
        self.root.bind_all("<KeyRelease>", self._on_local_key_release, add="+")
        self._show_page("Global")
        self.root.after(50, self._poll_recorder)
        self.root.after(50, self._poll_ui_events)
        if auto_connect:
            self.root.after(150, self._connect_demo if demo else self._connect)

    # ── BUILD ──────────────────────────────────────────────────────────────────
    def _build_ui(self):
        # ── TOP BAR ───────────────────────────────────────────────────────────
        topbar = tk.Frame(self.root, bg=SURFACE, height=52)
        topbar.pack(fill="x")
        topbar.pack_propagate(False)

        logo_area = tk.Frame(topbar, bg=SURFACE)
        logo_area.pack(side="left", padx=(20, 0))

        dot_canvas = tk.Canvas(logo_area, width=14, height=14,
                               bg=SURFACE, highlightthickness=0)
        dot_canvas.pack(side="left", pady=18)
        dot_canvas.create_oval(1, 1, 13, 13, outline=ACCENT, width=2)

        tk.Label(logo_area, text="  GESTUREPUCK", bg=SURFACE, fg=TEXT,
                 font=("Courier New", 12, "bold"),
                 ).pack(side="left")

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
        # Two compact rows prevent long device names/status fields from being
        # clipped while keeping the same connect/scan behavior.
        devbar = tk.Frame(self.root, bg=BG)
        devbar.pack(fill="x", padx=20, pady=(8, 6))

        devrow = tk.Frame(devbar, bg=BG)
        devrow.pack(fill="x")

        # Keep the last-gesture pill on the far right with enough reserved width.
        gf = tk.Frame(devrow, bg=BG)
        gf.pack(side="right", padx=(16, 0))
        mk_label(gf, "LAST GESTURE", fg=TEXT_DIM).pack(side="left")
        self._gesture_badge = tk.Label(
            gf, textvariable=self.last_gesture,
            bg=SURFACE2, fg=ACCENT,
            font=("Courier New", 11, "bold"),
            width=22, anchor="center",
            padx=10, pady=2,
        )
        self._gesture_badge.pack(side="left", padx=(8, 0))

        # --- Unified connection section: Direct USB/COM or Bluetooth BLE ---
        controls = tk.Frame(devrow, bg=BG)
        controls.pack(side="left", fill="x", expand=True)

        mk_label(controls, "CONNECT BY", fg=TEXT_DIM).pack(side="left", padx=(0, 8))
        self._conn_type_combo = ttk.Combobox(
            controls,
            textvariable=self.connection_type_var,
            values=("Direct", "Bluetooth"),
            state="readonly",
            width=11,
            font=FONT_MONO,
        )
        self._conn_type_combo.pack(side="left", padx=(0, 8), ipady=2)
        self._conn_type_combo.bind("<<ComboboxSelected>>", self._on_connection_type_changed)

        mk_label(controls, "DEVICE", fg=TEXT_DIM).pack(side="left", padx=(0, 8))
        self.port_var = tk.StringVar(value=self.default_port)  # raw value: COM5 or BLE:<addr>
        self._device_combo = ttk.Combobox(
            controls,
            textvariable=self.device_var,
            values=(),
            state="normal",
            width=42,
            font=FONT_MONO,
        )
        self._device_combo.pack(side="left", padx=(0, 8), ipady=2)
        self._device_combo.bind("<<ComboboxSelected>>", self._on_device_selected)

        mk_btn(controls, "SCAN", self._scan_devices, bg=ACCENT2, fg=BG, width=8).pack(side="left", padx=(0, 4))
        mk_btn(controls, "CONNECT", self._connect, bg=ACCENT, fg=BG, width=10).pack(side="left", padx=(0, 4))
        mk_btn(controls, "DISCONNECT", self._disconnect_manual, bg=SURFACE2, fg=REC_CLR, width=12).pack(side="left", padx=(0, 4))
        mk_btn(controls, "DEMO", self._connect_demo, bg=SURFACE2, fg=TEXT_MED, width=7).pack(side="left", padx=(0, 4))

        info_row = tk.Frame(devbar, bg=BG)
        info_row.pack(fill="x", pady=(5, 0))

        # Connection-mode indicator (updates dynamically)
        self._conn_mode_var = tk.StringVar(value="")
        self._conn_mode_lbl = tk.Label(
            info_row, textvariable=self._conn_mode_var,
            bg=BG, fg=TEXT_DIM, font=FONT_LABEL, anchor="w",
        )
        self._conn_mode_lbl.pack(side="left")

        mk_label(info_row, f" · {self.baud}", fg=TEXT_DIM).pack(side="left", padx=(4, 0))

        self._setup_hint_var = tk.StringVar(value="")
        self._setup_hint_lbl = tk.Label(
            info_row, textvariable=self._setup_hint_var,
            bg=BG, fg=TEXT_DIM, font=FONT_LABEL, anchor="w",
        )
        self._setup_hint_lbl.pack(side="left", padx=(14, 0), fill="x", expand=True)

        setup_actions = tk.Frame(devbar, bg=BG)
        setup_actions.pack(fill="x", pady=(6, 0))
        mk_label(setup_actions, "SETUP", fg=TEXT_DIM).pack(side="left", padx=(0, 8))
        mk_btn(setup_actions, "RECALIBRATE", self._recalibrate, bg=SURFACE2, fg=ACCENT, width=13).pack(side="left", padx=(0, 6))
        mk_btn(setup_actions, "SENSOR CAL", self._calibrate_sensors, bg=SURFACE2, fg=SAVE_CLR, width=12).pack(side="left", padx=(0, 6))
        mk_label(
            setup_actions,
            "Run SENSOR CAL after changing puck placement or sensor alignment.",
            fg=TEXT_DIM,
        ).pack(side="left", padx=(6, 0))

        mk_separator(self.root).pack(fill="x")

        # ── BODY ──────────────────────────────────────────────────────────────
        body = tk.Frame(self.root, bg=BG)
        body.pack(fill="both", expand=True)

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

            def _enter(e, btn=b):
                if btn.cget("bg") != ACCENT_DIM:
                    btn.config(bg=SURFACE2, fg=TEXT)

            def _leave(e, btn=b):
                if btn.cget("bg") != ACCENT_DIM:
                    btn.config(bg=SURFACE, fg=TEXT_MED)

            b.bind("<Enter>", _enter)
            b.bind("<Leave>", _leave)

        render_pack_sidebar_section(
            sidebar,
            self.mode_manager,
            self._nav_btns,
            on_show_pack=self._show_pack_page,
            on_import_done=self._on_pack_imported,
            root=self.root,
        )
        tk.Frame(body, bg=BORDER, width=1).pack(side="left", fill="y")

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

    # ── BLE SCAN DIALOG ────────────────────────────────────────────────────────
    def _open_ble_scan(self):
        """Open the BLE scan dialog. On device selection, port_var is set to BLE:<addr>."""
        self.logger.log("ble_scan_open", "opening BLE scan dialog")
        BleScanDialog(self.root, self.port_var, self.logger)

    # ── NAVIGATION ────────────────────────────────────────────────────────────
    def _show_page(self, app_name):
        self.logger.log("nav", f"show_page={app_name}")
        self.current_page = app_name
        for name, btn in self._nav_btns.items():
            if name == app_name:
                btn.config(bg=ACCENT_DIM, fg=TEXT)
            else:
                btn.config(bg=SURFACE, fg=TEXT_MED)
        self._render_page(app_name)

    # ── RENDER ────────────────────────────────────────────────────────────────
    def _render_page(self, app_name):
        self.logger.log("render", f"page={app_name} mappings={len(self.mappings.get(app_name, {}))}")
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

        badge = tk.Label(row, text=gesture, bg=ACCENT_DIM, fg=TEXT,
                         font=FONT_BADGE, padx=6, pady=2, width=16, anchor="w")
        badge.pack(side="left", padx=(8, 12), pady=6)

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

        mk_btn(row, "⏺  REC",
               lambda g=gesture: self._start_record(g),
               bg=SURFACE, fg=REC_CLR).pack(side="left", padx=(0, 4))

        #mk_btn(row, "✓  SAVE",
               #lambda g=gesture, mv=mvar, lv=lvar: self._save(
                   #g, mv.get(), lv.get()),
               #bg=SURFACE, fg=SAVE_CLR).pack(side="left", padx=(0, 4))

        mk_btn(row, "✕",
               lambda g=gesture: self._delete(g),
               bg=SURFACE, fg=DEL_CLR).pack(side="left", padx=(0, 8))

    # ── DEVICE / CONNECT ──────────────────────────────────────────────────────
    def _on_connection_type_changed(self, event=None):
        mode = self.connection_type_var.get()
        # Changing transport is setup mode. Stop the active stream first so
        # gestures/macros cannot fire while the user is switching devices.
        if self.engine is not None or self.connection is not None:
            self.logger.log("connection_type_change", "disconnecting active transport before switch")
            self._connect_generation += 1
            self._stop_existing_engine()
            self.last_gesture.set("—")
            self._set_status("Not Connected", TEXT_DIM)
        self._device_display_to_value.clear()
        self._device_combo.configure(values=())
        if mode == "Direct":
            self.port_var.set(self.default_port or "")
            self.device_var.set(self.default_port or "")
            self._conn_mode_var.set("MODE · DIRECT USB")
            self._conn_mode_lbl.config(fg=ACCENT)
            if hasattr(self, "_setup_hint_var"):
                self._setup_hint_var.set("Direct mode uses your USB serial port, for example COM5 or /dev/cu.usbserial-xxxx.")
        else:
            self.port_var.set("")
            self.device_var.set("Press SCAN, then choose GesturePuck")
            self._conn_mode_var.set("MODE · BLUETOOTH")
            self._conn_mode_lbl.config(fg=ACCENT2)
            if hasattr(self, "_setup_hint_var"):
                self._setup_hint_var.set("Bluetooth mode shows friendly names only; the BLE address stays hidden internally.")
        self._refresh_calibration_hint()

    def _on_device_selected(self, event=None):
        shown = self.device_var.get().strip()
        raw = self._device_display_to_value.get(shown, shown)
        self.port_var.set(raw)
        self.logger.log("device_select", f"shown={shown!r} raw={raw!r}")

    def _resolve_selected_device_value(self) -> str:
        shown = self.device_var.get().strip()
        raw = self._device_display_to_value.get(shown, shown).strip()
        mode = self.connection_type_var.get()

        if mode == "Bluetooth":
            if raw.upper().startswith("BLE:"):
                return raw
            # Allow pasting just the BLE address into the box.
            if raw and ":" in raw and not raw.upper().startswith("COM"):
                return f"BLE:{raw}"
            return raw

        # Direct mode: if user typed a friendly label like "COM5 · USB Serial",
        # keep only the actual port before the separator.
        if " · " in raw:
            raw = raw.split(" · ", 1)[0].strip()
        return raw

    def _scan_devices(self):
        mode = self.connection_type_var.get()
        self._set_status("Scanning…", TEXT_DIM)
        self.logger.log("scan", f"mode={mode!r}")

        def task():
            if mode == "Bluetooth":
                devices = bluetooth_spp.list_devices()
                self.root.after(0, lambda: self._update_scanned_devices("Bluetooth", devices))
            else:
                devices = list_serial_devices()
                self.root.after(0, lambda: self._update_scanned_devices("Direct", devices))

        threading.Thread(target=task, daemon=True).start()

    def _update_scanned_devices(self, mode, devices):
        self.devices = devices
        self._device_display_to_value.clear()

        if mode == "Bluetooth":
            # Strongly prefer the advertised name GesturePuck. Other BLE devices
            # are still shown below it for debugging.
            pucks = [(addr, name) for addr, name in devices if "GesturePuck" in (name or "")]
            ordered = pucks + [(addr, name) for addr, name in devices if (addr, name) not in pucks]
            labels = []
            for addr, label in _unique_display_labels(ordered, prefer_name=True):
                self._device_display_to_value[label] = f"BLE:{addr}"
                labels.append(label)
            self._device_combo.configure(values=labels)
            if labels:
                chosen = labels[0]
                self.device_var.set(chosen)
                self.port_var.set(self._device_display_to_value[chosen])
                self._set_status("Not Connected", TEXT_MED)
                self.logger.log("ble_scan", f"found={len(devices)} selected={chosen!r}")
            else:
                self.device_var.set("No BLE devices found")
                self.port_var.set("")
                self._set_status("Not Connected", TEXT_DIM)
                self.logger.log("ble_scan", "no devices found")
            return

        labels = []
        clean_devices = _unique_display_labels(devices, prefer_name=False)
        for port, label in clean_devices:
            self._device_display_to_value[label] = port
            labels.append(label)
        self._device_combo.configure(values=labels)
        if labels:
            # Prefer COM5 when available because that is your usual GesturePuck
            # direct port; otherwise choose the first detected port.
            chosen = next((label for port, label in clean_devices if port.upper() == "COM5"), labels[0])
            self.device_var.set(chosen)
            self.port_var.set(self._device_display_to_value[chosen])
            self._set_status("Not Connected", TEXT_MED)
            self.logger.log("serial_scan", f"found={len(devices)} selected={chosen!r}")
        else:
            self.device_var.set("No serial ports found")
            self.port_var.set("")
            self._set_status("Not Connected", TEXT_DIM)
            self.logger.log("serial_scan", "no serial ports found")

    def _disconnect_manual(self):
        """Manual disconnect button for serial/demo/BLE without changing mappings."""
        self.logger.log("disconnect_click", "manual disconnect requested")
        self._connect_generation += 1
        self._stop_existing_engine()
        self._serial_is_calibrating = False
        with self._ble_state_lock:
            self._ble_connected = False
            self._ble_is_calibrating = False
        self._set_status("Not Connected", TEXT_DIM)

    def _connect(self):
        """Connect using the selected Direct/Bluetooth mode and device."""
        port_value = self._resolve_selected_device_value()
        self.port_var.set(port_value)
        mode = self.connection_type_var.get()
        self.logger.log("connect_click", f"mode={mode!r} port_value={port_value!r}")

        if mode == "Bluetooth" or port_value.upper().startswith("BLE:"):
            ble_addr = port_value[4:].strip() if port_value.upper().startswith("BLE:") else port_value.strip()
            if not ble_addr:
                messagebox.showerror("Error", "Choose Bluetooth, press SCAN, then select GesturePuck.")
                return
            self._start_ble_engine(ble_addr)
        else:
            if not port_value:
                self.logger.log("connect_error", "empty serial port")
                messagebox.showerror("Error", "Choose Direct, press SCAN, then select a COM/USB port.")
                return
            self._start_engine(port=port_value, demo=False)

    def _connect_demo(self):
        self.logger.log("connect_demo_click", "starting demo source")
        self._start_engine(port=None, demo=True)

    def _recalibrate(self):
        """Recalibrate either the COM/serial engine or the BLE frame classifier."""
        with self._ble_state_lock:
            ble_ready = self.connection is not None and self._ble_connected
        self.logger.log(
            "recalibrate_click",
            f"engine_present={self.engine is not None} ble_ready={ble_ready}",
        )

        # Serial/COM path: the GestureEngine owns the pipeline.
        if self.engine is not None:
            try:
                self._serial_is_calibrating = True
                self._serial_calibrating_until = time.time() + 0.9
                self._set_status("Calibrating", ACCENT)
                self.engine.recalibrate()
                self._schedule_ready(delay_ms=900)
            except Exception as exc:
                self._serial_is_calibrating = False
                self.logger.exception("recalibrate_exception", exc)
                self._set_status("Recalibrate Error", REC_CLR)
                messagebox.showerror("Recalibrate failed", str(exc))
            return

        # BLE path: BLE does not use GestureEngine, but it still has a
        # SignalPipeline + StrokeGestureDetector receiving FRAME lines.
        if ble_ready:
            try:
                with self._ble_state_lock:
                    if self._ble_pipeline is None or self._ble_detector is None:
                        self._ble_pipeline, self._ble_detector = self._make_ble_classifier()
                    self._ble_pipeline.start_calibration()
                    self._ble_detector.clear()
                    self._ble_is_calibrating = True
                    self._ble_pending_frames = {}
                    self._ble_last_visible = False
                    self._ble_last_quality = 0.0
                    self._ble_last_measurement_status = "calibrating"
                self._set_status("Calibrating", ACCENT2)
                self._schedule_ready(delay_ms=900)
            except Exception as exc:
                with self._ble_state_lock:
                    self._ble_is_calibrating = False
                self.logger.exception("ble_recalibrate_exception", exc)
                self._set_status("Recalibrate Error", REC_CLR)
                messagebox.showerror("BLE recalibrate failed", str(exc))
            return

        self._set_status("Not Connected", TEXT_DIM)
        messagebox.showinfo("Recalibrate", "Connect to the puck before recalibrating.")

    def _calibration_hint_text(self) -> str:
        try:
            path, transforms, _weights = load_sensor_calibration("auto")
        except Exception as exc:
            self.logger.exception("sensor_calibration_hint_error", exc)
            return "Sensor calibration: file unreadable."
        if transforms:
            sensors = ", ".join(f"ToF#{sensor_id}" for sensor_id in sorted(transforms))
            return f"Sensor calibration: active for {sensors}."
        path = path or default_sensor_calibration_path()
        return f"Sensor calibration: not fitted yet ({path.name})."

    def _refresh_calibration_hint(self):
        if not hasattr(self, "_setup_hint_var"):
            return
        mode = self.connection_type_var.get()
        if mode == "Bluetooth":
            base = "Bluetooth mode shows friendly names only; the BLE address stays hidden internally."
        else:
            base = "Direct mode uses your USB serial port, for example COM5 or /dev/cu.usbserial-xxxx."
        self._setup_hint_var.set(f"{base}  {self._calibration_hint_text()}")

    def _calibrate_sensors(self):
        """Run the five-point ToF sensor alignment inside the Tkinter app."""
        if self._sensor_calibration_pending:
            self._set_status("Calibrating", ACCENT)
            return
        dialog = self._sensor_calibration_dialog
        try:
            if dialog is not None and dialog.winfo_exists():
                dialog.lift()
                dialog.focus_force()
                return
        except Exception:
            self._sensor_calibration_dialog = None

        with self._ble_state_lock:
            ble_ready = self.connection is not None and self._ble_connected
        mode = self.connection_type_var.get()
        if mode == "Bluetooth" or ble_ready:
            messagebox.showinfo(
                "Sensor calibration",
                "Sensor calibration currently uses Direct USB so the app can reopen the serial stream cleanly. "
                "Switch to Direct, select the USB serial port, then run SENSOR CAL.",
            )
            return

        active_engine = self.engine
        active_args = getattr(active_engine, "args", None)
        if bool(getattr(active_args, "demo", False)):
            messagebox.showinfo("Sensor calibration", "Calibration needs the physical puck, not demo mode.")
            return

        port = str(getattr(active_args, "port", None) or self._resolve_selected_device_value()).strip()
        if port.upper().startswith("BLE:"):
            port = ""
        if not port:
            messagebox.showerror("Sensor calibration", "Select the Direct USB serial port first.")
            return

        restart_after_cancel = active_engine is not None
        self.logger.log(
            "sensor_calibration_start",
            f"port={port!r} baud={self.baud} restart_after_cancel={restart_after_cancel}",
        )
        self._connect_generation += 1
        self._stop_existing_engine()
        self._set_status("Calibrating", ACCENT)
        self._sensor_calibration_pending = True

        delay_ms = 750 if restart_after_cancel else 100
        self.root.after(
            delay_ms,
            lambda p=port, restart=restart_after_cancel: self._open_sensor_calibration_dialog(p, restart),
        )

    def _open_sensor_calibration_dialog(self, port, restart_after_cancel):
        self._sensor_calibration_pending = False
        if self.root.winfo_exists() == 0:
            return
        self._sensor_calibration_dialog = SensorCalibrationDialog(
            self.root,
            port=port,
            baud=self.baud,
            serial_debug=self.serial_debug,
            serial_debug_log=self.serial_debug_log,
            serial_debug_bytes=self.serial_debug_bytes,
            logger=self.logger,
            on_done=lambda sensors, path, p=port: self._on_sensor_calibration_done(p, sensors, path),
            on_cancel=lambda p=port, restart=restart_after_cancel: self._on_sensor_calibration_cancelled(p, restart),
        )

    def _on_sensor_calibration_done(self, port, sensors, path):
        self._sensor_calibration_dialog = None
        self._sensor_calibration_pending = False
        sensor_summary = ", ".join(
            f"ToF#{sensor_id}:rms={info.get('rms_error_cells')}"
            for sensor_id, info in sorted(sensors.items(), key=lambda item: int(item[0]))
        )
        self.logger.log("sensor_calibration_done", f"path={path} {sensor_summary}")
        self._refresh_calibration_hint()
        self._set_status("Calibrated", ACCENT)
        self._start_engine(port=port, demo=False)

    def _on_sensor_calibration_cancelled(self, port, restart):
        self._sensor_calibration_dialog = None
        self._sensor_calibration_pending = False
        self.logger.log("sensor_calibration_cancelled", f"port={port!r} restart={restart}")
        self._refresh_calibration_hint()
        if restart and port:
            self._start_engine(port=port, demo=False)
        else:
            self._set_status("Not Connected", TEXT_DIM)

    # ── BLE ENGINE ────────────────────────────────────────────────────────────
    def _make_ble_classifier(self):
        """Create a fresh copy of the same gesture pipeline used by the serial engine."""
        args = build_arg_parser().parse_args([])
        args.demo = False
        args.dual = True
        args.port = None
        args.baud = self.baud
        args.serial_debug = self.serial_debug
        args.serial_debug_log = self.serial_debug_log
        args.serial_debug_bytes = self.serial_debug_bytes
        configure_runtime_args(args)
        return FusedSignalPipeline(args), StrokeGestureDetector(args)

    def _start_ble_engine(self, ble_addr: str):
        """
        Stop any existing engine/connection, then open a BLE UART connection.

        Important: the ESP32 firmware sends FRAME/#PROX lines over BLE, not only
        pre-classified GESTURE lines. So BLE needs its own classifier state and
        must parse FRAME lines exactly like the serial path does.
        """
        self._stop_existing_engine()
        self._connect_generation += 1
        self.logger.log("ble_connect", f"addr={ble_addr!r}")
        self._set_status("Connecting…", TEXT_DIM)
        self.connection_type_var.set("Bluetooth")
        self._conn_mode_var.set("MODE · BLUETOOTH")
        self._conn_mode_lbl.config(fg=ACCENT2)

        with self._ble_state_lock:
            self._ble_pipeline, self._ble_detector = self._make_ble_classifier()
            self._ble_frames_seen = 0
            self._ble_lines_seen = 0
            self._ble_prox_seen = 0
            self._ble_connected = False
            self._ble_is_calibrating = False
            self._ble_connected = False
            self._ble_last_proximity = 0
            self._ble_last_hand_present = False
            self._ble_connected = False
            self._ble_last_visible = False
            self._ble_last_quality = 0.0
            self._ble_last_measurement_status = ""
            self._ble_is_calibrating = False
            self._ble_pending_frames = {}
            self._ble_seen_frame3 = False

        def task():
            try:
                conn = bluetooth_spp.connect(
                    ble_addr,
                    self._on_ble_line,
                    on_status=self._on_ble_status,
                )
                self.connection = conn
                self.logger.log("ble_connect", "BLE worker started")
            except Exception as exc:
                self.logger.exception("ble_connect_exception", exc)
                self._ui_events.put(("error", "BLE Connection failed", str(exc)))

        threading.Thread(target=task, daemon=True).start()

    def _on_ble_status(self, text: str):
        """Status callback from the BLE transport thread."""
        self.logger.log("ble_status", text)
        if text == "BLE CONNECTED":
            with self._ble_state_lock:
                self._ble_connected = True
            self._ui_events.put(("status", "Waiting for Frames", TEXT_MED))
            return
        if text == "BLE DISCONNECTED" or text.startswith("BLE ERROR"):
            with self._ble_state_lock:
                self._ble_connected = False
                self._ble_is_calibrating = False
            label = "Disconnected" if text == "BLE DISCONNECTED" else "BLE Error"
            self._ui_events.put(("status", label, REC_CLR))
            return
        if text in {"BLE WAITING FOR FRAMES", "BLE RECEIVING DATA"}:
            label = "Waiting for Frames" if "WAITING" in text else "Receiving Frames"
            self._ui_events.put(("status", label, TEXT_MED if "WAITING" in text else ACCENT2))
            return
        self._ui_events.put(("status", self._clean_status_text(text), TEXT_MED))

    def _parse_ble_frame_line(self, line: str):
        """Parse FRAME/FRAME1/FRAME2/FRAME3 from BLE."""
        if line.startswith("FRAME3,"):
            sensor_id = 3
            line = "FRAME," + line.split(",", 1)[1]
        elif line.startswith("FRAME2,"):
            sensor_id = 2
            line = "FRAME," + line.split(",", 1)[1]
        elif line.startswith("FRAME1,"):
            sensor_id = 1
            line = "FRAME," + line.split(",", 1)[1]
        else:
            sensor_id = 1
        packet = parse_frame_line(line)
        if packet is None:
            return None
        return sensor_id, packet

    def _build_ble_fused_packet(self, primary_sensor: int, primary_packet):
        pending = self._ble_pending_frames
        primary = pending.get(primary_sensor) or primary_packet
        tof1_packet = pending.get(1) or primary
        tof1 = tof1_packet.values
        tof2 = pending[2].values if 2 in pending else np.full_like(tof1, np.nan)
        tof3 = pending[3].values if 3 in pending else np.full_like(tof1, np.nan)
        available = tuple(sensor_id for sensor_id in (1, 2, 3) if sensor_id in pending)
        return DualFramePacket(
            host_t=primary.host_t,
            tof1=tof1,
            tof2=tof2,
            tof3=tof3,
            proximity=self._ble_last_proximity,
            hand_present=self._ble_last_hand_present,
            seq=primary.seq,
            device_ms=primary.device_ms,
            primary_sensor=1 if 1 in pending else primary_sensor,
            available_sensors=available,
        )

    def _on_ble_line(self, line: str):
        """
        Called by the BLE connection for every text line received from the ESP32.
        Supported lines:
          FRAME,<seq>,<ms>,<64 values…>   -> parsed and classified locally
          FRAME1,<seq>,<ms>,<64 values…>  -> parsed and classified locally
          FRAME2,<seq>,<ms>,<64 values…>  -> parsed and fused locally
          FRAME3,<seq>,<ms>,<64 values…>  -> parsed and fused locally
          #PROX,<value>,<hand>             -> status/diagnostics
          GESTURE,<name>,<confidence>     -> optional direct firmware gesture
        """
        self.logger.log("ble_line", line[:180])
        with self._ble_state_lock:
            self._ble_lines_seen += 1

        if line.startswith("#PROX,"):
            try:
                parts = line.split(",")
                with self._ble_state_lock:
                    self._ble_last_proximity = int(parts[1])
                    self._ble_last_hand_present = int(parts[2]) == 1
                    self._ble_prox_seen += 1
                    frames_seen = self._ble_frames_seen
                    hand = self._ble_last_hand_present
                if frames_seen == 0:
                    status = "Waiting for Frames" if hand else "Waiting for Hand"
                    self._ui_events.put(("status", status, TEXT_MED))
            except (IndexError, ValueError):
                self.logger.log("ble_bad_prox", line[:120])
            return

        if line.startswith("GESTURE,"):
            parts = line.split(",")
            if len(parts) >= 3:
                try:
                    name = parts[1].strip()
                    confidence = float(parts[2].strip())
                    self._ui_events.put(("gesture", name, confidence))
                    self._ui_events.put(("status", "Ready", ACCENT2))
                except ValueError:
                    self.logger.log("ble_bad_gesture", line[:120])
            return

        if not line.startswith(("FRAME,", "FRAME1,", "FRAME2,", "FRAME3,")):
            # Keep setup chatter out of the UI, but leave it in the log.
            return

        parsed = self._parse_ble_frame_line(line)
        if parsed is None:
            return
        sensor_id, packet = parsed

        try:
            with self._ble_state_lock:
                if self._ble_pipeline is None or self._ble_detector is None:
                    self._ble_pipeline, self._ble_detector = self._make_ble_classifier()
                self._ble_pending_frames[sensor_id] = packet
                if sensor_id == 3:
                    self._ble_seen_frame3 = True
                should_classify = line.startswith("FRAME,") or sensor_id == 3 or (
                    sensor_id == 2 and not self._ble_seen_frame3
                )
                if not should_classify:
                    return
                fused_packet = self._build_ble_fused_packet(sensor_id, packet)
                self._ble_frames_seen += 1
                frames_seen = self._ble_frames_seen
                measurement = self._ble_pipeline.process(fused_packet)
                event = self._ble_detector.update(measurement)

            # First valid frame means the puck stream is alive. Show loading,
            # then Ready once the classifier has processed a few frames.
            with self._ble_state_lock:
                ble_calibrating = self._ble_is_calibrating
                measurement_status = getattr(measurement, "status", "")
                self._ble_last_measurement_status = measurement_status
                if ble_calibrating and not str(measurement_status).startswith("calibrating"):
                    self._ble_is_calibrating = False
                    ble_calibrating = False

            if ble_calibrating:
                self._ui_events.put(("status", "Calibrating", ACCENT2))
            elif frames_seen == 1:
                self._ui_events.put(("status", "Receiving Frames", ACCENT2))
                self._schedule_ready(delay_ms=900)
            elif frames_seen % 50 == 0 and self.status.get() != "Ready":
                self._ui_events.put(("status", "Receiving Frames", ACCENT2))
                self._schedule_ready(delay_ms=600)

            if event is not None:
                self.logger.log(
                    "ble_gesture",
                    f"name={event.name} confidence={event.confidence:.3f} details={event.details}",
                )
                self._ui_events.put(("gesture", event.name, event.confidence))
        except Exception as exc:
            self.logger.exception("ble_classifier_exception", exc)
            self._ui_events.put(("status", f"BLE ERROR {type(exc).__name__}", REC_CLR))

    # ── SERIAL ENGINE ─────────────────────────────────────────────────────────
    def _stop_existing_engine(self):
        """Stop whichever transport is currently active."""
        if self.engine is not None:
            self.logger.log("connect", "stopping existing serial engine")
            self.engine.stop()
            self.engine = None
        if self.connection is not None:
            self.logger.log("connect", "closing existing BLE connection")
            try:
                self.connection.close()
            except Exception:
                pass
            self.connection = None
        with self._ble_state_lock:
            self._ble_connected = False
            self._ble_is_calibrating = False
            self._ble_pipeline = None
            self._ble_detector = None
            self._ble_pending_frames = {}
            self._ble_seen_frame3 = False
            self._ble_frames_seen = 0
            self._ble_lines_seen = 0
            self._ble_prox_seen = 0

    def _start_engine(self, port, demo):
        self._stop_existing_engine()
        self._connect_generation += 1
        generation = self._connect_generation
        self.logger.log(
            "connect",
            f"generation={generation} demo={demo} port={port!r} baud={self.baud} "
            f"serial_debug={self.serial_debug} serial_debug_log={self.serial_debug_log!r}",
        )
        self._set_status("Connecting…", TEXT_DIM)

        # Update connection-mode label
        if demo:
            self._conn_mode_var.set("MODE · DEMO")
            self._conn_mode_lbl.config(fg=TEXT_MED)
        else:
            self.connection_type_var.set("Direct")
            self._conn_mode_var.set("MODE · DIRECT USB")
            self._conn_mode_lbl.config(fg=ACCENT)

        self.root.after(8000, lambda gen=generation: self._connect_watchdog(gen))

        def task():
            try:
                self.logger.log("connect_thread", f"generation={generation} constructing GestureEngine")
                eng = GestureEngine(
                    port=port or "",
                    on_gesture=self._on_gesture_event,
                    demo=demo,
                    dual=True,
                    baud=self.baud,
                    serial_debug=self.serial_debug,
                    serial_debug_log=self.serial_debug_log,
                    serial_debug_bytes=self.serial_debug_bytes,
                    on_status=self._on_engine_status,
                    logger=self.logger,
                )
                if generation != self._connect_generation:
                    self.logger.log("connect_thread", f"generation={generation} stale before start; closing engine")
                    eng.stop()
                    return
                self.logger.log("connect_thread", f"generation={generation} starting engine")
                self.engine = eng
                eng.start()
                if generation != self._connect_generation:
                    self.logger.log("connect_thread", f"generation={generation} stale after start; closing engine")
                    eng.stop()
                    if self.engine is eng:
                        self.engine = None
                    return
                self.logger.log("connect_thread", f"generation={generation} engine.start returned")
                if demo:
                    self.root.after(0, lambda: self._set_status("DEMO", ACCENT))
            except Exception as exc:
                message = str(exc)
                self.logger.exception("connect_exception", exc)
                self._connect_generation += 1
                self._ui_events.put(("error", "Connection failed", message))
        threading.Thread(target=task, daemon=True).start()

    def _connect_watchdog(self, generation):
        if generation != self._connect_generation:
            return
        if self.engine is None:
            self.logger.log(
                "connect_watchdog",
                f"generation={generation} still no engine object; serial open or engine startup may be blocked",
            )
            self._set_status("Connection Timeout", REC_CLR)
            return
        frames_seen = getattr(self.engine, "frames_seen", 0)
        stats = ""
        try:
            stats = self.engine.source.stats()
        except Exception as exc:
            stats = f"stats unavailable: {exc}"
        self.logger.log(
            "connect_watchdog",
            f"generation={generation} frames_seen={frames_seen} status={self.status.get()!r} {stats}",
        )
        if frames_seen == 0 and self.status.get() == "Connecting…":
            self._set_status("Waiting for Frames", TEXT_MED)

    def _clean_status_text(self, text):
        """Make top-right status consistent for Serial and BLE."""
        mapping = {
            "NOT CONNECTED": "Not Connected",
            "CONNECTING…": "Connecting…",
            "BLE CONNECTING…": "Connecting…",
            "WAITING FOR FRAMES": "Waiting for Frames",
            "BLE WAITING FOR FRAMES": "Waiting for Frames",
            "RECEIVING FRAMES": "Receiving Frames",
            "BLE RECEIVING FRAMES": "Receiving Frames",
            "RECALIBRATED": "Ready",
            "BACKGROUND CALIBRATED": "Ready",
            "BLE CONNECTED": "Waiting for Frames",
            "SCANNING…": "Scanning…",
            "WAITING FOR HAND": "Waiting for Frames",
        }
        if not isinstance(text, str):
            return str(text)
        upper = text.upper()
        if upper in mapping:
            return mapping[upper]
        if upper.startswith("RECALIBRATING") or "CALIBRATING" in upper:
            return "Calibrating"
        if upper.startswith("ERROR") or upper.startswith("BLE ERROR"):
            return "Error"
        if upper.startswith("BLE RECEIVING FRAMES"):
            return "Receiving Frames"
        return text

    def _schedule_ready(self, delay_ms=900):
        """Set Ready after current loading/calibration status has had time to show."""
        self._ready_token += 1
        token = self._ready_token

        def mark_ready():
            if token != self._ready_token:
                return
            self._serial_is_calibrating = False
            with self._ble_state_lock:
                self._ble_is_calibrating = False
            current = self.status.get()
            if current not in {"Error", "BLE Error", "Disconnected", "Not Connected", "Connection Timeout"}:
                self._set_status("Ready", ACCENT)

        self.root.after(delay_ms, mark_ready)

    def _set_status(self, text, color):
        text = self._clean_status_text(text)
        if text == "Ready":
            color = ACCENT
        elif text == "Receiving Frames":
            color = ACCENT2
        elif text == "Calibrating":
            color = ACCENT
        elif text == "Scanning…":
            color = TEXT_DIM
        if getattr(self, "status", None) is not None and self.status.get() != text:
            self.logger.log("status", f"{self.status.get()} -> {text}")
        self.status.set(text)
        self._status_lbl.config(fg=color)
        dot_color = ACCENT if color in {ACCENT, ACCENT2} else (
            REC_CLR if color == REC_CLR else TEXT_DIM)
        self._status_dot.delete("all")
        self._status_dot.create_oval(0, 0, 8, 8, fill=dot_color, outline="")

    def _on_engine_status(self, text):
        self.logger.log("engine_status", text)
        clean = self._clean_status_text(text)

        # In dual/direct mode calibration_frames is usually 0, so
        # GestureEngine.recalibrate() may immediately emit RECALIBRATED. Keep
        # the top-right pill on Calibrating briefly so the button does not look
        # like it did nothing. The reset still happens immediately.
        if clean == "Ready" and self._serial_is_calibrating and time.time() < self._serial_calibrating_until:
            self.logger.log("engine_status_suppressed", "holding Calibrating before Ready")
            return

        color = ACCENT if clean == "Ready" else (ACCENT2 if clean == "Receiving Frames" else TEXT_MED)
        self._ui_events.put(("status", clean, color))
        if clean == "Receiving Frames" and not self._serial_is_calibrating:
            self._schedule_ready(delay_ms=900)
        elif clean == "Ready":
            self._serial_is_calibrating = False
        elif clean == "Calibrating":
            self._serial_is_calibrating = True
            self._serial_calibrating_until = time.time() + 0.9

    def _poll_ui_events(self):
        try:
            while True:
                event = self._ui_events.get_nowait()
                kind = event[0]
                if kind == "status":
                    _, text, color = event
                    self._set_status(text, color)
                elif kind == "gesture":
                    _, gesture_name, confidence = event
                    self._handle_gesture(gesture_name, confidence)
                elif kind == "error":
                    _, title, message = event
                    self._set_status("ERROR", REC_CLR)
                    messagebox.showerror(title, message)
        except queue.Empty:
            pass
        self.root.after(50, self._poll_ui_events)

    def _on_close(self):
        self.logger.log("app_close", "closing GesturePuck UI")
        self._stop_existing_engine()
        self.logger.close()
        self.root.destroy()

    # ── EVENTS ────────────────────────────────────────────────────────────────
    def _send_led_gesture(self, gesture_name: str):
        """Tell the ESP32 firmware to play the LED animation for a gesture.

        This is intentionally best-effort: if the current connection cannot
        send commands, gesture detection and macros still keep working.
        """
        cmd = f"LED_GESTURE,{gesture_name}"

        # BLE UART path: BLEConnection exposes send(text).
        conn = getattr(self, "connection", None)
        if conn is not None and hasattr(conn, "send"):
            try:
                conn.send(cmd)
                self.logger.log("led_command", f"ble {cmd}")
                return
            except Exception as exc:
                self.logger.exception("led_command_ble_error", exc)

        # Direct USB/COM path: GestureEngine owns the pyserial object.
        engine = getattr(self, "engine", None)
        source = getattr(engine, "source", None)
        ser = getattr(source, "ser", None)
        if ser is not None:
            try:
                ser.write((cmd + "\n").encode("utf-8"))
                ser.flush()
                self.logger.log("led_command", f"serial {cmd}")
            except Exception as exc:
                self.logger.exception("led_command_serial_error", exc)

    def _on_gesture_event(self, gesture_name: str, confidence: float):
        self._ui_events.put(("gesture", gesture_name, confidence))

    def _handle_gesture(self, gesture_name: str, confidence: float):
        self.logger.log("gesture", f"name={gesture_name} confidence={confidence:.3f}")
        self.last_gesture.set(f"{gesture_name} ({confidence:.0%})")
        self._send_led_gesture(gesture_name)

    # ── Pack mode check — runs BEFORE default app detection ──────────────
        handled = self.mode_manager.handle(gesture_name, self.root)
        if handled:
            if gesture_name == "hold_center":
                self._on_mode_change()  # refresh UI to show Default mode
            self.logger.log("gesture_pack", f"gesture={gesture_name} handled by pack")
            return

    # ── Default mode — active app detection ──────────────────────────────
        active_app = get_mapped_app()
        macro = (
            self.mappings.get(active_app, {}).get(gesture_name, {}).get("macro")
            or
            self.mappings.get("Global", {}).get(gesture_name, {}).get("macro")
        )
        self.logger.log("gesture_macro", f"active_app={active_app!r} macro={macro!r}")
        if macro:
            try:
                result = macro_runner.run_macro(macro)
                self.logger.log("macro_run", f"gesture={gesture_name} macro={macro!r} result={result!r}")
            except Exception as exc:
                self.logger.exception("macro_error", exc)
                self._set_status("MACRO ERROR", REC_CLR)
        else:
            self.logger.log("macro_missing", f"gesture={gesture_name} active_app={active_app!r}")

    def _on_event(self, msg):
        print(f"[DEBUG] received: {repr(msg)}")
        if "_DOWN" in msg:
            gesture = msg.replace("_DOWN", "").strip()
            self.root.after(0, lambda: self.last_gesture.set(gesture))
            self._send_led_gesture(gesture)
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
        self.logger.log("record_start", f"gesture={gesture} page={self.current_page}")
        for tok, prev_g in list(self._pending.items()):
            prev_entry = self._entries.get(prev_g)
            prev_mvar  = self._macro_vars.get(prev_g)
            if prev_entry and prev_mvar:
                prev_entry.config(fg=MACRO_CLR)
                if prev_mvar.get() == "recording…":
                    prev_mvar.set("")
            self._pending.pop(tok, None)
            self.logger.log("record_cancel_pending", f"token={tok} gesture={prev_g}")

        self.recorder.cancel()

        token = self._next_token
        self._next_token += 1
        self._pending[token] = gesture

        mvar  = self._macro_vars[gesture]
        entry = self._entries[gesture]
        mvar.set("recording…")
        entry.config(fg=REC_CLR)
        self._local_record_token = token
        self._local_chord = []
        self._local_held = set()
        self.root.focus_set()
        self.recorder.arm(token)
        if not self.recorder.running:
            self.logger.log("record_warning", "pynput listener is not running; using Tk focused-window fallback")

    def _canonical_tk_key(self, event):
        keysym = (event.keysym or "").lower()
        if len(getattr(event, "char", "") or "") == 1 and event.char.isprintable():
            char = event.char.lower()
            if char != " ":
                return char
        return TK_KEY_NAMES.get(keysym, keysym)

    def _on_local_key_press(self, event):
        if self._local_record_token is None:
            return
        name = self._canonical_tk_key(event)
        if not name:
            return
        if name not in self._local_held:
            self._local_held.add(name)
            if name not in self._local_chord:
                self._local_chord.append(name)
        self.logger.log(
            "record_local_key_press",
            f"token={self._local_record_token} key={name} chord={'+'.join(self._local_chord)}",
        )
        return "break"

    def _on_local_key_release(self, event):
        if self._local_record_token is None:
            return
        name = self._canonical_tk_key(event)
        if not name:
            return
        if name in MODIFIER_NAMES:
            return "break"
        token = self._local_record_token
        chord = "+".join(self._local_chord)
        self.logger.log("record_local_result", f"token={token} chord={chord}")
        self._finish_recording(token, chord, "tk")
        return "break"

    def _clear_local_recording(self):
        self._local_record_token = None
        self._local_chord = []
        self._local_held = set()

    def _finish_recording(self, token, chord, source):
        gesture = self._pending.pop(token, None)
        if gesture is None:
            self.logger.log("record_result_ignored", f"token={token} chord={chord!r} source={source}")
            return
        mvar = self._macro_vars.get(gesture)
        entry = self._entries.get(gesture)
        if mvar is None or entry is None:
            self.logger.log(
                "record_result_missing_widget",
                f"token={token} gesture={gesture} chord={chord!r} source={source}",
            )
            return
        mvar.set(chord)
        entry.config(fg=MACRO_CLR)
        if self._local_record_token == token:
            self._clear_local_recording()
        self.recorder.cancel()
        self._save(gesture, mvar.get(), self._label_vars.get(gesture, tk.StringVar(value=gesture)).get())
        self.logger.log(
            "record_result_applied",
            f"token={token} gesture={gesture} chord={chord!r} source={source}",
        )

    def _poll_recorder(self):
        try:
            while True:
                item = self.recorder.results.get_nowait()
                if len(item) == 2:
                    token, chord = item
                    source = "global"
                else:
                    token, chord, source = item
                self._finish_recording(token, chord, source)
        except queue.Empty:
            pass
        try:
            while True:
                error = self.recorder.errors.get_nowait()
                self.logger.log("recorder_error", error)
                self._set_status("RECORDER ERROR", REC_CLR)
        except queue.Empty:
            pass
        self.root.after(50, self._poll_recorder)
        self.root.after(1000, self._periodic_save)

    # ── CRUD ──────────────────────────────────────────────────────────────────
    def _periodic_save(self):
        for gesture, mvar in self._macro_vars.items():
            self.mappings.setdefault(self.current_page, {})\
                .setdefault(gesture, {})["macro"] = mvar.get()
        for gesture, lvar in self._label_vars.items():
            self.mappings.setdefault(self.current_page, {})\
                .setdefault(gesture, {})["label"] = lvar.get()
        store.save(self.mappings)

    def _delete(self, gesture):
        if messagebox.askyesno("Delete", f"Delete mapping for '{gesture}'?"):
            self.logger.log("mapping_delete", f"page={self.current_page} gesture={gesture}")
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
        existing = self.mappings.get(app_name, {})
        i, name = len(existing) + 1, ""
        while not name or name in existing:
            name = f"Gesture {i}"; i += 1
        self.mappings.setdefault(app_name, {})[name] = {
            "label": name, "macro": ""}
        store.save(self.mappings)
        self._render_page(app_name)

    def _show_pack_page(self, pack_id: str):
        for name, btn in self._nav_btns.items():
            btn.config(bg=SURFACE, fg=TEXT_MED)
        pack_btn = self._nav_btns.get(f"pack:{pack_id}")
        if pack_btn:
            pack_btn.config(bg=ACCENT_DIM, fg=TEXT)
        pack = self.mode_manager._packs.get(pack_id)
        if pack:
            render_pack_page(
                self.content_frame,
                pack,
                self.mode_manager,
                on_mode_change=self._on_mode_change,
                on_remove=self._on_pack_removed,
            )

    def _on_mode_change(self):
        pack = self.mode_manager.active_pack()
        if pack:
            self._set_status(f"{pack.icon} {pack.name.upper()} ACTIVE", ACCENT2)
        else:
            self._set_status("Default Mode", TEXT_MED)
        self._rebuild_ui_keep_page()

    def _on_pack_imported(self, pack):
        self.logger.log("pack_imported", f"id={pack.id!r} name={pack.name!r}")
        self._rebuild_ui_keep_page()
 
    def _on_pack_removed(self, pack_id: str):
        self.logger.log("pack_removed", f"id={pack_id!r}")
        self._rebuild_ui_keep_page(fallback_page="Global")
 
    def _rebuild_ui_keep_page(self, fallback_page: str | None = None):
        page = fallback_page or self.current_page
        for w in self.root.winfo_children():
            w.destroy()
        self._nav_btns = {}
        self._build_ui()
        self._show_page(page)


# ── ENTRY POINT ────────────────────────────────────────────────────────────────
def main():
    if not store.load():
        store.save({"Global": {"Tap": {"label": "Tap", "macro": ""}}})
    logger = UiLogger(resolve_ui_log_path("auto"))
    root = tk.Tk()
    check_macos_permissions(logger)
    GesturePuckApp(root, logger=logger)
    root.mainloop()


if __name__ == "__main__":
    main()
