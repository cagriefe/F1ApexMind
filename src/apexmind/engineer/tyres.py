# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) ApexMind contributors
"""Tyre temperature / wear and car damage warnings.

Temperature warnings are spoken on straights (throttle open, steering straight); never mid-corner.
Thresholds should be calibrated with game data (the window varies by compound).
"""

from __future__ import annotations

from collections.abc import Callable

from ..telemetry.packets import CarDamage, CarTelemetry

Advice = Callable[[str, str], None]
_NAMES = ("rear left", "rear right", "front left", "front right")


class TyreCoach:
    def __init__(
        self,
        speak_cb: Advice,
        *,
        hot_inner_c: int = 112,
        cold_inner_c: int = 70,
        warmup_s: float = 90.0,
        temp_cooldown_s: float = 45.0,
        wear_steps: tuple[int, ...] = (30, 50, 70, 85),
        blister_pct: int = 25,
        damage_step: int = 5,
    ):
        self.speak = speak_cb
        self.hot_inner_c = hot_inner_c
        self.cold_inner_c = cold_inner_c
        self.warmup_s = warmup_s
        self.temp_cooldown_s = temp_cooldown_s
        self.wear_steps = wear_steps
        self.blister_pct = blister_pct
        self.damage_step = damage_step
        self.reset()

    def reset(self) -> None:
        self.elapsed = 0.0
        self._last_t: float | None = None
        self._last_temp_warn = -999.0
        self._wear_said: set[int] = set()
        self._blister_said: set[int] = set()
        self._pending: list[tuple[str, str]] = []
        self._dmg: CarDamage | None = None
        self._dmg_said: dict[str, float] = {}

    # ----- temperature (CarTelemetry) -----

    def on_telemetry(self, t: CarTelemetry, sim_time: float) -> None:
        dt = 0.0 if self._last_t is None else max(0.0, min(sim_time - self._last_t, 0.1))
        self._last_t = sim_time
        if t.speed_kmh > 80:
            self.elapsed += dt

        straight = t.throttle > 0.9 and abs(t.steer) < 0.08 and t.speed_kmh >= 150
        if not straight:
            return
        # Pending wear/tyre messages first; temperature after.
        if self._pending:
            kind, text = self._pending.pop(0)
            self.speak(kind, text)
            return
        inner = t.tyre_inner_temp
        if not inner or sim_time - self._last_temp_warn < self.temp_cooldown_s:
            return
        hot = max(range(4), key=lambda i: inner[i])
        cold = min(range(4), key=lambda i: inner[i])
        if inner[hot] >= self.hot_inner_c:
            self._last_temp_warn = sim_time
            self.speak("tyre_temp", f"{_NAMES[hot].capitalize()} tyre hot, {inner[hot]} degrees.")
        elif self.elapsed >= self.warmup_s and inner[cold] <= self.cold_inner_c:
            self._last_temp_warn = sim_time
            self.speak("tyre_temp", f"{_NAMES[cold].capitalize()} tyre cold, {inner[cold]} degrees.")

    # ----- wear + damage (CarDamage) -----

    def on_damage(self, d: CarDamage, sim_time: float) -> None:
        wear = d.tyres_wear
        worst = max(range(4), key=lambda i: wear[i])
        if self._wear_said and wear[worst] < min(self._wear_said) - 10:
            self._wear_said.clear()          # fresh tyres fitted
            self._blister_said.clear()
        for step in self.wear_steps:
            if wear[worst] >= step and step not in self._wear_said:
                self._wear_said.add(step)
                self._pending.append(
                    ("tyre_wear", f"Tyre wear {step} percent, {_NAMES[worst]} highest.")
                )
        for i in range(4):
            if d.tyre_blisters[i] >= self.blister_pct and i not in self._blister_said:
                self._blister_said.add(i)
                self._pending.append(("tyre_wear", f"Blistering on {_NAMES[i]}."))

        prev, self._dmg = self._dmg, d
        if prev is None:
            return
        parts = (
            ("front left wing", "front_left_wing"), ("front right wing", "front_right_wing"),
            ("rear wing", "rear_wing"), ("floor", "floor"), ("diffuser", "diffuser"),
            ("sidepod", "sidepod"), ("gearbox", "gearbox"), ("engine", "engine"),
        )
        for label, attr in parts:
            now, before = getattr(d, attr), getattr(prev, attr)
            if now - before >= self.damage_step and sim_time - self._dmg_said.get(attr, -999.0) > 3.0:
                self._dmg_said[attr] = sim_time
                self.speak("damage", f"{label.capitalize()} damage, {now} percent.")
        if d.drs_fault and not prev.drs_fault:
            self.speak("damage", "DRS fault.")
        if d.ers_fault and not prev.ers_fault:
            self.speak("damage", "ERS fault.")
