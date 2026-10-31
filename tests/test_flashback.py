# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) ApexMind contributors
"""Flashbacks, pauses and duplicate calls must not cost the driver their calls."""

import unittest

from apexmind.engineer import Analyzer

from sim import drive


def make(**kw):
    kw.setdefault("learn_n", 1)
    kw.setdefault("cooldown_s", 0.0)
    return Analyzer(speak_cb=lambda k, t: None, **kw)


class FlashbackKeepsCorners(unittest.TestCase):
    def test_rewind_keeps_the_learned_corners(self):
        an = make(calls=True)
        drive(an, laps=2, hz=20)
        corners = len(an.zones)
        self.assertGreater(corners, 0)
        an.rewind()
        self.assertEqual(len(an.zones), corners)
        self.assertTrue(all(z.laps for z in an.zones))

    def test_reset_still_wipes_everything(self):
        an = make(calls=True)
        drive(an, laps=2, hz=20)
        an.reset()
        self.assertEqual(an.zones, [])

    def test_calls_still_come_after_a_flashback_every_lap(self):
        # A driver who flashes back after every mistake: with a wipe, no lap ever kept its corners.
        an = make(calls=True)
        drive(an, laps=2, hz=20)
        for _ in range(3):
            an.rewind()
            ev = drive(an, laps=1, hz=20)
            self.assertTrue([e for e in ev if e[0].startswith("call_")], "no calls after a flashback")


class JumpsAreNotMissedCalls(unittest.TestCase):
    def test_a_position_jump_does_not_report_skipped_calls(self):
        an = make(calls=True)
        skipped = []
        an.on_skip = lambda text, reason: skipped.append(text)
        drive(an, laps=3, hz=20)
        skipped.clear()
        an._jump_frames = 3                  # what the analyzer sets when the car teleports/pauses
        an._report_skip("Gas 30", "its moment passed")
        self.assertEqual(skipped, [])
        an._jump_frames = 0
        an._report_skip("Gas 30", "its moment passed")
        self.assertEqual(skipped, ["Gas 30"])


if __name__ == "__main__":
    unittest.main()
