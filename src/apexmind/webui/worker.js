// SPDX-License-Identifier: AGPL-3.0-or-later
// Runs the real ApexMind Python package in the browser (Pyodide) so the web UI never re-implements the coach.
"use strict";

const PYODIDE_URL = "https://cdn.jsdelivr.net/pyodide/v0.26.4/full/";
let pyodide = null;

const post = (msg) => self.postMessage(msg);

async function init(id) {
  post({ id, type: "status", text: "Loading the Python runtime (first visit only)…" });
  importScripts(PYODIDE_URL + "pyodide.js");
  pyodide = await loadPyodide({ indexURL: PYODIDE_URL });
  post({ id, type: "status", text: "Loading ApexMind…" });
  const res = await fetch("apexmind.zip", { cache: "no-cache" });
  if (!res.ok) throw new Error("Could not load apexmind.zip (" + res.status + ")");
  pyodide.FS.mkdirTree("/apex");
  pyodide.unpackArchive(await res.arrayBuffer(), "zip", { extractDir: "/apex" });
  pyodide.runPython("import sys; sys.path.insert(0, '/apex')");
}

self.onmessage = async (e) => {
  const { id, type, text } = e.data;
  try {
    if (type !== "analyze") return;
    if (!pyodide) await init(id);
    post({ id, type: "status", text: "Replaying the session through the coach…" });
    pyodide.globals.set("csv_text", text);
    const json = await pyodide.runPythonAsync(
      "import json\nfrom apexmind.report import build_report\njson.dumps(build_report(csv_text))"
    );
    post({ id, type: "result", json });
  } catch (err) {
    const raw = String((err && err.message) || err);
    const last = raw.trim().split("\n").filter(Boolean).pop() || raw;
    post({ id, type: "error", message: last.replace(/^ValueError:\s*/, "") });
  }
};
