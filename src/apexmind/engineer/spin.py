# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) ApexMind contributors
"""Spin / slide coach: time-based, low latency.

lateral = projection of the car's real velocity onto its own 'right' direction (m/s)
  > 0: velocity points right -> nose is left -> "steer RIGHT"
  < 0: velocity points left -> nose is right -> "steer LEFT"
ratio = |lateral| / speed
  0.30+ clear slide, 0.45+ spin

Confirmation / recovery use seconds instead of frame counts, so the reaction time is
the same at 20 Hz and at 60 Hz.
"""

from __future__ import annotations

from collections.abc import Callable

from ..telemetry.packets import CarMotion, CarTelemetry

Advice = Callable[[str, str], None]


class SpinCoach:
    def __init__(
        self,
        speak_cb: Advice,
        *,
        ratio_spin: float = 0.45,
        ratio_slide: float = 0.32,
        ratio_mild: float = 0.18,
        ratio_recover: float = 0.15,
        throttle_hint: int = 30,
        confirm_s: float = 0.12,
        recover_s: float = 0.50,
        cooldown_s: float = 5.0,
    ):
        self.speak = speak_cb
        self.ratio_spin = ratio_spin
        self.ratio_slide = ratio_slide
        self.ratio_mild = ratio_mild
        self.ratio_recover = ratio_recover
        self.throttle_hint = throttle_hint
        self.confirm_s = confirm_s
        self.recover_s = recover_s
        self.cooldown_s = cooldown_s

        self.phase = "normal"
        self.confirm = 0.0
        self.recover = 0.0
        self.last_warn: dict[str, float] = {}
        self._said_at_spin: set[str] = set()
        self.sim_time = 0.0
        self.ratio = 0.0            # slip ratio of the last frame (used to suppress throttle calls)
        self._last_frame_time: float | None = None

    @property
    def is_spinning(self) -> bool:
        return self.phase == "spin"

    def reset(self) -> None:
        self.phase = "normal"
        self.confirm = 0.0
        self.recover = 0.0
        self.last_warn.clear()
        self._said_at_spin.clear()
        self._last_frame_time = None

    def on_frame(self, t: CarTelemetry, m: CarMotion, sim_time: float) -> None:
        dt = 0.0 if self._last_frame_time is None else sim_time - self._last_frame_time
        # Swallow a negative dt after a flashback or out-of-order packets.
        dt = max(0.0, min(dt, 0.10))
        self._last_frame_time = sim_time
        self.sim_time = sim_time

        speed_ms = (m.vx * m.vx + m.vz * m.vz) ** 0.5
        if speed_ms < 1.5:
            ratio = 0.0
        else:
            lateral = m.vx * m.rx + m.vz * m.rz
            ratio = abs(lateral) / max(speed_ms, 6.0)
        self.ratio = ratio

        if self.phase == "normal":
            if ratio >= self.ratio_spin:
                self.confirm += dt
                if self.confirm >= self.confirm_s:
                    lateral = m.vx * m.rx + m.vz * m.rz
                    self._enter_spin(lateral)
                return
            self.confirm = 0.0

            # 1) too much throttle on exit + mild slide -> throttle hint
            if (
                ratio >= self.ratio_mild
                and t.throttle > 0.75
                and speed_ms > 20.0
                and self._gate("mild_throttle")
            ):
                self.speak(
                    "throttle",
                    f"Too much throttle — hold about {self.throttle_hint}% until it grips.",
                )
            # 2) clear slide
            elif ratio >= self.ratio_slide and speed_ms > 12.0 and self._gate("slide"):
                self.speak("slide", "Rear's stepping out — smooth hands!")
            return

        # spin mode
        self._during_spin(t)
        if ratio < self.ratio_recover:
            self.recover += dt
            if self.recover >= self.recover_s:
                self.phase = "normal"
                self.recover = 0.0
                self.confirm = 0.0
                self.speak(
                    "recover",
                    f"Recovered. Ease back to about {self.throttle_hint}% throttle.",
                )
        else:
            self.recover = 0.0

    # ----- helpers -----

    def _enter_spin(self, lateral: float) -> None:
        self.phase = "spin"
        self.confirm = 0.0
        self.recover = 0.0
        self._said_at_spin.clear()
        # lateral > 0: velocity right -> nose left -> counter-steer right
        side = "RIGHT" if lateral > 0 else "LEFT"
        self.speak("spin", f"Spin! Clutch in — steer {side} — no throttle!")

    def _during_spin(self, t: CarTelemetry) -> None:
        if t.throttle > 0.8:
            self._once("no_gas", "Off the throttle — clutch down!")
        elif t.brake > 0.4:
            self._once("no_brake", "No brakes mid-spin — steer it out!")

    def _once(self, key: str, text: str) -> None:
        if key in self._said_at_spin:
            return
        self._said_at_spin.add(key)
        self.speak(f"spin_{key}", text)

    def _gate(self, key: str) -> bool:
        if (self.sim_time - self.last_warn.get(key, -999.0)) < self.cooldown_s:
            return False
        self.last_warn[key] = self.sim_time
        return True
