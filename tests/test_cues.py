# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) ApexMind contributors
import unittest

from apexmind.engineer import Analyzer
from apexmind.telemetry.packets import CarMotion, CarTelemetry

TRACK = 5000.0


def tel(speed=200, throttle=0.0, brake=0.0, steer=0.0, gear=7):
    return CarTelemetry(
        speed_kmh=int(speed), throttle=throttle, steer=steer, brake=brake,
        clutch=0, gear=gear, rpm=11000, drs=0, rev_percent=80,
    )


def mot(speed_kmh=200.0):
    v = speed_kmh / 3.6
    return CarMotion(
        x=0.0, y=0.0, z=0.0, vx=v, vy=0.0, vz=0.0,
        g_lat=0.0, g_long=0.0, g_vert=1.0,
        yaw=0.0, pitch=0.0, roll=0.0,
        fx=1.0, fy=0.0, fz=0.0,
        rx=0.0, ry=0.0, rz=1.0,
    )


class CueTimingTest(unittest.TestCase):
    def setUp(self):
        self.cues: list[str] = []
        self.msgs: list[tuple[str, str]] = []
        self.an = Analyzer(
            speak_cb=lambda k, t: self.msgs.append((k, t)),
            learn_n=1,
            cooldown_s=0.0,
            cue_lead_s=1.5,
            cue_min_lead_m=12.0,
            cue_max_lead_m=150.0,
        )
        self.an.set_track_length(TRACK)
        self.an.set_cues(
            lambda: self.cues.append("brake"),
            lambda: self.cues.append("throttle"),
        )
        self.t = 0.0

    def frame(self, dist, lap, speed=200.0, brake=0.0, throttle=0.0, steer=0.0):
        self.an.on_frame(
            tel(speed, throttle, brake, steer),
            mot(speed),
            self.t,
            lap_distance=dist,
            lap_num=lap,
        )
        self.t += 0.1

    def learn_zone(self):
        # lap 1: brake at 1000 m, release at 1100 m; turn-in at 1120, throttle at 1150
        self.frame(900, 1, 200, 0.0)
        self.frame(1000, 1, 200, 0.6)
        self.frame(1050, 1, 150, 0.6)
        self.frame(1100, 1, 100, 0.0)
        for i in range(4):
            self.frame(1120 + i * 10, 1, 100, 0.0, 1.0, 0.5)

        self.assertEqual(len(self.an.zones), 1)
        self.assertEqual(self.msgs, [])  # learning lap is silent

    def test_cue_fires_well_before_the_corner(self):
        self.learn_zone()
        self.cues.clear()

        # 100 m before: at 200 km/h the lead is ~83 m -> no beep yet
        self.frame(900, 2, 200)
        self.assertEqual(self.cues, [])

        # 80 m before: the beep should come
        self.frame(920, 2, 200)
        self.assertEqual(self.cues, ["brake"])

        # Must not repeat in the same lap
        self.frame(930, 2, 200)
        self.assertEqual(self.cues, ["brake"])

        # We passed the brake point: no brake beep
        self.frame(1005, 2, 200)
        self.assertEqual(self.cues, ["brake"])

        # Throttle lift point ~1103 (pedal threshold crossing, interpolated); one high beep at 1090
        self.frame(1090, 2, 200)
        self.assertEqual(self.cues, ["brake", "throttle"])

    def test_throttle_cue_is_not_shadowed_by_already_cued_brake(self):
        # Brake at 900 m, throttle at 958 m (only 58 m later). At 200 km/h the lead is ~83 m.
        self.frame(800, 1, 200, 0.0)
        self.frame(900, 1, 200, 0.6)
        self.frame(920, 1, 150, 0.6)
        self.frame(950, 1, 100, 0.0)
        for i in range(4):
            self.frame(952 + i * 2, 1, 100, 0.0, 1.0, 0.5)
        self.cues.clear()

        # 820 m: ~80 m to the brakes -> brake beep
        self.frame(820, 2, 200)
        self.assertEqual(self.cues, ["brake"])

        # 880 m: not yet at the brake point (900 m); even though the throttle target is 78 m
        # ahead, the "throttle" beep must not play before the brake.
        self.frame(880, 2, 200)
        self.assertEqual(self.cues, ["brake"])

        # 900 m: at the brake point; throttle target 58 m ahead -> throttle beep
        # should come right away, without being shadowed by the brake beep.
        self.frame(900, 2, 200)
        self.assertEqual(self.cues, ["brake", "throttle"])

    def test_zero_lead_cues_at_brake_point(self):
        self.learn_zone()
        self.an.cue_lead_s = 0.0
        self.an.cue_min_lead_m = 0.0
        self.cues.clear()

        # 5 m before (frame interval ~5.5 m, half is 2.8 m): no beep yet
        self.frame(995, 2, 200)
        self.assertEqual(self.cues, [])

        # Exactly at the brake point: double beep
        self.frame(1000, 2, 200)
        self.assertEqual(self.cues, ["brake"])

    def test_cue_at_250_kmh_is_about_100m_early(self):
        self.learn_zone()
        self.cues.clear()
        # 250 km/h -> 1.5 s = ~104 m. The beep should come 100 m before.
        self.frame(900, 2, 250)
        self.assertEqual(self.cues, ["brake"])

    def test_no_cue_before_learning(self):
        self.frame(900, 1, 200)
        self.assertEqual(self.cues, [])

    def test_outlier_does_not_poison_baseline(self):
        self.learn_zone()
        self.msgs.clear()

        # lap 2: 50 m early + very hard braking
        self.frame(950, 2, 200, 0.0)
        self.frame(950, 2, 200, 1.0)
        self.frame(1000, 2, 150, 1.0)
        self.frame(1050, 2, 100, 0.0)
        self.frame(1100, 2, 100, 0.0, 1.0, 0.5)

        kinds = [k for k, _ in self.msgs]
        self.assertIn("brake", kinds)
        self.assertTrue(any("EARLY" in t for _, t in self.msgs))
        self.assertTrue(any("too hard" in t for _, t in self.msgs))

        z = self.an.zones[0]
        # The outlier lap must not be added to the baseline
        self.assertEqual(z.brake_lap, [1000.0])
        self.assertEqual(z.depth, [0.6])

        # lap 3 repeats the same mistake: it must still warn (baseline did not drift)
        self.msgs.clear()
        self.frame(950, 3, 200, 0.0)
        self.frame(950, 3, 200, 1.0)
        self.frame(1000, 3, 150, 1.0)
        self.frame(1050, 3, 100, 0.0)
        self.frame(1100, 3, 100, 0.0, 1.0, 0.5)
        self.assertTrue(any("EARLY" in t for _, t in self.msgs))
        self.assertTrue(any("too hard" in t for _, t in self.msgs))


if __name__ == "__main__":
    unittest.main()
