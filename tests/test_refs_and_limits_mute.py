# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) ApexMind contributors
import json
import tempfile
import unittest
from pathlib import Path

from apexmind.engineer import Analyzer, TrackLimits
from apexmind.engineer.analyzer import merge_refs
from apexmind.telemetry.packets import CarTelemetry, LapData
from sim import drive


def tel(speed=280):
    return CarTelemetry(speed_kmh=speed, throttle=1.0, steer=0.0, brake=0.0, clutch=0, gear=7,
                        rpm=11000, drs=0, rev_percent=80, surface_type=(0, 0, 0, 0))


def lap(num, dist=100.0, invalid=False):
    return LapData(lap_distance=dist, current_lap_num=num, current_lap_invalid=invalid)


class LimitsStopNagging(unittest.TestCase):
    def run_lap(self, tl, num, t0, invalid=False):
        """A lap passing from 1000 m to 1300 m; each step is 10 m."""
        tl.on_lap(lap(num))
        warned = 0
        for k in range(31):
            d = 1000.0 + k * 10.0
            before = len(self.out)
            tl.on_telemetry(tel(), t0 + k * 0.036, d)
            warned += len(self.out) - before
        return warned

    def test_silent_after_two_clean_passes_and_single_old_hits_never_nag(self):
        self.out = []
        tl = TrackLimits(lambda k, t: self.out.append((k, t)))
        tl.track_length = 7007.0
        tl._add_hotspot(1150.0)               # violated in this session
        counts = [self.run_lap(tl, n, n * 100.0) for n in range(2, 7)]
        self.assertEqual(counts, [1, 1, 0, 0, 0])        # goes quiet after 2 clean passes

        # saved, one-off old spot: never warns
        self.out = []
        old = TrackLimits(lambda k, t: self.out.append((k, t)))
        old.track_length = 7007.0
        path = Path(tempfile.mkdtemp()) / "h.json"
        path.write_text(json.dumps({"10": [[1150.0, 1]]}))
        old.load(path, 10)
        self.assertEqual([self.run_lap(old, n, n * 100.0) for n in range(2, 5)], [0, 0, 0])

        # saved repeat (hits>=2) spot warns, goes quiet after clean passes
        self.out = []
        rep = TrackLimits(lambda k, t: self.out.append((k, t)))
        rep.track_length = 7007.0
        path.write_text(json.dumps({"10": [[1150.0, 3]]}))
        rep.load(path, 10)
        self.assertEqual([self.run_lap(rep, n, n * 100.0) for n in range(2, 6)], [1, 1, 0, 0])

    def test_new_violation_rearms_the_warning(self):
        self.out = []
        tl = TrackLimits(lambda k, t: self.out.append((k, t)))
        tl.track_length = 7007.0
        tl._add_hotspot(1150.0)
        for n in (2, 3, 4):
            self.run_lap(tl, n, n * 100.0)
        self.assertEqual(self.run_lap(tl, 5, 500.0), 0)
        tl._add_hotspot(1155.0)              # violated again at the same spot
        self.assertEqual(self.run_lap(tl, 6, 600.0), 1)


class PersistentReferences(unittest.TestCase):
    def test_calls_work_from_lap_one_with_saved_references(self):
        first = Analyzer(lambda k, t: None, learn_n=1, cooldown_s=0, calls=True)
        drive(first, laps=5, hz=60, lap_times={1: 92000, 2: 91500, 3: 91000, 4: 91200})
        data = json.loads(json.dumps(first.export_refs()))        # must survive JSON
        self.assertGreaterEqual(len(data["zones"]), 1)

        fresh = Analyzer(lambda k, t: None, learn_n=1, cooldown_s=0, calls=True)
        fresh.set_track_length(4323.0)
        self.assertGreaterEqual(fresh.import_refs(data), 1)
        ev = drive(fresh, laps=2, hz=60)
        lap1 = [e[3] for e in ev if e[0].startswith("call_") and e[1] == 1]
        self.assertEqual(lap1[0], "Brake 90")
        self.assertEqual(lap1[-1], "Full")

    def test_wrong_track_or_nonempty_analyzer_ignores_refs(self):
        a = Analyzer(lambda k, t: None, learn_n=1, cooldown_s=0, calls=True)
        drive(a, laps=4, hz=60, lap_times={1: 92000, 2: 91500, 3: 91000})
        data = a.export_refs()
        other = Analyzer(lambda k, t: None)
        other.set_track_length(7007.0)
        self.assertEqual(other.import_refs(data), 0)          # different track length
        self.assertEqual(a.import_refs(data), 0)              # already learned

    def test_merge_keeps_three_fastest_laps_per_corner(self):
        def export(ms_list):
            laps = [{"ms": ms, "sample": {"brake_lap": 1000.0 + i, "depth": 0.9}}
                    for i, ms in enumerate(ms_list)]
            return {"track_length": 4323.0, "zones": [{"laps": laps}]}

        merged = merge_refs([export([95000, 93000]), export([94000, 92000])])
        self.assertEqual(len(merged["zones"]), 1)
        self.assertEqual([lap["ms"] for lap in merged["zones"][0]["laps"]], [92000, 93000, 94000])


if __name__ == "__main__":
    unittest.main()
