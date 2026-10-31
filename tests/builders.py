# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) ApexMind contributors
"""Synthetic F1 25 UDP packets (format 2025)."""

from __future__ import annotations

import struct

N = 22


def header(pid, uid=5, t=1.0, player=0, fmt=2025):
    return struct.pack("<HBBBBBQfIIBB", fmt, 25, 1, 0, 1, pid, uid, t, 1, 1, player, 255)


def telemetry(speed=200, thr=0.0, brk=0.0, steer=0.0, inner=(90, 90, 90, 90), surf_t=(100,) * 4,
              press=(23.0,) * 4, btemp=(500,) * 4, surface=(0, 0, 0, 0), **hdr):
    car = struct.pack("<HfffBbHBB", speed, thr, steer, brk, 0, 6, 11000, 0, 80)
    car += struct.pack("<H", 0)
    car += struct.pack("<4H4B4BH4f4B", *btemp, *surf_t, *inner, 90, *press, *surface)
    assert len(car) == 60
    return header(6, **hdr) + car + bytes(60 * (N - 1))


def motion(speed_kmh=200.0, **hdr):
    v = speed_kmh / 3.6
    car = struct.pack("<6f3h3h6f", 0, 0, 0, v, 0, 0, 32767, 0, 0, 0, 0, 32767, 0, 0, 1, 0, 0, 0)
    assert len(car) == 60
    return header(0, **hdr) + car + bytes(60 * (N - 1))


def lapdata(num, dist, cur_ms, last_ms=0, sector=0, invalid=0, cc=0, warnings=0, **hdr):
    car = bytearray(57)
    struct.pack_into("<II", car, 0, last_ms, cur_ms)
    struct.pack_into("<f", car, 20, dist)
    car[32] = 1
    car[33] = num
    car[36] = sector
    car[37] = invalid
    car[39] = warnings
    car[40] = cc
    car[44] = 4
    return header(2, **hdr) + bytes(car) + bytes(57 * (N - 1))


def motion_ex(slip=(0, 0, 0, 0), wheel_speed=(50, 50, 50, 50), **hdr):
    body = struct.pack("<12f4f4f4f", *([0.0] * 12), *wheel_speed, *slip, *([0.0] * 4))
    return header(13, **hdr) + body + bytes(100)


def damage(wear=(10, 10, 10, 10), blisters=(0, 0, 0, 0), rear_wing=0, floor=0, drs=0, **hdr):
    car = bytearray(46)
    struct.pack_into("<4f", car, 0, *wear)
    car[24:28] = bytes(blisters)
    car[28 + 2] = rear_wing
    car[28 + 3] = floor
    car[28 + 6] = drs
    return header(10, **hdr) + bytes(car) + bytes(46 * (N - 1))


def session(track_length=4323, track_id=17, session_type=18, **hdr):
    body = struct.pack("<BbbBHBB", 0, 30, 25, 5, track_length, session_type, track_id)
    return header(1, **hdr) + body + bytes(40)
