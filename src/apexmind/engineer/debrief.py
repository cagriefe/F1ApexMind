# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) ApexMind contributors
"""End-of-lap summary.

Analyzer/SpinCoach speak instant mistakes; this module judges the lap as a whole:
  - lap time and gap to the best lap (only valid laps count towards the best)
  - the area with the most calls during the lap (brake / apex / throttle / slides / spins)
  - an instant warning is handled by TrackLimits, not here
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Callable

from ..telemetry.packets import LapData

Advice = Callable[[str, str], None]

_LABELS = {
    "brake": "braking",
    "apex": "apex",
    "throttle": "throttle",
    "slide": "slides",
    "spin": "spins",
    "lockup": "lock-ups",
    "call_traction": "wheelspin",
    "wide": "off-track moments",
}


def format_lap_time(ms: int) -> str:
    total = ms / 1000.0
    minutes = int(total // 60)
    return f"{minutes}:{total - minutes * 60:06.3f}"


class LapDebrief:
    def __init__(self, speak_cb: Advice, *, min_lap_ms: int = 20_000):
        self.speak = speak_cb
        self.min_lap_ms = min_lap_ms
        self.best_ms: int | None = None
        self.laps_done = 0
        self.reset()

    def reset(self) -> None:
        self.lap_num: int | None = None
        self.invalid = False
        self.counts: Counter[str] = Counter()

    def new_session(self) -> None:
        """New session / track: the best lap and counters are reset."""
        self.best_ms = None
        self.laps_done = 0
        self.reset()

    def note(self, kind: str) -> None:
        """Add a spoken warning to the lap statistics."""
        if kind in _LABELS:
            self.counts[kind] += 1

    def update(self, lap: LapData) -> None:
        """Called on every LapData packet."""
        if self.lap_num is None:
            self._start(lap)
            return
        if lap.current_lap_num != self.lap_num:
            finished = self.lap_num
            if lap.current_lap_num > finished:
                self._finish(finished, lap.last_lap_time_ms)
            self._start(lap)
            return
        if lap.current_lap_invalid:
            self.invalid = True

    def _start(self, lap: LapData) -> None:
        self.lap_num = lap.current_lap_num
        self.counts = Counter()
        self.invalid = lap.current_lap_invalid

    def _finish(self, lap_num: int, lap_ms: int) -> None:
        if lap_ms < self.min_lap_ms:
            return
        time_txt = format_lap_time(lap_ms)
        valid = not self.invalid
        parts: list[str] = []

        if not valid:
            parts.append(f"Lap {lap_num} deleted, {time_txt}.")
        elif self.best_ms is None:
            parts.append(f"Lap {lap_num}, {time_txt}. Benchmark set.")
        elif lap_ms < self.best_ms:
            gain = (self.best_ms - lap_ms) / 1000.0
            parts.append(f"New best lap, {time_txt}. Minus {gain:.3f}.")
        else:
            gap = (lap_ms - self.best_ms) / 1000.0
            parts.append(f"Lap {lap_num}, {time_txt}. Plus {gap:.3f} to best.")

        if self.counts:
            kind, n = self.counts.most_common(1)[0]
            parts.append(f"Most calls: {_LABELS[kind]}, {n}.")
        elif self.laps_done >= 1 and valid:
            parts.append("Clean lap, no calls.")

        if valid and (self.best_ms is None or lap_ms < self.best_ms):
            self.best_ms = lap_ms
        self.laps_done += 1
        self.speak("debrief", " ".join(parts))
