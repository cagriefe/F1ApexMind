# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) ApexMind contributors
import unittest

from apexmind.engineer.debrief import LapDebrief, format_lap_time
from apexmind.telemetry.packets import LapData


def lap(num, last_ms=0, invalid=False):
    return LapData(
        lap_distance=0.0,
        current_lap_num=num,
        last_lap_time_ms=last_ms,
        current_lap_invalid=invalid,
    )


class DebriefTests(unittest.TestCase):
    def setUp(self):
        self.msgs = []
        self.d = LapDebrief(lambda k, t: self.msgs.append((k, t)))

    def test_format(self):
        self.assertEqual(format_lap_time(92456), "1:32.456")
        self.assertEqual(format_lap_time(59999), "0:59.999")

    def test_benchmark_then_slower_then_best(self):
        self.d.update(lap(1))
        self.d.update(lap(2, 92000))
        self.assertIn("Benchmark set", self.msgs[-1][1])
        self.d.update(lap(3, 92500))
        self.assertIn("Plus 0.500 to best", self.msgs[-1][1])
        self.d.update(lap(4, 91000))
        self.assertIn("New best lap, 1:31.000. Minus 1.000", self.msgs[-1][1])

    def test_invalid_lap_is_deleted_and_not_best(self):
        self.d.update(lap(1))
        self.d.update(lap(1, invalid=True))
        self.d.update(lap(2, 80000))
        self.assertIn("deleted", self.msgs[-1][1])
        self.assertIsNone(self.d.best_ms)

    def test_main_issue_and_clean_lap(self):
        self.d.update(lap(1))
        self.d.update(lap(2, 92000))  # lap 1 finished, first lap never says "clean"
        self.assertNotIn("Clean", self.msgs[-1][1])
        for k in ("brake", "brake", "apex", "recover"):
            self.d.note(k)
        self.d.update(lap(3, 92100))
        self.assertIn("Most calls: braking, 2", self.msgs[-1][1])
        self.d.update(lap(4, 92200))
        self.assertIn("Clean lap, no calls", self.msgs[-1][1])

    def test_short_or_backwards_lap_is_ignored(self):
        self.d.update(lap(3))
        self.d.update(lap(4, 0))
        self.assertEqual(self.msgs, [])
        self.d.update(lap(2, 90000))  # flashback: lap number went down
        self.assertEqual(self.msgs, [])

    def test_new_session_clears_best(self):
        self.d.update(lap(1))
        self.d.update(lap(2, 90000))
        self.d.new_session()
        self.assertIsNone(self.d.best_ms)


if __name__ == "__main__":
    unittest.main()
