#!/usr/bin/env python3
"""Interactive affine calibration for the GesturePuck ToF sensor cluster."""

from __future__ import annotations

import argparse
import json
import math
import sys
import time
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import numpy as np

from lidar_gesture_studio import (
    DEFAULT_SENSOR_CALIBRATION_FILE,
    DualSensorSerialFrameSource,
    FusedSignalPipeline,
    SerialDebugLogger,
    build_arg_parser,
    configure_runtime_args,
    default_sensor_calibration_path,
    resolve_serial_debug_path,
)


XY_TARGETS: Tuple[Tuple[str, str, Tuple[float, float]], ...] = (
    ("center", "over the center of the puck", (3.5, 3.5)),
    ("up", "near the top edge of the puck", (3.5, 1.5)),
    ("down", "near the bottom edge of the puck", (3.5, 5.5)),
    ("left", "near the left edge of the puck", (1.5, 3.5)),
    ("right", "near the right edge of the puck", (5.5, 3.5)),
)
Z_UP_TARGET = "z_up"
Z_DOWN_TARGET = "z_down"
Z_TARGETS: Tuple[Tuple[str, str, Tuple[float, float]], ...] = (
    (Z_UP_TARGET, "above the center at the raised pull height", (3.5, 3.5)),
    (Z_DOWN_TARGET, "above the center at the lowered push height", (3.5, 3.5)),
)
TARGETS: Tuple[Tuple[str, str, Tuple[float, float]], ...] = XY_TARGETS + Z_TARGETS
Z_TARGET_NAMES = {Z_UP_TARGET, Z_DOWN_TARGET}
MIN_Z_SPAN_MM = 20.0
PUSH_THRESHOLD_FRACTION = 0.55
MIN_CALIBRATED_PUSH_MM = 35.0
MAX_CALIBRATED_PUSH_MM = 260.0


Sample = Dict[str, float]
CaptureMap = Dict[str, Dict[int, Sample]]


def finite_float(value: object) -> Optional[float]:
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    if not np.isfinite(out):
        return None
    return out


def resolve_output_path(value: str) -> Path:
    if value.lower() == "auto":
        return default_sensor_calibration_path()
    path = Path(value).expanduser()
    if path.exists() and path.is_dir():
        return path / DEFAULT_SENSOR_CALIBRATION_FILE
    return path


def prompt_enter(message: str) -> None:
    try:
        input(message)
    except KeyboardInterrupt as exc:
        raise SystemExit("\nCalibration cancelled.") from exc


def build_runtime_args(cli_args: argparse.Namespace) -> argparse.Namespace:
    args = build_arg_parser().parse_args([])
    args.demo = False
    args.dual = True
    args.port = cli_args.port
    args.baud = cli_args.baud
    args.serial_debug = cli_args.serial_debug
    args.serial_debug_log = cli_args.serial_debug_log
    args.serial_debug_bytes = cli_args.serial_debug_bytes
    args.flip_x = cli_args.flip_x
    args.flip_y = cli_args.flip_y
    args.transpose = cli_args.transpose
    args.sensor_calibration = "off"
    args.fusion_mode = "best-track"
    args.gesture_max_mm = cli_args.gesture_max_mm
    args.min_quality = cli_args.min_quality
    args.min_component_cells = cli_args.min_component_cells
    configure_runtime_args(args)
    return args


def read_visible_sensor_samples(
    source: DualSensorSerialFrameSource,
    pipeline: FusedSignalPipeline,
    *,
    sample_seconds: float,
    warmup_seconds: float,
    min_quality: float,
) -> Tuple[Dict[int, List[Tuple[float, float, float, float]]], int]:
    samples: Dict[int, List[Tuple[float, float, float, float]]] = {}
    frames_seen = 0
    sample_start = time.time() + warmup_seconds
    deadline = sample_start + sample_seconds

    while time.time() < deadline:
        packet = source.read_latest()
        if packet is None:
            time.sleep(0.01)
            continue

        measurement = pipeline.process(packet)
        frames_seen += 1
        if time.time() < sample_start:
            continue

        sensor_measurements = measurement.fusion.get("sensor_measurements", {}) if measurement.fusion else {}
        if not isinstance(sensor_measurements, dict):
            continue
        for key, info in sensor_measurements.items():
            if not isinstance(info, dict) or not info.get("visible"):
                continue
            quality = finite_float(info.get("quality"))
            x = finite_float(info.get("x"))
            y = finite_float(info.get("y"))
            z = finite_float(info.get("z"))
            if quality is None or x is None or y is None or z is None:
                continue
            if quality < min_quality:
                continue
            try:
                sensor_id = int(key)
            except (TypeError, ValueError):
                continue
            samples.setdefault(sensor_id, []).append((x, y, z, quality))
    return samples, frames_seen


def summarize_sensor_samples(
    samples: Dict[int, List[Tuple[float, float, float, float]]],
    *,
    min_samples: int,
) -> Dict[int, Sample]:
    summary: Dict[int, Sample] = {}
    for sensor_id, rows in samples.items():
        if len(rows) < min_samples:
            continue
        arr = np.asarray(rows, dtype=float)
        summary[sensor_id] = {
            "x": float(np.median(arr[:, 0])),
            "y": float(np.median(arr[:, 1])),
            "z": float(np.median(arr[:, 2])),
            "quality": float(np.median(arr[:, 3])),
            "n": float(len(rows)),
        }
    return summary


def collect_target(
    source: DualSensorSerialFrameSource,
    pipeline: FusedSignalPipeline,
    *,
    name: str,
    description: str,
    sample_seconds: float,
    warmup_seconds: float,
    min_quality: float,
    min_samples: int,
) -> Dict[int, Sample]:
    while True:
        print(f"\n{name.upper()}: put one finger/card {description}.")
        prompt_enter("Remove your hand, then press Enter when the field is clear. ")
        while source.read_latest() is not None:
            pass
        pipeline.start_calibration()
        prompt_enter("Place it now and keep it still, then press Enter immediately to sample. ")
        samples, frames_seen = read_visible_sensor_samples(
            source,
            pipeline,
            sample_seconds=sample_seconds,
            warmup_seconds=warmup_seconds,
            min_quality=min_quality,
        )
        summary = summarize_sensor_samples(samples, min_samples=min_samples)
        if summary:
            parts = []
            for sensor_id in sorted(summary):
                item = summary[sensor_id]
                parts.append(
                    f"ToF#{sensor_id} x={item['x']:.2f} y={item['y']:.2f} "
                    f"z={item['z']:.0f}mm q={item['quality']:.2f} n={int(item['n'])}"
                )
            print(f"Captured {name}: " + "; ".join(parts))
            return summary
        print(
            f"No usable samples captured for {name} "
            f"(frames={frames_seen}, min_quality={min_quality}, min_samples={min_samples}). Try that point again."
        )


def fit_sensor_z_calibrations(captures: CaptureMap) -> Tuple[Dict[str, Dict[str, object]], Optional[Dict[str, object]]]:
    z_up = captures.get(Z_UP_TARGET, {})
    z_down = captures.get(Z_DOWN_TARGET, {})
    rows: List[Tuple[int, Sample, Sample, float]] = []
    spans: List[float] = []
    down_values: List[float] = []

    for sensor_id in sorted(set(z_up) & set(z_down)):
        up_sample = z_up.get(sensor_id, {})
        down_sample = z_down.get(sensor_id, {})
        up_z = finite_float(up_sample.get("z"))
        down_z = finite_float(down_sample.get("z"))
        if up_z is None or down_z is None:
            continue
        raw_span = up_z - down_z
        if abs(raw_span) < MIN_Z_SPAN_MM:
            print(
                f"Skipping ToF#{sensor_id} Z calibration: z_up/z_down span "
                f"{raw_span:.1f} mm is too small; need at least {MIN_Z_SPAN_MM:.0f} mm."
            )
            continue
        rows.append((sensor_id, down_sample, up_sample, raw_span))
        spans.append(abs(raw_span))
        down_values.append(down_z)

    if not rows:
        return {}, None

    common_down = float(np.median(np.asarray(down_values, dtype=float)))
    common_span = float(np.median(np.asarray(spans, dtype=float)))
    common_up = common_down + common_span
    push_mm = float(np.clip(common_span * PUSH_THRESHOLD_FRACTION, MIN_CALIBRATED_PUSH_MM, MAX_CALIBRATED_PUSH_MM))

    fitted: Dict[str, Dict[str, object]] = {}
    for sensor_id, down_sample, up_sample, raw_span in rows:
        down_z = float(down_sample["z"])
        up_z = float(up_sample["z"])
        scale = common_span / raw_span
        offset = common_down - scale * down_z
        fitted[str(sensor_id)] = {
            "down_raw_mm": round(down_z, 3),
            "up_raw_mm": round(up_z, 3),
            "span_raw_mm": round(float(raw_span), 3),
            "down_calibrated_mm": round(common_down, 3),
            "up_calibrated_mm": round(common_up, 3),
            "span_calibrated_mm": round(common_span, 3),
            "scale": round(float(scale), 8),
            "offset": round(float(offset), 5),
            "quality": round(
                float(min(
                    finite_float(down_sample.get("quality")) or 0.0,
                    finite_float(up_sample.get("quality")) or 0.0,
                )),
                5,
            ),
            "samples": {
                Z_DOWN_TARGET: {
                    "x": round(float(down_sample["x"]), 5),
                    "y": round(float(down_sample["y"]), 5),
                    "z": round(down_z, 3),
                    "quality": round(float(down_sample["quality"]), 5),
                    "n": int(down_sample["n"]),
                },
                Z_UP_TARGET: {
                    "x": round(float(up_sample["x"]), 5),
                    "y": round(float(up_sample["y"]), 5),
                    "z": round(up_z, 3),
                    "quality": round(float(up_sample["quality"]), 5),
                    "n": int(up_sample["n"]),
                },
            },
        }

    z_axis = {
        "down_mm": round(common_down, 3),
        "up_mm": round(common_up, 3),
        "span_mm": round(common_span, 3),
        "push_mm": round(push_mm, 3),
        "motion_z_scale_mm": round(float(np.clip(common_span * 1.35, 80.0, 600.0)), 3),
        "sensor_count": len(fitted),
    }
    return fitted, z_axis


def fit_sensor_affines(captures: CaptureMap) -> Dict[str, Dict[str, object]]:
    targets_by_name = {name: common for name, _description, common in XY_TARGETS}
    samples_by_sensor: Dict[int, List[Tuple[str, Sample, Tuple[float, float]]]] = {}
    for name, sensor_samples in captures.items():
        if name not in targets_by_name:
            continue
        common = targets_by_name[name]
        for sensor_id, sample in sensor_samples.items():
            samples_by_sensor.setdefault(sensor_id, []).append((name, sample, common))

    fitted: Dict[str, Dict[str, object]] = {}
    for sensor_id, rows in sorted(samples_by_sensor.items()):
        if len(rows) < 3:
            print(f"Skipping ToF#{sensor_id}: only {len(rows)} usable calibration points; need at least 3.")
            continue

        a = np.asarray([[sample["x"], sample["y"], 1.0] for _name, sample, _common in rows], dtype=float)
        b = np.asarray([common for _name, _sample, common in rows], dtype=float)
        coeff, *_ = np.linalg.lstsq(a, b, rcond=None)
        predicted = a @ coeff
        residuals = predicted - b
        rms = float(math.sqrt(np.mean(np.sum(residuals * residuals, axis=1))))

        matrix = [
            [float(coeff[0, 0]), float(coeff[1, 0]), float(coeff[2, 0])],
            [float(coeff[0, 1]), float(coeff[1, 1]), float(coeff[2, 1])],
        ]
        sensor_weight = float(np.clip(1.0 / (1.0 + rms / 2.0), 0.45, 1.05))
        fitted[str(sensor_id)] = {
            "matrix": matrix,
            "sensor_weight": round(sensor_weight, 5),
            "rms_error_cells": round(rms, 5),
            "points_used": len(rows),
            "samples": {
                name: {
                    "x": round(float(sample["x"]), 5),
                    "y": round(float(sample["y"]), 5),
                    "z": round(float(sample["z"]), 3),
                    "quality": round(float(sample["quality"]), 5),
                    "n": int(sample["n"]),
                    "target": [float(common[0]), float(common[1])],
                }
                for name, sample, common in rows
            },
        }

    z_fits, _z_axis = fit_sensor_z_calibrations(captures)
    for sensor_id, z_calibration in z_fits.items():
        fitted.setdefault(
            sensor_id,
            {
                "sensor_weight": 1.0,
                "rms_error_cells": None,
                "points_used": 0,
                "samples": {},
            },
        )
        fitted[sensor_id]["z_calibration"] = z_calibration
    return fitted


def save_calibration(path: Path, captures: CaptureMap, sensors: Dict[str, Dict[str, object]]) -> None:
    z_fits, z_axis = fit_sensor_z_calibrations(captures)
    for sensor_id, z_calibration in z_fits.items():
        sensors.setdefault(
            sensor_id,
            {
                "sensor_weight": 1.0,
                "rms_error_cells": None,
                "points_used": 0,
                "samples": {},
            },
        )
        sensors[sensor_id]["z_calibration"] = z_calibration
    payload = {
        "version": 2,
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%S%z", time.localtime()),
        "coordinate_frame": {
            "grid_size": 8,
            "x": "left-to-right",
            "y": "top-to-bottom",
            "z": "down is toward puck, up is away from puck",
        },
        "targets": {name: [common[0], common[1]] for name, _description, common in TARGETS},
        "target_axes": {
            name: "z" if name in Z_TARGET_NAMES else "xy"
            for name, _description, _common in TARGETS
        },
        "sensors": sensors,
        "capture_summary": {
            name: {
                str(sensor_id): {
                    "x": round(sample["x"], 5),
                    "y": round(sample["y"], 5),
                    "z": round(sample["z"], 3),
                    "quality": round(sample["quality"], 5),
                    "n": int(sample["n"]),
                }
                for sensor_id, sample in sensor_samples.items()
            }
            for name, sensor_samples in captures.items()
        },
    }
    if z_axis is not None:
        payload["z_axis"] = z_axis
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=2)
        fh.write("\n")


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Calibrate the three ToF sensor coordinate frames, including push/pull Z depth.")
    p.add_argument("--port", required=True, help="Serial port, e.g. /dev/cu.usbserial-0001")
    p.add_argument("--baud", type=int, default=115200)
    p.add_argument("--output", default="auto", help="Calibration JSON path, directory, or 'auto'")
    p.add_argument("--sample-seconds", type=float, default=1.2)
    p.add_argument("--warmup-seconds", type=float, default=0.15)
    p.add_argument("--min-quality", type=float, default=0.18)
    p.add_argument("--min-samples", type=int, default=3)
    p.add_argument("--min-component-cells", type=int, default=2)
    p.add_argument("--gesture-max-mm", type=int, default=1600)
    p.add_argument("--flip-x", action="store_true")
    p.add_argument("--flip-y", action="store_true")
    p.add_argument("--transpose", action="store_true")
    p.add_argument("--serial-debug", action="store_true")
    p.add_argument("--serial-debug-log", default=None)
    p.add_argument("--serial-debug-bytes", action="store_true")
    return p


def main() -> int:
    cli_args = build_parser().parse_args()
    output_path = resolve_output_path(cli_args.output)
    runtime_args = build_runtime_args(cli_args)
    serial_debug_path = resolve_serial_debug_path(cli_args.serial_debug_log)
    serial_debug = SerialDebugLogger(
        enabled=cli_args.serial_debug,
        path=serial_debug_path,
        log_reads=cli_args.serial_debug_bytes,
    )
    source = DualSensorSerialFrameSource(cli_args.port, cli_args.baud, serial_debug=serial_debug)
    pipeline = FusedSignalPipeline(runtime_args)

    print("GesturePuck sensor calibration")
    print("Use the same physical orientation and push/pull height range you use during gestures.")
    print("Press Ctrl-C to cancel. The output file will be:")
    print(f"  {output_path}")

    captures: CaptureMap = {}
    try:
        source.start()
        print("\nReader started. Wait a second for serial data to settle.")
        time.sleep(0.5)
        for name, description, _common in TARGETS:
            captures[name] = collect_target(
                source,
                pipeline,
                name=name,
                description=description,
                sample_seconds=cli_args.sample_seconds,
                warmup_seconds=cli_args.warmup_seconds,
                min_quality=cli_args.min_quality,
                min_samples=cli_args.min_samples,
            )
        sensors = fit_sensor_affines(captures)
        if not sensors:
            raise SystemExit("No sensors had enough usable points to fit calibration.")
        save_calibration(output_path, captures, sensors)
    finally:
        source.close()

    print("\nCalibration saved.")
    for sensor_id, info in sorted(sensors.items(), key=lambda item: int(item[0])):
        z_info = info.get("z_calibration") if isinstance(info, dict) else None
        z_text = ""
        if isinstance(z_info, dict):
            z_text = f" z_span={z_info.get('span_calibrated_mm')}mm"
        rms = info.get("rms_error_cells")
        rms_text = f"{rms} cells" if rms is not None else "xy skipped"
        print(
            f"  ToF#{sensor_id}: rms={rms_text} "
            f"weight={info['sensor_weight']} points={info['points_used']}{z_text}"
        )
    _z_fits, z_axis = fit_sensor_z_calibrations(captures)
    if z_axis is not None:
        print(
            f"  Z push/pull: span={z_axis['span_mm']}mm "
            f"push_mm={z_axis['push_mm']} motion_z_scale_mm={z_axis['motion_z_scale_mm']}"
        )
    print("\nNext run the detector with calibration enabled:")
    print(
        f"  MPLCONFIGDIR=/private/tmp .venv/bin/python lidar_gesture_studio.py "
        f"--port {cli_args.port} --dual --sensor-calibration auto --fusion-mode auto "
        f"--serial-debug --diag-log auto"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())