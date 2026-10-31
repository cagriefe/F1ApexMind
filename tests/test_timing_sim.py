# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) ApexMind contributors
import unittest

from apexmind.engineer import Analyzer

from sim import drive


def make(**kw):
    kw.setdefault("learn_n", 1)
    kw.setdefault("cooldown_s", 0.0)
    return Analyzer(speak_cb=lambda k, t: None, **kw)


class CueTimingSimulation(unittest.TestCase):
    def test_default_lead_zero_actually_fires_at_the_brake_point(self):
        # Old bug: with --cue-lead 0 no beep played at all on real (fractional) frames.
        for hz in (30, 60):
            for v_in in (150.0, 220.0, 300.0):
                with self.subTest(hz=hz, v=v_in):
                    ev = drive(make(), laps=4, hz=hz, brake_at=1000.0, v_in=v_in)
                    cues = [e for e in ev if e[0] == "cue_brake"]
                    self.assertEqual([c[1] for c in cues], [2, 3, 4])  # once per lap
                    for _k, _lap, dist, _t in cues:
                        # Learned point is <=1 frame late + the beep has half-frame accuracy
                        tol = 1.5 * v_in / 3.6 / hz + 1.0
                        self.assertLessEqual(abs(dist - 1000.0), tol)

    def test_latency_compensation_scales_with_speed(self):
        for v_in in (150.0, 300.0):
            with self.subTest(v=v_in):
                ev = drive(make(cue_latency_s=0.15), laps=3, hz=60, v_in=v_in)
                cues = [e for e in ev if e[0] == "cue_brake"]
                self.assertTrue(cues)
                expected = v_in / 3.6 * 0.15
                for _k, _lap, dist, _t in cues:
                    self.assertAlmostEqual(1000.0 - dist, expected, delta=v_in / 3.6 / 60 + 1.0)

    def test_throttle_cue_lands_on_learned_throttle_point_once_per_lap(self):
        ev = drive(make(), laps=4, hz=60)
        cues = [e for e in ev if e[0] == "cue_throttle"]
        self.assertEqual([c[1] for c in cues], [2, 3, 4])
        spread = max(c[2] for c in cues) - min(c[2] for c in cues)
        self.assertLess(spread, 3.0)  # same point every lap


class SpokenCallsSimulation(unittest.TestCase):
    def lap_calls(self, ev, lap):
        return [(e[0], e[3]) for e in ev if e[0].startswith("call_") and e[1] == lap]

    def test_staged_script_brake_ease_gas_full(self):
        ev = drive(make(calls=True), laps=4, hz=20, depth=0.90, hold=0.60)
        # Verification: the first 2 laps are learning; calls start from lap 3
        self.assertEqual(self.lap_calls(ev, 2), [])
        for lap in (3, 4):
            calls = self.lap_calls(ev, lap)
            kinds = [k for k, _ in calls]
            self.assertEqual(calls[0], ("call_brake", "Brake 90"))
            self.assertEqual(calls[-1], ("call_full", "Full"))
            self.assertIn("call_ease", kinds)
            # order: brake -> release step(s) -> throttle step(s) -> full throttle
            order = {"call_brake": 0, "call_ease": 1, "call_gas": 2, "call_full": 3}
            self.assertEqual([order[k] for k in kinds], sorted(order[k] for k in kinds))
            ease = [int(t) for k, t in calls if k == "call_ease"]
            gas = [int(t.split()[-1]) for k, t in calls if k == "call_gas"]
            # spoken form: the word only on the first call of each phase, bare numbers after it
            texts = [t for _k, t in calls]
            words = " ".join(texts).split()
            self.assertTrue(all(w.isdigit() or w in ("Brake", "Gas", "Full") for w in words), words)
            self.assertEqual(sum(t.startswith("Brake") for t in texts), 1)
            self.assertLessEqual(sum(t.startswith("Gas") for t in texts), 1)
            self.assertEqual(ease, sorted(ease, reverse=True))     # brake levels fall
            self.assertEqual(gas, sorted(gas))                      # throttle levels rise
            self.assertTrue(all(0 <= v <= 90 for v in ease + gas))
            self.assertEqual(ease[-1], 0)                           # the brake release is called as "0"

    def test_every_scripted_call_is_spoken_every_lap(self):
        # The driver's complaint: some numbers ("50") were skipped. Nothing in the script may be dropped.
        def phrase(text):
            return 0.5 if text.startswith(("Brake", "Gas")) else 0.35

        for v_in in (200.0, 300.0):
            for jitter in (False, True):
                with self.subTest(v=v_in, jitter=jitter):
                    an = make(calls=True, phrase_duration=phrase)
                    expected: dict[int, list[str]] = {}

                    def snapshot(lap, d, _t, _v, an=an, expected=expected):
                        if 850.0 <= d < 900.0 and lap not in expected and an.zones and an.zones[0].script:
                            expected[lap] = [t for _d, _k, t, _s in an.zones[0].script]

                    ev = drive(an, laps=8, v_in=v_in, hz=20, jitter=jitter, schedule=True, pre_frame=snapshot)
                    checked = 0
                    for lap in range(3, 9):
                        if lap not in expected:
                            continue
                        said = [t for _k, t in self.lap_calls(ev, lap)]
                        self.assertEqual(said, expected[lap], (lap, said, expected[lap]))
                        checked += 1
                    self.assertGreaterEqual(checked, 4)

    def test_corners_in_the_second_half_of_the_lap_get_their_gas_calls(self):
        # Regression: a target more than half a lap ahead wraps to "behind" in signed distance, which
        # silently cancelled every throttle call of corners in the second half of the lap.
        for brake_at in (1000.0, 2600.0, 3300.0):
            with self.subTest(brake_at=brake_at):
                an = make(calls=True)
                ev = drive(an, laps=5, hz=60, brake_at=brake_at, jitter=True, schedule=True)
                for lap in (3, 4, 5):
                    texts = [t for _k, t in self.lap_calls(ev, lap)]
                    self.assertTrue(texts and texts[0].startswith("Brake"), texts)
                    self.assertTrue(any(t.startswith("Gas") for t in texts), (lap, texts))

    def test_calls_do_not_overlap_clips(self):
        an = make(calls=True)
        an.phrase_duration = lambda text: 0.45
        drive(an, laps=4, hz=20)
        script = an.zones[0].script
        self.assertGreaterEqual(len(script), 4)
        # start times at least the clip duration apart (from the reference speed)
        starts = []
        t = 0.0
        prev_d = 0.0
        prof = an.zones[0].profile
        for d, _k, text, _s in script:
            v = max(8.0, prof[min(len(prof) - 1, int(d / 4))][3])
            t += (d - prev_d) / v
            prev_d = d
            starts.append(t - an._speech_lead_s(text))
        for a, b in zip(starts, starts[1:], strict=False):
            self.assertGreater(b - a, 0.45)

    def test_call_finishes_before_the_point(self):
        an = make(calls=True, speech_rate_wpm=330)
        ev = drive(an, laps=4, hz=60, v_in=250.0)
        call = next(e for e in ev if e[0] == "call_brake" and e[1] == 3)
        ahead = 1000.0 - call[2]
        speed = 250.0 / 3.6
        self.assertGreater(ahead, speed * 0.15)
        self.assertLess(ahead, speed * an._speech_lead_s("Brake 90") + 2.0)

    def test_calls_off_by_default(self):
        ev = drive(make(), laps=4)
        self.assertFalse([e for e in ev if e[0].startswith("call_")])

    def test_gas_calls_replaced_by_easy_when_sliding(self):
        an = make(calls=True)
        an.slide_ratio = 0.5            # the car is sliding
        ev = drive(an, laps=4, hz=20)
        kinds = {e[0] for e in ev if e[0].startswith("call_")}
        self.assertIn("call_easy", kinds)
        self.assertNotIn("call_full", kinds)
        self.assertNotIn("call_gas", kinds)
        self.assertIn("call_brake", kinds)    # brake calls are unaffected

    def test_single_pass_zones_stay_silent(self):
        an = make(calls=True)
        ev = drive(an, laps=2, hz=20)         # only 1 reference lap: no calls
        self.assertFalse([e for e in ev if e[0].startswith("call_")])


class SchedulerPrecision(unittest.TestCase):
    """Real F1 25 stream: irregular 40-170 ms frames (20 Hz); trigger measured against the learned point."""

    @staticmethod
    def errors(schedule, v_in):
        ev = drive(make(), laps=7, v_in=v_in, jitter=True, schedule=schedule)
        cues = [e for e in ev if e[0] == "cue_brake"]
        return [abs(e[2] - float(e[3])) for e in cues]

    def test_scheduled_cue_lands_on_the_learned_point(self):
        for v_in in (200.0, 300.0):
            with self.subTest(v=v_in):
                errs = self.errors(True, v_in)
                self.assertEqual(len(errs), 6)
                self.assertLess(max(errs), 1.5, errs)

    def test_scheduler_beats_frame_quantization(self):
        without = self.errors(False, 300.0)
        with_sched = self.errors(True, 300.0)
        self.assertGreater(max(without), 3.0)         # frame size is a clear error
        self.assertLess(max(with_sched), max(without) / 2)


class ReferenceFromBestLaps(unittest.TestCase):
    def test_best_laps_define_reference_not_the_first_lap(self):
        # Lap 1 is bad (brake 30 m early, slow); laps 2-4 are the real reference, fast.
        an = make()
        times = {1: 95000, 2: 91000, 3: 91200, 4: 91100}
        drive(an, laps=5, brake_at=1000.0, lap_shift={1: -30.0}, lap_times=times)
        z = an.zones[0]
        from apexmind.engineer.analyzer import _circular_median
        ref = _circular_median(z.brake_lap, an.track_length)
        self.assertAlmostEqual(ref, 1000.0, delta=2.0)
        self.assertEqual(len(z.laps), 3)

    def test_invalid_lap_is_not_learned(self):
        an = make()

        def flag(lap, d, t, v):
            an.lap_invalid = lap == 2   # lap 2 is a track-limit violation

        drive(an, laps=4, brake_at=1000.0, lap_shift={2: -40.0},
              lap_times={1: 92000, 2: 85000, 3: 92100}, pre_frame=flag)
        z = an.zones[0]
        # An invalid lap may stay recorded but is not used as a reference while a valid lap exists.
        self.assertTrue(all(abs(s - 1000.0) < 3.0 for s in z.brake_lap), z.brake_lap)
        self.assertEqual(len(z.brake_lap), 2)


if __name__ == "__main__":
    unittest.main()
