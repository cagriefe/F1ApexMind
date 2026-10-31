# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) ApexMind contributors
"""A real `apexmind listen --web` process: the dashboard sees the same telemetry and calls as the console."""

import json
import os
import socket
import subprocess
import sys
import time
import unittest
import urllib.request
from pathlib import Path
from unittest import mock

import builders as b
from apexmind import cli

ROOT = Path(__file__).resolve().parent.parent


def free_port(kind):
    s = socket.socket(socket.AF_INET, kind)
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


class WebDashboardEndToEnd(unittest.TestCase):
    def fetch(self, base, path):
        with urllib.request.urlopen(base + path, timeout=3) as r:
            return r.read()

    def test_live_state_and_advice_reach_the_dashboard(self):
        udp, tcp = free_port(socket.SOCK_DGRAM), free_port(socket.SOCK_STREAM)
        env = {k: v for k, v in os.environ.items() if k != "PYTHONPATH"}
        env["PYTHONPATH"] = str(ROOT / "src")
        proc = subprocess.Popen(
            [sys.executable, "-u", "-m", "apexmind", "listen", "--mute", "--log", "off",
             "--port", str(udp), "--web", str(tcp)],
            env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
        )
        base = "ht" + f"tp://localhost:{tcp}"
        try:
            for _ in range(60):
                try:
                    snap = json.loads(self.fetch(base, "/api/live"))
                    break
                except OSError:
                    time.sleep(0.1)
            else:
                self.fail("the dashboard never came up")
            self.assertTrue(snap["live"])
            self.assertIn(b"ApexMind", self.fetch(base, "/"))

            tx = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            clock = [0.0]

            def send(buf):
                tx.sendto(buf, ("127.0.0.1", udp))
                time.sleep(0.002)

            def tick():
                clock[0] += 0.02
                return clock[0]

            send(b.session(t=tick()))
            for _ in range(25):
                ts = tick()
                send(b.lapdata(3, 100.0, 5000, t=ts))
                send(b.motion(200.0, t=ts))
                send(b.motion_ex(slip=(0, 0, -0.6, 0), t=ts))
                send(b.telemetry(200, 0.0, 0.8, 0.0, t=ts))
            time.sleep(0.5)
            snap = json.loads(self.fetch(base, "/api/live"))
            self.assertEqual(snap["state"]["speed"], 200)
            self.assertAlmostEqual(snap["state"]["brake"], 0.8, places=1)
            self.assertEqual(snap["state"]["lap"], 3)
            self.assertIn("lockup", [e["kind"] for e in snap["events"]])
        finally:
            proc.terminate()
            out = proc.communicate(timeout=10)[0]
        self.assertNotIn("Traceback", out)
        self.assertIn("Web dashboard", out)


class OpenFlag(unittest.TestCase):
    def test_open_flag_is_accepted(self):
        args = cli.build_parser().parse_args(["listen", "--open"])
        self.assertTrue(args.open)
        self.assertIsNone(args.web)       # --open turns the dashboard on when the command starts

    def test_dashboard_is_opened_in_the_browser(self):
        with mock.patch.object(cli.webbrowser, "open") as opener:
            cli._open_later("dashboard-url", delay_s=0.0)
            for _ in range(50):
                if opener.called:
                    break
                time.sleep(0.02)
        opener.assert_called_once_with("dashboard-url")


if __name__ == "__main__":
    unittest.main()
