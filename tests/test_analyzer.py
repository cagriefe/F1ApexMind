# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) ApexMind contributors
import unittest

from apexmind.engineer import Analyzer
from apexmind.telemetry.packets import CarMotion, CarTelemetry


def tel(speed, throttle=0.0, brake=0.0, steer=0.0, gear=4):
    return CarTelemetry(
        speed_kmh=int(speed), throttle=throttle, steer=steer, brake=brake,
        clutch=0, gear=gear, rpm=9000, drs=0, rev_percent=70,
    )


def mot(x, vx=30.0, vz=0.0):
    """Drive down +x: velocity vector +x; 2D world coordinates."""
    return CarMotion(
        x=x, y=0.0, z=0.0,
        vx=vx, vy=0.0, vz=vz,
        g_lat=0.0, g_long=0.0, g_vert=1.0,
        yaw=0.0, pitch=0.0, roll=0.0,
    )


class AnalyzerBrakeTest(unittest.TestCase):
    def setUp(self):
        self.msgs: list[tuple[str, str]] = []
        self.an = Analyzer(speak_cb=lambda k, t: self.msgs.append((k, t)), learn_n=1, cooldown_s=10.0)

    def frames(self, seq):
        t = 0.0
        for (x, speed, brake, throttle, steer) in seq:
            self.an.on_frame(tel(speed, throttle, brake, steer), mot(x), t)
            t += 0.1

    def test_learns_then_flags_early_brake_and_hard_brake(self):
        # LAP 1: braking starts at x=100, depth ~0.6, entry speed 255
        self.frames([
            (98.0, 260, 0.0, 1.0, 0.0),
            (100.0, 255, 0.60, 0.0, 0.0),   # zone opens
            (94.0, 234, 0.60, 0.0, 0.0),
            (88.0, 220, 0.60, 0.0, 0.0),
            (76.0, 200, 0.05, 0.0, 0.0),
            (64.0, 185, 0.0, 1.0, 0.0),     # zone closes
            (68.0, 180, 0.0, 1.0, 0.45),    # turn-in (x=68)
            (60.0, 170, 0.0, 1.0, 0.45),
        ])
        self.assertEqual(len(self.an.zones), 1)
        self.assertEqual(self.msgs, [])          # silent on the learning lap

        # LAP 2: near the same point (x=102): baseline stays consistent
        self.frames([
            (130.0, 260, 0.0, 1.0, 0.0),
            (102.0, 255, 0.62, 0.0, 0.0),   # dist |102-100| = 2 -> same zone
            (92.0, 230, 0.62, 0.0, 0.0),
            (80.0, 205, 0.05, 0.0, 0.0),
            (70.0, 190, 0.0, 1.0, 0.0),
            (74.0, 185, 0.0, 1.0, 0.45),    # turn-in ~74 (+6m, ehe < 25)
            (64.0, 175, 0.0, 1.0, 0.45),
        ])
        self.assertEqual([m[0] for m in self.msgs], [])   # no deviation -> silent

        # LAP 3: braking 32 m EARLY + depth 1.0 (avg ~0.61) -> two warnings
        self.frames([
            (130.0, 260, 0.0, 1.0, 0.0),
            (68.0, 250, 0.55, 0.0, 0.0),    # |68 - 101| = 33 -> same zone; signed -33 < -30
            (60.0, 220, 1.00, 0.0, 0.0),    # depth 1.0
            (52.0, 180, 0.00, 0.0, 0.0),
            (50.0, 175, 0.00, 1.0, 0.45),   # turn-in (x=50): 50-71 = -21... <25? bak
            (42.0, 165, 0.0, 1.0, 0.45),
        ])
        kinds = [k for k, _ in self.msgs]
        self.assertIn("brake", kinds)
        early = next(t for k, t in self.msgs if "EARLY" in t)
        self.assertIn("33 meters", early)
        hard = next(t for k, t in self.msgs if "too hard" in t)
        self.assertIn("Braking too hard", hard)

    def test_late_apex_speaks(self):
        # create a zone and collect turn-in; then come back much later
        self.frames([
            (98.0, 260, 0.0, 1.0, 0.0),
            (100.0, 255, 0.60, 0.0, 0.0),
            (88.0, 220, 0.60, 0.0, 0.0),
            (64.0, 185, 0.0, 1.0, 0.0),    # close
            (68.0, 180, 0.0, 1.0, 0.45),   # turn-in avg 68
            (60.0, 170, 0.0, 1.0, 0.45),
        ])
        self.msgs.clear()
        self.frames([
            (130.0, 260, 0.0, 1.0, 0.0),
            (102.0, 255, 0.60, 0.0, 0.0),
            (90.0, 220, 0.60, 0.0, 0.0),
            (66.0, 185, 0.0, 1.0, 0.0),    # close; brake<0.3
            (100.0, 175, 0.0, 1.0, 0.45),  # turn-in x=100: 100-68 = +32 -> LATE
            (92.0, 170, 0.0, 1.0, 0.45),
        ])
        kinds = [k for k, _ in self.msgs]
        self.assertIn("apex", kinds)
        self.assertIn("32 meters", next(t for k, t in self.msgs if k == "apex"))


if __name__ == "__main__":
    unittest.main()
