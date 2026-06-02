#!/usr/bin/env python3
"""
Analyze a LiDAR Gesture Studio v2 diagnostic JSONL log to debug 3x ToF fusion.

Run a session with:  ... --dual --diag-log auto
Then:                python analyze_fusion.py logs/lidar_diag_XXXX.jsonl

It answers three questions:
  1. Is each ToF head actually seeing the hand?
  2. During a stroke, does the "best" head switch (the thing that breaks swipes)?
  3. Do gesture events fire, and do they match the intended label?

Pure stdlib, no numpy needed.
"""

import json
import math
import sys
from collections import Counter, defaultdict


def load(path):
    meta, frames = {}, []
    with open(path, "r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue
            if obj.get("type") == "meta":
                meta = obj
            elif obj.get("type") == "frame":
                frames.append(obj)
    return meta, frames


def is_num(v):
    return isinstance(v, (int, float)) and math.isfinite(v)


def main():
    if len(sys.argv) < 2:
        print("usage: python analyze_fusion.py <diag.jsonl>")
        return 1

    path = sys.argv[1]
    meta, frames = load(path)
    if not frames:
        print("no frame records found in", path)
        return 1

    n = len(frames)
    t0 = frames[0].get("t") or 0.0
    t1 = frames[-1].get("t") or 0.0
    dur = max(1e-6, t1 - t0)

    print(f"file:           {path}")
    if meta.get("intended_label"):
        print(f"intended label: {meta['intended_label']}")
    print(f"frames:         {n}   duration: {dur:.1f}s   fps: {n / dur:.1f}")
    print()

    # ---- per-head visibility and quality ----
    present, visible = Counter(), Counter()
    qsum, qn = defaultdict(float), defaultdict(int)
    for fr in frames:
        sm = (fr.get("fusion") or {}).get("sensor_measurements") or {}
        for sid, m in sm.items():
            present[sid] += 1
            if m.get("visible"):
                visible[sid] += 1
                if is_num(m.get("quality")):
                    qsum[sid] += m["quality"]
                    qn[sid] += 1

    print("per-head (frames where the head reported a measurement):")
    if not present:
        print("  no per-sensor measurements logged. Was this run --dual?")
    for sid in sorted(present):
        p, v = present[sid], visible[sid]
        q = qsum[sid] / qn[sid] if qn[sid] else 0.0
        print(f"  ToF#{sid}: present {p:5d}  visible {v:5d} ({100 * v / max(1, p):3.0f}%)  mean quality when visible {q:.2f}")
    print()

    # ---- fusion mode + best-head distribution ----
    modes, best = Counter(), Counter()
    for fr in frames:
        fu = fr.get("fusion") or {}
        modes[str(fu.get("mode"))] += 1
        b = fu.get("best_sensor")
        if b is not None:
            best[str(b)] += 1

    print("fusion mode distribution:")
    for name, c in modes.most_common():
        print(f"  {name:36s} {c:5d} ({100 * c / n:3.0f}%)")
    if best:
        print("best-head distribution (fused frames only):")
        for b, c in best.most_common():
            print(f"  ToF#{b}: {c}")
    print()

    # ---- stroke spans via active_samples; best-head switching inside each ----
    strokes, cur = [], None
    for fr in frames:
        if (fr.get("active_samples") or 0) > 0:
            cur = cur or []
            cur.append(fr)
        elif cur:
            strokes.append(cur)
            cur = None
    if cur:
        strokes.append(cur)

    print(f"active-stroke spans: {len(strokes)}")
    total_switches = 0
    for i, span in enumerate(strokes, 1):
        bs = [(fr.get("fusion") or {}).get("best_sensor") for fr in span]
        bs = [b for b in bs if b is not None]
        switches = sum(1 for j in range(1, len(bs)) if bs[j] != bs[j - 1])
        total_switches += switches
        vis_union = set()
        for fr in span:
            for s in (fr.get("fusion") or {}).get("visible_sensors") or []:
                vis_union.add(s)
        span_dur = (span[-1].get("t") or 0) - (span[0].get("t") or 0)
        ev = next((fr["event"] for fr in reversed(span) if fr.get("event")), None)
        label = f"{ev['name']} {ev['confidence']:.2f}" if ev else "(no event)"
        flag = "  <-- head handoff mid-stroke" if switches else ""
        print(f"  stroke {i}: {len(span):3d} frames {span_dur:.2f}s  "
              f"best-head switches={switches}  visible heads={sorted(vis_union)}  -> {label}{flag}")
    if strokes:
        print(f"  total best-head switches across strokes: {total_switches}")
        print("  (any switches > 0 means the fused centroid jumps between heads, "
              "which corrupts swipe direction)")
    print()

    # ---- events ----
    events = [(fr.get("t"), fr["event"]["name"], fr["event"].get("confidence", 0.0))
              for fr in frames if fr.get("event")]
    print(f"gesture events fired: {len(events)}")
    for t, name, c in events:
        print(f"  t={t:.2f}s  {name:12s} conf={c:.2f}")
    if meta.get("intended_label") and events:
        good = sum(1 for _, name, _ in events if name == meta["intended_label"])
        print(f"  matching intended '{meta['intended_label']}': {good}/{len(events)}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())