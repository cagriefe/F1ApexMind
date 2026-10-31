# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) ApexMind contributors
"""Traction (wheelspin) guard: a short call that lowers the throttle level BEFORE grip is lost.

Real data (Spa, F1 25): rear wheel slip ratio (positive = wheelspin) rises to 0.3-0.5 about 0.35-0.9 s
before the slide/spin warning; under normal power it stays below 0.1 at 98% throttle. Threshold 0.25.
The call names a throttle level about 30 points below the current one (1.0 -> ``Gas 70``, 0.5 -> ``Gas 30``).
With steering applied (high lateral load) the threshold drops to 0.18.
On the real recording 5 of 5 slides were caught 0.2-0.8 s before the warning.
"""

from __future__ import annotations

from collections.abc import Callable

from ..telemetry.packets import CarTelemetry, MotionEx

Advice = Callable[[str, str], None]


class TractionCoach:
    def __init__(
        self,
        speak_cb: Advice,
        *,
        slip: float = 0.25,
        slip_loaded: float = 0.18,
        loaded_steer: float = 0.20,
        min_throttle: float = 0.40,
        min_speed_kmh: float = 40.0,
        confirm_s: float = 0.03,
        cooldown_s: float = 1.2,
        drop: float = 0.30,
        max_age_s: float = 0.25,
    ):
        self.speak = speak_cb
        self.slip = slip
        self.slip_loaded = slip_loaded
        self.loaded_steer = loaded_steer
        self.min_throttle = min_throttle
        self.min_speed_kmh = min_speed_kmh
        self.confirm_s = confirm_s
        self.cooldown_s = cooldown_s
        self.drop = drop
        self.max_age_s = max_age_s
        self.count = 0
        self.rear_slip = 0.0
        self.reset()

    def reset(self) -> None:
        self._acc = 0.0
        self._last_t: float | None = None
        self._last_warn = -999.0
        self.rear_slip = 0.0

    def on_frame(self, t: CarTelemetry, mex: MotionEx | None, mex_time: float, sim_time: float) -> None:
        dt = 0.0 if self._last_t is None else max(0.0, min(sim_time - self._last_t, 0.1))
        self._last_t = sim_time
        if mex is None or sim_time - mex_time > self.max_age_s:
            self.rear_slip = 0.0
            self._acc = 0.0
            return
        self.rear_slip = max(mex.wheel_slip_ratio[0], mex.wheel_slip_ratio[1], 0.0)   # RL, RR
        # with steering applied (high lateral load) grip is lost earlier and faster: lower the threshold
        limit = self.slip_loaded if abs(t.steer) >= self.loaded_steer else self.slip
        if t.throttle < self.min_throttle or t.speed_kmh < self.min_speed_kmh or self.rear_slip < limit:
            self._acc = 0.0
            return
        self._acc += dt
        if self._acc >= self.confirm_s and sim_time - self._last_warn >= self.cooldown_s:
            self._last_warn = sim_time
            self.count += 1
            target = max(t.throttle - self.drop, t.throttle * 0.6)    # 1.0 -> 70, 0.5 -> 30, 0.35 -> 20
            level = max(10, int(round(target * 10)) * 10)
            self.speak("call_traction", f"Gas {min(level, 90)}")
