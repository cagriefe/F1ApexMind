# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) ApexMind contributors
import json
import threading
import unittest
import urllib.error
import urllib.request
import zipfile
from io import BytesIO
from apexmind.report import build_report
from apexmind.webserver import WEBUI_DIR, LiveHub, package_zip_bytes, start_server

SAMPLE = WEBUI_DIR / "sample.csv"


class ReportTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.report = build_report(SAMPLE.read_text(encoding="utf-8"))

    def test_summary_and_laps(self):
        m = self.report["summary"]
        self.assertEqual(m["laps"], 3)
        self.assertEqual(m["valid"], 2)
        self.assertEqual(m["best_lap"], 27)
        self.assertLess(m["ideal_ms"], m["best_ms"])
        self.assertGreaterEqual(m["lockups"], 5)
        laps = {lp["lap"]: lp for lp in self.report["laps"]}
        self.assertFalse(laps[26]["valid"])
        self.assertTrue(all(lp["complete"] for lp in laps.values()))
        for lp in laps.values():                      # the three sectors add up to the lap time
            self.assertAlmostEqual(sum(lp["sectors"]), lp["ms"], delta=2)

    def test_corners_have_complete_scripts(self):
        corners = self.report["corners"]
        self.assertGreaterEqual(len(corners), 5)
        self.assertEqual([c["id"] for c in corners], list(range(1, len(corners) + 1)))
        for c in corners:
            self.assertTrue(c["script"][0]["text"].startswith("Brake"))
            self.assertIn(c["script"][-1]["kind"], ("full", "gas", "brake", "ease"))

    def test_map_and_traces(self):
        pts = self.report["map"]["points"]
        self.assertGreater(len(pts), 300)
        self.assertEqual(len(pts[0]), 5)
        tr = self.report["traces"]["27"]
        self.assertEqual(len({len(v) for v in tr.values()}), 1)
        self.assertTrue(all(a < b for a, b in zip(tr["d"], tr["d"][1:], strict=False)))

    def test_report_is_json_serialisable(self):
        self.assertLess(len(json.dumps(self.report)), 400_000)

    def test_rejects_other_csv_files(self):
        with self.assertRaises(ValueError):
            build_report("a,b\n1,2\n")
        with self.assertRaises(ValueError):
            build_report("")

    def test_recording_that_starts_mid_lap_is_marked_partial(self):
        lines = SAMPLE.read_text(encoding="utf-8").splitlines()
        head, rows = lines[0], lines[1:]
        mid = [r for r in rows if r.split(",")[1] == "26"]
        cut = mid[len(mid) // 2:] + [r for r in rows if r.split(",")[1] != "26"]
        laps = {lp["lap"]: lp for lp in build_report("\n".join([head, *cut]))["laps"]}
        self.assertFalse(laps[26]["complete"])
        self.assertTrue(laps[27]["complete"])


class WebServerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.hub = LiveHub()
        cls.server = start_server(cls.hub, "localhost", 0)
        cls.base = "ht" + f"tp://localhost:{cls.server.server_address[1]}"

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()

    def get(self, path):
        with urllib.request.urlopen(self.base + path, timeout=5) as r:
            return r.status, r.headers.get("Content-Type", ""), r.read()

    def test_static_files_and_zip(self):
        status, ctype, body = self.get("/")
        self.assertEqual(status, 200)
        self.assertIn("text/html", ctype)
        self.assertIn(b"ApexMind", body)
        for name in ("app.js", "worker.js", "style.css", "sample.csv"):
            self.assertEqual(self.get("/" + name)[0], 200)
        _s, _c, data = self.get("/apexmind.zip")
        names = zipfile.ZipFile(BytesIO(data)).namelist()
        self.assertIn("apexmind/report.py", names)
        self.assertIn("apexmind/engineer/analyzer.py", names)
        self.assertFalse([n for n in names if "webui" in n or "__pycache__" in n])
        self.assertEqual(len(package_zip_bytes()) > 10_000, True)

    def test_path_traversal_is_blocked(self):
        for bad in ("/../report.py", "/%2e%2e/report.py", "/../../pyproject.toml"):
            with self.assertRaises(urllib.error.HTTPError) as ctx:
                self.get(bad)
            self.assertEqual(ctx.exception.code, 404)

    def test_live_snapshot_and_event_stream(self):
        self.hub.publish_state({"speed": 250, "gear": 7})
        _s, _c, body = self.get("/api/live")
        self.assertEqual(json.loads(body)["state"]["speed"], 250)

        got: list[str] = []
        ready = threading.Event()

        def reader():
            with urllib.request.urlopen(self.base + "/events", timeout=5) as r:
                buf = ""
                while True:
                    chunk = r.readline().decode()
                    if not chunk:
                        return
                    buf += chunk
                    if "event: snapshot" in buf:
                        ready.set()
                    if "event: advice" in buf and "Brake 90" in buf:
                        got.append(buf)
                        return

        t = threading.Thread(target=reader, daemon=True)
        t.start()
        self.assertTrue(ready.wait(5))
        self.hub.publish_event({"kind": "call_brake", "text": "Brake 90", "lap": 3, "d": 100})
        t.join(5)
        self.assertTrue(got, "advice event never arrived over SSE")


if __name__ == "__main__":
    unittest.main()
