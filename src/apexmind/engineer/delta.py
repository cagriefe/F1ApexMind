# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) ApexMind contributors
"""Live sector / lap delta against the best valid lap's (distance -> time) trace.

Reference: the best VALID lap of this session. At a sector change (when LapData.sector changes) the delta
for that sector and the total delta are spoken. With no reference the first lap is only recorded.
"""

from __future__ import annotations

from bisect import bisect_left
from collections.abc import Callable

from ..telemetry.packets import LapData
from .debrief import format_lap_time

Advice = Callable[[str, str], None]


def _interp(trace: list[tuple[float, int]], dist: float) -> float | None:
    """Time (ms) at the given distance along the trace; None if outside its coverage."""
    if not trace or dist < trace[0][0] or dist > trace[-1][0]:
        return None
    xs = [p[0] for p in trace]
    i = bisect_left(xs, dist)
    if i == 0:
        return float(trace[0][1])
    (d0, t0), (d1, t1) = trace[i - 1], trace[i]
    if d1 == d0:
        return float(t1)
    return t0 + (t1 - t0) * (dist - d0) / (d1 - d0)


class DeltaTracker:
    def __init__(self, speak_cb: Advice, *, min_lap_ms: int = 20_000, step_m: float = 5.0):
        self.speak = speak_cb
        self.min_lap_ms = min_lap_ms
        self.step_m = step_m
        self.best_ms: int | None = None
        self.best_trace: list[tuple[float, int]] = []
        self.reset_lap()
        self._lap_num: int | None = None

    def reset_lap(self) -> None:
        self.trace: list[tuple[float, int]] = []
        self.broken = False           # trace broken (flashback / jump back): cannot become the best
        self.invalid = False
        self._sector = 0
        self._marks: list[tuple[float, int]] = []   # sector boundaries (distance, time)

    def new_session(self) -> None:
        self.best_ms = None
        self.best_trace = []
        self.reset_lap()
        self._lap_num = None

    def update(self, lap: LapData) -> None:
        if self._lap_num is None:
            self._lap_num = lap.current_lap_num
        elif lap.current_lap_num != self._lap_num:
            if lap.current_lap_num > self._lap_num:
                self._finish(lap.last_lap_time_ms)
            self.reset_lap()
            self._lap_num = lap.current_lap_num

        if lap.current_lap_invalid:
            self.invalid = True
        dist, ms = lap.lap_distance, lap.current_lap_time_ms
        if dist < 0 or ms <= 0:
            return
        if self.trace and dist < self.trace[-1][0] - 50.0:
            self.broken = True        # jump back = flashback
            return
        if not self.trace or dist - self.trace[-1][0] >= self.step_m:
            self.trace.append((dist, ms))

        if lap.sector != self._sector:
            if lap.sector == self._sector + 1:
                self._on_sector(self._sector, dist, ms)
            self._sector = lap.sector

    def _on_sector(self, done: int, dist: float, ms: int) -> None:
        self._marks.append((dist, ms))
        ref_now = _interp(self.best_trace, dist) if self.best_trace else None
        if ref_now is None or self.best_ms is None:
            split = ms - (self._marks[done - 1][1] if done > 0 else 0)
            self.speak("delta", f"Sector {done + 1}, {format_lap_time(split)}.")
            return
        total = (ms - ref_now) / 1000.0
        if done == 0:
            self.speak("delta", f"Sector 1, {self._fmt(total)}.")
            return
        prev_d, prev_ms = self._marks[done - 1]
        ref_prev = _interp(self.best_trace, prev_d)
        if ref_prev is None:
            self.speak("delta", f"Sector {done + 1}, {self._fmt(total)} on the lap.")
            return
        sector = ((ms - prev_ms) - (ref_now - ref_prev)) / 1000.0
        self.speak("delta", f"Sector {done + 1}, {self._fmt(sector)}. Total {self._fmt(total)}.")

    @staticmethod
    def _fmt(delta_s: float) -> str:
        if abs(delta_s) < 0.005:
            return "equal"
        return f"{'plus' if delta_s > 0 else 'minus'} {abs(delta_s):.2f}"

    def _finish(self, lap_ms: int) -> None:
        if lap_ms < self.min_lap_ms or self.invalid or self.broken or len(self.trace) < 20:
            return
        if self.best_ms is None or lap_ms < self.best_ms:
            self.best_ms = lap_ms
            self.best_trace = list(self.trace)
