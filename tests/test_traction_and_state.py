# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) ApexMind contributors
import unittest

from apexmind.engineer import Analyzer, TractionCoach
from apexmind.telemetry.packets import CarTelemetry, MotionEx
from sim import drive


def tel(thr=1.0, steer=0.0, speed=120):
    return CarTelemetry(speed_kmh=speed, throttle=thr, steer=steer, brake=0.0, clutch=0, gear=3,
                        rpm=9000, drs=0, rev_percent=70)


def mex(rl=0.0, rr=0.0):
    return MotionEx(wheel_speed=(40.0,) * 4, wheel_slip_ratio=(rl, rr, 0.0, 0.0), wheel_slip_angle=(0.0,) * 4)


class Collect:
    def __init__(self):
        self.msgs = []

    def __call__(self, kind, text):
        self.msgs.append((kind, text))


class TractionTests(unittest.TestCase):
    def feed(self, tr, thr, steer, rl, rr, frames=4):
        for i in range(frames):
            now = i * 0.017
            tr.on_frame(tel(thr, steer), mex(rl, rr), now, now)

    def test_full_throttle_wheelspin_says_gas_70(self):
        out = Collect()
        self.feed(TractionCoach(out), 1.0, 0.0, 0.1, 0.35)
        self.assertEqual(out.msgs, [("call_traction", "Gas 70")])

    def test_levels_scale_with_current_throttle(self):
        for thr, expect in ((0.5, "Gas 30"), (0.45, "Gas 30"), (0.8, "Gas 50")):
            with self.subTest(thr=thr):
                out = Collect()
                self.feed(TractionCoach(out), thr, 0.0, 0.0, 0.4)
                self.assertEqual(out.msgs[0][1], expect)

    def test_loaded_corner_triggers_earlier(self):
        quiet, loaded = Collect(), Collect()
        self.feed(TractionCoach(quiet), 0.8, 0.05, 0.0, 0.20)    # straight: 0.20 < 0.25 -> quiet
        self.feed(TractionCoach(loaded), 0.8, 0.40, 0.0, 0.20)   # steering open: 0.20 >= 0.18 -> warning
        self.assertEqual(quiet.msgs, [])
        self.assertEqual(len(loaded.msgs), 1)

    def test_normal_power_and_cooldown(self):
        out = Collect()
        tr = TractionCoach(out)
        self.feed(tr, 1.0, 0.0, 0.05, 0.08)
        self.assertEqual(out.msgs, [])
        for i in range(60):          # continuous wheelspin for 1 s: cooldown (1.2 s) allows a single call
            tr.on_frame(tel(1.0), mex(0.0, 0.4), 1.0 + i * 0.017, 1.0 + i * 0.017)
        self.assertEqual(len(out.msgs), 1)
        self.assertGreater(tr.rear_slip, 0.3)


class GasCallsFollowState(unittest.TestCase):
    @staticmethod
    def gas_calls(ev, lap):
        return [e[0] for e in ev if e[1] == lap and e[0] in ("call_gas", "call_full")]

    def test_no_gas_up_calls_while_steering_is_still_wound_up(self):
        an = Analyzer(lambda k, t: None, learn_n=1, cooldown_s=0, calls=True)
        ev = drive(an, laps=5, hz=60, steer_extra={4: 0.8})
        self.assertIn("call_full", self.gas_calls(ev, 3))      # normal lap: has a full-throttle call
        self.assertNotIn("call_full", self.gas_calls(ev, 4))   # steering still too open: no "Full"
        self.assertLessEqual(len(self.gas_calls(ev, 4)), 1)    # only the first step before the steering opens
        self.assertIn("call_full", self.gas_calls(ev, 5))     # back to normal on the next lap
        brake4 = [e for e in ev if e[0] == "call_brake" and e[1] == 4]
        self.assertTrue(brake4)                                # brake calls are unaffected

    def test_gas_call_fires_when_steering_unwinds_even_if_late(self):
        an = Analyzer(lambda k, t: None, learn_n=1, cooldown_s=0, calls=True)
        ev = drive(an, laps=4, hz=60)
        full = next(e for e in ev if e[0] == "call_full" and e[1] == 3)
        self.assertEqual(full[3], "Full")
        calls = [e[0] for e in ev if e[1] == 3 and e[0].startswith("call_")]
        order = {"call_brake": 0, "call_ease": 1, "call_gas": 2, "call_full": 3}
        self.assertEqual([order[c] for c in calls], sorted(order[c] for c in calls))   # order stays intact


if __name__ == "__main__":
    unittest.main()
