# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) ApexMind contributors
import os
import queue
import unittest
import wave

from apexmind.engineer.voice import Voice, _beep_path


class BeepFileTest(unittest.TestCase):
    def test_beep_wav_is_valid(self):
        path = _beep_path("test_single", 880, 80, volume=0.5)
        self.assertIsNotNone(path)
        assert path is not None
        self.assertTrue(os.path.exists(path))
        with wave.open(path, "rb") as w:
            self.assertEqual(w.getnchannels(), 1)
            self.assertEqual(w.getsampwidth(), 2)
            self.assertEqual(w.getframerate(), 22050)
            self.assertGreater(w.getnframes(), 0)

    def test_double_beep_is_longer(self):
        single = _beep_path("test_cmp_single", 700, 60, volume=0.5)
        double = _beep_path("test_cmp_double", 700, 60, gap_ms=40, double=True, volume=0.5)
        assert single is not None and double is not None
        with wave.open(single, "rb") as w1, wave.open(double, "rb") as w2:
            self.assertGreater(w2.getnframes(), w1.getnframes())


class DisabledVoiceTest(unittest.TestCase):
    def test_disabled_voice_is_safe(self):
        v = Voice(enabled=False)
        v.speak("hello")
        v.cue_brake()
        v.cue_throttle()
        v.cue_spin()
        v.close()

    def test_spin_cue_jumps_the_queue(self):
        v = Voice(enabled=False)
        try:
            v.enabled = True
            v._cue_q.put("brake")
            v._cue_q.put("throttle")
            v._enqueue_cue("spin")
            self.assertEqual(v._cue_q.get_nowait(), "spin")
            with self.assertRaises(queue.Empty):
                v._cue_q.get_nowait()
        finally:
            v.enabled = False
            v.close()


if __name__ == "__main__":
    unittest.main()
