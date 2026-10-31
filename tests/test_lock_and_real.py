# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) ApexMind contributors
import csv
import statistics
import unittest
from pathlib import Path

from apexmind.engineer import Analyzer
from apexmind.replay import replay_csv
from sim import drive

SAMPLE = Path(__file__).resolve().parent.parent / "src" / "apexmind" / "webui" / "sample.csv"
REAL = [SAMPLE] if SAMPLE.exists() else []


class LockCapsBrakeCall(unittest.TestCase):
    def test_zone_that_locks_every_lap_gets_a_lower_brake_target(self):
        an = Analyzer(lambda k, t: None, learn_n=1, cooldown_s=0, calls=True)

        def lock_hook(lap, d, t, v):
            if 1004 < d < 1010 and an.open is not None:
                an.note_lockup()

        ev = drive(an, laps=4, hz=20, depth=1.0, on_frame=lock_hook)
        brake = [e for e in ev if e[0] == "call_brake" and e[1] == 3]
        self.assertEqual(brake[0][3], "Brake 80")     # pedal is 100% but it locks up, so it was capped to 85


@unittest.skipUnless(REAL, "no real recording available")
class RealSessionRegression(unittest.TestCase):
    """Call timing on the bundled real F1 25 sample (Spa, three laps, irregular frames)."""

    @classmethod
    def setUpClass(cls):
        cls.path = REAL[0]
        cls.events, cls.an = replay_csv(cls.path)
        cls.rows = list(csv.DictReader(open(cls.path, encoding="utf-8")))

    def test_scripts_are_staged_and_sane(self):
        scripts = [z.script for z in self.an.zones if z.laps and len(z.laps) >= 2 and z.script]
        self.assertGreaterEqual(len(scripts), 4)
        for script in scripts:
            texts = [e[2] for e in script]
            for t in texts:
                self.assertRegex(t, r"^(Brake \d+|\d+|Gas \d+|Full)$")
            ds = [e[0] for e in script]
            self.assertEqual(ds, sorted(ds))
            self.assertTrue(texts[0].startswith("Brake"))
            self.assertEqual(sum(t.startswith("Brake") for t in texts), 1)

    def test_brake_call_to_actual_onset_is_tight(self):
        def f(r, k):
            try:
                return float(r[k])
            except ValueError:
                return 0.0

        gaps = []
        for e in self.events:
            if e.kind != "call_brake":
                continue
            for i in range(1, len(self.rows)):
                a, b = self.rows[i - 1], self.rows[i]
                if f(b, "t") > e.t and f(b, "brake") >= 0.12 > f(a, "brake") and f(b, "t") - e.t < 4:
                    fr = (0.12 - f(a, "brake")) / (f(b, "brake") - f(a, "brake"))
                    gaps.append(f(a, "t") + fr * (f(b, "t") - f(a, "t")) - e.t)
                    break
        self.assertGreaterEqual(len(gaps), 5)
        self.assertGreater(statistics.mean(gaps), 0.4)
        self.assertLess(statistics.mean(gaps), 1.0)
        self.assertLess(statistics.pstdev(gaps), 0.2)      # the call lands consistently before the onset

    def test_every_learned_corner_is_called_completely_in_the_last_lap(self):
        last = max(e.lap for e in self.events)
        said = [e for e in self.events if e.kind.startswith("call_") and e.lap == last
                and e.kind not in ("call_limits", "call_traction")]
        corners = [z for z in self.an.zones if z.laps and len(z.laps) >= 2 and z.script]
        self.assertEqual(sum(1 for e in said if e.kind == "call_brake"), len(corners))
        self.assertGreaterEqual(sum(1 for e in said if e.kind in ("call_full", "call_gas")), len(corners))

    def test_front_lockups_are_detected_with_calibrated_threshold(self):
        locks = [e for e in self.events if e.kind == "lockup"]
        self.assertGreaterEqual(len(locks), 5)
        self.assertTrue(all("locked" in e.text for e in locks))

    def test_track_limit_warning_comes_before_the_games_flag(self):
        inv = [e for e in self.events if e.kind == "lap_invalid"]
        self.assertGreaterEqual(len(inv), 1)
        wide = [e for e in self.events if e.kind == "wide"]     # early warnings, if the car was off-tarmac
        for w in wide:
            nxt = min((i.t for i in inv if i.t >= w.t), default=None)
            if nxt is not None:
                self.assertLess(nxt - w.t, 1.5)


if __name__ == "__main__":
    unittest.main()
