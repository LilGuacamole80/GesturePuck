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
        logger=None,
    ):
        self.on_gesture = on_gesture
        self.on_status = on_status
        self.logger = logger
        self._stop = threading.Event()
        self._state_lock = threading.RLock()
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
        self._log(
            "engine_init",
            f"demo={demo} dual={self.args.dual} port={self.args.port!r} baud={self.args.baud} "
            f"calibration_frames={self.args.calibration_frames}",
        )

        serial_debug_path = resolve_serial_debug_path(self.args.serial_debug_log)
        self.serial_debug = SerialDebugLogger(
            enabled=self.args.serial_debug,
            path=serial_debug_path,
            log_reads=self.args.serial_debug_bytes,
        )

        if demo:
            # Demo: synthetic single-sensor sweep, no calibration delay
            self._log("engine_source", "creating DemoFrameSource")
            self.source = DemoFrameSource(max_mm=self.args.max_mm)
            self._dual = False
        elif self.args.dual:
            # Current firmware uses the dual parser even during one-ToF bring-up:
            # APDS #PROX lines plus FRAME/FRAME1 CSV from ToF #1.
            self._emit_status(f"OPENING SERIAL {port}")
            self._log("engine_source", f"opening DualSensorSerialFrameSource port={port!r} baud={self.args.baud}")
            self.source = DualSensorSerialFrameSource(
                port,
                self.args.baud,
                serial_debug=self.serial_debug,
            )
            self._log("engine_source", "dual serial source opened")
            self._dual = True
        else:
            self._emit_status(f"OPENING SERIAL {port}")
            self._log("engine_source", f"opening SerialFrameSource port={port!r} baud={self.args.baud}")
            self.source = SerialFrameSource(
                port,
                self.args.baud,
                serial_debug=self.serial_debug,
            )
            self._log("engine_source", "serial source opened")
            self._dual = False

        self.pipeline = SignalPipeline(self.args)
        self.detector = StrokeGestureDetector(self.args)
        self._thread = threading.Thread(target=self._loop, daemon=True)

    # ── Public API ────────────────────────────────────────────────────────────

    def start(self) -> None:
        self._log("engine_start", "starting source and processing thread")
        self.source.start()
        self._thread.start()
        self._log("engine_start", "started")

    def stop(self) -> None:
        self._log("engine_stop", "stopping")
        self._stop.set()
        self.source.close()
        self.serial_debug.close()
        if self._thread.is_alive():
            self._thread.join(timeout=0.5)
        self._log("engine_stop", "stopped")

    def recalibrate(self) -> None:
        """Reset gesture state and restart background calibration if enabled."""
        with self._state_lock:
            self.pipeline.start_calibration()
            self.detector.clear()
        if self.args.calibration_frames > 0:
            self._emit_status(f"RECALIBRATING {self.args.calibration_frames} FRAMES")
            self._log("engine_recalibrate", f"calibration_frames={self.args.calibration_frames}")
        else:
            self._emit_status("RECALIBRATED")
            self._log("engine_recalibrate", "state reset; background calibration disabled")

    # ── Internal loop ─────────────────────────────────────────────────────────

    def _log(self, event: str, message: str) -> None:
        if self.logger is not None:
            self.logger.log(event, message)

    def _log_exception(self, event: str, exc: BaseException) -> None:
        if self.logger is not None:
            self.logger.exception(event, exc)

    def _emit_status(self, text: str) -> None:
        self._log("engine_status", text)
        if self.on_status is not None:
            self.on_status(text)

    def _loop(self) -> None:
        try:
            self._emit_status("WAITING FOR FRAMES")
            last_wait_log = 0.0
            while not self._stop.is_set():
                raw = self.source.read_latest()

                if raw is None:
                    now = time.time()
                    if now - last_wait_log >= 2.0:
                        last_wait_log = now
                        try:
                            stats = self.source.stats()
                        except Exception as exc:
                            stats = f"stats unavailable: {exc}"
                        self._log("engine_wait", stats)
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
                elif self.frames_seen % 100 == 0:
                    self._log("engine_frames", f"frames_seen={self.frames_seen} protocol={packet.protocol}")

                with self._state_lock:
                    measurement = self.pipeline.process(packet)
                    event = self.detector.update(measurement)

                if event is not None:
                    self._log(
                        "engine_gesture",
                        f"name={event.name} confidence={event.confidence:.3f} details={event.details}",
                    )
                    # Fire callback — the UI uses root.after() to marshal back to
                    # the main thread, so it is safe to call directly here.
                    self.on_gesture(event.name, event.confidence)
        except Exception as exc:
            self._log_exception("engine_loop_exception", exc)
            self._emit_status(f"ERROR {type(exc).__name__}")
