# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) ApexMind contributors
"""Session recording for calibration and debugging.

Writes two files (``DIR/session-YYYYmmdd-HHMMSS.*``):
  .csv      signals at up to ~20 Hz: lap distance, speed, pedals, wheel slip, tyres/surface, damage...
  .samples  raw UDP datagrams (the first N of every packet type + all events), used to verify
            the packet layout against real game data. Small (~1-2 MB).

``read_samples`` reads a .samples file back.
"""

from __future__ import annotations

import csv
import struct
import time
from collections.abc import Iterator
from pathlib import Path

COLUMNS = [
    "t", "lap", "dist", "lap_ms", "sector", "speed", "throttle", "brake", "steer", "gear", "rpm",
    "g_lat", "g_long", "vx", "vz",
    "slip_rl", "slip_rr", "slip_fl", "slip_fr",
    "wspd_rl", "wspd_rr", "wspd_fl", "wspd_fr",
    "tin_rl", "tin_rr", "tin_fl", "tin_fr",
    "tsurf_rl", "tsurf_rr", "tsurf_fl", "tsurf_fr",
    "press_rl", "press_rr", "press_fl", "press_fr",
    "btemp_rl", "btemp_rr", "btemp_fl", "btemp_fr",
    "surf_rl", "surf_rr", "surf_fl", "surf_fr",
    "wear_rl", "wear_rr", "wear_fl", "wear_fr",
    "blist_rl", "blist_rr", "blist_fl", "blist_fr",
    "dmg_flwing", "dmg_frwing", "dmg_rwing", "dmg_floor", "dmg_diffuser", "dmg_sidepod",
    "dmg_gearbox", "dmg_engine",
    "invalid", "cc_warn", "warnings", "pit", "events",
]
_REC = struct.Struct("<dHI")
_MAGIC = b"APXS1\n"


class Recorder:
    def __init__(self, directory: str | Path, *, hz: float = 20.0, raw_per_id: int = 20):
        self.dir = Path(directory).expanduser()
        self.dir.mkdir(parents=True, exist_ok=True)
        stamp = time.strftime("%Y%m%d-%H%M%S")
        self.csv_path = self.dir / f"session-{stamp}.csv"
        self.samples_path = self.dir / f"session-{stamp}.samples"
        self._csv_f = open(self.csv_path, "w", newline="", encoding="utf-8")  # noqa: SIM115
        self._csv = csv.writer(self._csv_f)
        self._csv.writerow(COLUMNS)
        self._raw_f = open(self.samples_path, "wb")  # noqa: SIM115
        self._raw_f.write(_MAGIC)
        self.min_dt = 1.0 / hz
        self.raw_per_id = raw_per_id
        self._seen: dict[int, int] = {}
        self._last_t = -1e9
        self._rows = 0
        self.pending_events: list[str] = []

    def packet(self, buf: bytes, packet_id: int, event_code: str | None = None) -> None:
        if event_code == "BUTN":
            return
        if packet_id == 3:
            keep = True
        else:
            n = self._seen.get(packet_id, 0)
            # first N packets, then every 200th (to capture real moving values)
            keep = n < self.raw_per_id or (n % 200 == 0 and n < 200 * 150)
            self._seen[packet_id] = n + 1
        if keep:
            self._raw_f.write(_REC.pack(time.time(), packet_id, len(buf)) + bytes(buf))

    def event(self, code: str) -> None:
        if code != "BUTN":
            self.pending_events.append(code)

    def row(self, t: float, values: dict) -> None:
        if t - self._last_t < self.min_dt:
            return
        self._last_t = t
        values = dict(values, t=f"{t:.3f}", events="|".join(self.pending_events))
        self.pending_events.clear()
        self._csv.writerow([_fmt(values.get(c)) for c in COLUMNS])
        self._rows += 1
        if self._rows % 100 == 0:
            self._csv_f.flush()
            self._raw_f.flush()

    def close(self) -> None:
        for f in (self._csv_f, self._raw_f):
            try:
                f.flush()
                f.close()
            except OSError:
                pass


def _fmt(v) -> str:
    if v is None:
        return ""
    if isinstance(v, float):
        return f"{v:.6g}" if abs(v) < 1000 else f"{v:.2f}"
    return str(v)


def read_samples(path: str | Path) -> Iterator[tuple[float, int, bytes]]:
    with open(path, "rb") as f:
        if f.read(len(_MAGIC)) != _MAGIC:
            raise ValueError("not a .samples file")
        while True:
            head = f.read(_REC.size)
            if len(head) < _REC.size:
                return
            wall, pid, n = _REC.unpack(head)
            yield wall, pid, f.read(n)
