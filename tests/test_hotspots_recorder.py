# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) ApexMind contributors
import csv
import json
import tempfile
import unittest
from pathlib import Path

from apexmind.engineer import TrackLimits
from apexmind.recorder import Recorder
from apexmind.replay import _precise_time


class HotspotPersistence(unittest.TestCase):
    def test_save_load_roundtrip_per_track(self):
        path = Path(tempfile.mkdtemp()) / "h.json"
        a = TrackLimits(lambda k, t: None)
        a.track_length = 7007.0
        for h in (1180.0, 3950.0):
            a._add_hotspot(h)
        a.save(path, 10)
        a2 = TrackLimits(lambda k, t: None)
        a2.track_length = 7007.0
        a2._add_hotspot(500.0)
        a2.save(path, 5)
        b = TrackLimits(lambda k, t: None)
        b.track_length = 7007.0
        self.assertEqual(b.load(path, 10), 2)
        self.assertEqual(sorted(b.hotspots), [1180.0, 3950.0])
        c = TrackLimits(lambda k, t: None)
        self.assertEqual(c.load(path, 99), 0)
        self.assertEqual(c.load(Path(tempfile.mkdtemp()) / "missing.json", 10), 0)
        self.assertEqual(set(json.loads(path.read_text())), {"5", "10"})


class RecorderTime(unittest.TestCase):
    def test_session_time_keeps_millisecond_precision_after_1000_s(self):
        d = tempfile.mkdtemp()
        rec = Recorder(d, hz=1000)
        rec.row(1722.345, {"lap": 3, "dist": 4937.31})
        rec.row(1722.362, {"lap": 3, "dist": 4938.31})
        rec.close()
        rows = list(csv.DictReader(open(rec.csv_path, encoding="utf-8")))
        self.assertEqual([r["t"] for r in rows], ["1722.345", "1722.362"])

    def test_old_truncated_time_is_rebuilt_from_lap_ms(self):
        rows = [{"lap": "2", "lap_ms": str(ms), "t": f"{1722.0 + ms / 1000.0:.1f}"}
                for ms in (1000, 1017, 1033, 1050, 1067, 1083)]
        _precise_time(rows)
        ts = [float(r["t"]) for r in rows]
        self.assertAlmostEqual(ts[1] - ts[0], 0.017, places=3)
        self.assertAlmostEqual(ts[-1] - ts[0], 0.083, places=3)


if __name__ == "__main__":
    unittest.main()
