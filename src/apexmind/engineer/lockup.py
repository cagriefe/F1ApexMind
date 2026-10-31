# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) ApexMind contributors
"""Wheel lock-up: wheel speed clearly below vehicle speed while braking.

Uses ``wheelSlipRatio`` from the MotionEx packet (id=13). While braking with the throttle closed,
a lock-up is flagged when the absolute slip ratio exceeds the threshold (sign-independent).
The threshold was calibrated on a real session: normal hard braking |slip| ~0.01-0.05, lock-up 0.3-1.0.
Adjustable with ``--lock-slip`` (default 0.25).
"""

from __future__ import annotations

from collections.abc import Callable

from ..telemetry.packets import CarTelemetry, MotionEx

Advice = Callable[[str, str], None]
_NAMES = {0: "rear left", 1: "rear right", 2: "front left", 3: "front right"}


class LockupCoach:
    def __init__(
        self,
        speak_cb: Advice,
        *,
        slip_threshold: float = 0.25,
        min_brake: float = 0.25,
        max_throttle: float = 0.20,
        min_speed_kmh: float = 70.0,
        confirm_s: float = 0.06,
        cooldown_s: float = 5.0,
        max_age_s: float = 0.25,
    ):
        self.speak = speak_cb
        self.slip_threshold = slip_threshold
        self.min_brake = min_brake
        self.max_throttle = max_throttle
        self.min_speed_kmh = min_speed_kmh
        self.confirm_s = confirm_s
        self.cooldown_s = cooldown_s
        self.max_age_s = max_age_s
        self._acc = {0: 0.0, 1: 0.0, 2: 0.0, 3: 0.0}
        self._last_t: float | None = None
        self._last_warn = -999.0
        self.count = 0
        self.last_peak: tuple[int, float] | None = None

    def reset(self) -> None:
        self._acc = dict.fromkeys(self._acc, 0.0)
        self._last_t = None
        self._last_warn = -999.0

    def on_frame(self, t: CarTelemetry, mex: MotionEx | None, mex_time: float, sim_time: float) -> None:
        dt = 0.0 if self._last_t is None else max(0.0, min(sim_time - self._last_t, 0.1))
        self._last_t = sim_time
        if (
            mex is None
            or sim_time - mex_time > self.max_age_s
            or t.brake < self.min_brake
            or t.throttle > self.max_throttle
            or t.speed_kmh < self.min_speed_kmh
        ):
            self._acc = dict.fromkeys(self._acc, 0.0)
            return

        locked: list[int] = []
        for i, slip in enumerate(mex.wheel_slip_ratio):
            if abs(slip) >= self.slip_threshold:
                self._acc[i] += dt
                if self._acc[i] >= self.confirm_s:
                    locked.append(i)
            else:
                self._acc[i] = 0.0
        if not locked or sim_time - self._last_warn < self.cooldown_s:
            return
        self._last_warn = sim_time
        self.count += 1
        worst = max(locked, key=lambda i: abs(mex.wheel_slip_ratio[i]))
        self.last_peak = (worst, abs(mex.wheel_slip_ratio[worst]))
        fronts = [i for i in locked if i >= 2]
        if len(fronts) == 2:
            self.speak("lockup", "Fronts locked!")
        elif fronts:
            self.speak("lockup", f"{_NAMES[fronts[0]].capitalize()} locked!")
        else:
            self.speak("lockup", "Rears locked!")
