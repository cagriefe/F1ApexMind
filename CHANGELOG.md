# Changelog

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
