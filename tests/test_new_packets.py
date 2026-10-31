# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) ApexMind contributors
import unittest

import builders as b
from apexmind.telemetry.header import parse_header
from apexmind.telemetry.packets import (
    parse_car_damage,
    parse_car_telemetry,
    parse_lap_data,
    parse_motion_ex,
)


def payload(buf):
    h, n = parse_header(buf)
    return h, memoryview(buf)[n:]


class NewPacketParsing(unittest.TestCase):
    def test_telemetry_extras(self):
        buf = b.telemetry(
            speed=250, thr=1.0, inner=(81, 82, 83, 84), surf_t=(91, 92, 93, 94),
            press=(21.5, 22.5, 23.5, 24.5), btemp=(401, 402, 403, 404), surface=(0, 1, 7, 4),
        )
        h, p = payload(buf)
        t = parse_car_telemetry(h.format, h.player_index, p)
        self.assertEqual(t.speed_kmh, 250)
        self.assertEqual(t.tyre_inner_temp, (81, 82, 83, 84))
        self.assertEqual(t.tyre_surface_temp, (91, 92, 93, 94))
        self.assertEqual(t.brakes_temp, (401, 402, 403, 404))
        self.assertEqual(t.surface_type, (0, 1, 7, 4))
        self.assertEqual(t.tyre_pressure, (21.5, 22.5, 23.5, 24.5))

    def test_lap_extras_and_old_fields_unchanged(self):
        buf = b.lapdata(3, 1234.5, 61000, last_ms=92000, sector=1, invalid=1, cc=2, warnings=3)
        h, p = payload(buf)
        lap = parse_lap_data(h.format, h.player_index, p)
        self.assertEqual((lap.current_lap_num, lap.sector, lap.corner_cutting_warnings), (3, 1, 2))
        self.assertEqual((lap.total_warnings, lap.current_lap_invalid), (3, True))
        self.assertAlmostEqual(lap.lap_distance, 1234.5, places=2)
        self.assertEqual((lap.last_lap_time_ms, lap.current_lap_time_ms), (92000, 61000))

    def test_motion_ex_slip(self):
        h, p = payload(b.motion_ex(slip=(-0.1, -0.2, -0.5, -0.6), wheel_speed=(40, 41, 42, 43)))
        m = parse_motion_ex(h.format, p)
        self.assertAlmostEqual(m.wheel_slip_ratio[2], -0.5, places=5)
        self.assertAlmostEqual(m.wheel_speed[3], 43.0, places=4)

    def test_damage(self):
        pkt = b.damage(wear=(12.5, 13.5, 30.0, 31.0), blisters=(0, 0, 25, 0), rear_wing=20, floor=7, drs=1)
        h, p = payload(pkt)
        d = parse_car_damage(h.format, h.player_index, p)
        self.assertEqual(d.tyres_wear, (12.5, 13.5, 30.0, 31.0))
        self.assertEqual((d.rear_wing, d.floor, d.drs_fault), (20, 7, 1))
        self.assertEqual(d.tyre_blisters[2], 25)


if __name__ == "__main__":
    unittest.main()
