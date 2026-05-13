"""
GestureEngine — connects the ESP32 dual-sensor serial stream to the
gesture-classification pipeline and fires on_gesture() callbacks.

Real hardware  : DualSensorSerialFrameSource  (#PROX proximity lines plus
                 FRAME/FRAME1 CSV or MLD1/MLD2 binary packets)
Demo mode      : DemoFrameSource  (synthetic sine-wave sweep)
"""

import threading
import time

from lidar_gesture_studio import (
    DualSensorSerialFrameSource,
    SerialFrameSource,
    SignalPipeline,
    StrokeGestureDetector,
    DemoFrameSource,
    SerialDebugLogger,
    build_arg_parser,
    configure_runtime_args,
    resolve_serial_debug_path,
)


class GestureEngine:
    """
    Runs the full sensing → classification → callback pipeline.

    Parameters
    ----------
    port : str
        Serial port path, e.g. ``/dev/cu.usbmodem2101`` or ``COM3``.
        Ignored when *demo* is True.
    on_gesture : callable(name: str, confidence: float)
        Called (from a background thread via root.after) whenever a gesture
        is recognised.
    demo : bool
        If True, use the synthetic DemoFrameSource instead of real hardware.
    """

    def __init__(
        self,
        port: str,
        on_gesture,
        demo: bool = False,
        *,
        dual: bool = True,
        baud: int | None = None,
        serial_debug: bool = False,
        serial_debug_log: str | None = None,
        serial_debug_bytes: bool = False,
        on_status=None,
    ):
        self.on_gesture = on_gesture
        self.on_status = on_status
        self._stop = threading.Event()
        self.frames_seen = 0

        # Build default args (no CLI parsing needed)
        self.args = build_arg_parser().parse_args([])
        self.args.demo = demo
        self.args.dual = bool(dual and not demo)
        self.args.port = port or None
        self.args.baud = baud
        self.args.serial_debug = serial_debug
        self.args.serial_debug_log = serial_debug_log
        self.args.serial_debug_bytes = serial_debug_bytes
        configure_runtime_args(self.args)

        serial_debug_path = resolve_serial_debug_path(self.args.serial_debug_log)
        self.serial_debug = SerialDebugLogger(
            enabled=self.args.serial_debug,
            path=serial_debug_path,
            log_reads=self.args.serial_debug_bytes,
        )

        if demo:
            # Demo: synthetic single-sensor sweep, no calibration delay
            self.source = DemoFrameSource(max_mm=self.args.max_mm)
            self._dual = False
        elif self.args.dual:
            # Current firmware uses the dual parser even during one-ToF bring-up:
            # APDS #PROX lines plus FRAME/FRAME1 CSV from ToF #1.
            self.source = DualSensorSerialFrameSource(
                port,
                self.args.baud,
                serial_debug=self.serial_debug,
            )
            self._dual = True
        else:
            self.source = SerialFrameSource(
                port,
                self.args.baud,
                serial_debug=self.serial_debug,
            )
            self._dual = False

        self.pipeline = SignalPipeline(self.args)
        self.detector = StrokeGestureDetector(self.args)
        self._thread = threading.Thread(target=self._loop, daemon=True)

    # ── Public API ────────────────────────────────────────────────────────────

    def start(self) -> None:
        self.source.start()
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self.source.close()
        self.serial_debug.close()
        if self._thread.is_alive():
            self._thread.join(timeout=0.5)

    # ── Internal loop ─────────────────────────────────────────────────────────

    def _emit_status(self, text: str) -> None:
        if self.on_status is not None:
            self.on_status(text)

    def _loop(self) -> None:
        self._emit_status("WAITING FOR FRAMES")
        while not self._stop.is_set():
            raw = self.source.read_latest()

            if raw is None:
                time.sleep(0.01)
                continue

            # DualSensorSerialFrameSource returns a DualFramePacket;
            # SignalPipeline.process() expects a plain FramePacket (ToF #1).
            if self._dual:
                packet = raw.to_frame_packet()
            else:
                packet = raw   # DemoFrameSource already returns a FramePacket

            self.frames_seen += 1
            if self.frames_seen == 1:
                self._emit_status("RECEIVING FRAMES")

            measurement = self.pipeline.process(packet)
            event = self.detector.update(measurement)

            if event is not None:
                # Fire callback — the UI uses root.after() to marshal back to
                # the main thread, so it is safe to call directly here.
                self.on_gesture(event.name, event.confidence)
