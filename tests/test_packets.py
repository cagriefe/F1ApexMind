# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) ApexMind contributors
import struct
import unittest

from apexmind.telemetry.packets import (
    CAR_COUNTS,
    LAP_STRIDES,
    MOTION_STRIDES,
    TELEMETRY_STRIDES,
    parse_car_telemetry,
    parse_event_code,
    parse_lap_data,
    parse_motion,
    parse_session,
)


class CarTelemetryTest(unittest.TestCase):
    def _payload(self, fmt, player_index):
        stride = TELEMETRY_STRIDES[fmt]
        payload = bytearray(CAR_COUNTS[fmt] * stride)
        off = stride * player_index
        struct.pack_into(
            "<HfffBbHBB",
            payload,
            off,
            287,      # speed
            0.85,     # throttle
            -0.25,    # steer
            0.60,     # brake
            42,       # clutch
            7,        # gear
            11500,    # rpm
            1,        # drs
            88,       # rev percent
        )
        return payload, off

    def test_2025_fields_are_at_correct_offsets(self):
        payload, _ = self._payload(2025, 3)
        t = parse_car_telemetry(2025, 3, payload)
        self.assertEqual(t.speed_kmh, 287)
        self.assertAlmostEqual(t.throttle, 0.85, places=5)
        self.assertAlmostEqual(t.steer, -0.25, places=5)
        self.assertAlmostEqual(t.brake, 0.60, places=5)
        self.assertEqual(t.clutch, 42)
        self.assertEqual(t.gear, 7)
        self.assertEqual(t.rpm, 11500)
        self.assertEqual(t.drs, 1)
        self.assertEqual(t.rev_percent, 88)

    def test_2022_uses_same_head(self):
        payload, _ = self._payload(2022, 1)
        t = parse_car_telemetry(2022, 1, payload)
        self.assertEqual(t.speed_kmh, 287)
        self.assertEqual(t.gear, 7)

    def test_2026_uses_59_byte_stride(self):
        self.assertEqual(TELEMETRY_STRIDES[2026], 59)
        payload, _ = self._payload(2026, 4)
        t = parse_car_telemetry(2026, 4, payload)
        self.assertEqual(t.speed_kmh, 287)
        self.assertEqual(t.rpm, 11500)

    def test_invalid_player_index(self):
        payload = bytearray(22 * 60)
        with self.assertRaises(ValueError):
            parse_car_telemetry(2025, 22, payload)

    def test_short_payload(self):
        with self.assertRaises(ValueError):
            parse_car_telemetry(2025, 0, b"\x00" * 5)


class MotionTest(unittest.TestCase):
    def test_2025_motion(self):
        stride = MOTION_STRIDES[2025]
        payload = bytearray(22 * stride)
        off = stride * 2
        struct.pack_into(
            "<6f3h3h6f",
            payload,
            off,
            100.0, 5.0, 200.0,   # world position
            10.0, 0.0, 30.0,     # world velocity
            32767, 0, 0,         # forward (1,0,0)
            0, 0, 32767,         # right (0,0,1)
            1.2, -0.5, 1.0,      # g lat/long/vert
            0.1, 0.02, 0.03,     # yaw/pitch/roll
        )
        m = parse_motion(2025, 2, payload)
        self.assertAlmostEqual(m.x, 100.0)
        self.assertAlmostEqual(m.vx, 10.0)
        self.assertAlmostEqual(m.fx, 1.0, places=4)
        self.assertAlmostEqual(m.rz, 1.0, places=4)
        self.assertAlmostEqual(m.g_lat, 1.2)

    def test_2026_motion_is_54_bytes_and_quantised_gforce(self):
        self.assertEqual(MOTION_STRIDES[2026], 54)
        payload = bytearray(24 * 54)
        off = 54 * 4
        struct.pack_into(
            "<6f3h3h3h3f",
            payload,
            off,
            1.0, 2.0, 3.0,       # position
            4.0, 5.0, 6.0,       # velocity
            32767, 0, 0,         # forward
            0, 0, 32767,         # right
            1000, -500, 1000,    # g lat/long/vert (x1000)
            0.1, 0.2, 0.3,       # yaw/pitch/roll
        )
        m = parse_motion(2026, 4, payload)
        self.assertAlmostEqual(m.x, 1.0)
        self.assertAlmostEqual(m.g_lat, 1.0)
        self.assertAlmostEqual(m.g_long, -0.5)
        self.assertAlmostEqual(m.fx, 1.0, places=4)
        self.assertAlmostEqual(m.rz, 1.0, places=4)

    def test_advanced_moves_position(self):
        stride = MOTION_STRIDES[2025]
        payload = bytearray(22 * stride)
        struct.pack_into(
            "<6f3h3h6f", payload, 0,
            0.0, 0.0, 0.0, 10.0, 0.0, 20.0,
            32767, 0, 0, 0, 0, 32767,
            0.0, 0.0, 0.0, 0.0, 0.0, 0.0,
        )
        m = parse_motion(2025, 0, payload).advanced(0.1)
        self.assertAlmostEqual(m.x, 1.0)
        self.assertAlmostEqual(m.z, 2.0)


class LapDataTest(unittest.TestCase):
    def test_2025_lap_data(self):
        stride = LAP_STRIDES[2025]
        payload = bytearray(22 * stride)
        off = stride * 5
        struct.pack_into("<II", payload, off, 92500, 31200)
        struct.pack_into("<f", payload, off + 20, 1234.5)
        payload[off + 32] = 7     # car position
        payload[off + 33] = 4     # current lap
        payload[off + 34] = 0     # pit status
        payload[off + 37] = 0     # invalid
        payload[off + 44] = 4     # driver status
        lap = parse_lap_data(2025, 5, payload)
        self.assertAlmostEqual(lap.lap_distance, 1234.5)
        self.assertEqual(lap.current_lap_num, 4)
        self.assertEqual(lap.last_lap_time_ms, 92500)
        self.assertEqual(lap.current_lap_time_ms, 31200)
        self.assertEqual(lap.car_position, 7)
        self.assertEqual(lap.driver_status, 4)
        self.assertFalse(lap.current_lap_invalid)

    def test_2022_lap_data_offsets(self):
        stride = LAP_STRIDES[2022]
        payload = bytearray(22 * stride)
        off = stride * 2
        struct.pack_into("<II", payload, off, 100000, 50000)
        struct.pack_into("<f", payload, off + 12, 2222.0)
        payload[off + 24] = 9     # car position
        payload[off + 25] = 3     # current lap
        payload[off + 26] = 2     # pit status
        payload[off + 29] = 1     # invalid
        payload[off + 35] = 2     # driver status
        lap = parse_lap_data(2022, 2, payload)
        self.assertAlmostEqual(lap.lap_distance, 2222.0)
        self.assertEqual(lap.current_lap_num, 3)
        self.assertEqual(lap.pit_status, 2)
        self.assertTrue(lap.current_lap_invalid)
        self.assertEqual(lap.driver_status, 2)


class SessionAndEventTest(unittest.TestCase):
    def test_session_track_info(self):
        payload = bytearray(724)
        struct.pack_into("<BbbBHBB", payload, 0, 1, 30, 25, 5, 5891, 10, 7)
        s = parse_session(2025, payload)
        self.assertEqual(s.track_length, 5891)
        self.assertEqual(s.track_id, 7)
        self.assertEqual(s.session_type, 10)
        self.assertEqual(s.total_laps, 5)
        self.assertEqual(s.weather, 1)

    def test_event_code(self):
        self.assertEqual(parse_event_code(2025, b"FLBK\x00\x00"), "FLBK")

    def test_session_short_payload(self):
        with self.assertRaises(ValueError):
            parse_session(2025, b"\x00" * 4)


if __name__ == "__main__":
    unittest.main()
