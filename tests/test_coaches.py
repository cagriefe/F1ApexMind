# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) ApexMind contributors
import unittest

from apexmind.engineer import DeltaTracker, LockupCoach, TrackLimits, TyreCoach
from apexmind.telemetry.packets import CarDamage, CarTelemetry, LapData, MotionEx


def tel(speed=200, thr=0.0, brk=0.0, steer=0.0, inner=None, surface=None):
    return CarTelemetry(
        speed_kmh=speed, throttle=thr, steer=steer, brake=brk, clutch=0, gear=6, rpm=11000,
        drs=0, rev_percent=80, tyre_inner_temp=inner, surface_type=surface,
    )


def mex(slip):
    return MotionEx(wheel_speed=(50.0,) * 4, wheel_slip_ratio=slip, wheel_slip_angle=(0.0,) * 4)


class Collect:
    def __init__(self):
        self.msgs = []

    def __call__(self, kind, text):
        self.msgs.append((kind, text))


class LockupTests(unittest.TestCase):
    def run_frames(self, slip, brake=0.8, throttle=0.0, frames=10):
        out = Collect()
        c = LockupCoach(out)
        for i in range(frames):
            now = i * 0.02
            c.on_frame(tel(200, throttle, brake), mex(slip), now, now)
        return out.msgs

    def test_front_left_lock_once(self):
        msgs = self.run_frames((0.0, 0.0, -0.5, -0.05))
        self.assertEqual(len(msgs), 1)
        self.assertEqual(msgs[0][1], "Front left locked!")

    def test_normal_heavy_braking_is_silent(self):
        self.assertEqual(self.run_frames((-0.1, -0.1, -0.15, -0.15)), [])

    def test_no_warning_off_brake_or_with_throttle(self):
        self.assertEqual(self.run_frames((0, 0, -0.6, -0.6), brake=0.1), [])
        self.assertEqual(self.run_frames((0, 0, 0.6, 0.6), brake=0.0, throttle=1.0), [])

    def test_single_noisy_frame_ignored(self):
        self.assertEqual(self.run_frames((0, 0, -0.6, 0), frames=2), [])

    def test_stale_motion_ex_ignored(self):
        out = Collect()
        c = LockupCoach(out)
        for i in range(10):
            now = 5 + i * 0.02
            c.on_frame(tel(200, 0.0, 0.8), mex((0, 0, -0.9, -0.9)), 0.0, now)
        self.assertEqual(out.msgs, [])


class TyreTests(unittest.TestCase):
    def dmg(self, wear=(10.0,) * 4, rw=0, blist=(0,) * 4, drs=0):
        return CarDamage(tyres_wear=wear, tyres_damage=(0,) * 4, brakes_damage=(0,) * 4,
                         tyre_blisters=blist, front_left_wing=0, front_right_wing=0, rear_wing=rw,
                         floor=0, diffuser=0, sidepod=0, drs_fault=drs, ers_fault=0, gearbox=0, engine=0)

    def test_hot_tyre_only_on_straight(self):
        out = Collect()
        c = TyreCoach(out)
        c.on_telemetry(tel(200, 0.5, 0.0, 0.3, inner=(90, 90, 120, 90)), 1.0)   # corner
        self.assertEqual(out.msgs, [])
        c.on_telemetry(tel(250, 1.0, 0.0, 0.0, inner=(90, 90, 120, 90)), 2.0)   # straight
        self.assertIn("Front left tyre hot, 120", out.msgs[0][1])
        c.on_telemetry(tel(250, 1.0, 0.0, 0.0, inner=(90, 90, 120, 90)), 3.0)   # cooldown
        self.assertEqual(len(out.msgs), 1)

    def test_wear_steps_spoken_once_on_straight(self):
        out = Collect()
        c = TyreCoach(out)
        c.on_damage(self.dmg(), 0.0)
        c.on_damage(self.dmg(wear=(10, 10, 52.0, 40)), 1.0)
        self.assertEqual(out.msgs, [])  # waits for a straight first
        for i in range(3):
            c.on_telemetry(tel(250, 1.0, 0.0, 0.0, inner=(95,) * 4), 2.0 + i)
        texts = [t for _k, t in out.msgs]
        self.assertEqual(texts, ["Tyre wear 30 percent, front left highest.",
                                 "Tyre wear 50 percent, front left highest."])

    def test_damage_reported_when_it_increases(self):
        out = Collect()
        c = TyreCoach(out)
        c.on_damage(self.dmg(), 0.0)
        c.on_damage(self.dmg(rw=30), 1.0)
        c.on_damage(self.dmg(rw=30, drs=1), 2.0)
        texts = [t for _k, t in out.msgs]
        self.assertEqual(texts, ["Rear wing damage, 30 percent.", "DRS fault."])


def lap(num, dist, ms, sector=0, invalid=False, last=0, cc=0):
    return LapData(lap_distance=dist, current_lap_num=num, current_lap_time_ms=ms,
                   last_lap_time_ms=last, current_lap_invalid=invalid, sector=sector,
                   corner_cutting_warnings=cc)


class LimitsTests(unittest.TestCase):
    def test_invalid_flip_speaks_once_and_warning_counts(self):
        out = Collect()
        tl = TrackLimits(out)
        tl.on_lap(lap(2, 100, 5000))
        tl.on_lap(lap(2, 900, 40000, invalid=True))
        tl.on_lap(lap(2, 950, 41000, invalid=True))
        self.assertEqual([k for k, _ in out.msgs], ["lap_invalid"])
        tl.on_lap(lap(3, 10, 100))
        tl.on_lap(lap(3, 500, 20000, cc=1))
        self.assertEqual(out.msgs[-1], ("limits", "Track limits warning 1."))

    def test_no_wheel_on_tarmac_warns_before_the_game_flag(self):
        # Real data: a kerb + grass combination (no wheel on tarmac) deletes the lap after ~0.4 s.
        out = Collect()
        tl = TrackLimits(out)
        for i in range(10):
            tl.on_telemetry(tel(280, surface=(1, 7, 1, 7)), i * 0.05)
        self.assertEqual(out.msgs, [("wide", "Off track!")])
        out.msgs.clear()
        tl2 = TrackLimits(out)
        for i in range(10):
            tl2.on_telemetry(tel(280, surface=(0, 7, 7, 7)), i * 0.05)   # one wheel on tarmac
        self.assertEqual(out.msgs, [])
        for i in range(10):
            tl2.on_telemetry(tel(60, surface=(7, 7, 7, 7)), 1 + i * 0.05)  # slow: spin/gravel, no warning
        self.assertEqual(out.msgs, [])

    def test_violation_spot_is_remembered_and_warned_next_lap(self):
        out = Collect()
        tl = TrackLimits(out)
        tl.track_length = 7007.0
        tl.on_lap(lap(2, 1100.0, 0))
        for i in range(8):     # violation starts at 1150 m, 280 km/h
            tl.on_telemetry(tel(280, surface=(1, 7, 1, 7)), i * 0.05, 1150.0 + i * 3.9)
        tl.on_lap(lap(2, 1185.0, 0, invalid=True), 0.4)
        self.assertEqual(len(tl.hotspots), 1)
        self.assertAlmostEqual(tl.hotspots[0], 1150.0, delta=2.0)
        out.msgs.clear()
        tl.on_lap(lap(3, 10.0, 0))
        tl.on_telemetry(tel(280), 100.0, 1050.0)                 # 100 m before = 1.3 s: still early
        self.assertEqual(out.msgs, [])
        tl.on_telemetry(tel(280), 100.05, 1085.0)                # 65 m before = 0.84 s
        self.assertEqual(out.msgs, [("call_limits", "Limits")])
        tl.on_telemetry(tel(280), 100.1, 1090.0)                 # not again in the same lap
        self.assertEqual(len(out.msgs), 1)


class DeltaTests(unittest.TestCase):
    def drive_lap(self, d, num, ms_per_m, last=0, invalid_at=None):
        for dist in range(0, 4300, 50):
            sector = 0 if dist < 1400 else (1 if dist < 2900 else 2)
            d.update(lap(num, float(dist), int(dist * ms_per_m), sector=sector, last=last,
                         invalid=invalid_at is not None and dist >= invalid_at))

    def test_sector_deltas_against_best_lap(self):
        out = Collect()
        d = DeltaTracker(out)
        self.drive_lap(d, 1, 20.0)
        self.assertEqual([t for _k, t in out.msgs][0][:9], "Sector 1,")   # no reference: time only
        out.msgs.clear()
        self.drive_lap(d, 2, 19.0, last=86000)    # 1 s per 1000 m faster
        texts = [t for _k, t in out.msgs]
        self.assertTrue(texts[0].startswith("Sector 1, minus 1."), texts)
        self.assertIn("Total minus", texts[1])

    def test_invalid_lap_never_becomes_reference(self):
        out = Collect()
        d = DeltaTracker(out)
        self.drive_lap(d, 1, 15.0, invalid_at=100)       # very fast but invalid
        self.drive_lap(d, 2, 20.0, last=64500)
        self.assertIsNone(d.best_ms)


if __name__ == "__main__":
    unittest.main()
