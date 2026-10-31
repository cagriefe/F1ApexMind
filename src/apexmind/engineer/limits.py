# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) ApexMind contributors
"""Track limits: the game's own flag, corner-cutting warnings, surface type and a "hotspot" memory.

Real data (Spa, F1 25 TT): the game deletes the lap about 0.4-0.6 s after no wheel is on tarmac
(a kerb + grass combination). The white line is not a surface type; the authority is the game's own
``currentLapInvalid`` / ``cornerCuttingWarnings`` fields.

  - when the flag drops: "Track limits. Lap deleted."
  - no wheel on tarmac (speed >= 100): "Off track!" about 0.3 s BEFORE the flag
  - the place where the violation started is remembered; on later laps a short "Limits" call on approach
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable
from pathlib import Path

from ..telemetry.packets import CarTelemetry, LapData

Advice = Callable[[str, str], None]
_LOG = logging.getLogger(__name__)


def _signed(target: float, current: float, length: float | None) -> float:
    if length and length > 0:
        half = length / 2.0
        return ((target - current + half) % length) - half
    return target - current


class TrackLimits:
    def __init__(
        self,
        speak_cb: Advice,
        *,
        wide_confirm_s: float = 0.12,
        wide_cooldown_s: float = 6.0,
        wide_min_kmh: float = 100.0,
        hotspot_radius_m: float = 100.0,
        warn_lead_s: float = 1.0,
        max_hotspots: int = 8,
    ):
        self.speak = speak_cb
        self.wide_confirm_s = wide_confirm_s
        self.wide_cooldown_s = wide_cooldown_s
        self.wide_min_kmh = wide_min_kmh
        self.hotspot_radius_m = hotspot_radius_m
        self.warn_lead_s = warn_lead_s
        self.max_hotspots = max_hotspots
        self.track_length: float | None = None
        self.violations = 0
        self.hotspots: list[float] = []
        self.hits: list[int] = []        # how many times it was violated
        self.clean: list[int] = []       # consecutive laps passed cleanly
        self.fresh: list[bool] = []      # violated in this session?
        self.warn_cooldown_s = 8.0
        self.reset()

    def reset(self) -> None:
        self._lap: int | None = None
        self._invalid = False
        self._cc = 0
        self._wide_acc = 0.0
        self._wide_last = -999.0
        self._last_t: float | None = None
        self._streak_onset: float | None = None
        self._streak_end_t = -999.0
        self._last_onset: float | None = None
        self._warned: set[int] = set()
        self._passed: set[int] = set()
        self._prev_ahead: dict[int, float] = {}
        self._last_warn_t = -999.0
        self._speed = 0.0
        self._dist: float | None = None

    def new_session(self) -> None:
        self.hotspots.clear()
        self.hits.clear()
        self.clean.clear()
        self.fresh.clear()
        self.reset()

    # ----- persistent per-track hotspots (across sessions) -----

    def load(self, path: str | Path, track_id: int) -> int:
        """Load the saved violation spots for this track; warnings start from the first lap."""
        try:
            data = json.loads(Path(path).expanduser().read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return 0
        for item in data.get(str(track_id), []):
            pos, hits = (item, 1) if isinstance(item, (int, float)) else (item[0], int(item[1]))
            if not any(abs(_signed(float(pos), x, self.track_length)) <= self.hotspot_radius_m
                       for x in self.hotspots):
                self.hotspots.append(float(pos))
                self.hits.append(hits)
                self.clean.append(0)
                self.fresh.append(False)
        return len(self.hotspots)

    def save(self, path: str | Path, track_id: int) -> None:
        p = Path(path).expanduser()
        try:
            data = json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}
        except (OSError, ValueError):
            data = {}
        data[str(track_id)] = [[round(h, 1), n] for h, n in zip(self.hotspots, self.hits, strict=False)]
        try:
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(json.dumps(data, indent=1, sort_keys=True), encoding="utf-8")
        except OSError:
            _LOG.debug("could not save hotspots", exc_info=True)

    on_new_hotspot: Callable[[], None] | None = None

    def _add_hotspot(self, dist: float) -> None:
        for i, h in enumerate(self.hotspots):
            if abs(_signed(dist, h, self.track_length)) <= self.hotspot_radius_m:
                shift = _signed(dist, h, self.track_length) * 0.3
                self.hotspots[i] = (h + shift) % (self.track_length or 1e12)
                self.hits[i] += 1
                self.clean[i] = 0
                self.fresh[i] = True
                return
        if len(self.hotspots) < self.max_hotspots:
            self.hotspots.append(dist)
            self.hits.append(1)
            self.clean.append(0)
            self.fresh.append(True)
            if self.on_new_hotspot:
                self.on_new_hotspot()

    def on_lap(self, lap: LapData, sim_time: float = 0.0) -> None:
        self._dist = lap.lap_distance
        if self._lap is None or lap.current_lap_num != self._lap:
            self._lap = lap.current_lap_num
            self._invalid = lap.current_lap_invalid
            self._cc = lap.corner_cutting_warnings
            self._warned.clear()
            self._passed.clear()
            self._prev_ahead.clear()
            return
        if lap.current_lap_invalid and not self._invalid:
            self._invalid = True
            self.violations += 1
            where = self._streak_onset
            if where is None and sim_time - self._streak_end_t < 2.0:
                where = self._last_onset
            if where is None and self._speed >= 80.0:
                where = lap.lap_distance - 20.0
            if where is not None and self._speed >= 80.0:
                self._add_hotspot(where)
            _LOG.info("track limits: lap invalid lap=%s dist=%.0f onset=%s", lap.current_lap_num,
                      lap.lap_distance, None if where is None else round(where))
            self.speak("lap_invalid", "Track limits. Lap deleted.")
        elif lap.corner_cutting_warnings > self._cc:
            self.violations += 1
            _LOG.info("track limits warning: %s lap=%s dist=%.0f",
                      lap.corner_cutting_warnings, lap.current_lap_num, lap.lap_distance)
            self.speak("limits", f"Track limits warning {lap.corner_cutting_warnings}.")
        self._cc = lap.corner_cutting_warnings

    def on_telemetry(self, t: CarTelemetry, sim_time: float, lap_distance: float | None = None) -> None:
        dt = 0.0 if self._last_t is None else max(0.0, min(sim_time - self._last_t, 0.1))
        self._last_t = sim_time
        self._speed = float(t.speed_kmh)
        if lap_distance is not None:
            self._dist = lap_distance
        self._warn_hotspots(t, lap_distance, sim_time)

        surf = t.surface_type
        no_tarmac = bool(surf) and all(s != 0 for s in surf) and t.speed_kmh >= self.wide_min_kmh
        if not no_tarmac:
            if self._streak_onset is not None:
                self._streak_end_t = sim_time
                self._last_onset = self._streak_onset
            self._streak_onset = None
            self._wide_acc = 0.0
            return
        if self._streak_onset is None:
            self._streak_onset = lap_distance if lap_distance is not None else self._dist
        self._wide_acc += dt
        if self._wide_acc >= self.wide_confirm_s and sim_time - self._wide_last >= self.wide_cooldown_s:
            self._wide_last = sim_time
            self.speak("wide", "Off track!")

    def _warn_hotspots(self, t: CarTelemetry, lap_distance: float | None, sim_time: float) -> None:
        """Only for repeat or fresh-this-session spots, and go quiet after 2 clean passes."""
        if lap_distance is None or not self.hotspots or not self.track_length or self._lap is None:
            return
        speed = t.speed_kmh / 3.6
        if speed < 15.0:
            return
        for i, h in enumerate(self.hotspots):
            ahead = _signed(h, lap_distance, self.track_length)
            prev = self._prev_ahead.get(i)
            self._prev_ahead[i] = ahead
            if prev is not None and prev > 0 >= ahead and i not in self._passed:
                self._passed.add(i)
                if not self._invalid:
                    self.clean[i] += 1        # passed this lap without a violation
            if i in self._warned:
                continue
            if self.clean[i] >= 2 or not (self.hits[i] >= 2 or self.fresh[i]):
                continue
            if sim_time - self._last_warn_t < self.warn_cooldown_s:
                continue
            if speed * 0.25 <= ahead <= speed * self.warn_lead_s:
                self._warned.add(i)
                self._last_warn_t = sim_time
                self.speak("call_limits", "Limits")
