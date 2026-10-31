# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) ApexMind contributors
"""Pre-rendered clip infrastructure: with fake `say`/`afplay` (no macOS needed)."""

import os
import stat
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from apexmind.engineer import Analyzer
from apexmind.engineer.voice import Voice

FAKE_SAY = """#!{py}
import sys, wave
a = sys.argv[1:]
if a[:2] == ["-v", "?"]:
    sys.exit(0)
out, text, i = None, "", 0
while i < len(a):
    if a[i] == "-o":
        out = a[i + 1]; i += 2
    elif a[i] in ("-r", "-v"):
        i += 2
    elif a[i].startswith("--"):
        i += 1
    else:
        text = a[i]; i += 1
if out:
    n = int(22050 * (0.10 + 0.20 * len(text.split())))
    w = wave.open(out, "wb"); w.setnchannels(1); w.setsampwidth(2); w.setframerate(22050)
    w.writeframes(b"\\0\\0" * n); w.close()
"""
FAKE_AFPLAY = """#!{py}
import os, sys, time, wave
args = [a for a in sys.argv[1:]]
quiet = args[:2] == ["-v", "0"]
path = args[-1]
if quiet:
    w = wave.open(path, "rb"); dur = w.getnframes() / w.getframerate(); w.close()
    time.sleep(float(os.environ.get("FAKE_LATENCY", "0")) + dur)
    sys.exit(0)
with open(os.environ["FAKE_LOG"], "a") as f:
    f.write(f"{{time.monotonic():.4f}} {{os.path.basename(path)}}\\n")
time.sleep(float(os.environ.get("FAKE_PLAY", "0")))
"""


class PhraseTests(unittest.TestCase):
    def setUp(self):
        self.bin = tempfile.mkdtemp()
        self.log = os.path.join(self.bin, "afplay.log")
        for name, body in (("say", FAKE_SAY), ("afplay", FAKE_AFPLAY)):
            p = Path(self.bin) / name
            p.write_text(body.format(py=sys.executable), encoding="utf-8")
            p.chmod(p.stat().st_mode | stat.S_IEXEC)
        env = {"PATH": self.bin + os.pathsep + os.environ["PATH"], "FAKE_LOG": self.log}
        patches = [mock.patch.dict(os.environ, env), mock.patch.object(sys, "platform", "darwin")]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)

    def test_prerender_duration_and_instant_playback(self):
        v = Voice(enabled=True, name="auto", rate=333)
        self.addCleanup(v.close)
        phrases = ["Brake 80", "Full"]
        v.prepare_phrases(phrases, background=False)
        self.assertAlmostEqual(v.phrase_duration("Brake 80"), 0.5, delta=0.02)
        self.assertAlmostEqual(v.phrase_duration("Full"), 0.3, delta=0.02)
        self.assertIsNone(v.phrase_duration("Never rendered"))
        t0 = time.monotonic()
        v.call("Brake 80")
        for _ in range(100):
            if os.path.exists(self.log):
                break
            time.sleep(0.01)
        self.assertTrue(os.path.exists(self.log))
        self.assertLess(time.monotonic() - t0, 0.5)       # no live synthesis: plays instantly

    def test_latency_is_measured_automatically(self):
        for fake in (0.04, 0.15):
            with self.subTest(latency=fake), mock.patch.dict(os.environ, {"FAKE_LATENCY": str(fake)}):
                v = Voice(enabled=True, name="auto", rate=333)
                self.addCleanup(v.close)
                measured = v.measure_latency(runs=3)
                self.assertIsNotNone(measured)
                self.assertAlmostEqual(measured, fake, delta=0.05)

    def test_stale_call_is_dropped_not_played_late(self):
        v = Voice(enabled=True, name="auto", rate=333)
        self.addCleanup(v.close)
        v.prepare_phrases(["Full"], background=False)
        v._phrase_q.put_nowait((time.monotonic() - 5.0, "Full", 0.6))   # queued 5 s ago
        time.sleep(0.8)
        self.assertFalse(os.path.exists(self.log))

    def log_lines(self, n, timeout=2.0):
        t0 = time.monotonic()
        while time.monotonic() - t0 < timeout:
            if os.path.exists(self.log):
                lines = Path(self.log).read_text(encoding="utf-8").splitlines()
                if len(lines) >= n:
                    return lines
            time.sleep(0.01)
        return Path(self.log).read_text(encoding="utf-8").splitlines() if os.path.exists(self.log) else []

    def test_clips_never_queue_behind_each_other(self):
        # A call used to wait for the previous clip to finish and was silently dropped if it waited too long.
        with mock.patch.dict(os.environ, {"FAKE_PLAY": "0.7"}):
            v = Voice(enabled=True, name="auto", rate=333)
            self.addCleanup(v.close)
            v.prepare_phrases(["Brake 80", "Full"], background=False)
            v.call("Brake 80")
            time.sleep(0.2)
            v.call("Full")
            lines = self.log_lines(2)
            self.assertEqual(len(lines), 2, lines)
            starts = [float(line.split()[0]) for line in lines]
            self.assertLess(starts[1] - starts[0], 0.45)      # serial playback would take >= 0.7 s
            self.assertEqual(v.dropped_calls, 0)

    def test_dropped_call_is_reported_not_silent(self):
        v = Voice(enabled=True, name="auto", rate=333)
        self.addCleanup(v.close)
        v.prepare_phrases(["Full"], background=False)
        seen = []
        v.on_call_dropped = lambda text, reason: seen.append((text, reason))
        v._phrase_q.put_nowait((time.monotonic() - 5.0, "Full", 0.6))
        time.sleep(0.8)
        self.assertEqual([s[0] for s in seen], ["Full"])
        self.assertEqual(v.dropped_calls, 1)

    def test_analyzer_uses_real_clip_durations_for_lead(self):
        v = Voice(enabled=True, name="auto", rate=333)
        self.addCleanup(v.close)
        v.prepare_phrases(Analyzer.call_phrases(), background=False)
        an = Analyzer(lambda k, t: None, phrase_duration=v.phrase_duration, call_margin_s=0.15)
        short = an._speech_lead_s("Full")
        longer = an._speech_lead_s("Brake 80")
        expected = v.phrase_duration("Brake 80") - v.phrase_duration("Full")
        self.assertAlmostEqual(longer - short, expected, places=6)
        self.assertEqual(len(Analyzer.call_phrases()), len(set(Analyzer.call_phrases())))
        self.assertIn("30", Analyzer.call_phrases())
        self.assertIn("0", Analyzer.call_phrases())


if __name__ == "__main__":
    unittest.main()
