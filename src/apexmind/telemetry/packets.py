# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) ApexMind contributors
"""Decoding of the UDP packet types the coach needs.

Supported packets:
  - Motion (id=0)
  - Session (id=1)        -> track length / track id
  - Lap Data (id=2)       -> lap number, lap distance, lap time
  - Event (id=3)          -> event codes such as SSTA / SEND / FLBK
  - Car Telemetry (id=6)  -> speed, throttle, brake, steering, gear, RPM

F1 22/23/24/25: 22 cars.
F1 25 2026 Season Pack (format 2026): 24 cars, different motion/telemetry stride.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass, replace

PACKET_IDS = {
    0: "motion",
    1: "session",
    2: "lapdata",
    3: "event",
    4: "participants",
    5: "car_setups",
    6: "car_telemetry",
    7: "car_status",
    8: "final_classification",
    9: "lobby_info",
    10: "car_damage",
    11: "session_history",
    12: "tyre_sets",
    13: "motion_ex",
    14: "time_trial",
    15: "lap_positions",
    16: "car_telemetry_2",   # 2026 Season Pack
}

# ---- car counts and per-car block lengths -------------------------------

CAR_COUNTS = {2022: 22, 2023: 22, 2024: 22, 2025: 22, 2026: 24}

# CarTelemetryData: first 20 bytes are identical in all formats; 59-byte block in 2026.
TELEMETRY_STRIDES = {2022: 60, 2023: 60, 2024: 60, 2025: 60, 2026: 59}

# CarMotionData: 60 bytes in 2022-2025; 54 bytes in 2026 (G-forces are int16).
MOTION_STRIDES = {2022: 60, 2023: 60, 2024: 60, 2025: 60, 2026: 54}

# LapData: 43 bytes in 2022; 57 bytes in 2023+.
LAP_STRIDES = {2022: 43, 2023: 57, 2024: 57, 2025: 57, 2026: 57}

# Raw car telemetry (id=6) leading fields:
# speed u16 | throttle f32 | steer f32 | brake f32 | clutch u8 | gear i8 |
# rpm u16 | drs u8 | revPercent u8
_TEL_HEAD = struct.Struct("<HfffBbHBB")

# Motion (id=0) per-car block (2022-2025, 60 bytes):
# worldPos 3f | worldVel 3f | forwardVec 3h | rightVec 3h | gLat,gLong,gVert,yaw,pitch,roll 6f
_MOTION = struct.Struct("<6f3h3h6f")

# 2026 Motion: G-forces are int16 (divide by 1000), yaw/pitch/roll as 3f.
_MOTION_2026 = struct.Struct("<6f3h3h3h3f")

_VNORM = 32767.0  # direction vectors are normalised int16
_GFORCE_2026 = 1000.0


@dataclass(slots=True)
class CarTelemetry:
    speed_kmh: int          # u16, game unit: km/h
    throttle: float         # 0.0-1.0
    steer: float            # -1.0..1.0
    brake: float            # 0.0-1.0
    clutch: int             # 0-100
    gear: int               # -1=R, 0=N, 1-8
    rpm: int
    drs: int                # 0/1
    rev_percent: int
    # Wheel order everywhere: RL, RR, FL, FR. None in format 2026.
    brakes_temp: tuple[int, ...] | None = None
    tyre_surface_temp: tuple[int, ...] | None = None
    tyre_inner_temp: tuple[int, ...] | None = None
    tyre_pressure: tuple[float, ...] | None = None
    surface_type: tuple[int, ...] | None = None   # 0=tarmac 1=kerb 4=gravel 7=grass ...


@dataclass(slots=True)
class CarMotion:
    x: float                # world coordinate (metres)
    y: float                # height
    z: float                # world coordinate
    vx: float               # velocity vector (m/s)
    vy: float
    vz: float
    g_lat: float
    g_long: float
    g_vert: float
    yaw: float
    pitch: float
    roll: float
    # car direction unit vectors, used for spin/drift detection:
    fx: float = 0.0         # nose direction — x component
    fy: float = 0.0
    fz: float = 0.0         # nose direction — z component
    rx: float = 0.0         # right direction — x component
    ry: float = 0.0
    rz: float = 0.0         # right direction — z component

    def advanced(self, dt: float) -> CarMotion:
        """Advance the motion sample to the telemetry time by linear extrapolation.

        Keeps the position from going stale when packets arrive at different rates.
        """
        if abs(dt) < 1e-4:
            return self
        return replace(self, x=self.x + self.vx * dt, z=self.z + self.vz * dt)


@dataclass(slots=True)
class LapData:
    lap_distance: float          # distance into the lap (m); may be negative before start/finish
    current_lap_num: int
    current_lap_time_ms: int = 0
    last_lap_time_ms: int = 0
    car_position: int = 0
    pit_status: int = 0          # 0=none, 1=pitting, 2=in pit area
    driver_status: int = 4       # 4=on track
    current_lap_invalid: bool = False
    sector: int = 0              # 0=S1, 1=S2, 2=S3
    total_warnings: int = 0
    corner_cutting_warnings: int = 0


@dataclass(slots=True)
class SessionInfo:
    track_length: int
    track_id: int = -1
    session_type: int = 0
    total_laps: int = 0
    weather: int = 0


@dataclass(slots=True)
class MotionEx:
    """Player car only. Wheel order: RL, RR, FL, FR."""

    wheel_speed: tuple[float, ...]
    wheel_slip_ratio: tuple[float, ...]
    wheel_slip_angle: tuple[float, ...]


@dataclass(slots=True)
class CarDamage:
    """Damage percentages (0-100). Wheel order: RL, RR, FL, FR."""

    tyres_wear: tuple[float, ...]
    tyres_damage: tuple[int, ...]
    brakes_damage: tuple[int, ...]
    tyre_blisters: tuple[int, ...]
    front_left_wing: int
    front_right_wing: int
    rear_wing: int
    floor: int
    diffuser: int
    sidepod: int
    drs_fault: int
    ers_fault: int
    gearbox: int
    engine: int


# After the first 20 bytes of CarTelemetry: revLightsBitValue u16 (skipped), then
# brakesTemp 4xu16 | surfaceTemp 4xu8 | innerTemp 4xu8 | engineTemp u16 | pressure 4xf |
# surfaceType 4xu8  -> 38 bytes starting at byte 22 (60 in total)
_TEL_EXTRA = struct.Struct("<4H4B4BH4f4B")
_TEL_EXTRA_OFFSET = 22
_TEL_EXTRA_FORMATS = (2022, 2023, 2024, 2025)

# MotionEx (id=13) payload: suspPos/Vel/Acc (3x4f) | wheelSpeed 4f | slipRatio 4f | slipAngle 4f ...
_MOTION_EX = struct.Struct("<12f4f4f4f")
_MOTION_EX_FORMATS = (2023, 2024, 2025)

# CarDamage (id=10) bytes per car: 2022/2023 -> 42, 2024/2025 -> 46 (tyreBlisters added).
DAMAGE_STRIDES = {2022: 42, 2023: 42, 2024: 46, 2025: 46}


def _car_count(fmt: int) -> int:
    try:
        return CAR_COUNTS[fmt]
    except KeyError as e:
        raise ValueError(f"unsupported packet format: {fmt}") from e


def _player_offset(fmt: int, player_index: int, stride: int, payload_len: int,
                   minimum: int, kind: str) -> int:
    count = _car_count(fmt)
    if not 0 <= player_index < count:
        raise ValueError(f"{kind}: invalid car index {player_index} (0-{count - 1})")
    off = stride * player_index
    if payload_len < off + minimum:
        raise ValueError(f"{kind} payload too short: {payload_len} < {off + minimum}")
    return off


def parse_car_telemetry(fmt: int, player_index: int, payload: bytes | memoryview) -> CarTelemetry:
    """Decode the player car's telemetry slice from the packet."""
    stride = TELEMETRY_STRIDES.get(fmt)
    if stride is None:
        raise ValueError(f"no telemetry stride defined for format {fmt}")
    off = _player_offset(fmt, player_index, stride, len(payload), _TEL_HEAD.size, "telemetry")
    f = _TEL_HEAD.unpack_from(payload, off)
    tel = CarTelemetry(
        speed_kmh=f[0],
        throttle=f[1],
        steer=f[2],
        brake=f[3],
        clutch=f[4],
        gear=f[5],
        rpm=f[6],
        drs=f[7],
        rev_percent=f[8],
    )
    if fmt in _TEL_EXTRA_FORMATS and len(payload) >= off + _TEL_EXTRA_OFFSET + _TEL_EXTRA.size:
        e = _TEL_EXTRA.unpack_from(payload, off + _TEL_EXTRA_OFFSET)
        tel.brakes_temp = e[0:4]
        tel.tyre_surface_temp = e[4:8]
        tel.tyre_inner_temp = e[8:12]
        tel.tyre_pressure = e[13:17]
        tel.surface_type = e[17:21]
    return tel


def parse_motion(fmt: int, player_index: int, payload: bytes | memoryview) -> CarMotion:
    """Decode the player car's motion slice from the packet."""
    stride = MOTION_STRIDES.get(fmt)
    if stride is None:
        raise ValueError(f"no motion stride defined for format {fmt}")

    if fmt == 2026:
        off = _player_offset(fmt, player_index, stride, len(payload), _MOTION_2026.size, "motion")
        f = _MOTION_2026.unpack_from(payload, off)
        return CarMotion(
            x=f[0], y=f[1], z=f[2],
            vx=f[3], vy=f[4], vz=f[5],
            g_lat=f[12] / _GFORCE_2026,
            g_long=f[13] / _GFORCE_2026,
            g_vert=f[14] / _GFORCE_2026,
            yaw=f[15], pitch=f[16], roll=f[17],
            fx=f[6] / _VNORM, fy=f[7] / _VNORM, fz=f[8] / _VNORM,
            rx=f[9] / _VNORM, ry=f[10] / _VNORM, rz=f[11] / _VNORM,
        )

    off = _player_offset(fmt, player_index, stride, len(payload), _MOTION.size, "motion")
    f = _MOTION.unpack_from(payload, off)
    return CarMotion(
        x=f[0], y=f[1], z=f[2],
        vx=f[3], vy=f[4], vz=f[5],
        g_lat=f[12], g_long=f[13], g_vert=f[14],
        yaw=f[15], pitch=f[16], roll=f[17],
        fx=f[6] / _VNORM, fy=f[7] / _VNORM, fz=f[8] / _VNORM,
        rx=f[9] / _VNORM, ry=f[10] / _VNORM, rz=f[11] / _VNORM,
    )


def parse_lap_data(fmt: int, player_index: int, payload: bytes | memoryview) -> LapData:
    """Decode the player's lap info (lap distance, lap number, time)."""
    stride = LAP_STRIDES.get(fmt)
    if stride is None:
        raise ValueError(f"no lap data stride defined for format {fmt}")

    if fmt == 2022:
        # 2022 LapData: lapDistance offset 12, currentLapNum offset 25
        off = _player_offset(fmt, player_index, stride, len(payload), 43, "lap data")
        lap_distance = struct.unpack_from("<f", payload, off + 12)[0]
        current_lap_num = payload[off + 25]
        car_position = payload[off + 24]
        pit_status = payload[off + 26]
        current_lap_invalid = bool(payload[off + 29])
        driver_status = payload[off + 35]
    else:
        # 2023+: lapDistance offset 20, currentLapNum offset 33
        off = _player_offset(fmt, player_index, stride, len(payload), 57, "lap data")
        lap_distance = struct.unpack_from("<f", payload, off + 20)[0]
        current_lap_num = payload[off + 33]
        car_position = payload[off + 32]
        pit_status = payload[off + 34]
        current_lap_invalid = bool(payload[off + 37])
        driver_status = payload[off + 44]

    sector = total_warnings = cc_warnings = 0
    if fmt >= 2024:
        sector = payload[off + 36]
        total_warnings = payload[off + 39]
        cc_warnings = payload[off + 40]

    last_lap_time_ms = struct.unpack_from("<I", payload, off)[0]
    current_lap_time_ms = struct.unpack_from("<I", payload, off + 4)[0]

    return LapData(
        lap_distance=lap_distance,
        current_lap_num=current_lap_num,
        current_lap_time_ms=current_lap_time_ms,
        last_lap_time_ms=last_lap_time_ms,
        car_position=car_position,
        pit_status=pit_status,
        driver_status=driver_status,
        current_lap_invalid=current_lap_invalid,
        sector=sector,
        total_warnings=total_warnings,
        corner_cutting_warnings=cc_warnings,
    )


def parse_motion_ex(fmt: int, payload: bytes | memoryview) -> MotionEx:
    """Extra player-only motion data: wheel speed / slip ratio."""
    if fmt not in _MOTION_EX_FORMATS:
        raise ValueError(f"motion_ex is not supported for format {fmt}")
    if len(payload) < _MOTION_EX.size:
        raise ValueError(f"motion_ex payload too short: {len(payload)} < {_MOTION_EX.size}")
    f = _MOTION_EX.unpack_from(payload, 0)
    return MotionEx(wheel_speed=f[12:16], wheel_slip_ratio=f[16:20], wheel_slip_angle=f[20:24])


def parse_car_damage(fmt: int, player_index: int, payload: bytes | memoryview) -> CarDamage:
    """The player's tyre wear and component damage."""
    stride = DAMAGE_STRIDES.get(fmt)
    if stride is None:
        raise ValueError(f"no damage stride defined for format {fmt}")
    off = _player_offset(fmt, player_index, stride, len(payload), stride, "damage")
    wear = struct.unpack_from("<4f", payload, off)
    tyres_damage = tuple(payload[off + 16 : off + 20])
    brakes_damage = tuple(payload[off + 20 : off + 24])
    if stride == 46:
        blisters = tuple(payload[off + 24 : off + 28])
        base = off + 28
    else:
        blisters = (0, 0, 0, 0)
        base = off + 24
    b = payload
    return CarDamage(
        tyres_wear=wear,
        tyres_damage=tyres_damage,
        brakes_damage=brakes_damage,
        tyre_blisters=blisters,
        front_left_wing=b[base],
        front_right_wing=b[base + 1],
        rear_wing=b[base + 2],
        floor=b[base + 3],
        diffuser=b[base + 4],
        sidepod=b[base + 5],
        drs_fault=b[base + 6],
        ers_fault=b[base + 7],
        gearbox=b[base + 8],
        engine=b[base + 9],
    )


def parse_session(fmt: int, payload: bytes | memoryview) -> SessionInfo:
    """Decode the leading fields of the Session packet (track length / id)."""
    _car_count(fmt)  # validate the format
    if len(payload) < 8:
        raise ValueError(f"session payload too short: {len(payload)} < 8")
    weather, _track_temp, _air_temp, total_laps, track_length, session_type, track_id = (
        struct.unpack_from("<BbbBHBB", payload, 0)
    )
    return SessionInfo(
        track_length=track_length,
        track_id=track_id,
        session_type=session_type,
        total_laps=total_laps,
        weather=weather,
    )


def parse_event_code(fmt: int, payload: bytes | memoryview) -> str:
    """Return the 4-letter event code from an Event packet (SSTA, SEND, FLBK...)."""
    _car_count(fmt)
    if len(payload) < 4:
        raise ValueError(f"event payload too short: {len(payload)} < 4")
    return bytes(payload[:4]).decode("ascii", errors="replace")
