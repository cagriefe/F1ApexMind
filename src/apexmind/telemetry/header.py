# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) ApexMind contributors
"""UDP packet header parsing.

Supports F1 22/23/24/25 and the F1 25 2026 Season Pack (packet format 2026).
All fields are little-endian and packed.

Correct header sizes:
  2022      -> 24 bytes (no gameYear / overallFrameIdentifier)
  2023-2026 -> 29 bytes (gameYear + overallFrameIdentifier present)
"""

from __future__ import annotations

import struct
from dataclasses import dataclass

# format -> header length in bytes
HEADER_SIZES = {2022: 24, 2023: 29, 2024: 29, 2025: 29, 2026: 29}
SUPPORTED_FORMATS = tuple(sorted(HEADER_SIZES))

# 2022: no gameYear or overallFrameIdentifier.
_HEADER_2022 = struct.Struct("<HBBBBQfIBB")
# 2023+: gameYear and overallFrameIdentifier added.
_HEADER_2023_PLUS = struct.Struct("<HBBBBBQfIIBB")


@dataclass(slots=True)
class PacketHeader:
    format: int          # 2022 / 2023 / 2024 / 2025 / 2026
    game_year: int       # 22 for 2022; the game year for 2023+ (25 -> F1 25)
    major: int
    minor: int
    packet_version: int
    packet_id: int       # 0=motion, 1=session, 2=lapdata, 6=telemetry ...
    session_uid: int
    session_time: float
    frame_id: int
    overall_frame_id: int
    player_index: int    # index of the player's car
    secondary_player_index: int = 0


class UnknownFormat(Exception):
    def __init__(self, fmt: int):
        supported = ", ".join(str(f) for f in SUPPORTED_FORMATS)
        super().__init__(
            f"unknown packet format: {fmt} (supported: {supported}). "
            "Set the in-game UDP Format to 2025 or 2026."
        )


def parse_header(buf: bytes | bytearray | memoryview) -> tuple[PacketHeader, int]:
    """Parse an incoming datagram into a header.

    Returns ``(header, header_length)``.
    """
    if len(buf) < 2:
        raise ValueError("packet too short for a header")

    (fmt,) = struct.unpack_from("<H", buf, 0)
    if fmt not in HEADER_SIZES:
        raise UnknownFormat(fmt)

    if fmt == 2022:
        s = _HEADER_2022
        if len(buf) < s.size:
            raise ValueError(f"header too short: {len(buf)} < {s.size}")
        f = s.unpack_from(buf, 0)
        h = PacketHeader(
            format=fmt,
            game_year=22,
            major=f[1],
            minor=f[2],
            packet_version=f[3],
            packet_id=f[4],
            session_uid=f[5],
            session_time=f[6],
            frame_id=f[7],
            overall_frame_id=0,
            player_index=f[8],
            secondary_player_index=f[9],
        )
    else:
        s = _HEADER_2023_PLUS
        if len(buf) < s.size:
            raise ValueError(f"header too short: {len(buf)} < {s.size}")
        f = s.unpack_from(buf, 0)
        h = PacketHeader(
            format=fmt,
            game_year=f[1],
            major=f[2],
            minor=f[3],
            packet_version=f[4],
            packet_id=f[5],
            session_uid=f[6],
            session_time=f[7],
            frame_id=f[8],
            overall_frame_id=f[9],
            player_index=f[10],
            secondary_player_index=f[11],
        )

    return h, s.size
