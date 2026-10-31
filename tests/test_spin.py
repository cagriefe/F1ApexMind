# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) ApexMind contributors
import unittest

from apexmind.engineer import SpinCoach
from apexmind.telemetry.packets import CarMotion, CarTelemetry


def tel(speed, throttle=0.0, brake=0.0):
    return CarTelemetry(
        speed_kmh=speed, throttle=throttle, steer=0.0, brake=brake,
        clutch=0, gear=3, rpm=9000, drs=0, rev_percent=70,
    )


def mot(vx, vz, fx=(1.0, 0.0), right=(0.0, 1.0)):
    """The car faces +x; +z is right; pass the velocity vector as (vx, vz)."""
    return CarMotion(
        x=0.0, y=0.0, z=0.0,
        vx=vx, vy=0.0, vz=vz,
        g_lat=0.0, g_long=0.0, g_vert=1.0,
        yaw=0.0, pitch=0.0, roll=0.0,
        fx=fx[0], fy=0.0, fz=fx[1],
        rx=right[0], ry=0.0, rz=right[1],
    )


class SpinTest(unittest.TestCase):
    def setUp(self):
        self.msgs = []
        self.sc = SpinCoach(speak_cb=lambda k, t: self.msgs.append((k, t)))

    def run_frames(self, t, telem, m, n=1, dt=1 / 60):
        for _ in range(n):
            self.sc.on_frame(telem, m, t)
            t += dt
        return t

    def test_spin_detected_and_guides(self):
        # the car goes towards +x but more than half of its speed is along +z (RIGHT) -> spin
        t = 0.0
        m = mot(vx=20.0, vz=15.0, fx=(1.0, 0.0), right=(0.0, 1.0))
        # hafif kays? 0.6-1/60 ~. (0.6/25 = 0.6-abort) confirm_frames = 10
        t = self.run_frames(t, tel(90, throttle=1.0), m, n=10)
        kinds = [k for k, _ in self.msgs]
        self.assertIn("spin", kinds)
        txt = next(x for k, x in self.msgs if k == "spin")
        self.assertIn("RIGHT", txt)          # lateral >0 -> sag
        # if the driver gives too much throttle while spinning, warn (said on the previous frame,
        # reset now so the "once" behaviour can be tested independently)
        self.msgs.clear()
        self.sc._said_at_spin.clear()
        self.run_frames(t, tel(70, throttle=0.9), m)
        self.assertIn("spin_no_gas", [k for k, _ in self.msgs])
        # recovery: speed settled
        self.msgs.clear()
        m2 = mot(vx=25.0, vz=0.0)
        self.run_frames(t, tel(90, throttle=0.3), m2, n=34)
        self.assertIn("recover", [k for k, _ in self.msgs])
        self.assertFalse(self.sc.is_spinning)

    def test_left_spin(self):
        m = mot(vx=20.0, vz=-15.0, right=(0.0, 1.0))
        self.run_frames(0.0, tel(90), m, n=10)
        txt = next(x for k, x in self.msgs if k == "spin")
        self.assertIn("LEFT", txt)

    def test_normal_driving_no_false_positive(self):
        m = mot(vx=30.0, vz=1.0)      # slight rightward heading (straight race)
        self.run_frames(0.0, tel(150, throttle=0.9), m, n=60)
        self.assertEqual(self.msgs, [])


if __name__ == "__main__":
    unittest.main()
