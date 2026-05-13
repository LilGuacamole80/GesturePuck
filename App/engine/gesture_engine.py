import threading
import time
from lidar_gesture_studio import (
    SerialFrameSource, SignalPipeline, StrokeGestureDetector,
    DemoFrameSource, build_arg_parser
)
import argparse

class GestureEngine:
    def __init__(self, port: str, on_gesture, demo=False):
        self.on_gesture = on_gesture
        self._stop = threading.Event()
        
        self.args = build_arg_parser().parse_args([])  # ← was _build_default_parser()
        
        if demo:
            self.args.calibration_frames = 0
            self.source = DemoFrameSource(max_mm=self.args.max_mm)
        else:
            self.args.port = port
            self.source = SerialFrameSource(port, self.args.baud)
        
        self.pipeline = SignalPipeline(self.args)
        self.detector = StrokeGestureDetector(self.args)
        self._thread = threading.Thread(target=self._loop, daemon=True)

    def start(self):
        self.source.start()
        self._thread.start()

    def stop(self):
        self._stop.set()
        self.source.close()

    def _loop(self):
        while not self._stop.is_set():
            packet = self.source.read_latest()
            if packet is None:
                time.sleep(0.01)
                continue
            measurement = self.pipeline.process(packet)
            event = self.detector.update(measurement)
            if event is not None:
                self.on_gesture(event.name, event.confidence)