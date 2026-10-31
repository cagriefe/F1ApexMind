# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) ApexMind contributors
"""Track memory: corners learned in one session are known in every later one."""

import json
import os
import socket
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

import builders as b
from apexmind.engineer import Analyzer
from apexmind.engineer.analyzer import merge_refs
from apexmind.memory import TrackMemory

from sim import drive

ROOT = Path(__file__).resolve().parent.parent
TRACK_ID, TRACK_LEN = 17, 4323.0


def learned(laps=4, **kw):
    an = Analyzer(lambda k, t: None, learn_n=1, cooldown_s=0, calls=True)
    drive(an, laps=laps, hz=60, **kw)
    return an


class Persistence(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()

    def test_a_new_process_knows_the_track_and_calls_from_lap_one(self):
        first = learned(lap_times={1: 92000, 2: 91500, 3: 91000})
        TrackMemory(self.dir).save(TRACK_ID, first.export_refs())

        data = TrackMemory(self.dir).load(TRACK_ID, TRACK_LEN)          # a fresh object: "another day"
        fresh = Analyzer(lambda k, t: None, learn_n=1, cooldown_s=0, calls=True)
        fresh.set_track_length(TRACK_LEN)
        self.assertGreaterEqual(fresh.import_refs(data), 1)
        ev = drive(fresh, laps=1, hz=60)
        calls = [e[3] for e in ev if e[0].startswith("call_") and e[1] == 1]
        self.assertEqual((calls[0], calls[-1]), ("Brake 90", "Full"))

    def test_laps_without_a_time_are_remembered_too(self):
        # Practice with flashbacks and invalid laps: no clean timed lap, but the corners are still known.
        an = learned(laps=3)                                              # untimed laps
        refs = an.export_refs()
        self.assertGreaterEqual(len(refs["zones"]), 1)
        self.assertTrue(TrackMemory(self.dir).save(TRACK_ID, refs))
        self.assertIsNotNone(TrackMemory(self.dir).load(TRACK_ID, TRACK_LEN))

    def test_saving_again_never_duplicates_laps_or_loses_the_best(self):
        mem = TrackMemory(self.dir)
        best = learned(lap_times={1: 92000, 2: 90000, 3: 91000})
        mem.save(TRACK_ID, best.export_refs())
        for _ in range(3):                                                # a session re-saves every lap
            mem.save(TRACK_ID, best.export_refs())
        mem.save(TRACK_ID, learned(lap_times={1: 99000, 2: 98000, 3: 97000}).export_refs())   # weaker day
        laps = mem.load(TRACK_ID)["zones"][0]["laps"]
        ms = [lap["ms"] for lap in laps]
        self.assertEqual(ms, sorted(ms))
        self.assertEqual(len(ms), len(set(ms)), "duplicate laps")
        self.assertEqual(ms[0], 90000)                                    # the best lap survived

    def test_another_layout_is_not_loaded_and_replaces_on_save(self):
        mem = TrackMemory(self.dir)
        mem.save(TRACK_ID, learned(lap_times={1: 92000, 2: 91500, 3: 91000}).export_refs())
        self.assertIsNotNone(mem.load(TRACK_ID, TRACK_LEN + 5))
        self.assertIsNone(mem.load(TRACK_ID, TRACK_LEN + 400))            # different layout, same id

    def test_corrupt_file_is_ignored_and_no_temp_file_is_left(self):
        mem = TrackMemory(self.dir)
        mem.save(TRACK_ID, learned(lap_times={1: 92000, 2: 91500}).export_refs())
        path = Path(self.dir) / "tracks" / f"{TRACK_ID}.json"
        self.assertEqual(list(path.parent.glob("*.tmp")), [])
        path.write_text("{ not json", encoding="utf-8")
        self.assertIsNone(mem.load(TRACK_ID))
        self.assertTrue(mem.save(TRACK_ID, learned(lap_times={1: 92000, 2: 91500}).export_refs()))
        self.assertIsNotNone(mem.load(TRACK_ID))

    def test_the_old_refs_file_is_still_read(self):
        refs = learned(lap_times={1: 92000, 2: 91500, 3: 91000}).export_refs()
        Path(self.dir, "refs.json").write_text(json.dumps({str(TRACK_ID): refs}), encoding="utf-8")
        mem = TrackMemory(self.dir)
        self.assertIsNotNone(mem.load(TRACK_ID, TRACK_LEN))
        self.assertEqual([t["track_id"] for t in mem.tracks()], [TRACK_ID])

    def test_forget(self):
        mem = TrackMemory(self.dir)
        mem.save(TRACK_ID, learned(lap_times={1: 92000, 2: 91500}).export_refs())
        self.assertTrue(mem.forget(TRACK_ID))
        self.assertIsNone(mem.load(TRACK_ID))
        self.assertFalse(mem.forget(TRACK_ID))


class MergeKeepsNeighbouringCorners(unittest.TestCase):
    def test_two_corners_close_together_stay_two_corners(self):
        def export(ms):
            def zone(pos):
                return {"laps": [{"ms": ms, "sample": {"brake_lap": pos, "depth": 0.9}}]}
            return {"track_length": 4323.0, "zones": [zone(1000.0), zone(1040.0)]}     # a chicane

        merged = merge_refs([export(90000), export(91000)])
        self.assertEqual(len(merged["zones"]), 2)
        self.assertTrue(all(len(z["laps"]) == 2 for z in merged["zones"]))


def free_port():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


class CommandLine(unittest.TestCase):
    def env(self):
        env = {k: v for k, v in os.environ.items() if k != "PYTHONPATH"}
        env["PYTHONPATH"] = str(ROOT / "src")
        return env

    def test_listen_announces_remembered_corners_and_memory_command_lists_them(self):
        mem_dir = tempfile.mkdtemp()
        TrackMemory(mem_dir).save(
            TRACK_ID, learned(lap_times={1: 92000, 2: 91500, 3: 91000}).export_refs())
        udp = free_port()
        proc = subprocess.Popen(
            [sys.executable, "-u", "-m", "apexmind", "listen", "--mute", "--log", "off",
             "--port", str(udp), "--memory", mem_dir],
            env=self.env(), stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
        )
        time.sleep(1.2)
        tx = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        for i in range(5):
            tx.sendto(b.session(track_length=int(TRACK_LEN), track_id=TRACK_ID, t=0.02 * i),
                      ("127.0.0.1", udp))
            time.sleep(0.05)
        time.sleep(0.5)
        proc.terminate()
        out = proc.communicate(timeout=10)[0]
        self.assertIn("Track memory:", out)
        self.assertIn("calls work from the first lap", out)

        listing = subprocess.run(
            [sys.executable, "-m", "apexmind", "memory", "--dir", mem_dir],
            env=self.env(), capture_output=True, text=True, timeout=30,
        ).stdout
        self.assertIn("Red Bull Ring", listing)
        self.assertIn("corners", listing)


if __name__ == "__main__":
    unittest.main()
