# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) ApexMind contributors
"""A real `apexmind listen` process + a synthetic UDP stream: verifies every module is wired into the CLI."""

import os
import socket
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

import builders as b
from apexmind.recorder import read_samples

ROOT = Path(__file__).resolve().parent.parent
TRACK = 4323


def free_port():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


class EndToEnd(unittest.TestCase):
    def test_full_session(self):
        port = free_port()
        rec = tempfile.mkdtemp()
        env = {k: v for k, v in os.environ.items() if k != "PYTHONPATH"}
        env["PYTHONPATH"] = str(ROOT / "src")
        proc = subprocess.Popen(
            [sys.executable, "-u", "-m", "apexmind", "listen", "--mute", "--log", "off",
             "--port", str(port), "--record", rec],
            env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
        )
        time.sleep(1.2)
        tx = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        clock = [0.0]

        def send(buf):
            tx.sendto(buf, ("127.0.0.1", port))
            time.sleep(0.002)

        def tick(dt=0.02):
            clock[0] += dt
            return clock[0]

        send(b.session(t=tick()))
        send(b.damage(t=tick()))
        # two laps of 4300 m each; lap 2 is faster; a track-limit violation at 2000 m in lap 2
        for lap, ms_per_m in ((1, 20.0), (2, 19.0)):
            for dist in range(0, 4300, 25):
                sector = 0 if dist < 1400 else (1 if dist < 2900 else 2)
                invalid = int(lap == 2 and dist >= 2000)
                last = 86000 if (lap == 2 and dist < 100) else 0
                ts = tick()
                send(b.lapdata(lap, float(dist), int(dist * ms_per_m), last_ms=last, sector=sector,
                               invalid=invalid, t=ts))
                send(b.motion(180.0, t=ts))
                send(b.telemetry(180, 1.0, 0.0, 0.0, inner=(95, 95, 120, 95), t=ts))
        # lock-up: front-left wheel under braking
        for _ in range(12):
            ts = tick()
            send(b.lapdata(3, 100.0, 5000, t=ts))
            send(b.motion(200.0, t=ts))
            send(b.motion_ex(slip=(0, 0, -0.6, 0), t=ts))
            send(b.telemetry(200, 0.0, 0.8, 0.0, t=ts))
        send(b.damage(rear_wing=25, t=tick()))
        time.sleep(0.6)
        proc.terminate()
        out = proc.communicate(timeout=10)[0]

        adv = [ln for ln in out.splitlines() if "⚡" in ln]
        joined = "\n".join(adv)
        self.assertNotIn("Traceback", out)
        self.assertIn("[delta] Sector 1", joined)
        self.assertIn("minus", joined)                       # lap 2 is faster than the best lap
        self.assertIn("[lap_invalid] Track limits. Lap deleted.", joined)
        self.assertIn("[lockup] Front left locked!", joined)
        self.assertIn("[tyre_temp] Front left tyre hot, 120", joined)
        self.assertIn("[damage] Rear wing damage, 25 percent.", joined)
        self.assertIn("[debrief]", joined)

        csvs = list(Path(rec).glob("*.csv"))
        samples = list(Path(rec).glob("*.samples"))
        self.assertEqual((len(csvs), len(samples)), (1, 1))
        rows = csvs[0].read_text(encoding="utf-8").splitlines()
        self.assertGreater(len(rows), 50)
        self.assertIn("slip_fl", rows[0])
        ids = {pid for _t, pid, _b in read_samples(samples[0])}
        self.assertTrue({0, 1, 2, 6, 10, 13} <= ids, ids)


if __name__ == "__main__":
    unittest.main()
