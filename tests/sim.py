# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) ApexMind contributors
"""Continuous driving simulation: feeds the Analyzer a realistic frame stream (30/60 Hz, different speeds)."""

from __future__ import annotations

from apexmind.engineer import Analyzer
from apexmind.telemetry.packets import CarMotion, CarTelemetry

TRACK = 4323.0


def tel(speed, throttle, brake, steer=0.0):
    return CarTelemetry(
        speed_kmh=int(speed), throttle=throttle, steer=steer, brake=brake,
        clutch=0, gear=6, rpm=11000, drs=0, rev_percent=80,
    )


def mot(kmh):
    v = kmh / 3.6
    return CarMotion(
        x=0, y=0, z=0, vx=v, vy=0, vz=0, g_lat=0, g_long=0, g_vert=1,
        yaw=0, pitch=0, roll=0, fx=1, fy=0, fz=0, rx=0, ry=0, rz=1,
    )


def profile(d, brake_at, v_in, depth=0.9, hold=0.6):
    """(speed km/h, throttle, brake, steering) — a single corner, graded pedals:
    brake depth -> 30% (60 m) -> 0 (100 m); throttle 20% (120 m) -> hold (145 m) -> 100% (175 m)."""
    x = d - brake_at
    if x < 0:
        return v_in, 1.0, 0.0, 0.0
    if x < 10:   # the pedal is pressed at 10 m (a real pedal ramps)
        return v_in - (v_in - 130) * x / 100, 0.0, depth * x / 10, 0.0
    if x < 60:
        return v_in - (v_in - 130) * x / 100, 0.0, depth + (0.3 - depth) * (x - 10) / 50, 0.0
    if x < 100:
        return v_in - (v_in - 130) * x / 100, 0.0, 0.3 * (100 - x) / 40, 0.0
    if x < 120:
        return 100.0, 0.0, 0.0, 0.5
    if x < 145:
        return 100.0 + (x - 120), 0.2 + (hold - 0.2) * (x - 120) / 25, 0.0, 0.5
    if x < 175:
        return 125.0 + (x - 145), hold + (1.0 - hold) * (x - 145) / 30, 0.0, 0.3
    return min(v_in, 155.0 + (x - 175) * 1.2), 1.0, 0.0, 0.0


def drive(an: Analyzer, laps=4, hz=60, brake_at=1000.0, v_in=250.0, depth=0.9, hold=0.6,
          lap_times=None, lap_shift=None, on_frame=None, pre_frame=None, jitter=False, schedule=False,
          steer_extra=None):
    """Returns: event list [(kind, lap, true_distance, text)].

    jitter: frame interval random 40-170 ms (like the real F1 25 20 Hz stream).
    schedule: gives the Analyzer a timer; triggers are recorded BETWEEN frames at the true distance.
    """
    import random

    rng = random.Random(7)
    events: list[tuple] = []
    cur = {"d": 0.0, "lap": 1}
    pending: list[tuple[float, object]] = []
    an.set_track_length(TRACK)
    def target():
        from apexmind.engineer.analyzer import _circular_median
        if not an.zones or not an.zones[0].brake_lap:
            return ""
        return f"{_circular_median(an.zones[0].brake_lap, TRACK):.3f}"

    an.set_cues(
        lambda: events.append(("cue_brake", cur["lap"], cur["d"], target())),
        lambda: events.append(("cue_throttle", cur["lap"], cur["d"], "")),
    )
    if schedule:
        an.scheduler = lambda delay, fn: pending.append((clock["t"] + delay, fn))
    base = an.speak

    def spy(kind, text):
        events.append((kind, cur["lap"], cur["d"], text))
        base(kind, text)

    an.speak = spy
    clock = {"t": 0.0}
    d, t, lap = 0.0, 0.0, 1
    prev = (0.0, 0.0, 0.0)   # (time, distance, speed m/s)
    while lap <= laps:
        dt = rng.uniform(0.04, 0.17) if jitter else 1.0 / hz
        shift = (lap_shift or {}).get(lap, 0.0)
        v, thr, brk, steer = profile(d, brake_at + shift, v_in, depth, hold)
        if steer_extra and 100 <= d - (brake_at + shift) < 260:
            steer += steer_extra.get(lap, 0.0)    # steering more open than the reference on exit
        # run the timers that fall due before this frame's time
        for item in sorted([p for p in pending if p[0] <= t], key=lambda p: p[0]):
            pending.remove(item)
            tf = item[0]
            cur["d"] = prev[1] + prev[2] * (tf - prev[0])
            item[1]()
        pending[:] = [p for p in pending if p[0] > t]
        clock["t"] = t
        cur.update(d=d, lap=lap)
        an.last_lap_ms = (lap_times or {}).get(lap - 1)
        if pre_frame:
            pre_frame(lap, d, t, v)
        an.on_frame(tel(v, thr, brk, steer), mot(v), t, lap_distance=d % TRACK, lap_num=lap)
        if on_frame:
            on_frame(lap, d, t, v)
        prev = (t, d, v / 3.6)
        d += v / 3.6 * dt
        t += dt
        if d >= TRACK:
            d -= TRACK
            lap += 1
    return events
