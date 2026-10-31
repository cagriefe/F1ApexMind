# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) ApexMind contributors
"""Header + payload parse test with full-size UDP datagrams."""

import struct
import unittest

from apexmind.telemetry.header import parse_header
from apexmind.telemetry.packets import (
    CAR_COUNTS,
    LAP_STRIDES,
    MOTION_STRIDES,
    TELEMETRY_STRIDES,
    parse_car_telemetry,
    parse_lap_data,
    parse_motion,
    parse_session,
)


def header(fmt: int, packet_id: int, player: int = 0, game_year: int = 25) -> bytes:
    if fmt == 2022:
        return struct.pack(
            "<HBBBBQfIBB", 2022, 1, 0, 1, packet_id, 42, 0.0, 1, player, 255
        )
    return struct.pack(
        "<HBBBBBQfIIBB",
        fmt, game_year, 1, 0, 1, packet_id, 42, 0.0, 1, 1, player, 255,
    )


class FullPacketSizeTest(unittest.TestCase):
    def test_f125_telemetry_1352(self):
        payload = bytearray(CAR_COUNTS[2025] * TELEMETRY_STRIDES[2025] + 3)
        off = 3 * TELEMETRY_STRIDES[2025]
        struct.pack_into("<HfffBbHBB", payload, off, 250, 0.8, -0.2, 0.5, 0, 6, 11000, 0, 80)
        raw = header(2025, 6, player=3) + payload
        self.assertEqual(len(raw), 1352)
        h, hsz = parse_header(raw)
        self.assertEqual(h.packet_id, 6)
        self.assertEqual(h.player_index, 3)
        t = parse_car_telemetry(h.format, h.player_index, memoryview(raw)[hsz:])
        self.assertEqual(t.speed_kmh, 250)
        self.assertEqual(t.gear, 6)

    def test_f125_motion_1349(self):
        payload = bytearray(CAR_COUNTS[2025] * MOTION_STRIDES[2025])
        raw = header(2025, 0) + payload
        self.assertEqual(len(raw), 1349)
        h, hsz = parse_header(raw)
        m = parse_motion(h.format, h.player_index, memoryview(raw)[hsz:])
        self.assertEqual(m.x, 0.0)

    def test_f125_lapdata_1285(self):
        payload = bytearray(CAR_COUNTS[2025] * LAP_STRIDES[2025] + 2)
        struct.pack_into("<f", payload, 20, 123.5)
        payload[33] = 2
        raw = header(2025, 2) + payload
        self.assertEqual(len(raw), 1285)
        h, hsz = parse_header(raw)
        lap = parse_lap_data(h.format, h.player_index, memoryview(raw)[hsz:])
        self.assertAlmostEqual(lap.lap_distance, 123.5)
        self.assertEqual(lap.current_lap_num, 2)

    def test_f125_session_753(self):
        payload = bytearray(753 - 29)
        struct.pack_into("<BbbBHBB", payload, 0, 0, 30, 25, 5, 5891, 10, 7)
        raw = header(2025, 1) + payload
        self.assertEqual(len(raw), 753)
        h, hsz = parse_header(raw)
        s = parse_session(h.format, memoryview(raw)[hsz:])
        self.assertEqual(s.track_length, 5891)

    def test_f126_telemetry_1448(self):
        payload = bytearray(CAR_COUNTS[2026] * TELEMETRY_STRIDES[2026] + 3)
        off = 5 * TELEMETRY_STRIDES[2026]
        struct.pack_into("<HfffBbHBB", payload, off, 300, 0.9, 0.1, 0.0, 0, 8, 12000, 1, 90)
        raw = header(2026, 6, player=5, game_year=26) + payload
        self.assertEqual(len(raw), 1448)
        h, hsz = parse_header(raw)
        t = parse_car_telemetry(h.format, h.player_index, memoryview(raw)[hsz:])
        self.assertEqual(t.speed_kmh, 300)
        self.assertEqual(t.gear, 8)

    def test_f126_motion_1325(self):
        payload = bytearray(CAR_COUNTS[2026] * MOTION_STRIDES[2026])
        raw = header(2026, 0, game_year=26) + payload
        self.assertEqual(len(raw), 1325)
        h, hsz = parse_header(raw)
        m = parse_motion(h.format, h.player_index, memoryview(raw)[hsz:])
        self.assertEqual(m.g_vert, 0.0)

    def test_f126_lapdata_1399(self):
        payload = bytearray(CAR_COUNTS[2026] * LAP_STRIDES[2026] + 2)
        raw = header(2026, 2, game_year=26) + payload
        self.assertEqual(len(raw), 1399)
        h, hsz = parse_header(raw)
        lap = parse_lap_data(h.format, h.player_index, memoryview(raw)[hsz:])
        self.assertEqual(lap.current_lap_num, 0)


if __name__ == "__main__":
    unittest.main()
