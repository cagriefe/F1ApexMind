# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) ApexMind contributors
import struct
import unittest

from apexmind.telemetry.header import (
    HEADER_SIZES,
    SUPPORTED_FORMATS,
    UnknownFormat,
    parse_header,
)


class HeaderTest(unittest.TestCase):
    """Verifies the real F1 UDP header layouts."""

    def test_2022_roundtrip(self):
        raw = struct.pack(
            "<HBBBBQfIBB",
            2022,      # packetFormat
            1, 12,     # major, minor
            2,         # packetVersion
            6,         # packetId (car telemetry)
            555,       # sessionUID
            0.25,      # sessionTime
            7,         # frameId
            10, 0,     # playerIndex, secondary
        )
        h, size = parse_header(raw)
        self.assertEqual(size, HEADER_SIZES[2022])
        self.assertEqual(size, 24)
        self.assertEqual(h.format, 2022)
        self.assertEqual(h.game_year, 22)
        self.assertEqual(h.packet_id, 6)
        self.assertEqual(h.player_index, 10)
        self.assertEqual(h.secondary_player_index, 0)
        self.assertEqual(h.overall_frame_id, 0)

    def test_2023_roundtrip(self):
        raw = struct.pack(
            "<HBBBBBQfIIBB",
            2023,      # packetFormat
            23,        # gameYear
            1, 12,     # major, minor
            2,         # packetVersion
            6,         # packetId
            555,       # sessionUID
            0.25,      # sessionTime
            7, 8,      # frameId, overallFrameId
            10, 0,     # playerIndex, secondary
        )
        h, size = parse_header(raw)
        self.assertEqual(size, 29)
        self.assertEqual(h.format, 2023)
        self.assertEqual(h.game_year, 23)
        self.assertEqual(h.packet_id, 6)
        self.assertEqual(h.player_index, 10)
        self.assertEqual(h.overall_frame_id, 8)

    def test_2024_roundtrip(self):
        raw = struct.pack(
            "<HBBBBBQfIIBB",
            2024,      # packetFormat
            25,        # gameYear (F1 25 also uses this layout)
            1, 26,     # major, minor
            4,         # packetVersion
            2,         # packetId (lapdata)
            987654321, # sessionUID
            1.5,       # sessionTime
            100, 101,  # frameId, overallFrameId
            3, 0,      # playerIndex, secondary
        )
        h, size = parse_header(raw)
        self.assertEqual(size, HEADER_SIZES[2024])
        self.assertEqual(size, 29)
        self.assertEqual(h.format, 2024)
        self.assertEqual(h.game_year, 25)
        self.assertEqual(h.packet_id, 2)
        self.assertEqual(h.session_uid, 987654321)
        self.assertAlmostEqual(h.session_time, 1.5)
        self.assertEqual(h.player_index, 3)

    def test_2025_roundtrip(self):
        raw = struct.pack(
            "<HBBBBBQfIIBB",
            2025, 25, 1, 0, 1, 0, 42, 2.5, 10, 11, 4, 255,
        )
        h, size = parse_header(raw)
        self.assertEqual(size, 29)
        self.assertEqual(h.format, 2025)
        self.assertEqual(h.game_year, 25)
        self.assertEqual(h.packet_id, 0)
        self.assertEqual(h.player_index, 4)
        self.assertEqual(h.secondary_player_index, 255)

    def test_2026_roundtrip(self):
        raw = struct.pack(
            "<HBBBBBQfIIBB",
            2026, 26, 1, 0, 1, 0, 99, 0.1, 1, 1, 23, 255,
        )
        h, size = parse_header(raw)
        self.assertEqual(size, 29)
        self.assertEqual(h.format, 2026)
        self.assertEqual(h.game_year, 26)
        self.assertEqual(h.player_index, 23)

    def test_unknown_format(self):
        raw = struct.pack("<H", 1999) + b"\x00" * 40
        with self.assertRaises(UnknownFormat):
            parse_header(raw)

    def test_short_header(self):
        with self.assertRaises(ValueError):
            parse_header(b"\x01")

    def test_supported_formats(self):
        self.assertEqual(SUPPORTED_FORMATS, (2022, 2023, 2024, 2025, 2026))


if __name__ == "__main__":
    unittest.main()
