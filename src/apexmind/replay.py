# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) ApexMind contributors
"""Show offline how the coach would interpret a recorded session (the ``--record`` CSV).

``apexmind replay path/to/session-....csv`` prints what it would have said and when (no audio).
Used to tune thresholds and the call script against real data.
"""

from __future__ import annotations

import csv
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from .engineer import (
    Analyzer,
    DeltaTracker,
    LapDebrief,
    LockupCoach,
    TrackLimits,
    TractionCoach,
    TyreCoach,
)
from .telemetry.packets import CarDamage, CarMotion, CarTelemetry, LapData, MotionEx


@dataclass
class Event:
    t: float
    lap: int
    dist: float
    kind: str
    text: str


def _f(row: dict, key: str, default: float = 0.0) -> float:
    try:
        v = row.get(key, "")
        return float(v) if v not in ("", None) else default
    except ValueError:
        return default


def _quad(row: dict, prefix: str, cast=float):
    keys = [f"{prefix}_{n}" for n in ("rl", "rr", "fl", "fr")]
    if any(row.get(k, "") == "" for k in keys):
        return None
    return tuple(cast(float(row[k])) for k in keys)


def _precise_time(rows: list[dict]) -> None:
    """Old recordings truncated the 't' column to 0.1 s after 1000 s: rebuild it from the lap time (ms)."""
    by: dict[str, list[dict]] = {}
    for r in rows:
        if _f(r, "lap_ms") > 0:
            by.setdefault(r.get("lap", ""), []).append(r)
    for lap_rows in by.values():
        offs = sorted(_f(r, "t") - _f(r, "lap_ms") / 1000.0 for r in lap_rows)
        off = offs[len(offs) // 2]
        for r in lap_rows:
            r["t"] = f"{off + _f(r, 'lap_ms') / 1000.0:.3f}"


def replay_csv(path: str | Path, **kwargs) -> tuple[list[Event], Analyzer]:
    """Replay a recorded CSV file; see :func:`replay_rows` for the keyword options."""
    with open(path, encoding="utf-8", newline="") as f:
        rows = list(csv.DictReader(f))
    return replay_rows(rows, **kwargs)


def replay_rows(
    rows: list[dict],
    *,
    calls: bool = True,
    cue_latency: float = 0.12,
    voice_rate: int = 330,
    phrase_duration: Callable[[str], float | None] | None = None,
    lock_slip: float = 0.25,
) -> tuple[list[Event], Analyzer]:
    """Run the whole coach over recorded rows. Returns the events it would have spoken + the analyzer."""
    _precise_time(rows)
    events: list[Event] = []
    cur = {"t": 0.0, "lap": 0, "dist": 0.0}

    def speak(kind: str, text: str) -> None:
        debrief.note(kind)
        if kind == "lockup":
            an.note_lockup()
        events.append(Event(cur["t"], cur["lap"], cur["dist"], kind, text))

    debrief = LapDebrief(speak)
    delta = DeltaTracker(speak)
    limits = TrackLimits(speak)
    lockup = LockupCoach(speak, slip_threshold=lock_slip)
    traction = TractionCoach(speak)
    tyres = TyreCoach(speak)
    an = Analyzer(
        speak, learn_n=1, cooldown_s=8.0, calls=calls, cue_latency_s=cue_latency,
        speech_rate_wpm=voice_rate, posthoc_throttle=not calls,
        phrase_duration=phrase_duration or (lambda text: 0.18 + 0.14 * len(text.split())),
    )
    def cue(kind: str) -> Callable[[], None]:
        return lambda: events.append(Event(cur["t"], cur["lap"], cur["dist"], kind, ""))

    an.set_cues(cue("cue_brake"), None if calls else cue("cue_throttle"))

    an.limits = limits  # type: ignore[attr-defined]
    # virtual clock + timer: scheduled calls fire between frames at the interpolated distance, like live
    pending: list[tuple[float, Callable[[], None]]] = []
    clock = {"t": 0.0}
    prev = {"t": 0.0, "dist": 0.0, "v": 0.0}
    an.scheduler = lambda delay, fn: pending.append((clock["t"] + delay, fn))
    prev_lap: int | None = None
    last_ms: int | None = None
    lap_max = 0.0
    track_set = False
    for r in rows:
        lap = int(_f(r, "lap"))
        dist = _f(r, "dist")
        lap_ms = _f(r, "lap_ms")
        t = _f(r, "t")
        if prev_lap is not None and lap != prev_lap:
            last_ms = int(lap_max) if lap_max >= 20000 else None
            lap_max = 0.0
        lap_max = max(lap_max, lap_ms)
        prev_lap = lap
        cur.update(t=t, lap=lap, dist=dist)
        if dist < 0 or lap_ms <= 0:
            continue
        for item in sorted((p for p in pending if p[0] <= t), key=lambda p: p[0]):
            pending.remove(item)
            cur.update(t=item[0], dist=prev["dist"] + prev["v"] * (item[0] - prev["t"]))
            item[1]()
        cur.update(t=t, dist=dist)
        clock["t"] = t
        prev.update(t=t, dist=dist, v=_f(r, "speed") / 3.6)
        if not track_set:
            # the CSV has no track length: estimate it from the largest distance seen
            length = max(_f(x, "dist") for x in rows)
            an.set_track_length(length)
            limits.track_length = length
            track_set = True

        tel = CarTelemetry(
            speed_kmh=int(_f(r, "speed")), throttle=_f(r, "throttle"), steer=_f(r, "steer"),
            brake=_f(r, "brake"), clutch=0, gear=int(_f(r, "gear")), rpm=int(_f(r, "rpm")), drs=0,
            rev_percent=0, brakes_temp=_quad(r, "btemp", int), tyre_surface_temp=_quad(r, "tsurf", int),
            tyre_inner_temp=_quad(r, "tin", int), tyre_pressure=_quad(r, "press"),
            surface_type=_quad(r, "surf", int),
        )
        mot = CarMotion(
            x=0, y=0, z=0, vx=_f(r, "vx"), vy=0, vz=_f(r, "vz"), g_lat=_f(r, "g_lat"),
            g_long=_f(r, "g_long"), g_vert=1, yaw=0, pitch=0, roll=0,
        )
        invalid = r.get("invalid") == "1"
        lap_data = LapData(
            lap_distance=dist, current_lap_num=lap, current_lap_time_ms=int(lap_ms),
            last_lap_time_ms=last_ms or 0, current_lap_invalid=invalid,
            sector=int(_f(r, "sector")), corner_cutting_warnings=int(_f(r, "cc_warn")),
            total_warnings=int(_f(r, "warnings")), pit_status=int(_f(r, "pit")),
        )
        slip = _quad(r, "slip")
        mex = None if slip is None else MotionEx(
            wheel_speed=_quad(r, "wspd") or (0.0,) * 4, wheel_slip_ratio=slip, wheel_slip_angle=(0.0,) * 4
        )
        wear = _quad(r, "wear")
        if wear is not None:
            tyres.on_damage(
                CarDamage(
                    tyres_wear=wear, tyres_damage=(0,) * 4, brakes_damage=(0,) * 4,
                    tyre_blisters=tuple(int(x) for x in (_quad(r, "blist") or (0,) * 4)),
                    front_left_wing=int(_f(r, "dmg_flwing")), front_right_wing=int(_f(r, "dmg_frwing")),
                    rear_wing=int(_f(r, "dmg_rwing")), floor=int(_f(r, "dmg_floor")),
                    diffuser=int(_f(r, "dmg_diffuser")), sidepod=int(_f(r, "dmg_sidepod")),
                    drs_fault=0, ers_fault=0, gearbox=int(_f(r, "dmg_gearbox")),
                    engine=int(_f(r, "dmg_engine")),
                ),
                t,
            )

        limits.on_lap(lap_data, t)
        debrief.update(lap_data)
        delta.update(lap_data)
        an.last_lap_ms = last_ms
        an.lap_invalid = invalid
        an.suppressed = lap_data.pit_status != 0
        an.on_frame(tel, mot, t, lap_distance=dist, lap_num=lap)
        lockup.on_frame(tel, mex, t, t)
        traction.on_frame(tel, mex, t, t)
        an.rear_slip = traction.rear_slip
        tyres.on_telemetry(tel, t)
        limits.on_telemetry(tel, t, dist)
    return events, an


def main(argv: list[str]) -> int:
    import argparse

    p = argparse.ArgumentParser(prog="apexmind replay")
    p.add_argument("csv", nargs="+")
    p.add_argument("--no-calls", action="store_true")
    p.add_argument("--kinds", default="", help="comma-separated kind filter (e.g. call_,lockup)")
    p.add_argument("--scripts", action="store_true", help="print the learned corner scripts")
    p.add_argument("--save-hotspots", metavar="FILE", help="write track-limit violation spots to FILE")
    p.add_argument("--save-refs", metavar="FILE", help="write best-lap references (corners) to FILE")
    p.add_argument("--track-id", type=int, default=0)
    a = p.parse_args(argv)
    exports = []

    merged = TrackLimits(lambda k, t: None)
    for path in a.csv:
        events, an = replay_csv(path, calls=not a.no_calls)
        exports.append(an.export_refs())
        lim = an.limits  # type: ignore[attr-defined]
        merged.track_length = lim.track_length
        for h in lim.hotspots:
            merged._add_hotspot(h)
        kinds = [k for k in a.kinds.split(",") if k]
        if len(a.csv) > 1:
            print(f"== {path}")
        for e in events:
            if kinds and not any(e.kind.startswith(k) for k in kinds):
                continue
            print(f"{e.t:8.1f}s  lap {e.lap}  {e.dist:6.0f} m  [{e.kind}] {e.text}")
        if a.scripts:
            print("\n--- corner scripts (distance from the brake start point) ---")
            for z in sorted((z for z in an.zones if z.laps and z.brake_lap), key=lambda z: z.brake_lap[0]):
                print(f"brake ~{z.brake_lap[0]:.0f} m ({len(z.laps)} laps): "
                      + ", ".join(f"+{d:.0f} m {t}" for d, _k, t, _s in z.script))
    if a.save_refs:
        import json

        from .engineer.analyzer import merge_refs

        out = Path(a.save_refs).expanduser()
        data = json.loads(out.read_text(encoding="utf-8")) if out.exists() else {}
        data[str(a.track_id)] = merge_refs(exports)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(data, separators=(",", ":")), encoding="utf-8")
        print(f"{len(data[str(a.track_id)]['zones']) } corner references saved: {a.save_refs}")
    if a.save_hotspots:
        merged.save(a.save_hotspots, a.track_id)
        print(f"{len(merged.hotspots)} hotspots saved: {a.save_hotspots}")
    return 0
