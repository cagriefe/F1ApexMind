# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) ApexMind contributors
"""Turn a recorded session CSV into a JSON-friendly report.

Used by the web UI (in the browser through Pyodide, or from the local ``--web`` server) and by
``apexmind report``. The report re-runs the whole coach over the recording, so the "calls" and
"corners" it shows are exactly what the live coach would have said.
"""

from __future__ import annotations

import csv
import io
import json
import math
import statistics
import sys

from .replay import _f, replay_rows

_HIDDEN_KINDS = {"cue_brake", "cue_throttle"}
_REQUIRED_COLUMNS = {"lap", "dist", "lap_ms", "speed", "throttle", "brake"}


def _lap_stats(rows: list[dict], track_len: float) -> tuple[list[dict], dict[int, list[dict]]]:
    by_lap: dict[int, list[dict]] = {}
    info: dict[int, dict] = {}
    for r in rows:
        lap = int(_f(r, "lap"))
        ms = _f(r, "lap_ms")
        dist = _f(r, "dist")
        if lap <= 0 or ms <= 0 or dist < 0:
            continue
        li = info.get(lap)
        if li is None:
            li = info[lap] = {
                "lap": lap, "ms": 0.0, "invalid": False, "d_first": dist, "d_max": dist,
                "sector_at": {}, "t0": _f(r, "t"),
            }
            by_lap[lap] = []
        by_lap[lap].append(r)
        li["ms"] = max(li["ms"], ms)
        li["invalid"] = li["invalid"] or r.get("invalid") == "1"
        li["d_max"] = max(li["d_max"], dist)
        li["sector_at"].setdefault(int(_f(r, "sector")), ms)
    numbers = sorted(info)
    laps = []
    for i, n in enumerate(numbers):
        li = info[n]
        started = li["d_first"] < 250      # the recording began near the start line, not mid-lap
        finished = i < len(numbers) - 1 or li["d_max"] > track_len - 60
        complete = bool(started and finished and li["ms"] >= 20000)
        at = li["sector_at"]
        sectors = None
        if complete and 1 in at and 2 in at:
            sectors = [int(at[1]), int(at[2] - at[1]), int(li["ms"] - at[2])]
        laps.append({
            "lap": n, "ms": int(li["ms"]), "valid": not li["invalid"], "complete": complete,
            "sectors": sectors, "calls": 0, "lockups": 0, "limit_warnings": 0,
        })
    return laps, by_lap


def _trace(rows: list[dict], bin_m: float) -> dict:
    last: dict[int, dict] = {}
    for r in rows:
        last[int(_f(r, "dist") // bin_m)] = r
    keys = sorted(last)
    return {
        "d": [int(k * bin_m) for k in keys],
        "v": [int(_f(last[k], "speed")) for k in keys],
        "th": [round(_f(last[k], "throttle") * 100) for k in keys],
        "br": [round(_f(last[k], "brake") * 100) for k in keys],
        "st": [round(_f(last[k], "steer") * 100) for k in keys],
        "g": [int(_f(last[k], "gear")) for k in keys],
    }


def _track_map(rows: list[dict], step_m: float = 8.0) -> list[list[float]]:
    """Dead-reckon the racing line from the velocity vector; returns [dist, x, y, kmh, brake%] points."""
    pts: list[list[float]] = []
    x = y = 0.0
    prev = None
    for r in rows:
        t, vx, vz = _f(r, "t"), _f(r, "vx"), _f(r, "vz")
        if prev is not None:
            dt = t - prev[0]
            if 0.0 < dt < 0.5:
                x += 0.5 * (vx + prev[1]) * dt
                y += 0.5 * (vz + prev[2]) * dt
        prev = (t, vx, vz)
        pts.append([_f(r, "dist"), x, y, _f(r, "speed"), _f(r, "brake") * 100, _f(r, "steer")])
    if len(pts) < 50:
        return []
    # close the loop: spread the residual drift linearly over the lap
    d0, d1 = pts[0][0], pts[-1][0]
    span = max(d1 - d0, 1.0)
    ex, ey = pts[-1][1] - pts[0][1], pts[-1][2] - pts[0][2]
    for p in pts:
        f = (p[0] - d0) / span
        p[1] -= ex * f
        p[2] -= ey * f
    # right turns (steer > 0) must run clockwise on screen (y down); mirror otherwise
    score = 0.0
    for a, b, c in zip(pts, pts[5:], pts[10:], strict=False):
        h1 = math.atan2(b[2] - a[2], b[1] - a[1])
        h2 = math.atan2(c[2] - b[2], c[1] - b[1])
        dh = (h2 - h1 + math.pi) % (2 * math.pi) - math.pi
        score += b[5] * dh
    if score < 0:
        for p in pts:
            p[2] = -p[2]
    out, last_d = [], -1e9
    for p in pts:
        if p[0] - last_d >= step_m:
            out.append([round(p[0]), round(p[1], 1), round(p[2], 1), round(p[3]), round(p[4])])
            last_d = p[0]
    return out


def _corners(an) -> list[dict]:
    zones = [z for z in an.zones if z.brake_lap and (z.laps or z.n)]
    zones.sort(key=lambda z: statistics.median(z.brake_lap))
    out = []
    for i, z in enumerate(zones, 1):
        brake_at = statistics.median(z.brake_lap)
        prof = z.profile or []
        speeds = [p[3] * 3.6 for p in prof] or [0.0]
        out.append({
            "id": i,
            "brake_at": round(brake_at),
            "laps": len(z.laps) or z.n,
            "spread_m": round(statistics.pstdev(z.brake_lap), 1) if len(z.brake_lap) > 1 else None,
            "peak": round(max((p[1] for p in prof), default=z.depth_avg) * 100),
            "entry_kmh": round(speeds[0]),
            "min_kmh": round(min(speeds)),
            "length_m": round(prof[-1][0]) if prof else None,
            "lock_frac": round(z.lock_frac, 2),
            "script": [{"d": round(d), "kind": k, "text": t} for d, k, t, _s in z.script],
        })
    return out


def build_report(source: str | bytes, *, max_events: int = 4000, bin_m: float = 10.0) -> dict:
    """Analyse a recording written by ``apexmind listen --record`` and return a JSON-friendly dict."""
    text = source.decode("utf-8-sig") if isinstance(source, (bytes, bytearray)) else source
    rows = list(csv.DictReader(io.StringIO(text)))
    if not rows or not _REQUIRED_COLUMNS.issubset(rows[0]):
        raise ValueError("This does not look like an ApexMind recording (the .csv written by --record).")
    events, an = replay_rows(rows)
    track_len = max(_f(r, "dist") for r in rows)
    laps, by_lap = _lap_stats(rows, track_len)
    lap_idx = {lp["lap"]: lp for lp in laps}
    for e in events:
        lp = lap_idx.get(e.lap)
        if lp is None:
            continue
        if e.kind.startswith("call_") and e.kind not in ("call_limits", "call_traction"):
            lp["calls"] += 1
        elif e.kind == "lockup":
            lp["lockups"] += 1
        elif e.kind in ("wide", "call_limits"):
            lp["limit_warnings"] += 1

    good = [lp for lp in laps if lp["complete"] and lp["valid"]]
    best = min(good, key=lambda lp: lp["ms"]) if good else None
    ideal = None
    if len(good) >= 2 and all(lp["sectors"] for lp in good):
        ideal = sum(min(lp["sectors"][i] for lp in good) for i in range(3))
    map_lap = best or next((lp for lp in laps if lp["complete"]), None)
    times = [lp["ms"] for lp in good]
    t_all = [_f(r, "t") for r in rows]
    duration = max(t_all) - min(t_all) if t_all else 0.0
    hot = an.limits.hotspots  # type: ignore[attr-defined]
    hits = an.limits.hits  # type: ignore[attr-defined]
    return {
        "version": 1,
        "summary": {
            "track_length_m": round(track_len),
            "laps": len(laps),
            "complete": sum(1 for lp in laps if lp["complete"]),
            "valid": len(good),
            "best_lap": best["lap"] if best else None,
            "best_ms": best["ms"] if best else None,
            "ideal_ms": ideal,
            "consistency_ms": round(statistics.pstdev(times)) if len(times) > 2 else None,
            "violations": sum(1 for e in events if e.kind == "lap_invalid"),
            "lockups": sum(1 for e in events if e.kind == "lockup"),
            "calls": sum(lp["calls"] for lp in laps),
            "duration_s": round(duration),
            "samples": len(rows),
            "hz": round(len(rows) / duration, 1) if duration else None,
        },
        "laps": laps,
        "corners": _corners(an),
        "limit_spots": [{"d": round(h), "hits": n} for h, n in zip(hot, hits, strict=False)],
        "events": [
            {"t": round(e.t, 1), "lap": e.lap, "d": round(e.dist), "kind": e.kind, "text": e.text}
            for e in events if e.kind not in _HIDDEN_KINDS
        ][:max_events],
        "traces": {str(n): _trace(rs, bin_m) for n, rs in by_lap.items() if len(rs) >= 30},
        "map": {"lap": map_lap["lap"], "points": _track_map(by_lap[map_lap["lap"]])} if map_lap else None,
    }


def main(argv: list[str]) -> int:
    import argparse

    p = argparse.ArgumentParser(prog="apexmind report", description="Analyse a recorded session as JSON.")
    p.add_argument("csv", help="recording written by `apexmind listen --record`")
    p.add_argument("-o", "--output", help="write the JSON here instead of stdout")
    a = p.parse_args(argv)
    with open(a.csv, encoding="utf-8", newline="") as f:
        report = build_report(f.read())
    data = json.dumps(report, separators=(",", ":"))
    if a.output:
        with open(a.output, "w", encoding="utf-8") as f:
            f.write(data)
    else:
        sys.stdout.write(data + "\n")
    return 0
