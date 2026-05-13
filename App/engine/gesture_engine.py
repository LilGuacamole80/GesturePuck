"""
GestureEngine — connects the ESP32 dual-sensor serial stream to the
gesture-classification pipeline and fires on_gesture() callbacks.

Real hardware  : DualSensorSerialFrameSource  (MLD1 + MLD2 binary packets,
                 #PROX proximity lines)
Demo mode      : DemoFrameSource  (synthetic sine-wave sweep)
"""

import threading
import time

from lidar_gesture_studio import (
    DualSensorSerialFrameSource,
    SignalPipeline,
    StrokeGestureDetector,
    DemoFrameSource,
    build_arg_parser,
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

    def __init__(self, port: str, on_gesture, demo: bool = False):
        self.on_gesture = on_gesture
        self._stop = threading.Event()

        # Build default args (no CLI parsing needed)
        self.args = build_arg_parser().parse_args([])

        if demo:
            # Demo: synthetic single-sensor sweep, no calibration delay
            self.args.calibration_frames = 0
            self.source = DemoFrameSource(max_mm=self.args.max_mm)
            self._dual = False
        else:
            # Real hardware: dual ToF + APDS proximity gate
            self.args.port = port
            self.source = DualSensorSerialFrameSource(port, self.args.baud)
            self._dual = True

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

    # ── Internal loop ─────────────────────────────────────────────────────────

    def _loop(self) -> None:
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

            measurement = self.pipeline.process(packet)
            event = self.detector.update(measurement)

            if event is not None:
                # Fire callback — the UI uses root.after() to marshal back to
                # the main thread, so it is safe to call directly here.
                self.on_gesture(event.name, event.confidence)