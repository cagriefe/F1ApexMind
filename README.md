# ApexMind

A real-time AI race engineer for F1 games. It listens to the game's UDP telemetry, learns **your own** best laps and calls out what to do with the pedals, corner by corner, in short spoken phrases like a human coach would.

```
Brake 90  →  50  →  0  →  Gas 30  →  60  →  Full
```

It also warns about lock-ups, wheelspin, slides and spins, track limits, tyre temperatures and damage, and gives sector and lap deltas. Everything runs locally and the core has **no dependencies** beyond the Python standard library.

> **Status:** developed and verified against real F1 25 sessions (Time Trial, Spa). Other tracks should work because everything is learned from your laps, but are less tested. Tyre-wear and damage packets have not been verified with real data yet. Spoken output uses macOS `say`/`afplay`; on Linux and Windows run with `--mute` and use the console or the web dashboard.

## Quick start

You need Python 3.10 or newer and a game that can send UDP telemetry (F1 22 to F1 25, including the F1 25 2026 Season Pack).

1. In the game: **Settings → Telemetry Settings** and set
   - UDP Telemetry: **On**
   - UDP Broadcast Mode: Off
   - UDP IP Address: the IP of the computer running ApexMind
   - UDP Port: **20777**
   - UDP Send Rate: **60 Hz** (20 Hz works, 60 Hz is more precise)
   - UDP Format: **2025** (or 2026 for the Season Pack)
2. Start ApexMind
   - macOS: double-click `start.command`
   - anywhere: `PYTHONPATH=src python3 -m apexmind listen --web` (or `pip install -e .` and run `apexmind listen --web`)
3. Drive. The first laps are used for learning; calls start once a corner has been seen on two valid laps. Best laps are remembered between sessions, so on a known track the calls work from the first lap.

Open `localhost:8765` in a browser for the web dashboard (see below). Stop with Ctrl+C.

## What you hear

| Call | Meaning |
| --- | --- |
| `Brake 90` | Brake now, to about 90 % pedal. The level comes from your best laps. |
| `50` | Ease to about 50 % |
| `0` | Fully off the brake (only when there is time between brake release and throttle) |
| `Gas 30`, `60` | Throttle to about 30 %, then 60 % |
| `Full` | Flat out |
| `Gas 70` while on power | Wheelspin ahead: lower the throttle to that level |
| `Easy` | The car is sliding: no throttle increase |
| `Limits` | You went off track here before; stay inside |
| `Front left locked!` | Wheel lock-up under braking |

The word is spoken only on the first call of each phase, numbers follow. Calls are timed by speed so each one ends just before its point, and they are spaced so they never overlap. If a call cannot be said on time it is skipped rather than said late.

## Web dashboard

ApexMind ships a small web app (no build step, no dependencies) with two views:

- **Analyze:** drop a recording (the CSV written by `--record`) to see your laps and sectors, the ideal lap, a speed-coloured racing line, every corner with its braking point and call script, a speed/pedal chart you can compare lap against lap, and the event log. Analysis runs in your browser: the real Python package is executed with [Pyodide](https://pyodide.org), so the UI never re-implements the coach.
- **Live:** while `apexmind listen --web` runs, a live view shows speed, pedals, lap time and the calls as they are spoken. Use `--web-host 0.0.0.0` to open it from a phone on the same network (anyone on that network can then read it).

The static version of the app can be hosted on GitHub Pages (see `.github/workflows/pages.yml`); there the Live view is hidden because browsers cannot receive UDP. Build it locally with `python tools/build_site.py`.

## Recording and replaying

```
apexmind listen --record recordings        # writes session-<time>.csv and .samples
apexmind replay recordings/session-*.csv   # what the coach would have said, with timing
apexmind replay a.csv --scripts            # the learned corner scripts
apexmind report a.csv -o report.json       # the JSON the web UI shows
```

`start.command` records automatically into `./recordings` (git-ignored). Use `tools/share-recordings.sh` to push recordings to a separate branch, for example to attach them to a bug report.

## Useful options

Run `apexmind listen --help` for all of them.

| Option | Default | Meaning |
| --- | --- | --- |
| `--port` | 20777 | UDP port to listen on |
| `--mute` | off | No audio, console output only |
| `--web [PORT]` | off | Serve the dashboard (8765 if no port is given) |
| `--calls on/off` | on | Graded pedal calls |
| `--cue-latency` | auto | Beep/voice latency compensation: `auto` measures `afplay` at start-up, or give seconds |
| `--voice`, `--voice-rate` | auto, 330 | macOS voice and speech rate (words per minute) |
| `--lock-slip` | 0.25 | Lock-up threshold, wheel slip ratio |
| `--record DIR` | off | Record the session |
| `--refs FILE` | `~/.apexmind/refs.json` | Best-lap references per track, so calls work from lap 1 |
| `--hotspots FILE` | `~/.apexmind/hotspots.json` | Where track limits were violated, per track |
| `--llm on/off/auto` | auto | Rephrase advice with a local [Ollama](https://ollama.com) model (adds latency) |

## How it works

- `telemetry/` decodes the UDP packets (header, car telemetry, motion, motion-ex, lap data, session, event, car damage) for formats 2022 to 2026.
- `engineer/analyzer.py` is the core: it learns brake, turn-in and throttle points along the **lap distance** (not world coordinates, which makes beeps arrive late), builds the reference from your best valid laps and turns it into the call script. Triggers fire on a timer, independent of the frame interval.
- `engineer/` also holds the coaches for lock-ups, traction, spins, tyres and damage, the live delta, track limits and the lap debrief; `voice.py` renders short clips with `say` so playback latency is constant and known.
- `replay.py` and `report.py` run the whole coach offline over a recording; `webserver.py` and `webui/` are the dashboard.

## Development

```
python3 -m venv .venv && . .venv/bin/activate
pip install -e '.[dev]'
ruff check src tests tools
pytest -q
python tools/build_site.py && python -m http.server -d _site 8000
```

The tests drive the analyzer with a synthetic car at realistic frame rates (`tests/sim.py`), build UDP packets byte by byte (`tests/builders.py`), run a real `apexmind listen` process against a UDP stream, and replay a bundled real session (`src/apexmind/webui/sample.csv`).

## Contributing

Issues and pull requests are welcome, especially recordings from other tracks, cars and game versions. See [CONTRIBUTING.md](CONTRIBUTING.md).

## License

ApexMind is free software licensed under the **GNU Affero General Public License v3.0 or later** (see [LICENSE](LICENSE)). In short: you may use, study, change and share it, but if you distribute a modified version, or let people use one over a network, you must make your source available under the same license.

ApexMind is an independent project and is not affiliated with or endorsed by Electronic Arts, Codemasters or Formula 1. F1 and related marks belong to their owners.
