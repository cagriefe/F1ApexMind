# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) ApexMind contributors
"""Track memory: what ApexMind has learned about a circuit survives sessions, restarts and days.

One small JSON file per track under ``<dir>/tracks/<track id>.json``. Every completed lap is merged
into it (best laps win, nothing is thrown away by a weaker session) and the file is replaced
atomically, so a crash or a closed laptop can never leave a half-written file.
"""

from __future__ import annotations

import json
import logging
import os
import time
from pathlib import Path

from .engineer.analyzer import merge_refs

_LOG = logging.getLogger("apexmind")

VERSION = 1
LAYOUT_TOLERANCE_M = 15.0

# F1 25 track ids (m_trackId) for readable output; unknown ids are shown as "track N".
TRACK_NAMES = {
    0: "Melbourne", 2: "Shanghai", 3: "Sakhir", 4: "Catalunya", 5: "Monaco", 6: "Montreal",
    7: "Silverstone", 9: "Hungaroring", 10: "Spa", 11: "Monza", 12: "Singapore", 13: "Suzuka",
    14: "Abu Dhabi", 15: "Austin", 16: "Interlagos", 17: "Red Bull Ring", 19: "Mexico City",
    20: "Baku", 26: "Zandvoort", 27: "Imola", 29: "Jeddah", 30: "Miami", 31: "Las Vegas",
    32: "Lusail",
}


def track_name(track_id: int | str) -> str:
    try:
        return TRACK_NAMES.get(int(track_id), f"track {track_id}")
    except (TypeError, ValueError):
        return f"track {track_id}"


class TrackMemory:
    def __init__(self, root: str | Path):
        self.root = Path(root).expanduser()

    # ----- files -----

    def _file(self, track_id: int | str) -> Path:
        return self.root / "tracks" / f"{int(track_id)}.json"

    @staticmethod
    def _read(path: Path) -> dict | None:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        return data if isinstance(data, dict) else None

    def _legacy(self, track_id: int | str) -> dict | None:
        """Pre-0.9.3 single-file store (`refs.json`, keyed by track id)."""
        data = self._read(self.root / "refs.json")
        entry = data.get(str(int(track_id))) if data else None
        return entry if isinstance(entry, dict) else None

    # ----- API -----

    def load(self, track_id: int | str, track_length: float | None = None) -> dict | None:
        """References of this track, or None if unknown or the layout (length) does not match."""
        data = self._read(self._file(track_id)) or self._legacy(track_id)
        if not data or not data.get("zones"):
            return None
        if track_length and abs((data.get("track_length") or 0) - track_length) > LAYOUT_TOLERANCE_M:
            return None
        return data

    def save(self, track_id: int | str, refs: dict) -> bool:
        """Merge ``refs`` into the stored memory of the track and write it atomically."""
        if not refs or not refs.get("zones"):
            return False
        old = self.load(track_id)
        same_layout = old is not None and (
            abs((old.get("track_length") or 0) - (refs.get("track_length") or 0)) <= LAYOUT_TOLERANCE_M
        )
        merged = merge_refs([old, refs] if same_layout else [refs])
        merged["track_length"] = refs.get("track_length") or (old or {}).get("track_length")
        merged.update({"version": VERSION, "track_id": int(track_id), "saved_at": int(time.time())})
        path = self._file(track_id)
        tmp = path.with_suffix(".tmp")
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp.write_text(json.dumps(merged, separators=(",", ":")), encoding="utf-8")
            os.replace(tmp, path)
        except OSError:
            _LOG.debug("could not save track memory", exc_info=True)
            return False
        return True

    def tracks(self) -> list[dict]:
        """One summary per remembered track (including tracks only present in the legacy file)."""
        found: dict[int, dict] = {}
        legacy = self._read(self.root / "refs.json") or {}
        for key, entry in legacy.items():
            if key.isdigit() and isinstance(entry, dict) and entry.get("zones"):
                found[int(key)] = self._summary(int(key), entry)
        for path in sorted((self.root / "tracks").glob("*.json")):
            data = self._read(path)
            if data and data.get("zones") and path.stem.isdigit():
                found[int(path.stem)] = self._summary(int(path.stem), data)
        return [found[k] for k in sorted(found)]

    @staticmethod
    def _summary(track_id: int, data: dict) -> dict:
        laps = [lap for z in data["zones"] for lap in z.get("laps", [])]
        best = [lap["ms"] for lap in laps if lap.get("rank", 0) == 0]
        return {
            "track_id": track_id, "name": track_name(track_id), "length": data.get("track_length"),
            "corners": len(data["zones"]), "saved_at": data.get("saved_at"),
            "best_ms": int(min(best)) if best else None,
        }

    def forget(self, track_id: int | str) -> bool:
        removed = False
        try:
            self._file(track_id).unlink()
            removed = True
        except OSError:
            pass
        legacy_path = self.root / "refs.json"
        legacy = self._read(legacy_path)
        if legacy and str(int(track_id)) in legacy:
            del legacy[str(int(track_id))]
            legacy_path.write_text(json.dumps(legacy, separators=(",", ":")), encoding="utf-8")
            removed = True
        return removed
