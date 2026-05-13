#!/usr/bin/env python3
"""
LiDAR Gesture Studio v2
=======================

For DFRobot SEN0628 / Matrix LiDAR streamed from an ESP32 as binary packets, with
legacy CSV accepted as a fallback:

    MLD1 + seq + millis + read_us + 64 uint16 values + checksum
    FRAME,v0,v1,...,v63
    FRAME,seq,millis,v0,v1,...,v63

The script focuses on reliability rather than just a flashy demo:
  - per-pixel background calibration
  - invalid-value handling
  - temporal median + exponential smoothing
  - largest connected-component tracking
  - smoothed centroid/depth tracking
  - complete-stroke gesture classification with cooldown/hysteresis
  - event logging, CSV logging, and gesture sample recording
  - optional macro execution through pyautogui

Run:
    uv run python lidar_gesture_studio.py --port /dev/cu.usbserial-0001 --dual --max-mm 2000

Useful keys while the plot window is focused:
    r   recalibrate background; keep hand out of view
    c   clear tracks/events
    p   pause/resume
    m   toggle macro execution
    1   record next completed stroke as swipe_left
    2   record next completed stroke as swipe_right
    3   record next completed stroke as swipe_up
    4   record next completed stroke as swipe_down
    5   record next completed stroke as push
    6   record next completed stroke as pull
    7   record next completed stroke as hold_center
    0   cancel pending recording label
    q   quit

Notes:
  - Close PlatformIO Serial Monitor before running this script.
  - For best calibration, do not put your hand in the FoV until calibration completes.
  - Tune orientation first. If left/right or up/down are reversed, use --flip-x / --flip-y / --transpose.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import queue
import struct
import sys
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Deque, Dict, Iterable, List, Optional, Sequence, TextIO, Tuple

import numpy as np
import matplotlib.pyplot as plt
from matplotlib.animation import FuncAnimation

GRID = 8
N_PIXELS = GRID * GRID
BINARY_MAGIC = b"MLD1"
BINARY_PACKET_SIZE = len(BINARY_MAGIC) + 4 + 4 + 4 + N_PIXELS * 2 + 2
TEXT_LINE_BUFFER_LIMIT = 4096
GESTURE_NAMES = [
    "swipe_left",
    "swipe_right",
    "swipe_up",
    "swipe_down",
    "push",
    "pull",
    "hold_center",
]
LABEL_KEYS = {
    "1": "swipe_left",
    "2": "swipe_right",
    "3": "swipe_up",
    "4": "swipe_down",
    "5": "push",
    "6": "pull",
    "7": "hold_center",
}

# APDS-9930 hand-detection threshold (must match HAND_THRESHOLD in main.cpp)
HAND_THRESHOLD = 500

# ---------------------------------------------------------------------------
# Macro mapping
# ---------------------------------------------------------------------------
# Safe defaults: prints only. After tuning, change these and run with --enable-macros.
# Example macOS ideas:
#   "swipe_left":  {"type": "hotkey", "keys": ["control", "left"]},
#   "swipe_right": {"type": "hotkey", "keys": ["control", "right"]},
#   "push":        {"type": "press", "key": "space"},
#   "pull":        {"type": "press", "key": "escape"},
GESTURE_MACROS: Dict[str, Dict[str, object]] = {
    "swipe_left":  {"type": "print", "message": "SWIPE LEFT"},
    "swipe_right": {"type": "print", "message": "SWIPE RIGHT"},
    "swipe_up":    {"type": "print", "message": "SWIPE UP"},
    "swipe_down":  {"type": "print", "message": "SWIPE DOWN"},
    "push":        {"type": "print", "message": "PUSH"},
    "pull":        {"type": "print", "message": "PULL"},
    "hold_center": {"type": "print", "message": "HOLD CENTER"},
}

# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------

@dataclass
class FramePacket:
    host_t: float
    values: np.ndarray                  # 8x8 float mm
    seq: Optional[int] = None
    device_ms: Optional[int] = None
    read_us: Optional[int] = None
    protocol: str = "csv"


@dataclass
class DualFramePacket:
    """Carries frames from both ToF sensors plus APDS proximity reading."""
    host_t: float
    tof1: np.ndarray                    # 8x8 float mm — primary (used for gesture pipeline)
    tof2: np.ndarray                    # 8x8 float mm — companion view
    proximity: int = 0                  # APDS-9930 raw proximity value
    hand_present: bool = False          # True when proximity > HAND_THRESHOLD
    seq: Optional[int] = None
    device_ms: Optional[int] = None
    primary_sensor: int = 1

    def to_frame_packet(self) -> FramePacket:
        """Convert to single-sensor FramePacket for the gesture pipeline."""
        protocol = "dual-tof" if self.primary_sensor == 1 else "dual-tof2-fallback"
        return FramePacket(
            host_t=self.host_t,
            values=self.tof1.copy(),
            seq=self.seq,
            device_ms=self.device_ms,
            protocol=protocol,
        )


@dataclass
class Measurement:
    t: float
    raw: np.ndarray                     # display-safe 8x8 mm
    filtered: np.ndarray                # filtered 8x8 mm
    valid: np.ndarray                   # valid pixel mask
    foreground: np.ndarray              # 0..1 likelihood map
    component: np.ndarray               # selected component mask
    visible: bool
    x: float = math.nan
    y: float = math.nan
    z: float = math.nan
    nearest: float = math.nan
    area: int = 0
    mass: float = 0.0
    quality: float = 0.0
    status: str = ""
    seq: Optional[int] = None
    device_ms: Optional[int] = None
    read_us: Optional[int] = None
    protocol: str = ""
    field_dx: float = 0.0
    field_dy: float = 0.0
    field_quality: float = 0.0


@dataclass
class TrackSample:
    t: float
    x: float
    y: float
    z: float
    area: int
    mass: float
    quality: float
    field_dx: float = 0.0
    field_dy: float = 0.0
    field_quality: float = 0.0


@dataclass
class Stroke:
    samples: List[TrackSample] = field(default_factory=list)
    started_t: float = 0.0
    last_seen_t: float = 0.0
    last_motion_t: float = 0.0
    missing_since_t: Optional[float] = None
    motion_peak: float = 0.0
    motion_path: float = 0.0
    best_name: Optional[str] = None
    best_score: float = 0.0
    best_details: str = ""
    best_features: Dict[str, float] = field(default_factory=dict)

    def append(self, s: TrackSample, motion_energy: float = 0.0) -> None:
        if not self.samples:
            self.started_t = s.t
            self.last_motion_t = s.t
        self.samples.append(s)
        self.last_seen_t = s.t
        self.missing_since_t = None
        self.motion_peak = max(self.motion_peak, motion_energy)
        self.motion_path += max(0.0, motion_energy)

    @property
    def duration(self) -> float:
        if len(self.samples) < 2:
            return 0.0
        return self.samples[-1].t - self.samples[0].t


@dataclass
class GestureEvent:
    name: str
    t: float
    confidence: float
    details: str
    stroke: Optional[Stroke] = None


@dataclass
class ComponentCandidate:
    mask: np.ndarray
    score: float
    area: int
    x: float
    y: float

# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------

def clamp01(x: float) -> float:
    if not np.isfinite(x):
        return 0.0
    return max(0.0, min(1.0, float(x)))


def robust_mean(values: Sequence[float]) -> float:
    arr = np.asarray(values, dtype=float)
    arr = arr[np.isfinite(arr)]
    if arr.size == 0:
        return math.nan
    if arr.size < 4:
        return float(np.mean(arr))
    lo, hi = np.percentile(arr, [15, 85])
    trimmed = arr[(arr >= lo) & (arr <= hi)]
    if trimmed.size == 0:
        return float(np.mean(arr))
    return float(np.mean(trimmed))


def finite_or_none(value: float) -> Optional[float]:
    if not np.isfinite(value):
        return None
    return float(value)


def round_float(value: float, digits: int = 4) -> Optional[float]:
    if not np.isfinite(value):
        return None
    return round(float(value), digits)


def rounded_list(values: Iterable[float], digits: int) -> List[Optional[float]]:
    return [round_float(float(v), digits) for v in values]


def sanitize_filename(value: str) -> str:
    cleaned = "".join(ch if ch.isalnum() or ch in ("-", "_") else "_" for ch in value.strip())
    return cleaned or "unlabeled"


def serializable_args(args: argparse.Namespace) -> Dict[str, object]:
    out: Dict[str, object] = {}
    for key, value in vars(args).items():
        if isinstance(value, Path):
            out[key] = str(value)
        elif isinstance(value, (str, int, float, bool)) or value is None:
            out[key] = value
        else:
            out[key] = str(value)
    return out


def resolve_serial_debug_path(value: Optional[str]) -> Optional[Path]:
    if value is None:
        return None
    if value.lower() == "auto":
        stamp = time.strftime("%Y%m%d_%H%M%S", time.localtime())
        return Path("logs") / f"serial_debug_{stamp}.log"
    path = Path(value).expanduser()
    if path.exists() and path.is_dir():
        stamp = time.strftime("%Y%m%d_%H%M%S", time.localtime())
        return path / f"serial_debug_{stamp}.log"
    return path


def apply_orientation(frame: np.ndarray, *, flip_x: bool, flip_y: bool, transpose: bool) -> np.ndarray:
    out = frame
    if transpose:
        out = out.T
    if flip_x:
        out = np.fliplr(out)
    if flip_y:
        out = np.flipud(out)
    return out.copy()


def parse_frame_line(line: str) -> Optional[FramePacket]:
    """
    Accepts:
      FRAME,v0,...,v63
      FRAME,t_ms,v0,...,v63
      FRAME,seq,t_ms,v0,...,v63

    The parser deliberately takes the last 64 comma-separated fields as pixel values,
    so adding metadata at the front will not break the visualizer.
    """
    line = line.strip()
    if not line.startswith("FRAME,"):
        return None
    fields = line.split(",")[1:]
    if len(fields) < N_PIXELS:
        return None

    pixel_fields = fields[-N_PIXELS:]
    meta_fields = fields[:-N_PIXELS]

    try:
        values = np.array([float(x) for x in pixel_fields], dtype=float).reshape((GRID, GRID))
    except ValueError:
        return None

    seq = None
    device_ms = None
    if len(meta_fields) == 1:
        try:
            device_ms = int(float(meta_fields[0]))
        except ValueError:
            pass
    elif len(meta_fields) >= 2:
        try:
            seq = int(float(meta_fields[-2]))
        except ValueError:
            pass
        try:
            device_ms = int(float(meta_fields[-1]))
        except ValueError:
            pass

    return FramePacket(host_t=time.time(), values=values, seq=seq, device_ms=device_ms, protocol="csv")


def parse_binary_packet(packet: bytes) -> Optional[FramePacket]:
    if len(packet) != BINARY_PACKET_SIZE or not packet.startswith(BINARY_MAGIC):
        return None
    expected = struct.unpack_from("<H", packet, BINARY_PACKET_SIZE - 2)[0]
    actual = sum(packet[:-2]) & 0xFFFF
    if actual != expected:
        return None

    seq, device_ms, read_us = struct.unpack_from("<III", packet, len(BINARY_MAGIC))
    values = np.array(
        struct.unpack_from("<" + "H" * N_PIXELS, packet, len(BINARY_MAGIC) + 12),
        dtype=float,
    ).reshape((GRID, GRID))
    return FramePacket(
        host_t=time.time(),
        values=values,
        seq=seq,
        device_ms=device_ms,
        read_us=read_us,
        protocol="bin",
    )


class SerialDebugLogger:
    """Small thread-safe logger for serial bytes and parser decisions."""

    def __init__(
        self,
        *,
        enabled: bool = False,
        path: Optional[Path] = None,
        log_reads: bool = False,
        max_preview: int = 180,
    ):
        self.enabled = enabled or path is not None
        self.path = path
        self.log_reads = log_reads
        self.max_preview = max(20, max_preview)
        self._lock = threading.Lock()
        self._fh: Optional[TextIO] = None
        if self.path is not None:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self._fh = self.path.open("a", encoding="utf-8")

    def _timestamp(self) -> str:
        now = time.time()
        base = time.strftime("%H:%M:%S", time.localtime(now))
        return f"{base}.{int((now % 1.0) * 1000):03d}"

    def _preview_bytes(self, raw: bytes) -> str:
        sample = raw[: self.max_preview]
        text = sample.decode("utf-8", errors="replace")
        text = text.replace("\r", "\\r").replace("\n", "\\n")
        suffix = f" (+{len(raw) - len(sample)} bytes)" if len(raw) > len(sample) else ""
        return f"{len(raw)} bytes ascii={text!r}{suffix}"

    def log(self, event: str, message: str) -> None:
        if not self.enabled:
            return
        line = f"[{self._timestamp()}] {event}: {message}"
        with self._lock:
            print(line, file=sys.stderr, flush=True)
            if self._fh is not None:
                self._fh.write(line + "\n")
                self._fh.flush()

    def raw_read(self, raw: bytes) -> None:
        if self.enabled and self.log_reads:
            self.log("rx", self._preview_bytes(raw))

    def raw_line(self, raw: bytes) -> None:
        if self.enabled:
            self.log("line", self._preview_bytes(raw.rstrip(b"\r\n")))

    def close(self) -> None:
        with self._lock:
            if self._fh is not None:
                self._fh.close()
                self._fh = None


def frame_stats(values: np.ndarray) -> str:
    finite = values[np.isfinite(values)]
    if finite.size == 0:
        return "no finite pixels"
    return (
        f"finite={finite.size}/{values.size} "
        f"min={float(np.min(finite)):.0f} max={float(np.max(finite)):.0f} "
        f"mean={float(np.mean(finite)):.0f}"
    )

# ---------------------------------------------------------------------------
# Data sources
# ---------------------------------------------------------------------------

class SerialFrameSource:
    def __init__(self, port: str, baud: int, serial_debug: Optional[SerialDebugLogger] = None):
        try:
            import serial  # type: ignore
        except ImportError as exc:
            raise SystemExit(
                "pyserial is not installed. Run:\n"
                "  source lidar-venv/bin/activate\n"
                "  python3 -m pip install pyserial"
            ) from exc
        self.port = port
        self.baud = baud
        self.serial_debug = serial_debug or SerialDebugLogger()
        self.ser = serial.Serial(port, baud, timeout=0.05)
        self.q: "queue.Queue[FramePacket]" = queue.Queue(maxsize=5)
        self.stop_event = threading.Event()
        self.thread = threading.Thread(target=self._read_loop, daemon=True)
        self.buf = bytearray()
        self.bytes_seen = 0
        self.lines_seen = 0
        self.frames_seen = 0
        self.binary_frames = 0
        self.csv_frames = 0
        self.bad_lines = 0
        self.checksum_errors = 0
        self.serial_errors = 0
        self.last_error = ""
        self.last_seq: Optional[int] = None
        self.dropped_seq = 0
        self.read_us_history: Deque[int] = deque(maxlen=120)
        self.last_event = "opened serial port"

    def _note(self, event: str, message: str, *, remember: bool = True) -> None:
        if remember:
            self.last_event = f"{event}: {message}"[:90]
        self.serial_debug.log(event, message)

    def start(self) -> None:
        time.sleep(1.0)
        self._note("serial", f"starting reader for {self.port} at {self.baud} baud")
        self.thread.start()

    def _read_loop(self) -> None:
        while not self.stop_event.is_set():
            try:
                waiting = self.ser.in_waiting
                raw = self.ser.read(waiting or 1)
            except Exception as exc:
                self.serial_errors += 1
                self.last_error = str(exc)
                time.sleep(0.05)
                continue
            if not raw:
                continue
            self.bytes_seen += len(raw)
            self.serial_debug.raw_read(raw)
            self.buf.extend(raw)
            self._drain_buffer()

    def _drain_buffer(self) -> None:
        while self.buf:
            magic_at = self.buf.find(BINARY_MAGIC)
            newline_at = self.buf.find(b"\n")

            if magic_at == -1 and newline_at == -1:
                if len(self.buf) > TEXT_LINE_BUFFER_LIMIT:
                    self.bad_lines += 1
                    keep = max(0, len(BINARY_MAGIC) - 1)
                    dropped_count = len(self.buf) - keep
                    self._note("drain", f"dropped {dropped_count} bytes from oversized unterminated text/binary buffer")
                    del self.buf[:-keep]
                return

            if magic_at != -1 and (newline_at == -1 or magic_at < newline_at):
                if magic_at > 0:
                    self._consume_text_prefix(bytes(self.buf[:magic_at]))
                    del self.buf[:magic_at]
                if len(self.buf) < BINARY_PACKET_SIZE:
                    return
                raw_packet = bytes(self.buf[:BINARY_PACKET_SIZE])
                packet = parse_binary_packet(raw_packet)
                if packet is None:
                    self.checksum_errors += 1
                    self._note("binary", "MLD1 checksum/packet parse failed; skipping one byte")
                    del self.buf[0]
                    continue
                del self.buf[:BINARY_PACKET_SIZE]
                self.binary_frames += 1
                self._note("binary", f"MLD1 frame seq={packet.seq} {frame_stats(packet.values)}")
                self._handle_packet(packet)
                continue

            if newline_at != -1:
                raw_line = bytes(self.buf[: newline_at + 1])
                del self.buf[: newline_at + 1]
                self._handle_line(raw_line)
                continue

    def _consume_text_prefix(self, raw: bytes) -> None:
        for line in raw.splitlines():
            if line.strip():
                self._handle_line(line + b"\n")

    def _handle_line(self, raw: bytes) -> None:
        self.lines_seen += 1
        self.serial_debug.raw_line(raw)
        line = raw.decode("utf-8", errors="ignore")
        packet = parse_frame_line(line)
        if packet is None:
            if line.strip() and not line.startswith("#"):
                self.bad_lines += 1
                self._note("text", f"ignored non-FRAME line: {line.strip()[:100]!r}")
            return
        self.csv_frames += 1
        self._note("text", f"FRAME csv parsed seq={packet.seq} {frame_stats(packet.values)}")
        self._handle_packet(packet)

    def _handle_packet(self, packet: FramePacket) -> None:
        if packet.seq is not None:
            if self.last_seq is not None and packet.seq > self.last_seq + 1:
                self.dropped_seq += packet.seq - self.last_seq - 1
            self.last_seq = packet.seq
        if packet.read_us is not None:
            self.read_us_history.append(packet.read_us)

        self.frames_seen += 1
        self._note("emit", f"queued frame #{self.frames_seen} protocol={packet.protocol} seq={packet.seq}")
        if self.q.full():
            try:
                self.q.get_nowait()
            except queue.Empty:
                pass
        try:
            self.q.put_nowait(packet)
        except queue.Full:
            pass

    def read_latest(self) -> Optional[FramePacket]:
        latest = None
        while True:
            try:
                latest = self.q.get_nowait()
            except queue.Empty:
                break
        return latest

    def stats(self) -> str:
        if self.read_us_history:
            read_ms = sum(self.read_us_history) / len(self.read_us_history) / 1000.0
            read_part = f" read={read_ms:.1f}ms"
        else:
            read_part = ""
        err_part = f" serial_err={self.serial_errors}" if self.serial_errors else ""
        last_part = f" last={self.last_event}" if self.last_event else ""
        return (
            f"serial bytes={self.bytes_seen} frames={self.frames_seen} bin={self.binary_frames} csv={self.csv_frames} "
            f"bad={self.bad_lines} csum={self.checksum_errors} seq_drop={self.dropped_seq}"
            f"{read_part}{err_part}{last_part}"
        )

    def close(self) -> None:
        self.stop_event.set()
        try:
            self.thread.join(timeout=0.5)
        except Exception:
            pass
        try:
            self.ser.close()
        except Exception:
            pass
        self.serial_debug.close()


class DemoFrameSource:
    """Fake 8x8 frames for testing without hardware."""
    def __init__(self, max_mm: int):
        self.max_mm = max_mm
        self.start_t = time.time()
        self.last_t = 0.0
        self.frames_seen = 0
        self.seq = 0

    def start(self) -> None:
        pass

    def read_latest(self) -> Optional[FramePacket]:
        now = time.time()
        if now - self.last_t < 0.033:
            return None
        self.last_t = now
        self.frames_seen += 1
        self.seq += 1

        t = now - self.start_t
        # Repeating demo: lateral swipe, then push/pull, then hold.
        phase = (t % 9.0)
        if phase < 3.0:
            x0 = 6.5 - 5.5 * (phase / 3.0)
            y0 = 3.5 + 0.2 * math.sin(t * 8)
            z0 = 850
        elif phase < 6.0:
            p = (phase - 3.0) / 3.0
            x0 = 3.5
            y0 = 3.5
            z0 = 1200 - 550 * math.sin(p * math.pi)
        else:
            x0 = 3.5 + 0.2 * math.sin(t * 2)
            y0 = 3.5 + 0.2 * math.cos(t * 2)
            z0 = 900

        yy, xx = np.mgrid[0:GRID, 0:GRID]
        blob = np.exp(-(((xx - x0) ** 2 + (yy - y0) ** 2) / 1.6))
        background = np.full((GRID, GRID), float(self.max_mm))
        frame = background - blob * (self.max_mm - z0)
        frame += np.random.normal(0, 18, size=(GRID, GRID))
        frame = np.clip(frame, 20, self.max_mm)
        return FramePacket(host_t=now, values=frame, seq=self.seq, device_ms=int((now - self.start_t) * 1000), protocol="demo")

    def stats(self) -> str:
        return f"demo frames={self.frames_seen}"

    def close(self) -> None:
        pass

# ---------------------------------------------------------------------------
# Dual-sensor serial source  (GesturePuck firmware with APDS-9930 + 2x ToF)
# ---------------------------------------------------------------------------

# The preferred firmware stream sends:
#   MLD1 packets  — ToF #1 binary frames (same format as original single-sensor firmware)
#   MLD2 packets  — ToF #2 binary frames (same format, different magic)
#   FRAME,seq,millis,64 values — compact ToF #1 CSV frames
#   FRAME2,seq,millis,64 values — compact ToF #2 CSV frames
#   #PROX,<val>,<0|1>\n  — APDS-9930 proximity line
#
# The current bring-up sketch also prints human-readable debug text:
#   APDS-9930 proximity: <val>
#   Hand present: YES|NO
#   ToF #1 frame:
#   <8 rows of 8 tab/space-separated values>
#   ToF #2 frame:
#   <8 rows of 8 tab/space-separated values>
#
# Packet layout for both MLD1 and MLD2 (146 bytes total):
#   4  bytes  magic ("MLD1" or "MLD2")
#   4  bytes  seq        uint32 LE
#   4  bytes  millis     uint32 LE
#   4  bytes  read_us    uint32 LE
#   128 bytes 64×uint16  pixel distances LE
#   2  bytes  checksum   uint16 LE (sum of all preceding bytes & 0xFFFF)

BINARY_MAGIC2 = b"MLD2"   # ToF #2 magic; BINARY_MAGIC (MLD1) already defined above

class DualSensorSerialFrameSource:
    """
    Reads the mixed binary + ASCII stream from the GesturePuck dual-sensor firmware.

    MLD1 binary packets → ToF #1 (gesture pipeline primary)
    MLD2 binary packets → ToF #2 (companion heatmap)
    #PROX text lines    → APDS-9930 proximity / hand-present flag
    FRAME CSV lines     → compact one-sensor bring-up stream
    debug text tables   → fallback parser for the serial-monitor bring-up sketch

    A DualFramePacket is emitted each time a matching ToF #1 + ToF #2 pair
    arrives with the same sequence number.  If the sequence numbers differ by
    more than one (e.g. a sensor read failed) the most recent frame from each
    sensor is used so the display never stalls.
    """

    def __init__(
        self,
        port: str,
        baud: int = 115200,
        serial_debug: Optional[SerialDebugLogger] = None,
    ):
        try:
            import serial  # type: ignore
        except ImportError as exc:
            raise SystemExit(
                "pyserial is not installed. Run:\n"
                "  pip install pyserial"
            ) from exc
        self.port = port
        self.baud = baud
        self.serial_debug = serial_debug or SerialDebugLogger()
        self.ser = serial.Serial(port, baud, timeout=0.05)
        self.q: "queue.Queue[DualFramePacket]" = queue.Queue(maxsize=5)
        self.stop_event = threading.Event()
        self.thread = threading.Thread(target=self._read_loop, daemon=True)
        self.buf = bytearray()

        # Latest parsed frames from each sensor. The visualizer can run with one
        # sensor; the second sensor is a companion view when available.
        self._pending1: Optional[FramePacket] = None
        self._pending2: Optional[FramePacket] = None

        # Latest APDS state
        self._proximity: int = 0
        self._hand_present: bool = False
        self._debug_frame_sensor: Optional[int] = None
        self._debug_rows: List[List[float]] = []
        self._debug_seq = 0
        self._debug_current_seq: Optional[int] = None

        # Stats
        self.frames_seen = 0
        self.bytes_seen = 0
        self.raw_reads = 0
        self.discarded_bytes = 0
        self.tof1_frames = 0
        self.tof2_frames = 0
        self.single_sensor_frames = 0
        self.prox_lines = 0
        self.hand_lines = 0
        self.text_lines = 0
        self.text_frames = 0
        self.checksum_errors = 0
        self.serial_errors = 0
        self.bad_lines = 0
        self.last_error = ""
        self.last_event = "opened serial port"

    def _note(self, event: str, message: str, *, remember: bool = True) -> None:
        if remember:
            self.last_event = f"{event}: {message}"[:90]
        self.serial_debug.log(event, message)

    def start(self) -> None:
        time.sleep(1.0)
        self._note("serial", f"starting dual reader for {self.port} at {self.baud} baud")
        self.thread.start()

    def _read_loop(self) -> None:
        while not self.stop_event.is_set():
            try:
                waiting = self.ser.in_waiting
                raw = self.ser.read(waiting or 1)
            except Exception as exc:
                self.serial_errors += 1
                self.last_error = str(exc)
                time.sleep(0.05)
                continue
            if not raw:
                continue
            self.bytes_seen += len(raw)
            self.raw_reads += 1
            self.serial_debug.raw_read(raw)
            self.buf.extend(raw)
            self._drain()

    def _drain(self) -> None:
        """
        Drain the byte buffer, handling interleaved binary packets and ASCII lines.

        Binary packets start with MLD1 or MLD2.
        ASCII status/debug lines end with '\\n'.
        Everything else is discarded.
        """
        while self.buf:
            magic1_at = self.buf.find(BINARY_MAGIC)
            magic2_at = self.buf.find(BINARY_MAGIC2)
            newline_at = self.buf.find(b"\n")
            magic_candidates = [p for p in (magic1_at, magic2_at) if p != -1]
            nearest_magic = min(magic_candidates) if magic_candidates else -1

            if nearest_magic == 0:
                if len(self.buf) < BINARY_PACKET_SIZE:
                    return   # wait for more bytes
                raw_pkt = bytes(self.buf[:BINARY_PACKET_SIZE])
                # Verify checksum
                expected = struct.unpack_from("<H", raw_pkt, BINARY_PACKET_SIZE - 2)[0]
                actual = sum(raw_pkt[:-2]) & 0xFFFF
                if actual != expected:
                    self.checksum_errors += 1
                    self._note(
                        "binary",
                        f"{raw_pkt[:4].decode('ascii', errors='replace')} checksum mismatch "
                        f"expected={expected} actual={actual}; skipping one byte",
                    )
                    del self.buf[0]   # skip one byte and retry
                    continue
                del self.buf[:BINARY_PACKET_SIZE]
                self._handle_binary(raw_pkt)
                continue

            if newline_at != -1 and (nearest_magic == -1 or newline_at < nearest_magic):
                raw_line = bytes(self.buf[: newline_at + 1])
                del self.buf[: newline_at + 1]
                self._handle_text_line(raw_line)
                continue

            if nearest_magic > 0:
                # Text or noise before a binary packet without a complete newline.
                # Drop it so the next loop can parse the packet at buffer start.
                dropped = bytes(self.buf[:nearest_magic])
                self.discarded_bytes += len(dropped)
                self._note("drain", f"dropped {len(dropped)} bytes before binary magic")
                del self.buf[:nearest_magic]
                continue

            if newline_at == -1:
                # Wait for the rest of an ASCII line. CSV FRAME lines are longer
                # than binary packets, so do not trim at BINARY_PACKET_SIZE here.
                if len(self.buf) > TEXT_LINE_BUFFER_LIMIT:
                    keep = len(BINARY_MAGIC) - 1
                    dropped_count = len(self.buf) - keep
                    self.discarded_bytes += dropped_count
                    self._note("drain", f"dropped {dropped_count} bytes from oversized unterminated text/binary buffer")
                    del self.buf[:-keep]
                return

    def _handle_binary(self, raw: bytes) -> None:
        magic = raw[:4]
        seq, device_ms, read_us = struct.unpack_from("<III", raw, 4)
        values = np.array(
            struct.unpack_from("<" + "H" * N_PIXELS, raw, 16),
            dtype=float,
        ).reshape((GRID, GRID))
        pkt = FramePacket(
            host_t=time.time(),
            values=values,
            seq=seq,
            device_ms=device_ms,
            read_us=read_us,
            protocol="bin",
        )
        if magic == BINARY_MAGIC:
            self._pending1 = pkt
            self.tof1_frames += 1
            self._note("binary", f"MLD1 seq={seq} read_us={read_us} {frame_stats(values)}")
            self._try_emit(primary_sensor=1)
        else:
            self._pending2 = pkt
            self.tof2_frames += 1
            self._note("binary", f"MLD2 seq={seq} read_us={read_us} {frame_stats(values)}")
            if self._pending1 is None:
                self._try_emit(primary_sensor=2)

    def _handle_prox_line(self, line: str) -> None:
        # Format: #PROX,<proximity>,<0|1>
        try:
            parts = line.split(",")
            self._proximity = int(parts[1])
            self._hand_present = int(parts[2]) == 1
            self.prox_lines += 1
            self._note("apds", f"#PROX proximity={self._proximity} hand={self._hand_present}")
        except (IndexError, ValueError):
            self.bad_lines += 1
            self._note("bad-line", f"invalid #PROX line: {line[:120]!r}")

    def _handle_text_line(self, raw: bytes) -> None:
        """Handle #PROX lines and the current Serial Monitor debug table format."""
        self.text_lines += 1
        self.serial_debug.raw_line(raw)
        line = raw.decode("utf-8", errors="ignore").strip()
        if not line:
            return

        if line.startswith("#PROX,"):
            self._handle_prox_line(line)
            return

        if line.startswith(("#ERR,", "#INFO,")):
            self._note("firmware", line)
            return

        if line.startswith(("FRAME,", "FRAME1,", "FRAME2,")):
            self._handle_csv_frame_line(line)
            return

        if line.startswith("APDS-9930 proximity:"):
            try:
                self._proximity = int(line.split(":", 1)[1].strip())
                self.prox_lines += 1
                self._note("apds", f"serial-monitor proximity={self._proximity}")
            except ValueError:
                self.bad_lines += 1
                self._note("bad-line", f"invalid APDS proximity line: {line[:120]!r}")
            return

        if line.startswith("Hand present:"):
            value = line.split(":", 1)[1].strip().lower()
            if value in ("yes", "1", "true"):
                self._hand_present = True
                self.hand_lines += 1
                self._note("apds", "hand_present=True")
            elif value in ("no", "0", "false"):
                self._hand_present = False
                self.hand_lines += 1
                self._note("apds", "hand_present=False")
            else:
                self.bad_lines += 1
                self._note("bad-line", f"invalid hand-present line: {line[:120]!r}")
            return

        if line.startswith("ToF #1 frame:"):
            self._note("tof-header", "starting ToF #1 debug text frame")
            self._begin_debug_frame(1)
            return

        if line.startswith("ToF #2 frame:"):
            self._note("tof-header", "starting ToF #2 debug text frame")
            self._begin_debug_frame(2)
            return

        if self._debug_frame_sensor is not None:
            row = self._parse_debug_row(line)
            if row is None:
                sensor = self._debug_frame_sensor
                self._cancel_debug_frame()
                self.bad_lines += 1
                self._note("bad-row", f"ToF #{sensor} row did not have 8 numeric values: {line[:120]!r}")
                return
            self._debug_rows.append(row)
            self._note(
                "tof-row",
                f"ToF #{self._debug_frame_sensor} row {len(self._debug_rows)}/8 "
                f"first={row[0]:.0f} last={row[-1]:.0f}",
            )
            if len(self._debug_rows) == GRID:
                self._finish_debug_frame()
            return

        # Ignore expected bring-up chatter that is useful in Serial Monitor but not data.
        if (
            line.startswith("=")
            or line.startswith("Starting ")
            or line.startswith("Scanning ")
            or line.startswith("Found I2C device")
            or line.startswith("I2C scan done")
            or line.startswith("Setup complete")
            or line.startswith("APDS:")
            or line.startswith("ToF #")
            or line.startswith("Skipping ToF read")
        ):
            self._note("text", f"ignored bring-up line: {line[:120]!r}", remember=False)
            return

        self._note("text", f"unrecognized line outside a ToF table: {line[:120]!r}")

    def _handle_csv_frame_line(self, line: str) -> None:
        sensor = 2 if line.startswith("FRAME2,") else 1
        if line.startswith(("FRAME1,", "FRAME2,")):
            parse_line = "FRAME," + line.split(",", 1)[1]
        else:
            parse_line = line
        pkt = parse_frame_line(parse_line)
        if pkt is None:
            self.bad_lines += 1
            self._note("bad-line", f"invalid FRAME csv line: {line[:120]!r}")
            return
        pkt.protocol = f"csv-tof{sensor}"
        if sensor == 1:
            self._pending1 = pkt
            self.tof1_frames += 1
        else:
            self._pending2 = pkt
            self.tof2_frames += 1
        self.text_frames += 1
        self._note("tof-frame", f"ToF #{sensor} csv frame parsed seq={pkt.seq} {frame_stats(pkt.values)}")
        if sensor == 1 or self._pending1 is None:
            self._try_emit(primary_sensor=sensor)

    def _begin_debug_frame(self, sensor: int) -> None:
        if sensor == 1:
            self._debug_seq += 1
            self._debug_current_seq = self._debug_seq
        elif self._debug_current_seq is None:
            self._debug_seq += 1
            self._debug_current_seq = self._debug_seq
        self._debug_frame_sensor = sensor
        self._debug_rows = []

    def _cancel_debug_frame(self) -> None:
        self._debug_frame_sensor = None
        self._debug_rows = []

    def _parse_debug_row(self, line: str) -> Optional[List[float]]:
        fields = line.replace(",", " ").split()
        if len(fields) != GRID:
            return None
        try:
            return [float(x) for x in fields]
        except ValueError:
            return None

    def _finish_debug_frame(self) -> None:
        if self._debug_frame_sensor is None or len(self._debug_rows) != GRID:
            self._cancel_debug_frame()
            return
        values = np.asarray(self._debug_rows, dtype=float).reshape((GRID, GRID))
        pkt = FramePacket(
            host_t=time.time(),
            values=values,
            seq=self._debug_current_seq,
            device_ms=None,
            read_us=None,
            protocol="debug-text",
        )
        sensor = self._debug_frame_sensor
        if sensor == 1:
            self._pending1 = pkt
            self.tof1_frames += 1
        else:
            self._pending2 = pkt
            self.tof2_frames += 1
        self.text_frames += 1
        self._note(
            "tof-frame",
            f"ToF #{sensor} debug text frame parsed seq={pkt.seq} {frame_stats(values)}",
        )
        self._cancel_debug_frame()
        if sensor == 1 or self._pending1 is None:
            self._try_emit(primary_sensor=sensor)
        if sensor == 2:
            self._debug_current_seq = None

    def _try_emit(self, primary_sensor: int = 1) -> None:
        """Emit a DualFramePacket from the latest available ToF data."""
        if self._pending1 is None and self._pending2 is None:
            return

        if self._pending1 is not None:
            primary = self._pending1
            tof1 = self._pending1.values
            tof2 = self._pending2.values if self._pending2 is not None else np.full_like(tof1, np.nan)
            primary_sensor = 1
        else:
            primary = self._pending2
            tof1 = self._pending2.values
            tof2 = self._pending2.values
            primary_sensor = 2

        if self._pending1 is None or self._pending2 is None:
            self.single_sensor_frames += 1

        mode = "paired" if self._pending1 is not None and self._pending2 is not None else "single-sensor"
        dual = DualFramePacket(
            host_t=primary.host_t,
            tof1=tof1,
            tof2=tof2,
            proximity=self._proximity,
            hand_present=self._hand_present,
            seq=primary.seq,
            device_ms=primary.device_ms,
            primary_sensor=primary_sensor,
        )
        self.frames_seen += 1
        self._note(
            "emit",
            f"{mode} dual packet #{self.frames_seen} primary=ToF#{primary_sensor} "
            f"seq={primary.seq} proximity={self._proximity} hand={self._hand_present}",
        )
        if self.q.full():
            try:
                self.q.get_nowait()
            except queue.Empty:
                pass
        try:
            self.q.put_nowait(dual)
        except queue.Full:
            pass

    def read_latest(self) -> Optional[DualFramePacket]:
        latest = None
        while True:
            try:
                latest = self.q.get_nowait()
            except queue.Empty:
                break
        return latest

    def stats(self) -> str:
        last_part = f" last={self.last_event}" if self.last_event else ""
        return (
            f"dual bytes={self.bytes_seen} reads={self.raw_reads} frames={self.frames_seen} "
            f"tof1={self.tof1_frames} tof2={self.tof2_frames} "
            f"single={self.single_sensor_frames} "
            f"text={self.text_frames}/{self.text_lines} prox={self.prox_lines} hand={self.hand_lines} "
            f"bad={self.bad_lines} csum_err={self.checksum_errors} "
            f"drop_bytes={self.discarded_bytes} serial_err={self.serial_errors}{last_part}"
        )

    def close(self) -> None:
        self.stop_event.set()
        try:
            self.thread.join(timeout=0.5)
        except Exception:
            pass
        try:
            self.ser.close()
        except Exception:
            pass
        self.serial_debug.close()

class SignalPipeline:
    def __init__(self, args: argparse.Namespace):
        self.args = args
        self.temporal: Deque[np.ndarray] = deque(maxlen=max(1, args.median_window))
        self.ema: Optional[np.ndarray] = None
        self.background: Optional[np.ndarray] = None
        self.background_noise: Optional[np.ndarray] = None
        self.calibration: List[np.ndarray] = []
        self.calibrating = args.calibration_frames > 0
        self.smooth_track: Optional[Tuple[float, float, float]] = None
        self.prev_motion_map: Optional[np.ndarray] = None
        self.last_visible_t = 0.0

    def start_calibration(self) -> None:
        self.temporal.clear()
        self.ema = None
        self.background = None
        self.background_noise = None
        self.calibration = []
        self.calibrating = self.args.calibration_frames > 0
        self.smooth_track = None
        self.prev_motion_map = None

    def _valid_mask(self, frame: np.ndarray) -> np.ndarray:
        valid = np.isfinite(frame)
        valid &= frame >= self.args.valid_min_mm
        valid &= frame <= self.args.sensor_max_mm
        if self.args.invalid_zero:
            valid &= frame > 0
        return valid

    def _filter_frame(self, frame: np.ndarray, valid: np.ndarray) -> np.ndarray:
        nan_frame = np.where(valid, frame, np.nan)
        self.temporal.append(nan_frame)
        stack = np.stack(list(self.temporal), axis=0)
        with np.errstate(all="ignore"):
            med = np.nanmedian(stack, axis=0)

        # Fill all-NaN cells from previous EMA or far distance.
        if self.ema is not None:
            med = np.where(np.isfinite(med), med, self.ema)
        else:
            med = np.where(np.isfinite(med), med, float(self.args.max_mm))

        if self.ema is None:
            self.ema = med.astype(float)
        else:
            alpha = self.args.ema_alpha
            self.ema = alpha * med + (1.0 - alpha) * self.ema
        return self.ema.copy()

    def _finish_calibration_if_ready(self) -> None:
        if len(self.calibration) < self.args.calibration_frames:
            return
        stack = np.stack(self.calibration, axis=0)
        with np.errstate(all="ignore"):
            bg = np.nanmedian(stack, axis=0)
            mad = np.nanmedian(np.abs(stack - bg), axis=0)
        bg = np.where(np.isfinite(bg), bg, float(self.args.max_mm))
        noise = 1.4826 * mad
        noise = np.where(np.isfinite(noise), noise, float(self.args.min_noise_mm))
        noise = np.clip(noise, self.args.min_noise_mm, self.args.max_noise_mm)
        self.background = bg
        self.background_noise = noise
        self.calibration = []
        self.calibrating = False

    def process(self, packet: FramePacket) -> Measurement:
        raw_oriented = apply_orientation(
            packet.values,
            flip_x=self.args.flip_x,
            flip_y=self.args.flip_y,
            transpose=self.args.transpose,
        )
        valid = self._valid_mask(raw_oriented)
        filt = self._filter_frame(raw_oriented, valid)
        raw_display = np.where(valid, raw_oriented, float(self.args.max_mm))
        raw_display = np.clip(raw_display, self.args.min_mm, self.args.max_mm)

        filt_valid = self._valid_mask(filt)
        filt_for_bg = np.where(filt_valid, filt, np.nan)

        if self.calibrating:
            self.calibration.append(filt_for_bg.copy())
            self._finish_calibration_if_ready()
            if self.calibrating:
                status = f"calibrating background {len(self.calibration)}/{self.args.calibration_frames}"
            else:
                status = "background calibrated"
            return Measurement(
                t=packet.host_t,
                raw=raw_display,
                filtered=np.clip(filt, self.args.min_mm, self.args.max_mm),
                valid=filt_valid,
                foreground=np.zeros((GRID, GRID), dtype=float),
                component=np.zeros((GRID, GRID), dtype=bool),
                visible=False,
                status=status,
                seq=packet.seq,
                device_ms=packet.device_ms,
                read_us=packet.read_us,
                protocol=packet.protocol,
            )

        foreground, status = self._foreground(filt, filt_valid)
        foreground = self._spatial_cleanup(foreground)
        field_dx, field_dy, field_quality = self._estimate_field_motion(foreground)
        component = self._select_component(foreground)
        visible, x, y, z, nearest, area, mass, quality = self._track_component(packet.host_t, filt, component, foreground)
        too_close = visible and np.isfinite(z) and z < self.args.min_track_z_mm
        if too_close:
            visible = False
            status = f"{status}; too close for reliable gesture"

        if not visible and not too_close and self.background is not None and self.args.adaptive_bg:
            self._adapt_background(filt, filt_valid)

        if visible:
            self.last_visible_t = packet.host_t
            x, y, z = self._smooth_xyz(packet.host_t, x, y, z)
        elif packet.host_t - self.last_visible_t > 0.25:
            self.smooth_track = None

        return Measurement(
            t=packet.host_t,
            raw=raw_display,
            filtered=np.clip(filt, self.args.min_mm, self.args.max_mm),
            valid=filt_valid,
            foreground=foreground,
            component=component,
            visible=visible,
            x=x,
            y=y,
            z=z,
            nearest=nearest,
            area=area,
            mass=mass,
            quality=quality,
            status=status if visible else f"{status}; no confident component",
            seq=packet.seq,
            device_ms=packet.device_ms,
            read_us=packet.read_us,
            protocol=packet.protocol,
            field_dx=field_dx,
            field_dy=field_dy,
            field_quality=field_quality,
        )

    def _foreground(self, filt: np.ndarray, valid: np.ndarray) -> Tuple[np.ndarray, str]:
        close_span = max(1.0, self.args.gesture_max_mm - self.args.min_mm)
        absolute_close = np.where(
            valid & (filt <= self.args.gesture_max_mm),
            (self.args.gesture_max_mm - filt) / close_span,
            0.0,
        )

        if self.background is None:
            fg = np.clip(absolute_close, 0.0, 1.0)
            return fg, "direct close-object mode"

        delta = self.background - filt
        if self.background_noise is None:
            delta_threshold = float(self.args.background_delta_mm)
        else:
            delta_threshold = np.maximum(
                float(self.args.background_delta_mm),
                self.args.noise_k * self.background_noise,
            )
        bg_fg = np.where(
            valid
            & (filt <= self.args.gesture_max_mm)
            & (delta >= delta_threshold),
            (delta - delta_threshold) / max(1.0, self.args.weight_range_mm),
            0.0,
        )
        # Mix in a little absolute closeness so hands are still detected if background is far/invalid.
        fg = np.maximum(bg_fg, absolute_close * self.args.absolute_close_mix)
        return np.clip(fg, 0.0, 1.0), "noise-adaptive background tracking"

    def _spatial_cleanup(self, foreground: np.ndarray) -> np.ndarray:
        min_neighbors = max(1, int(self.args.spatial_min_neighbors))
        if min_neighbors <= 1:
            return foreground
        mask = foreground > self.args.foreground_threshold
        if not np.any(mask):
            return foreground

        padded = np.pad(mask.astype(np.uint8), 1, mode="constant")
        counts = np.zeros_like(mask, dtype=np.uint8)
        for dy in range(3):
            for dx in range(3):
                counts += padded[dy : dy + GRID, dx : dx + GRID]
        keep = mask & (counts >= min_neighbors)
        return np.where(keep, foreground, 0.0)

    def _select_component(self, foreground: np.ndarray) -> np.ndarray:
        candidates = component_candidates(foreground, self.args.foreground_threshold)
        if not candidates:
            return np.zeros((GRID, GRID), dtype=bool)

        best_mass = max(max(c.score for c in candidates), 1e-6)
        if self.smooth_track is None:
            return max(candidates, key=lambda c: c.score).mask

        sx, sy, _ = self.smooth_track
        max_jump = max(0.1, self.args.track_max_jump_cells)
        continuity_weight = self.args.track_continuity_weight

        def candidate_score(c: ComponentCandidate) -> float:
            dist = math.hypot(c.x - sx, c.y - sy)
            continuity = clamp01(1.0 - dist / max_jump)
            return (c.score / best_mass) + continuity_weight * continuity

        return max(candidates, key=candidate_score).mask

    def _estimate_field_motion(self, foreground: np.ndarray) -> Tuple[float, float, float]:
        curr = foreground.astype(float, copy=True)
        prev = self.prev_motion_map
        self.prev_motion_map = curr.copy()

        if not self.args.field_motion or prev is None:
            return 0.0, 0.0, 0.0

        active = np.maximum(prev, curr) > self.args.foreground_threshold
        active_cells = int(active.sum())
        if active_cells < self.args.min_component_cells:
            return 0.0, 0.0, 0.0

        avg = 0.5 * (prev + curr)
        gy, gx = np.gradient(avg)
        it = curr - prev
        w = np.where(active, np.maximum(prev, curr), 0.0)

        a11 = float(np.sum(w * gx * gx))
        a22 = float(np.sum(w * gy * gy))
        a12 = float(np.sum(w * gx * gy))
        b1 = -float(np.sum(w * gx * it))
        b2 = -float(np.sum(w * gy * it))
        det = a11 * a22 - a12 * a12
        if det <= 1e-7:
            return 0.0, 0.0, 0.0

        dx = (b1 * a22 - b2 * a12) / det
        dy = (a11 * b2 - a12 * b1) / det
        limit = max(0.05, self.args.max_field_shift_cells)
        dx = float(np.clip(dx, -limit, limit))
        dy = float(np.clip(dy, -limit, limit))

        weight_sum = max(float(np.sum(w)), 1e-6)
        mean_change = float(np.sum(np.abs(it) * w) / weight_sum)
        grad_energy = float(np.sum(w * (gx * gx + gy * gy)) / weight_sum)
        support_score = clamp01((active_cells - self.args.min_component_cells + 1) / 10.0)
        change_score = clamp01(mean_change / max(0.01, self.args.field_change_scale))
        gradient_score = clamp01(grad_energy / max(0.001, self.args.field_gradient_scale))
        quality = clamp01(support_score * change_score * gradient_score)
        if math.hypot(dx, dy) < 0.015:
            quality *= 0.4
        return dx, dy, quality

    def _track_component(
        self,
        t: float,
        filt: np.ndarray,
        comp: np.ndarray,
        fg: np.ndarray,
    ) -> Tuple[bool, float, float, float, float, int, float, float]:
        area = int(comp.sum())
        if area == 0:
            return False, math.nan, math.nan, math.nan, math.nan, 0, 0.0, 0.0

        weights = np.where(comp, fg, 0.0)
        mass = float(weights.sum())
        yy, xx = np.mgrid[0:GRID, 0:GRID]
        denom = max(mass, 1e-9)
        blob_x = float((xx * weights).sum() / denom)
        blob_y = float((yy * weights).sum() / denom)
        blob_z = float((filt * weights).sum() / denom)

        span = max(1.0, self.args.gesture_max_mm - self.args.min_mm)
        near = np.clip((self.args.gesture_max_mm - filt) / span, 0.0, 1.0)
        core_weights = weights * (1.0 + self.args.near_core_boost * np.power(near, self.args.near_core_power))
        core_denom = max(float(core_weights.sum()), 1e-9)
        core_x = float((xx * core_weights).sum() / core_denom)
        core_y = float((yy * core_weights).sum() / core_denom)
        core_z = float((filt * core_weights).sum() / core_denom)

        core_blend = clamp01(self.args.near_core_blend)
        x = (1.0 - core_blend) * blob_x + core_blend * core_x
        y = (1.0 - core_blend) * blob_y + core_blend * core_y
        z = (1.0 - core_blend) * blob_z + core_blend * core_z
        nearest = float(np.nanmin(np.where(comp, filt, np.nan)))

        ys, xs = np.where(comp)
        bbox_area = max(1, (xs.max() - xs.min() + 1) * (ys.max() - ys.min() + 1))
        compactness = area / bbox_area
        area_score = clamp01((area - self.args.min_component_cells + 1) / max(1.0, self.args.good_component_cells))
        mass_score = clamp01(mass / max(0.01, self.args.good_mass))
        compact_score = clamp01(compactness / 0.55)
        distance_score = clamp01((self.args.gesture_max_mm - z) / max(1.0, self.args.gesture_max_mm - self.args.min_mm))
        quality = clamp01(0.35 * area_score + 0.35 * mass_score + 0.15 * compact_score + 0.15 * distance_score)
        visible = (area >= self.args.min_component_cells) and (mass >= self.args.min_mass) and (quality >= self.args.min_quality)
        return visible, x, y, z, nearest, area, mass, quality

    def _smooth_xyz(self, t: float, x: float, y: float, z: float) -> Tuple[float, float, float]:
        if self.smooth_track is None:
            self.smooth_track = (x, y, z)
            return x, y, z
        sx, sy, sz = self.smooth_track
        a_xy = self.args.centroid_alpha
        a_z = self.args.z_alpha
        sx = a_xy * x + (1.0 - a_xy) * sx
        sy = a_xy * y + (1.0 - a_xy) * sy
        sz = a_z * z + (1.0 - a_z) * sz
        self.smooth_track = (sx, sy, sz)
        return sx, sy, sz

    def _adapt_background(self, filt: np.ndarray, valid: np.ndarray) -> None:
        assert self.background is not None
        alpha = self.args.adaptive_bg_alpha
        current = np.where(valid, filt, self.background)
        self.background = (1.0 - alpha) * self.background + alpha * current


def component_candidates(weights: np.ndarray, threshold: float) -> List[ComponentCandidate]:
    mask = weights > threshold
    visited = np.zeros_like(mask, dtype=bool)
    candidates: List[ComponentCandidate] = []

    for y in range(GRID):
        for x in range(GRID):
            if not mask[y, x] or visited[y, x]:
                continue
            stack = [(y, x)]
            visited[y, x] = True
            cells: List[Tuple[int, int]] = []
            score = 0.0
            while stack:
                cy, cx = stack.pop()
                cells.append((cy, cx))
                score += float(weights[cy, cx])
                for dy in (-1, 0, 1):
                    for dx in (-1, 0, 1):
                        if dx == 0 and dy == 0:
                            continue
                        ny, nx = cy + dy, cx + dx
                        if 0 <= ny < GRID and 0 <= nx < GRID and mask[ny, nx] and not visited[ny, nx]:
                            visited[ny, nx] = True
                            stack.append((ny, nx))
            if not cells:
                continue
            denom = max(score, 1e-9)
            cx = sum(px * float(weights[py, px]) for py, px in cells) / denom
            cy = sum(py * float(weights[py, px]) for py, px in cells) / denom
            out = np.zeros_like(mask, dtype=bool)
            for py, px in cells:
                out[py, px] = True
            candidates.append(ComponentCandidate(mask=out, score=score, area=len(cells), x=float(cx), y=float(cy)))

    return candidates


def largest_component(weights: np.ndarray, threshold: float) -> np.ndarray:
    candidates = component_candidates(weights, threshold)
    if not candidates:
        return np.zeros_like(weights, dtype=bool)
    return max(candidates, key=lambda c: c.score).mask

# ---------------------------------------------------------------------------
# Gesture classification
# ---------------------------------------------------------------------------

class StrokeGestureDetector:
    def __init__(self, args: argparse.Namespace):
        self.args = args
        self.active: Optional[Stroke] = None
        self.last_visible_sample: Optional[TrackSample] = None
        self.cooldown_until = 0.0
        self.last_event: Optional[GestureEvent] = None
        self.left_view_since_event = True
        self.last_scores: Dict[str, float] = {name: 0.0 for name in GESTURE_NAMES}
        self.last_features: Dict[str, float] = {
            "dx": 0.0,
            "dy": 0.0,
            "dz": 0.0,
            "dt": 0.0,
            "path": 0.0,
            "linearity": 0.0,
            "speed": 0.0,
            "z_slope": 0.0,
            "z_trend": 0.0,
            "field_dx": 0.0,
            "field_dy": 0.0,
            "field_path": 0.0,
            "field_speed": 0.0,
            "field_quality": 0.0,
        }

    def clear(self) -> None:
        self.active = None
        self.last_visible_sample = None
        self.last_event = None
        self.left_view_since_event = True
        self.cooldown_until = 0.0
        self.last_scores = {name: 0.0 for name in GESTURE_NAMES}
        for k in self.last_features:
            self.last_features[k] = 0.0

    def update(self, m: Measurement) -> Optional[GestureEvent]:
        now = m.t

        if now < self.cooldown_until:
            self._decay_scores()
            if m.visible and m.quality >= self.args.enter_quality:
                self.last_visible_sample = TrackSample(
                    m.t, m.x, m.y, m.z, m.area, m.mass, m.quality, m.field_dx, m.field_dy, m.field_quality
                )
            else:
                self.last_visible_sample = None
                self.left_view_since_event = True
            return None

        if m.visible and m.quality >= self.args.enter_quality:
            s = TrackSample(m.t, m.x, m.y, m.z, m.area, m.mass, m.quality, m.field_dx, m.field_dy, m.field_quality)
            prev = self.last_visible_sample
            motion_energy = self._motion_energy(prev, s)
            self.last_visible_sample = s
            if self.active is None:
                if motion_energy < self.args.motion_start_energy:
                    self._decay_scores()
                    return None
                self.active = Stroke()
                if prev is not None and s.t - prev.t <= self.args.motion_prev_grace_s:
                    self.active.append(prev, 0.0)
                self.active.append(s, motion_energy)
                self.active.last_motion_t = now
            else:
                self.active.append(s, motion_energy)
                if motion_energy >= self.args.motion_continue_energy:
                    self.active.last_motion_t = now
            self._update_live_scores(self.active.samples)

            idle_for = now - self.active.last_motion_t
            if idle_for >= self.args.motion_idle_s:
                event = self._classify_and_reset(now, reason="motion_idle")
                return event

            if self.active.duration >= self.args.max_stroke_s and self.active.motion_peak >= self.args.motion_force_finish_energy:
                event = self._classify_and_reset(now, reason="motion_window")
                return event
            return None

        # No visible object or too-low quality.
        self.last_visible_sample = None
        self.left_view_since_event = True
        if self.active is None:
            self._decay_scores()
            return None

        if self.active.missing_since_t is None:
            self.active.missing_since_t = now
            return None

        if now - self.active.missing_since_t >= self.args.exit_grace_s:
            event = self._classify_and_reset(now, reason="exit")
            return event

        return None

    def _motion_energy(self, prev: Optional[TrackSample], curr: TrackSample) -> float:
        field_motion = math.hypot(curr.field_dx, curr.field_dy) * clamp01(curr.field_quality)
        if prev is None:
            return self.args.motion_field_weight * field_motion
        dt = max(1e-6, curr.t - prev.t)
        if dt > self.args.motion_prev_grace_s:
            return self.args.motion_field_weight * field_motion
        xy_motion = math.hypot(curr.x - prev.x, curr.y - prev.y)
        z_motion = min(abs(curr.z - prev.z) / max(1.0, self.args.motion_z_scale_mm), self.args.motion_z_cap)
        return xy_motion + self.args.motion_field_weight * field_motion + self.args.motion_z_weight * z_motion

    def _classify_and_reset(self, now: float, reason: str) -> Optional[GestureEvent]:
        stroke = self.active
        self.active = None
        if stroke is None or len(stroke.samples) < self.args.min_gesture_frames:
            self._decay_scores()
            return None
        if stroke.duration < self.args.min_stroke_s:
            self._decay_scores()
            return None
        if stroke.motion_peak < self.args.min_motion_peak or stroke.motion_path < self.args.min_motion_path:
            self._decay_scores()
            return None
        event = self._classify_stroke(stroke, now, reason=reason)
        if event is None:
            self._decay_scores()
            return None
        self._finish_after_event(event)
        return event

    def _finish_after_event(self, event: GestureEvent) -> None:
        self.last_event = event
        self.left_view_since_event = False
        self.cooldown_until = event.t + self.args.cooldown_s
        self.active = None
        for name in self.last_scores:
            self.last_scores[name] = 0.0
        self.last_scores[event.name] = event.confidence

    def _classify_stroke(self, stroke: Stroke, now: float, reason: str) -> Optional[GestureEvent]:
        scores, details, features = self._scores_for_samples(stroke.samples)
        self.last_scores = scores
        self.last_features = features
        best_name = max(scores, key=scores.get)
        best_score = scores[best_name]
        best_score, suppressed_return = self._cap_visible_return(best_name, best_score)
        if best_score >= self.args.gesture_confidence_threshold:
            self.last_scores[best_name] = best_score
            detail_parts = [details, f"reason={reason}"]
            if suppressed_return:
                detail_parts.append(f"suppressed_visible_return={best_name}")
            return GestureEvent(best_name, now, best_score, "; ".join(detail_parts), stroke=stroke)

        tail = self._tail_samples(stroke)
        if tail is not None:
            tail_scores, tail_details, tail_features = self._scores_for_samples(tail)
            tail_name = max(tail_scores, key=tail_scores.get)
            tail_score = tail_scores[tail_name]
            tail_score, suppressed_tail_return = self._cap_visible_return(tail_name, tail_score)
            if (
                tail_name.startswith("swipe_")
                and tail_score >= self.args.gesture_tail_confidence_threshold
            ):
                self.last_scores = {name: 0.0 for name in GESTURE_NAMES}
                self.last_scores[tail_name] = tail_score
                self.last_features = tail_features
                detail_parts = [tail_details, f"reason={reason}", "tail"]
                if suppressed_tail_return:
                    detail_parts.append(f"suppressed_visible_return={tail_name}")
                return GestureEvent(tail_name, now, tail_score, "; ".join(detail_parts), stroke=stroke)

        if stroke.best_name is None:
            return None

        peak_score, suppressed_peak_return = self._cap_visible_return(stroke.best_name, stroke.best_score)
        if peak_score < self.args.gesture_confidence_threshold:
            return None
        self.last_scores = {name: 0.0 for name in GESTURE_NAMES}
        self.last_scores[stroke.best_name] = peak_score
        self.last_features = stroke.best_features
        detail_parts = [stroke.best_details, f"reason={reason}", "peak_hold"]
        if best_score < self.args.gesture_confidence_threshold:
            self.last_scores[best_name] = best_score
        if suppressed_peak_return:
            detail_parts.append(f"suppressed_visible_return={stroke.best_name}")
        return GestureEvent(stroke.best_name, now, peak_score, "; ".join(detail_parts), stroke=stroke)

    def _update_live_scores(self, samples: List[TrackSample]) -> None:
        if len(samples) < 2:
            return
        scores, details, features = self._scores_for_samples(samples)
        self.last_scores = scores
        self.last_features = features
        if self.active is None or len(samples) < self.args.min_gesture_frames:
            return
        best_name = max(scores, key=scores.get)
        best_score = scores[best_name]
        if best_score > self.active.best_score:
            self.active.best_name = best_name
            self.active.best_score = best_score
            self.active.best_details = details
            self.active.best_features = dict(features)

    @staticmethod
    def _opposite_gesture(name: Optional[str]) -> Optional[str]:
        return {
            "swipe_up": "swipe_down",
            "swipe_down": "swipe_up",
            "swipe_left": "swipe_right",
            "swipe_right": "swipe_left",
        }.get(name or "")

    def _tail_samples(self, stroke: Stroke) -> Optional[List[TrackSample]]:
        if not stroke.samples:
            return None
        end_t = stroke.samples[-1].t
        tail = [s for s in stroke.samples if end_t - s.t <= self.args.gesture_tail_s]
        if len(tail) < self.args.min_gesture_frames:
            return None
        if tail[-1].t - tail[0].t < self.args.min_stroke_s:
            return None
        return tail

    def _cap_visible_return(self, name: Optional[str], score: float) -> Tuple[float, bool]:
        if not self.args.suppress_visible_return or self.left_view_since_event or self.last_event is None:
            return score, False
        if name != self._opposite_gesture(self.last_event.name):
            return score, False
        capped = min(score, self.args.visible_return_score_cap)
        return capped, capped < score

    def _scores_for_samples(self, samples: List[TrackSample]) -> Tuple[Dict[str, float], str, Dict[str, float]]:
        xs = np.array([s.x for s in samples], dtype=float)
        ys = np.array([s.y for s in samples], dtype=float)
        zs = np.array([s.z for s in samples], dtype=float)
        ts = np.array([s.t for s in samples], dtype=float)
        qs = np.array([s.quality for s in samples], dtype=float)
        fdxs = np.array([s.field_dx for s in samples], dtype=float)
        fdys = np.array([s.field_dy for s in samples], dtype=float)
        fqs = np.array([s.field_quality for s in samples], dtype=float)

        n = len(samples)
        k = max(1, int(round(n * self.args.endpoint_fraction)))
        x0 = robust_mean(xs[:k])
        y0 = robust_mean(ys[:k])
        z0 = robust_mean(zs[:k])
        x1 = robust_mean(xs[-k:])
        y1 = robust_mean(ys[-k:])
        z1 = robust_mean(zs[-k:])

        dx = x1 - x0
        dy = y1 - y0
        dz = z1 - z0
        dt = max(1e-6, ts[-1] - ts[0])
        xy_net = math.hypot(dx, dy)
        path = float(np.sum(np.hypot(np.diff(xs), np.diff(ys)))) if n >= 2 else 0.0
        linearity = clamp01(xy_net / max(path, 1e-6)) if path > 0 else 0.0
        speed = xy_net / dt
        quality = clamp01(float(np.nanmean(qs)))
        z_mean = float(np.nanmean(zs))
        field_weights = np.clip(fqs, 0.0, 1.0)
        field_gain = self.args.field_motion_gain
        field_dx = field_gain * float(np.sum(fdxs * field_weights))
        field_dy = field_gain * float(np.sum(fdys * field_weights))
        field_xy_net = math.hypot(field_dx, field_dy)
        field_path = field_gain * float(np.sum(np.hypot(fdxs, fdys) * field_weights))
        field_linearity = clamp01(field_xy_net / max(field_path, 1e-6)) if field_path > 0 else 0.0
        field_speed = field_xy_net / dt
        field_quality = clamp01(float(np.nanmean(field_weights)) / max(0.01, self.args.field_min_quality))
        if n >= 3:
            t_rel = ts - ts[0]
            z_slope = float(np.polyfit(t_rel, zs, 1)[0])
            fit_dz = z_slope * dt
            z_steps = np.diff(zs)
            push_trend = float(np.mean(z_steps < 0))
            pull_trend = float(np.mean(z_steps > 0))
        else:
            z_slope = dz / dt
            fit_dz = dz
            push_trend = 1.0 if dz < 0 else 0.0
            pull_trend = 1.0 if dz > 0 else 0.0

        x_dom = abs(dx) / max(abs(dy), 1e-6)
        y_dom = abs(dy) / max(abs(dx), 1e-6)
        field_x_dom = abs(field_dx) / max(abs(field_dy), 1e-6)
        field_y_dom = abs(field_dy) / max(abs(field_dx), 1e-6)
        lateral_motion = max(xy_net, field_xy_net * 0.75)
        lateral_stability_for_push = clamp01(1.0 - lateral_motion / max(0.01, self.args.push_max_xy_cells))

        swipe_base = clamp01((xy_net - self.args.swipe_cells * 0.55) / max(0.01, self.args.swipe_cells * 0.85))
        speed_score = clamp01(speed / max(0.01, self.args.min_swipe_speed))
        line_score = clamp01((linearity - 0.45) / 0.45)
        qual_score = clamp01(quality / max(0.01, self.args.enter_quality))
        field_swipe_base = clamp01((field_xy_net - self.args.field_swipe_cells * 0.55) / max(0.01, self.args.field_swipe_cells * 0.85))
        field_speed_score = clamp01(field_speed / max(0.01, self.args.min_swipe_speed * 0.65))
        field_line_score = clamp01((field_linearity - 0.25) / 0.55)

        scores = {name: 0.0 for name in GESTURE_NAMES}
        if abs(dx) >= self.args.swipe_cells * 0.65 and x_dom >= self.args.swipe_dominance:
            s = clamp01(swipe_base * speed_score * line_score * qual_score)
            if dx < 0:
                scores["swipe_left"] = s
            else:
                scores["swipe_right"] = s
        if abs(dy) >= self.args.swipe_cells * 0.65 and y_dom >= self.args.swipe_dominance:
            s = clamp01(swipe_base * speed_score * line_score * qual_score)
            if dy < 0:
                scores["swipe_up"] = s
            else:
                scores["swipe_down"] = s

        if (
            field_quality >= 0.35
            and abs(field_dx) >= self.args.field_swipe_cells * 0.65
            and field_x_dom >= self.args.swipe_dominance * 0.75
        ):
            s = clamp01(field_swipe_base * field_speed_score * field_line_score * field_quality * qual_score)
            s = self._cap_field_only_score(s, field_dx, dx)
            if field_dx < 0:
                scores["swipe_left"] = max(scores["swipe_left"], s)
            else:
                scores["swipe_right"] = max(scores["swipe_right"], s)
        if (
            field_quality >= 0.35
            and abs(field_dy) >= self.args.field_swipe_cells * 0.65
            and field_y_dom >= self.args.swipe_dominance * 0.75
        ):
            s = clamp01(field_swipe_base * field_speed_score * field_line_score * field_quality * qual_score)
            s = self._cap_field_only_score(s, field_dy, dy)
            if field_dy < 0:
                scores["swipe_up"] = max(scores["swipe_up"], s)
            else:
                scores["swipe_down"] = max(scores["swipe_down"], s)

        fitted_depth_change = 0.65 * dz + 0.35 * fit_dz
        push_amount = max(0.0, -fitted_depth_change)
        pull_amount = max(0.0, fitted_depth_change)
        push_trend_score = 0.55 + 0.45 * clamp01((push_trend - 0.45) / 0.45)
        pull_trend_score = 0.55 + 0.45 * clamp01((pull_trend - 0.45) / 0.45)
        depth_score_push = clamp01((push_amount - self.args.push_mm * 0.45) / max(1.0, self.args.push_mm * 0.7))
        depth_score_pull = clamp01((pull_amount - self.args.push_mm * 0.45) / max(1.0, self.args.push_mm * 0.7))
        depth_quality = clamp01(quality / max(0.01, self.args.enter_quality))
        scores["push"] = clamp01(depth_score_push * push_trend_score * lateral_stability_for_push * depth_quality)
        scores["pull"] = clamp01(depth_score_pull * pull_trend_score * lateral_stability_for_push * depth_quality)

        # Hold score is computed separately, but keep it visible in live bars.
        hold_event = self._detect_hold(samples, samples[-1].t)
        if hold_event is not None:
            scores["hold_center"] = hold_event.confidence

        features = {
            "dx": dx,
            "dy": dy,
            "dz": dz,
            "dt": dt,
            "path": path,
            "linearity": linearity,
            "speed": speed,
            "quality": quality,
            "z_mean": z_mean,
            "z_slope": z_slope,
            "z_trend": push_trend if push_amount >= pull_amount else pull_trend,
            "field_dx": field_dx,
            "field_dy": field_dy,
            "field_path": field_path,
            "field_speed": field_speed,
            "field_quality": field_quality,
        }
        details = (
            f"dx={dx:+.2f} dy={dy:+.2f} dz={dz:+.0f}mm "
            f"fdx={field_dx:+.2f} fdy={field_dy:+.2f} "
            f"dt={dt:.2f}s speed={speed:.2f}cells/s vz={z_slope:+.0f}mm/s "
            f"lin={linearity:.2f} q={quality:.2f}"
        )
        return scores, details, features

    def _cap_field_only_score(self, score: float, field_delta: float, centroid_delta: float) -> float:
        if score <= 0.0:
            return 0.0
        support = clamp01((abs(centroid_delta) - self.args.field_corroboration_cells) / max(0.01, self.args.field_corroboration_window))
        if abs(centroid_delta) > 0.05 and field_delta * centroid_delta < 0:
            support *= 0.25
        cap = self.args.field_only_score_cap + support * (1.0 - self.args.field_only_score_cap)
        return min(score, cap)

    def _detect_hold(self, samples: List[TrackSample], now: float) -> Optional[GestureEvent]:
        recent = [s for s in samples if now - s.t <= self.args.hold_s]
        if len(recent) < self.args.min_gesture_frames:
            return None
        duration = recent[-1].t - recent[0].t
        if duration < self.args.hold_s:
            return None
        xs = np.array([s.x for s in recent], dtype=float)
        ys = np.array([s.y for s in recent], dtype=float)
        zs = np.array([s.z for s in recent], dtype=float)
        qs = np.array([s.quality for s in recent], dtype=float)
        center_dist = float(np.mean(np.hypot(xs - 3.5, ys - 3.5)))
        jitter = float(np.mean(np.hypot(xs - np.mean(xs), ys - np.mean(ys))))
        center_score = clamp01(1.0 - center_dist / max(0.01, self.args.hold_radius_cells))
        jitter_score = clamp01(1.0 - jitter / max(0.01, self.args.hold_jitter_cells))
        z_score = clamp01((self.args.gesture_max_mm - float(np.mean(zs))) / max(1.0, self.args.gesture_max_mm - self.args.min_mm))
        q_score = clamp01(float(np.mean(qs)) / max(0.01, self.args.enter_quality))
        score = clamp01(center_score * jitter_score * (0.5 + 0.5 * z_score) * q_score)
        if score >= self.args.gesture_confidence_threshold:
            return GestureEvent(
                "hold_center",
                now,
                score,
                f"held {duration:.2f}s center_dist={center_dist:.2f} jitter={jitter:.2f}",
                stroke=Stroke(samples=list(recent), started_t=recent[0].t, last_seen_t=recent[-1].t),
            )
        return None

    def _decay_scores(self) -> None:
        for k in self.last_scores:
            self.last_scores[k] *= 0.88

# ---------------------------------------------------------------------------
# Macro execution
# ---------------------------------------------------------------------------

class MacroManager:
    def __init__(self, enabled: bool):
        self.enabled = enabled
        self.pyautogui = None
        self.last_error = ""
        if enabled:
            self._load_pyautogui()

    def _load_pyautogui(self) -> bool:
        if self.pyautogui is not None:
            return True
        try:
            import pyautogui  # type: ignore
            pyautogui.FAILSAFE = True
            self.pyautogui = pyautogui
            return True
        except Exception as exc:
            self.last_error = f"pyautogui unavailable: {exc}. Install with python3 -m pip install pyautogui"
            print(self.last_error, file=sys.stderr)
            return False

    def toggle(self) -> None:
        self.enabled = not self.enabled
        if self.enabled:
            self._load_pyautogui()
        print(f"Macro execution {'ENABLED' if self.enabled else 'disabled'}")

    def handle(self, event: GestureEvent) -> str:
        spec = GESTURE_MACROS.get(event.name, {"type": "print", "message": event.name})
        action_type = str(spec.get("type", "print"))
        if action_type == "print" or not self.enabled:
            msg = str(spec.get("message", event.name))
            out = f"{msg} conf={event.confidence:.2f} {event.details}"
            print(out)
            return out
        if not self._load_pyautogui():
            return self.last_error
        pg = self.pyautogui
        assert pg is not None
        try:
            if action_type == "press":
                key = self._normalize_key(str(spec["key"]))
                pg.press(key)
                return f"press:{key}"
            if action_type == "hotkey":
                keys = [self._normalize_key(str(k)) for k in spec["keys"]]  # type: ignore[index]
                pg.hotkey(*keys)
                return "hotkey:" + "+".join(keys)
            if action_type == "write":
                text = str(spec["text"])
                pg.write(text)
                return f"write:{text!r}"
            return f"unknown macro type:{action_type}"
        except Exception as exc:
            self.last_error = f"macro error: {exc}"
            print(self.last_error, file=sys.stderr)
            return self.last_error

    @staticmethod
    def _normalize_key(key: str) -> str:
        aliases = {
            "cmd": "command",
            "command": "command",
            "control": "ctrl",
            "ctl": "ctrl",
            "return": "enter",
            "esc": "escape",
        }
        return aliases.get(key.lower(), key.lower())

# ---------------------------------------------------------------------------
# Logging and sample recording
# ---------------------------------------------------------------------------

class CSVLogger:
    def __init__(self, path: Optional[str]):
        self.path = path
        self.file = None
        self.writer: Optional[csv.writer] = None
        if path:
            self.file = open(path, "w", newline="")
            self.writer = csv.writer(self.file)
            header = [
                "time", "seq", "device_ms", "read_us", "protocol",
                "visible", "x", "y", "z", "nearest", "area", "mass", "quality",
                "field_dx", "field_dy", "field_quality",
                "event", "event_confidence", "status",
            ] + [f"raw_{i}" for i in range(N_PIXELS)] + [f"fg_{i}" for i in range(N_PIXELS)]
            self.writer.writerow(header)

    def write(self, m: Measurement, event: Optional[GestureEvent]) -> None:
        if self.writer is None:
            return
        row = [
            f"{m.t:.6f}",
            m.seq if m.seq is not None else "",
            m.device_ms if m.device_ms is not None else "",
            m.read_us if m.read_us is not None else "",
            m.protocol,
            int(m.visible),
            f"{m.x:.3f}" if np.isfinite(m.x) else "",
            f"{m.y:.3f}" if np.isfinite(m.y) else "",
            f"{m.z:.1f}" if np.isfinite(m.z) else "",
            f"{m.nearest:.1f}" if np.isfinite(m.nearest) else "",
            m.area,
            f"{m.mass:.3f}",
            f"{m.quality:.3f}",
            f"{m.field_dx:.4f}",
            f"{m.field_dy:.4f}",
            f"{m.field_quality:.3f}",
            event.name if event else "",
            f"{event.confidence:.3f}" if event else "",
            m.status,
        ] + [f"{v:.1f}" for v in m.raw.reshape(-1)] + [f"{v:.3f}" for v in m.foreground.reshape(-1)]
        self.writer.writerow(row)

    def close(self) -> None:
        if self.file:
            self.file.close()


class DiagnosticLogger:
    def __init__(self, path: Optional[str], args: argparse.Namespace):
        self.path: Optional[Path] = None
        self.file = None
        self.frames_written = 0
        self.start_t = time.time()
        self.include_frames = bool(args.diag_include_frames)
        self.flush_frames = max(1, int(args.diag_flush_frames))

        if not path:
            return

        label = sanitize_filename(args.diag_label or "unlabeled")
        stamp = time.strftime("%Y%m%d_%H%M%S")
        requested = Path(path).expanduser()
        if path == "auto":
            requested = Path("logs")
        if requested.suffix.lower() != ".jsonl":
            requested = requested / f"lidar_diag_{stamp}_{label}.jsonl"
        requested.parent.mkdir(parents=True, exist_ok=True)

        self.path = requested
        self.file = open(requested, "w", encoding="utf-8")
        self._write_obj(
            {
                "type": "meta",
                "created_at": stamp,
                "intended_label": args.diag_label,
                "gesture_names": GESTURE_NAMES,
                "args": serializable_args(args),
            },
            flush=True,
        )

    @property
    def enabled(self) -> bool:
        return self.file is not None

    def _write_obj(self, obj: Dict[str, object], *, flush: bool = False) -> None:
        if self.file is None:
            return
        self.file.write(json.dumps(obj, separators=(",", ":"), allow_nan=False) + "\n")
        if flush:
            self.file.flush()

    def write(self, m: Measurement, detector: StrokeGestureDetector, event: Optional[GestureEvent], source_stats: str) -> None:
        if self.file is None:
            return

        active = detector.active
        payload: Dict[str, object] = {
            "type": "frame",
            "t": round(m.t - self.start_t, 6),
            "wall_t": round(m.t, 6),
            "seq": m.seq,
            "device_ms": m.device_ms,
            "read_us": m.read_us,
            "protocol": m.protocol,
            "status": m.status,
            "visible": m.visible,
            "x": finite_or_none(m.x),
            "y": finite_or_none(m.y),
            "z": finite_or_none(m.z),
            "nearest": finite_or_none(m.nearest),
            "area": m.area,
            "mass": round_float(m.mass, 5),
            "quality": round_float(m.quality, 5),
            "field_dx": round_float(m.field_dx, 5),
            "field_dy": round_float(m.field_dy, 5),
            "field_quality": round_float(m.field_quality, 5),
            "active_samples": 0 if active is None else len(active.samples),
            "active_duration": 0.0 if active is None else round_float(active.duration, 5),
            "active_motion_peak": 0.0 if active is None else round_float(active.motion_peak, 5),
            "active_motion_path": 0.0 if active is None else round_float(active.motion_path, 5),
            "active_idle_s": 0.0 if active is None else round_float(max(0.0, m.t - active.last_motion_t), 5),
            "active_best_name": None if active is None else active.best_name,
            "active_best_score": 0.0 if active is None else round_float(active.best_score, 5),
            "left_view_since_event": detector.left_view_since_event,
            "cooldown_s": round_float(max(0.0, detector.cooldown_until - m.t), 5),
            "scores": {k: round_float(v, 5) for k, v in detector.last_scores.items()},
            "features": {k: round_float(v, 5) for k, v in detector.last_features.items()},
            "event": None if event is None else {
                "name": event.name,
                "confidence": round_float(event.confidence, 5),
                "details": event.details,
            },
            "source": source_stats,
        }

        if self.include_frames:
            payload["raw"] = rounded_list(m.raw.reshape(-1), 1)
            payload["foreground"] = rounded_list(m.foreground.reshape(-1), 4)
            payload["component"] = [int(v) for v in m.component.reshape(-1)]

        self.frames_written += 1
        self._write_obj(payload, flush=(self.frames_written % self.flush_frames == 0 or event is not None))

    def close(self) -> None:
        if self.file:
            self._write_obj({"type": "end", "frames": self.frames_written}, flush=True)
            self.file.close()
            self.file = None


class SampleRecorder:
    def __init__(self, record_dir: Optional[str]):
        self.record_dir = Path(record_dir).expanduser() if record_dir else None
        self.pending_label: Optional[str] = None
        if self.record_dir:
            self.record_dir.mkdir(parents=True, exist_ok=True)

    def set_pending(self, label: Optional[str]) -> str:
        self.pending_label = label
        if label is None:
            return "recording label cleared"
        return f"next completed stroke will be saved as {label}"

    def maybe_save(self, event: GestureEvent) -> Optional[Path]:
        if self.record_dir is None or self.pending_label is None or event.stroke is None:
            return None
        label = self.pending_label
        self.pending_label = None
        samples = event.stroke.samples
        if not samples:
            return None
        arr = np.array(
            [[s.t, s.x, s.y, s.z, s.area, s.mass, s.quality, s.field_dx, s.field_dy, s.field_quality] for s in samples],
            dtype=float,
        )
        stamp = time.strftime("%Y%m%d_%H%M%S")
        fname = f"{stamp}_{label}_{int(time.time() * 1000) % 100000}.npz"
        path = self.record_dir / fname
        np.savez(
            path,
            label=label,
            event_name=event.name,
            confidence=event.confidence,
            details=event.details,
            samples=arr,
            columns=np.array(["t", "x", "y", "z", "area", "mass", "quality", "field_dx", "field_dy", "field_quality"]),
        )
        return path

# ---------------------------------------------------------------------------
# Visualization
# ---------------------------------------------------------------------------

class Visualizer:
    def __init__(
        self,
        args: argparse.Namespace,
        source,
        pipeline: SignalPipeline,
        detector: StrokeGestureDetector,
        macros: MacroManager,
        logger: CSVLogger,
        diag_logger: DiagnosticLogger,
        recorder: SampleRecorder,
    ):
        self.args = args
        self.source = source
        self.pipeline = pipeline
        self.detector = detector
        self.macros = macros
        self.logger = logger
        self.diag_logger = diag_logger
        self.recorder = recorder
        self.paused = False
        self.last_measurement: Optional[Measurement] = None
        self.events: Deque[str] = deque(maxlen=10)
        self.trail: Deque[Tuple[float, float, float]] = deque(maxlen=args.trail_len)
        self.z_history: Deque[Tuple[float, float, float, float]] = deque(maxlen=args.history_len)
        self.fps_times: Deque[float] = deque(maxlen=90)

        # Track the latest ToF #2 frame for display (only available in dual-sensor mode)
        self._latest_tof2: Optional[np.ndarray] = None
        self._latest_proximity: int = 0
        self._latest_hand_present: bool = False
        self._dual_mode: bool = isinstance(source, DualSensorSerialFrameSource)

        # ---- figure layout ----
        # Dual-sensor mode: wider figure with an extra column for ToF #2 + APDS status.
        # Single-sensor mode: original 3-column layout.
        if self._dual_mode:
            self.fig = plt.figure(figsize=(20, 9))
            gs = self.fig.add_gridspec(3, 4, height_ratios=[1.0, 1.0, 0.85])
        else:
            self.fig = plt.figure(figsize=(16, 9))
            gs = self.fig.add_gridspec(3, 3, height_ratios=[1.0, 1.0, 0.85])

        self.fig.canvas.mpl_connect("key_press_event", self.on_key)

        self.ax_raw = self.fig.add_subplot(gs[0, 0])
        self.ax_fg = self.fig.add_subplot(gs[0, 1])
        self.ax_component = self.fig.add_subplot(gs[0, 2])
        self.ax_trail = self.fig.add_subplot(gs[1, 0])
        self.ax_depth = self.fig.add_subplot(gs[1, 1])
        self.ax_scores = self.fig.add_subplot(gs[1, 2])
        self.ax_features = self.fig.add_subplot(gs[2, 0])

        if self._dual_mode:
            # Extra column: ToF #2 heatmap (top) + APDS status (middle) + events (bottom span)
            self.ax_tof2 = self.fig.add_subplot(gs[0, 3])
            self.ax_apds = self.fig.add_subplot(gs[1, 3])
            self.ax_events = self.fig.add_subplot(gs[2, 1:])
        else:
            self.ax_tof2 = None
            self.ax_apds = None
            self.ax_events = self.fig.add_subplot(gs[2, 1:])

        self.ax_events.axis("off")

        # ---- axes setup ----
        init = np.full((GRID, GRID), float(args.max_mm))
        self.raw_img = self.ax_raw.imshow(init, vmin=args.min_mm, vmax=args.max_mm, origin="upper")
        self.ax_raw.set_title("ToF #1 — Raw distance / mm")
        self.ax_raw.set_xlabel("X")
        self.ax_raw.set_ylabel("Y")
        self.fig.colorbar(self.raw_img, ax=self.ax_raw, fraction=0.046, pad=0.04)

        self.fg_img = self.ax_fg.imshow(np.zeros((GRID, GRID)), vmin=0, vmax=1, origin="upper")
        self.ax_fg.set_title("ToF #1 — Foreground likelihood")
        self.ax_fg.set_xlabel("X")
        self.ax_fg.set_ylabel("Y")
        self.fg_centroid, = self.ax_fg.plot([], [], marker="o", markersize=8)
        self.fig.colorbar(self.fg_img, ax=self.ax_fg, fraction=0.046, pad=0.04)

        self.comp_img = self.ax_component.imshow(np.zeros((GRID, GRID)), vmin=0, vmax=1, origin="upper")
        self.ax_component.set_title("ToF #1 — Selected hand blob")
        self.ax_component.set_xlabel("X")
        self.ax_component.set_ylabel("Y")
        self.comp_centroid, = self.ax_component.plot([], [], marker="o", markersize=8)

        # Pixel-value text overlays for ToF #1 raw heatmap
        self.value_texts: List[List[object]] = []
        self._value_text_cache = [["" for _ in range(GRID)] for _ in range(GRID)]
        for y in range(GRID):
            row = []
            for x in range(GRID):
                row.append(self.ax_raw.text(x, y, "", ha="center", va="center", fontsize=7))
            self.value_texts.append(row)

        # ---- ToF #2 companion heatmap (dual mode only) ----
        if self._dual_mode and self.ax_tof2 is not None:
            self.tof2_img = self.ax_tof2.imshow(init, vmin=args.min_mm, vmax=args.max_mm, origin="upper")
            self.ax_tof2.set_title("ToF #2 — Raw distance / mm")
            self.ax_tof2.set_xlabel("X")
            self.ax_tof2.set_ylabel("Y")
            self.fig.colorbar(self.tof2_img, ax=self.ax_tof2, fraction=0.046, pad=0.04)
            # Pixel-value overlays for ToF #2
            self.tof2_value_texts: List[List[object]] = []
            self._tof2_value_text_cache = [["" for _ in range(GRID)] for _ in range(GRID)]
            for y in range(GRID):
                row2 = []
                for x in range(GRID):
                    row2.append(self.ax_tof2.text(x, y, "", ha="center", va="center", fontsize=7))
                self.tof2_value_texts.append(row2)
        else:
            self.tof2_img = None
            self.tof2_value_texts = []
            self._tof2_value_text_cache = []

        # ---- APDS-9930 proximity panel (dual mode only) ----
        if self._dual_mode and self.ax_apds is not None:
            self.ax_apds.set_xlim(0, 1)
            self.ax_apds.set_ylim(0, 1)
            self.ax_apds.axis("off")
            self.ax_apds.set_title("APDS-9930 Proximity")
            self._apds_bg_rect = plt.Rectangle(
                (0.05, 0.05), 0.90, 0.90, color="grey", transform=self.ax_apds.transAxes,
                clip_on=False, zorder=0,
            )
            self.ax_apds.add_patch(self._apds_bg_rect)
            self._apds_text = self.ax_apds.text(
                0.5, 0.6, "No Hand", ha="center", va="center",
                fontsize=18, fontweight="bold", color="white",
                transform=self.ax_apds.transAxes,
            )
            self._apds_prox_text = self.ax_apds.text(
                0.5, 0.3, "proximity: 0", ha="center", va="center",
                fontsize=11, color="white",
                transform=self.ax_apds.transAxes,
            )
        else:
            self._apds_bg_rect = None
            self._apds_text = None
            self._apds_prox_text = None

        self.trail_line, = self.ax_trail.plot([], [], marker="o", markersize=3)
        self.current_point, = self.ax_trail.plot([], [], marker="o", markersize=9)
        self.ax_trail.set_xlim(-0.5, 7.5)
        self.ax_trail.set_ylim(7.5, -0.5)
        self.ax_trail.set_xlabel("X")
        self.ax_trail.set_ylabel("Y")
        self.ax_trail.grid(True)
        self.ax_trail.set_title("Smoothed centroid trail (ToF #1)")

        self.z_line, = self.ax_depth.plot([], [], label="weighted z")
        self.nearest_line, = self.ax_depth.plot([], [], label="nearest")
        self.quality_line, = self.ax_depth.plot([], [], label="quality x max")
        self.ax_depth.set_xlim(-args.history_seconds, 0)
        self.ax_depth.set_ylim(args.min_mm, args.max_mm)
        self.ax_depth.set_xlabel("seconds ago")
        self.ax_depth.set_ylabel("mm")
        self.ax_depth.set_title("Depth + quality history (ToF #1)")
        self.ax_depth.legend(loc="upper left", fontsize=8)

        self.score_bars = self.ax_scores.barh(GESTURE_NAMES, [0.0] * len(GESTURE_NAMES))
        self.ax_scores.set_xlim(0, 1)
        self.ax_scores.set_title("Live gesture scores")
        self.ax_scores.set_xlabel("confidence")

        self.feature_names = ["dx", "dy", "dz", "dt", "path", "linearity", "speed", "quality"]
        self.feature_text = self.ax_features.text(0.0, 1.0, "", ha="left", va="top", family="monospace", transform=self.ax_features.transAxes)
        self.ax_features.axis("off")
        self.event_text = self.ax_events.text(0.0, 1.0, "", ha="left", va="top", family="monospace", transform=self.ax_events.transAxes)

        title = "LiDAR Gesture Studio v2"
        if self._dual_mode:
            title += " — Dual Sensor (ToF #1 gesture | ToF #2 companion)"
        self.fig.suptitle(title)

    def on_key(self, event) -> None:
        k = str(event.key)
        if k == "q":
            plt.close(self.fig)
        elif k == "r":
            self.pipeline.start_calibration()
            self.detector.clear()
            self.events.appendleft("recalibrating background; keep hand out of view")
        elif k == "c":
            self.trail.clear()
            self.z_history.clear()
            self.detector.clear()
            self.events.clear()
            self.events.appendleft("cleared trail/history/events")
        elif k == "p":
            self.paused = not self.paused
            self.events.appendleft("paused" if self.paused else "resumed")
        elif k == "m":
            self.macros.toggle()
            self.events.appendleft(f"macros {'enabled' if self.macros.enabled else 'disabled'}")
        elif k in LABEL_KEYS:
            msg = self.recorder.set_pending(LABEL_KEYS[k])
            self.events.appendleft(msg)
        elif k == "0":
            msg = self.recorder.set_pending(None)
            self.events.appendleft(msg)

    def update(self, _idx: int):
        if self.paused:
            self._update_status()
            return []
        raw_packet = self.source.read_latest()
        if raw_packet is None:
            self._update_status()
            return []

        # Handle dual-sensor packets: extract ToF #2 data before processing ToF #1
        if isinstance(raw_packet, DualFramePacket):
            self._latest_tof2 = raw_packet.tof2
            self._latest_proximity = raw_packet.proximity
            self._latest_hand_present = raw_packet.hand_present
            packet = raw_packet.to_frame_packet()
        else:
            packet = raw_packet

        m = self.pipeline.process(packet)
        self.last_measurement = m
        self.fps_times.append(m.t)

        event = self.detector.update(m)
        if event is not None:
            macro_result = self.macros.handle(event)
            saved = self.recorder.maybe_save(event)
            saved_msg = f" | saved {saved.name}" if saved else ""
            self.events.appendleft(
                f"{event.name:12s} conf={event.confidence:.2f} {event.details} | {macro_result}{saved_msg}"
            )

        self.logger.write(m, event)
        self.diag_logger.write(m, self.detector, event, self.source.stats())
        if m.visible:
            self.trail.append((m.t, m.x, m.y))
            self.z_history.append((m.t, m.z, m.nearest, m.quality))
        else:
            self.z_history.append((m.t, math.nan, math.nan, 0.0))

        self._update_images(m)
        self._update_tof2()
        self._update_apds()
        self._update_trail()
        self._update_depth()
        self._update_scores()
        self._update_status()
        return []

    def _update_images(self, m: Measurement) -> None:
        self.raw_img.set_data(m.raw)
        self.fg_img.set_data(m.foreground)
        self.comp_img.set_data(m.component.astype(float))
        if m.visible:
            self.fg_centroid.set_data([m.x], [m.y])
            self.comp_centroid.set_data([m.x], [m.y])
        else:
            self.fg_centroid.set_data([], [])
            self.comp_centroid.set_data([], [])
        if self.args.show_values:
            for y in range(GRID):
                for x in range(GRID):
                    text = str(int(m.raw[y, x]))
                    if self._value_text_cache[y][x] != text:
                        self.value_texts[y][x].set_text(text)
                        self._value_text_cache[y][x] = text
        else:
            for y in range(GRID):
                for x in range(GRID):
                    if self._value_text_cache[y][x]:
                        self.value_texts[y][x].set_text("")
                        self._value_text_cache[y][x] = ""

    def _update_tof2(self) -> None:
        """Refresh the ToF #2 companion heatmap (dual-sensor mode only)."""
        if not self._dual_mode or self.tof2_img is None:
            return
        if self._latest_tof2 is None:
            return
        if not np.any(np.isfinite(self._latest_tof2)):
            return
        data = np.nan_to_num(
            self._latest_tof2,
            nan=float(self.args.max_mm),
            posinf=float(self.args.max_mm),
            neginf=float(self.args.min_mm),
        )
        data = np.clip(data, self.args.min_mm, self.args.max_mm)
        self.tof2_img.set_data(data)
        if self.args.show_values and self.tof2_value_texts:
            for y in range(GRID):
                for x in range(GRID):
                    text = str(int(data[y, x]))
                    if self._tof2_value_text_cache[y][x] != text:
                        self.tof2_value_texts[y][x].set_text(text)
                        self._tof2_value_text_cache[y][x] = text
        else:
            for y in range(GRID):
                for x in range(GRID):
                    if self.tof2_value_texts and self._tof2_value_text_cache[y][x]:
                        self.tof2_value_texts[y][x].set_text("")
                        self._tof2_value_text_cache[y][x] = ""

    def _update_apds(self) -> None:
        """Refresh the APDS-9930 proximity status panel (dual-sensor mode only)."""
        if not self._dual_mode or self._apds_bg_rect is None:
            return
        if self._latest_hand_present:
            color = "#2ecc71"   # green
            label = "✋ Hand Present"
        else:
            color = "#e74c3c"   # red
            label = "No Hand"
        self._apds_bg_rect.set_facecolor(color)
        if self._apds_text is not None:
            self._apds_text.set_text(label)
        if self._apds_prox_text is not None:
            self._apds_prox_text.set_text(f"proximity: {self._latest_proximity}")

    def _update_trail(self) -> None:
        if not self.trail:
            self.trail_line.set_data([], [])
            self.current_point.set_data([], [])
            return
        xs = [p[1] for p in self.trail]
        ys = [p[2] for p in self.trail]
        self.trail_line.set_data(xs, ys)
        self.current_point.set_data([xs[-1]], [ys[-1]])

    def _update_depth(self) -> None:
        if not self.z_history:
            self.z_line.set_data([], [])
            self.nearest_line.set_data([], [])
            self.quality_line.set_data([], [])
            return
        now = self.z_history[-1][0]
        xs = [p[0] - now for p in self.z_history]
        zs = [p[1] for p in self.z_history]
        nearest = [p[2] for p in self.z_history]
        quality_scaled = [self.args.min_mm + p[3] * (self.args.max_mm - self.args.min_mm) for p in self.z_history]
        self.z_line.set_data(xs, zs)
        self.nearest_line.set_data(xs, nearest)
        self.quality_line.set_data(xs, quality_scaled)

    def _update_scores(self) -> None:
        for bar, name in zip(self.score_bars, GESTURE_NAMES):
            bar.set_width(self.detector.last_scores.get(name, 0.0))

    def _fps(self) -> float:
        if len(self.fps_times) < 2:
            return 0.0
        dt = self.fps_times[-1] - self.fps_times[0]
        if dt <= 0:
            return 0.0
        return (len(self.fps_times) - 1) / dt

    def _update_status(self) -> None:
        m = self.last_measurement
        f = self.detector.last_features
        if m is None:
            meas = "waiting for LiDAR frames..."
        else:
            frame_parts = []
            if m.seq is not None:
                frame_parts.append(f"seq={m.seq}")
            if m.device_ms is not None:
                frame_parts.append(f"dev={m.device_ms}ms")
            if m.read_us is not None:
                frame_parts.append(f"read={m.read_us / 1000.0:.1f}ms")
            if m.protocol:
                frame_parts.append(m.protocol)
            frame_line = " ".join(frame_parts) if frame_parts else "--"
            if m.visible:
                meas = (
                    f"status:       {m.status}\n"
                    f"frame:        {frame_line}\n"
                    f"visible:      {m.visible}\n"
                    f"centroid:     x={m.x:.2f} y={m.y:.2f}\n"
                    f"z/nearest:    {m.z:.0f} / {m.nearest:.0f} mm\n"
                    f"field motion: dx={m.field_dx:+.2f} dy={m.field_dy:+.2f} q={m.field_quality:.2f}\n"
                    f"blob:         area={m.area} mass={m.mass:.2f}\n"
                    f"quality:      {m.quality:.2f}\n"
                )
            else:
                meas = (
                    f"status:       {m.status}\n"
                    f"frame:        {frame_line}\n"
                    f"visible:      {m.visible}\n"
                    f"centroid:     --\n"
                    f"z/nearest:    --\n"
                    f"field motion: dx={m.field_dx:+.2f} dy={m.field_dy:+.2f} q={m.field_quality:.2f}\n"
                    f"blob:         area={m.area} mass={m.mass:.2f}\n"
                    f"quality:      {m.quality:.2f}\n"
                )
        active = self.detector.active
        active_text = "none" if active is None else f"{len(active.samples)} samples, {active.duration:.2f}s"
        pending = self.recorder.pending_label or "none"
        cooldown = max(0.0, self.detector.cooldown_until - time.time())
        dual_line = ""
        if self._dual_mode:
            hand_str = "YES" if self._latest_hand_present else "no"
            dual_line = f"APDS-9930:     hand={hand_str}  proximity={self._latest_proximity}\n"
        feature_lines = (
            f"{meas}\n"
            f"active stroke: {active_text}\n"
            f"cooldown:      {cooldown:.2f}s\n"
            f"fps:           {self._fps():.1f}\n"
            f"macros:        {'ENABLED' if self.macros.enabled else 'disabled'}\n"
            f"record next:   {pending}\n"
            f"{dual_line}"
            f"source:        {self.source.stats()}\n\n"
            f"features:\n"
            f"  dx={f['dx']:+.2f}  dy={f['dy']:+.2f}  dz={f['dz']:+.0f}mm\n"
            f"  fdx={f.get('field_dx', 0):+.2f} fdy={f.get('field_dy', 0):+.2f} fq={f.get('field_quality', 0):.2f}\n"
            f"  dt={f['dt']:.2f}s  path={f['path']:.2f}  speed={f['speed']:.2f}  vz={f.get('z_slope', 0):+.0f}mm/s\n"
            f"  linearity={f['linearity']:.2f}  quality={f.get('quality', 0):.2f}\n"
        )
        self.feature_text.set_text(feature_lines)
        event_lines = "\n".join(list(self.events)) if self.events else "(no events yet)"
        key_help = (
            "keys: r recalibrate | c clear | p pause | m macros | "
            "1..7 record next stroke label | 0 cancel label | q quit"
        )
        self.event_text.set_text(f"{key_help}\n\nrecent events:\n{event_lines}")

    def run(self) -> None:
        ani = FuncAnimation(self.fig, self.update, interval=self.args.interval_ms, cache_frame_data=False)
        try:
            plt.tight_layout()
            plt.show()
        finally:
            self.logger.close()
            self.diag_logger.close()
            self.source.close()

# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Robust 8x8 LiDAR gesture visualizer and stroke detector.")

    src = p.add_argument_group("serial / source")
    src.add_argument("--port", default=None, help="Serial port, e.g. /dev/cu.usbmodem2101")
    src.add_argument(
        "--baud",
        type=int,
        default=None,
        help="ESP32 USB serial baud; defaults to 921600, or 115200 with --dual",
    )
    src.add_argument("--demo", action="store_true", help="Run without hardware using fake gesture data")
    src.add_argument(
        "--dual", action="store_true",
        help=(
            "Use dual-sensor mode: parse APDS-9930 + two ToF sensors from the ESP32 "
            "debug text format (main.cpp with APDS + tof1 + tof2). "
            "Automatically sets --baud 115200 unless overridden. "
            "ToF #1 feeds the gesture pipeline; ToF #2 is shown as a companion heatmap."
        ),
    )
    src.add_argument(
        "--serial-debug",
        action="store_true",
        help="Print serial receive/parser debug messages to stderr.",
    )
    src.add_argument(
        "--serial-debug-log",
        default=None,
        help="Write serial receive/parser debug messages to a file; use 'auto' for logs/serial_debug_*.log.",
    )
    src.add_argument(
        "--serial-debug-bytes",
        action=argparse.BooleanOptionalAction,
        default=False,
        help="Include every raw serial read chunk in serial debug output.",
    )

    display = p.add_argument_group("display")
    display.add_argument("--min-mm", type=int, default=20)
    display.add_argument("--max-mm", type=int, default=2000)
    display.add_argument("--sensor-max-mm", type=int, default=3500)
    display.add_argument("--valid-min-mm", type=int, default=20, help="Distances below this are treated as clipped/invalid for tracking")
    display.add_argument("--show-values", action="store_true")
    display.add_argument("--interval-ms", type=int, default=33)
    display.add_argument("--history-seconds", type=float, default=6.0)
    display.add_argument("--history-len", type=int, default=240)
    display.add_argument("--trail-len", type=int, default=120)

    orient = p.add_argument_group("orientation")
    orient.add_argument("--flip-x", action="store_true")
    orient.add_argument("--flip-y", action="store_true")
    orient.add_argument("--transpose", action="store_true")

    filt = p.add_argument_group("filtering / foreground")
    filt.add_argument(
        "--calibration-frames",
        type=int,
        default=None,
        help="Frames used for empty-scene background calibration; defaults to 60, or 0 with --dual/demo",
    )
    filt.add_argument("--invalid-zero", action=argparse.BooleanOptionalAction, default=True)
    filt.add_argument("--median-window", type=int, default=3, help="Temporal median frames; 1 disables")
    filt.add_argument("--ema-alpha", type=float, default=0.64, help="Raw distance smoothing alpha")
    filt.add_argument("--centroid-alpha", type=float, default=0.62)
    filt.add_argument("--z-alpha", type=float, default=0.62)
    filt.add_argument("--gesture-max-mm", type=int, default=1600)
    filt.add_argument("--min-track-z-mm", type=float, default=75.0, help="Suppress gesture classification when weighted depth is too close/reliably clipped")
    filt.add_argument("--background-delta-mm", type=int, default=70)
    filt.add_argument("--noise-k", type=float, default=4.0, help="Per-pixel noise multiplier for calibrated foreground threshold")
    filt.add_argument("--min-noise-mm", type=float, default=10.0)
    filt.add_argument("--max-noise-mm", type=float, default=120.0)
    filt.add_argument("--weight-range-mm", type=int, default=550)
    filt.add_argument("--absolute-close-mix", type=float, default=0.18)
    filt.add_argument("--foreground-threshold", type=float, default=0.10)
    filt.add_argument("--spatial-min-neighbors", type=int, default=2, help="Remove foreground cells with fewer active cells in their 3x3 neighborhood")
    filt.add_argument("--min-component-cells", type=int, default=2)
    filt.add_argument("--good-component-cells", type=float, default=7.0)
    filt.add_argument("--min-mass", type=float, default=0.24)
    filt.add_argument("--good-mass", type=float, default=2.6)
    filt.add_argument("--min-quality", type=float, default=0.24)
    filt.add_argument("--near-core-blend", type=float, default=0.45, help="Blend whole-blob centroid with nearest-part centroid")
    filt.add_argument("--near-core-boost", type=float, default=3.0)
    filt.add_argument("--near-core-power", type=float, default=2.0)
    filt.add_argument("--field-motion", action=argparse.BooleanOptionalAction, default=True)
    filt.add_argument("--field-motion-gain", type=float, default=1.35)
    filt.add_argument("--field-min-quality", type=float, default=0.18)
    filt.add_argument("--field-swipe-cells", type=float, default=1.0)
    filt.add_argument("--max-field-shift-cells", type=float, default=0.9)
    filt.add_argument("--field-change-scale", type=float, default=0.06)
    filt.add_argument("--field-gradient-scale", type=float, default=0.015)
    filt.add_argument("--adaptive-bg", action=argparse.BooleanOptionalAction, default=True)
    filt.add_argument("--adaptive-bg-alpha", type=float, default=0.0025)

    gest = p.add_argument_group("gesture / stroke tuning")
    gest.add_argument("--enter-quality", type=float, default=0.30)
    gest.add_argument("--exit-grace-s", type=float, default=0.12)
    gest.add_argument("--min-gesture-frames", type=int, default=4)
    gest.add_argument("--min-stroke-s", type=float, default=0.10)
    gest.add_argument("--max-stroke-s", type=float, default=1.35)
    gest.add_argument("--cooldown-s", type=float, default=0.55)
    gest.add_argument("--gesture-confidence-threshold", type=float, default=0.52)
    gest.add_argument("--gesture-tail-s", type=float, default=0.38, help="Recent stroke window used to resolve reset-then-swipe motion")
    gest.add_argument("--gesture-tail-confidence-threshold", type=float, default=0.50)
    gest.add_argument("--endpoint-fraction", type=float, default=0.22)
    gest.add_argument("--motion-start-energy", type=float, default=0.22)
    gest.add_argument("--motion-continue-energy", type=float, default=0.10)
    gest.add_argument("--motion-idle-s", type=float, default=0.26)
    gest.add_argument("--motion-prev-grace-s", type=float, default=0.22)
    gest.add_argument("--motion-field-weight", type=float, default=0.35)
    gest.add_argument("--motion-z-weight", type=float, default=0.55)
    gest.add_argument("--motion-z-scale-mm", type=float, default=350.0)
    gest.add_argument("--motion-z-cap", type=float, default=0.8)
    gest.add_argument("--min-motion-peak", type=float, default=0.34)
    gest.add_argument("--min-motion-path", type=float, default=0.75)
    gest.add_argument("--motion-force-finish-energy", type=float, default=0.75)
    gest.add_argument("--field-only-score-cap", type=float, default=0.48)
    gest.add_argument("--field-corroboration-cells", type=float, default=0.45)
    gest.add_argument("--field-corroboration-window", type=float, default=0.65)
    gest.add_argument("--suppress-visible-return", action=argparse.BooleanOptionalAction, default=True, help="After a gesture, cap the opposite-direction swipe until the hand leaves view")
    gest.add_argument("--visible-return-score-cap", type=float, default=0.30, help="Maximum confidence allowed for a visible opposite-direction return/reset")
    gest.add_argument("--track-max-jump-cells", type=float, default=2.5)
    gest.add_argument("--track-continuity-weight", type=float, default=0.75)
    gest.add_argument("--swipe-cells", type=float, default=1.25)
    gest.add_argument("--swipe-dominance", type=float, default=1.28)
    gest.add_argument("--min-swipe-speed", type=float, default=1.1)
    gest.add_argument("--push-mm", type=float, default=220)
    gest.add_argument("--push-max-xy-cells", type=float, default=1.15)
    gest.add_argument("--hold-s", type=float, default=0.70)
    gest.add_argument("--hold-radius-cells", type=float, default=1.55)
    gest.add_argument("--hold-jitter-cells", type=float, default=0.55)

    out = p.add_argument_group("macros / output")
    out.add_argument("--enable-macros", action="store_true")
    out.add_argument("--csv", default=None, help="Optional frame/feature/event CSV log")
    out.add_argument("--diag-log", default=None, help="JSONL diagnostic log path, directory, or 'auto'")
    out.add_argument("--diag-label", default=None, help="Intended gesture/action label for this diagnostic run")
    out.add_argument("--diag-include-frames", action=argparse.BooleanOptionalAction, default=True, help="Include raw/foreground/component 8x8 maps in diagnostic log")
    out.add_argument("--diag-flush-frames", type=int, default=5, help="Flush diagnostic log every N frames")
    out.add_argument("--record-dir", default=None, help="Directory for labelled gesture stroke samples")
    return p


def configure_runtime_args(args: argparse.Namespace) -> argparse.Namespace:
    """Apply the same runtime defaults for CLI, UI, and embedded engine use."""
    if args.baud is None:
        args.baud = 115200 if args.dual else 921600
    if args.calibration_frames is None:
        args.calibration_frames = 0 if (args.demo or args.dual) else 60
    if args.dual:
        # Current GesturePuck firmware uses 4000 as the far/no-return value, and
        # the close hand readings seen during bring-up can be below 20 mm.
        if args.sensor_max_mm == 3500:
            args.sensor_max_mm = 4000
        if args.valid_min_mm == 20:
            args.valid_min_mm = 1
        if args.min_mm == 20:
            args.min_mm = 0
        if args.min_track_z_mm == 75.0:
            args.min_track_z_mm = 1.0
    return args


def main() -> int:
    args = build_arg_parser().parse_args()
    if not args.demo and not args.port:
        raise SystemExit("Provide --port /dev/cu.usbmodem2101, or use --demo")

    configure_runtime_args(args)

    serial_debug_path = resolve_serial_debug_path(args.serial_debug_log)
    serial_debug = SerialDebugLogger(
        enabled=args.serial_debug,
        path=serial_debug_path,
        log_reads=args.serial_debug_bytes,
    )

    if args.demo:
        source = DemoFrameSource(max_mm=args.max_mm)
    elif args.dual:
        # Dual-sensor mode accepts both debug text at 115200 and binary MLD1/MLD2 streams.
        source = DualSensorSerialFrameSource(args.port, args.baud, serial_debug=serial_debug)
    else:
        source = SerialFrameSource(args.port, args.baud, serial_debug=serial_debug)

    pipeline = SignalPipeline(args)
    detector = StrokeGestureDetector(args)
    macros = MacroManager(args.enable_macros)
    logger = CSVLogger(args.csv)
    diag_logger = DiagnosticLogger(args.diag_log, args)
    recorder = SampleRecorder(args.record_dir)

    source.start()
    print("LiDAR Gesture Studio v2 started.")
    if args.dual:
        print("Dual-sensor mode: APDS-9930 proximity + ToF #1 (gesture) + ToF #2 (companion).")
        print(f"Reading {args.port} at {args.baud} baud.")
        print("This accepts the current Serial Monitor debug tables and binary MLD1/MLD2 firmware.")
    else:
        print("Close PlatformIO Serial Monitor before using this script.")
    if args.calibration_frames > 0:
        print("Keep your hand out of view during calibration, then perform gestures inside the 8x8 grid.")
    else:
        print("Background calibration is disabled; perform gestures inside the 8x8 grid.")
    print("Press r in the window to recalibrate. Use --flip-x / --flip-y if directions are mirrored.")
    if diag_logger.enabled and diag_logger.path is not None:
        print(f"Diagnostic log: {diag_logger.path}")
    if serial_debug.enabled:
        if serial_debug.path is not None:
            print(f"Serial debug log: {serial_debug.path}")
        else:
            print("Serial debug logging: stderr")
        if not args.serial_debug_bytes:
            print("Use --serial-debug-bytes to also dump raw serial read chunks.")
    print()

    viz = Visualizer(args, source, pipeline, detector, macros, logger, diag_logger, recorder)
    viz.run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
