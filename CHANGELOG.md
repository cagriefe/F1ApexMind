# Changelog

## 0.9.3
- Track memory: every finished lap (clean, invalid or untimed) is merged into a per-track file, so calls work from lap 1 in a later race, restart or day. Before, only clean timed laps were saved, so practice with track-limit violations or flashbacks left nothing behind.
- `apexmind memory` lists or forgets remembered tracks; `--refs FILE` became `--memory DIR`.
- Corners braked close together (chicanes) are no longer merged into one when references are combined.

## 0.9.2
- A flashback no longer wipes the corners learned so far (it used to, so drivers who flash back after every mistake never got a call).
- Calls start after one reference lap instead of two.
- Pauses, teleports and flashbacks no longer produce bursts of `call not spoken` reports.
- The same call is never spoken twice within a second.

## 0.9.1
- Pedal calls are never silently lost: clips no longer queue behind each other, and every call that cannot be spoken is reported with its reason (`call not spoken: ...`).
- After-the-fact sentences stay text-only while pedal calls are on.
- `--open` opens the web dashboard in your browser; `start.command` uses it.

## 0.9.0
- First public release: AGPL-3.0-or-later, English throughout.
- Web dashboard: live view and in-browser recording analysis (Pyodide), static hosting on GitHub Pages.
- Spoken script `Brake 90, 50, 0, Gas 30, 60, Full` with fixed mid-points, spaced so calls never overlap.
- Best-lap references and track-limit spots persist between sessions.
- Fix: throttle calls of corners in the second half of the lap were cancelled by lap-distance wrap-around.
