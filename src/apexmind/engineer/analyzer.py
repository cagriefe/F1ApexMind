# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) ApexMind contributors
"""Mistake detection engine: low latency, based on lap distance.

Why lap distance?
  In world coordinates the straight-line distance to a corner's braking point looks shorter than the
  real distance because the track curves around the corner. That means "beep after
  the corner". With LapData's ``m_lapDistance`` the distance ahead is measured along the
  track, so the beep fires exactly on time.

Warnings:
- early/late braking : brake start is far from the personal median for this corner
- too hard braking   : brake depth is harder than the median
- early/late apex    : steering point at corner entry
- early/late throttle: throttle application point at corner exit
- cue                 : short beep ``cue_lead_s`` seconds before the learned point

The baseline is the median of the last 7 laps; a single bad lap does not corrupt it.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from collections.abc import Callable

from ..telemetry.packets import CarMotion, CarTelemetry

_LOG = logging.getLogger(__name__)
Advice = Callable[[str, str], None]


# ---- small helpers ----------------------------------------------------------

def _median(values: list[float]) -> float:
    s = sorted(values)
    n = len(s)
    if n == 0:
        return 0.0
    mid = n // 2
    return s[mid] if n % 2 else (s[mid - 1] + s[mid]) / 2.0


def _median_pos(samples: list[tuple[float, float]]) -> tuple[float, float] | None:
    if not samples:
        return None
    return (_median([p[0] for p in samples]), _median([p[1] for p in samples]))


def _circular_median(samples: list[float], track_length: float | None) -> float | None:
    if not samples:
        return None
    if not track_length or track_length <= 0:
        return _median(samples)
    base = samples[0]
    half = track_length / 2.0
    unwrapped = [base + ((v - base + half) % track_length) - half for v in samples]
    return _median(unwrapped) % track_length


def _signed_delta(current: float, baseline: float, track_length: float | None) -> float:
    """Signed difference along the track; + ahead (late), - behind (early)."""
    if track_length and track_length > 0:
        half = track_length / 2.0
        return ((current - baseline + half) % track_length) - half
    return current - baseline


def _ahead(target: float, current: float, track_length: float | None) -> float:
    """Distance of the target still ahead along the track (0..track_length)."""
    if track_length and track_length > 0:
        return (target - current) % track_length
    return target - current


def _ahead_signed(target: float, current: float, track_length: float | None) -> float:
    """Signed distance to the target: + target ahead, - target passed (-L/2..L/2)."""
    if track_length and track_length > 0:
        half = track_length / 2.0
        return ((target - current + half) % track_length) - half
    return target - current


_UNRANKED = float("inf")


def _jsonable(v):
    if isinstance(v, (list, tuple)):
        return [_jsonable(x) for x in v]
    return v


def merge_refs(exports: list[dict], radius_m: float = 60.0, keep: int = 3) -> dict:
    """Merge the references of several sessions: the fastest ``keep`` laps per corner survive."""
    out: dict = {"track_length": None, "zones": []}
    for ex in exports:
        if not ex or not ex.get("zones"):
            continue
        tl = ex.get("track_length")
        out["track_length"] = out["track_length"] or tl
        for zd in ex["zones"]:
            pts = [lap["sample"]["brake_lap"] for lap in zd["laps"] if "brake_lap" in lap["sample"]]
            pos = _circular_median(pts, tl)
            if pos is None:
                continue
            target = next(
                (z for z in out["zones"]
                 if abs(_signed_delta(pos, z["pos"], tl)) <= radius_m), None,
            )
            if target is None:
                target = {"pos": pos, "laps": []}
                out["zones"].append(target)
            target["laps"].extend(zd["laps"])
    for z in out["zones"]:
        z["laps"].sort(key=lambda lap: lap["ms"])
        del z["laps"][keep:]
        z.pop("pos", None)
    return out


def _dist(a: tuple[float, float], b: tuple[float, float]) -> float:
    return math.hypot(a[0] - b[0], a[1] - b[1])


def _dot(v: tuple[float, float], d: tuple[float, float]) -> float:
    return v[0] * d[0] + v[1] * d[1]


def _unit_dir(vx: float, vz: float) -> tuple[float, float]:
    n = math.hypot(vx, vz)
    if n < 1e-3:
        return (1.0, 0.0)
    return (vx / n, vz / n)


def _clamp(value: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, value))


# ---- internal data structures -----------------------------------------------

@dataclass(slots=True)
class _Zone:
    """A braking point and the personal samples that belong to that corner."""

    brake_world: list[tuple[float, float]] = field(default_factory=list)
    brake_lap: list[float] = field(default_factory=list)
    depth: list[float] = field(default_factory=list)
    turnin_world: list[tuple[float, float]] = field(default_factory=list)
    turnin_lap: list[float] = field(default_factory=list)
    throttle_world: list[tuple[float, float]] = field(default_factory=list)
    throttle_lap: list[float] = field(default_factory=list)
    # throttle profile: where the pedal first lifted, where it reached 98%, and the hold percentage between
    thr_start_lap: list[float] = field(default_factory=list)
    thr_full_lap: list[float] = field(default_factory=list)
    thr_hold: list[float] = field(default_factory=list)
    # samples taken from completed laps: (ranking key, sample dict)
    laps: list[tuple[tuple[int, float], dict]] = field(default_factory=list)
    # reference pedal profile [(distance from brake onset m, brake, throttle, speed m/s)] and call script
    profile: list[tuple[float, ...]] = field(default_factory=list)
    # (distance, kind, text, |steering| reference)
    script: list[tuple[float, str, str, float]] = field(default_factory=list)
    lock_frac: float = 0.0     # fraction of reference laps in which a wheel locked at this corner

    @property
    def n(self) -> int:
        return len(self.depth)

    @property
    def pos(self) -> tuple[float, float] | None:
        return _median_pos(self.brake_world)

    @property
    def depth_avg(self) -> float:
        return _median(self.depth)

    @property
    def turnin_pos(self) -> tuple[float, float] | None:
        return _median_pos(self.turnin_world)

    @property
    def throttle_pos(self) -> tuple[float, float] | None:
        return _median_pos(self.throttle_world)


@dataclass(slots=True)
class _OpenZone:
    zone_id: int
    start_pos: tuple[float, float]
    start_lap_dist: float | None
    direction: tuple[float, float]
    entry_speed: float
    max_depth: float
    locked: bool = False


@dataclass
class AnalyzerStats:
    zones: int = 0
    events: int = 0
    cues: int = 0
    calls: int = 0


class Analyzer:
    def __init__(
        self,
        speak_cb: Advice,
        *,
        zone_match_m: float = 50.0,
        brake_enter: float = 0.12,
        brake_exit: float = 0.03,
        depth_min: float = 0.85,
        depth_delta: float = 0.10,
        brake_delta_m: float = 20.0,
        turnin_delta_m: float = 18.0,
        throttle_delta_m: float = 18.0,
        min_throttle: float = 0.55,
        throttle_confirm: int = 4,
        min_entry_speed: float = 45.0,
        learn_n: int = 1,
        cooldown_s: float = 8.0,
        # 0 = beep exactly on the brake/throttle point; 1.5 = 1.5 s before the point.
        cue_lead_s: float = 0.0,
        cue_min_lead_m: float = 0.0,
        cue_max_lead_m: float = 150.0,
        world_lateral_m: float = 25.0,
        sample_window: int = 7,
        # beep/voice latency compensation: beeps are played this many seconds EARLY
        cue_latency_s: float = 0.0,
        # spoken calls ("Brake 95", "Throttle 60", "Hundred now")
        calls: bool = False,
        speech_rate_wpm: int = 300,
        speech_latency_s: float = 0.35,
        call_margin_s: float = 0.15,
        call_gap_s: float = 0.30,
        # reference: median of the best N valid laps
        ref_laps: int = 3,
        # durations (s) of the pre-rendered voice clips, or None; estimated if absent
        phrase_duration: Callable[[str], float | None] | None = None,
        playback_latency_s: float = 0.10,
        call_min_depth: float = 0.35,
        # timer that makes the trigger moment independent of the frame interval: scheduler(delay_s, function)
        scheduler: Callable[[float, Callable[[], None]], None] | None = None,
        sched_horizon_s: float = 0.6,
        # a corner must be confirmed by at least this many laps before calls (event/spin corner noise)
        call_min_laps: int = 2,
        # throttle/full calls are state-triggered: steering may be this much more open than the reference
        steer_margin: float = 0.12,
        gas_window_after_m: float = 70.0,
        # the throttle EARLY/LATE feedback is noisy on real data: disabled while calls are on
        posthoc_throttle: bool = True,
    ):
        self.speak = speak_cb
        self.zone_match_m = zone_match_m
        self.brake_enter = brake_enter
        self.brake_exit = brake_exit
        self.depth_min = depth_min
        self.depth_delta = depth_delta
        self.brake_delta_m = brake_delta_m
        self.turnin_delta_m = turnin_delta_m
        self.throttle_delta_m = throttle_delta_m
        self.min_throttle = min_throttle
        self.throttle_confirm = throttle_confirm
        self.min_entry_speed = min_entry_speed
        self.learn_n = learn_n
        self.cooldown_s = cooldown_s
        self.cue_lead_s = cue_lead_s
        self.cue_min_lead_m = cue_min_lead_m
        self.cue_max_lead_m = cue_max_lead_m
        self.world_lateral_m = world_lateral_m
        self.sample_window = sample_window
        self.cue_latency_s = cue_latency_s
        self.calls = calls
        self.speech_rate_wpm = max(120, speech_rate_wpm)
        self.speech_latency_s = speech_latency_s
        self.call_margin_s = call_margin_s
        self.call_gap_s = call_gap_s       # silence between two consecutive calls
        self.ref_laps = max(1, ref_laps)
        self.phrase_duration = phrase_duration
        self.playback_latency_s = playback_latency_s
        self.call_min_depth = call_min_depth
        self.scheduler = scheduler
        self.sched_horizon_s = sched_horizon_s
        self.call_min_laps = call_min_laps
        self.steer_margin = steer_margin
        self.gas_window_after_m = gas_window_after_m
        self.posthoc_throttle = posthoc_throttle
        self.rear_slip = 0.0
        self._call_done: dict[int, int] = {}
        # diagnostics: records such as ("cue_brake", "z3 ahead=12.3")
        self.trace: Callable[[str, str], None] | None = None
        self.slide_ratio = 0.0       # the CLI updates this from SpinCoach every frame
        self.slide_block = 0.18      # at this slide, "Easy" replaces the throttle-increase calls
        self.rec: dict | None = None
        self.on_commit: Callable[[], None] | None = None   # reference changed (to save it)
        self._epoch = 0
        self._prev: tuple[float | None, float, float, float] | None = None
        self._pf: tuple[float | None, float, float, float] | None = None

        self.zones: list[_Zone] = []
        self.open: _OpenZone | None = None
        self.pending_turnin: int | None = None
        self.pending_throttle: int | None = None
        self.thr_confirm = 0
        self.last_speak: dict[str, float] = {}
        self.cue_last: dict[str, float] = {}
        self.cued_this_lap: set[str] = set()
        self.cue_brake: Callable[[], None] | None = None
        self.cue_throttle: Callable[[], None] | None = None
        self.sim_time = 0.0
        self.suppressed = False
        self.stats = AnalyzerStats()

        # end-of-lap info (the CLI updates it every frame)
        self._frame_dt = 1.0 / 60.0
        self._last_sim_time: float | None = None
        self.last_lap_ms: int | None = None
        self.lap_invalid = False
        self._lap_invalid_seen = False
        # samples of the current lap are folded into the reference when the lap ends (if valid)
        self.stage: dict[int, dict] = {}
        self.thr_prof: dict | None = None
        self._order = 0

        # lap / track context
        self.track_length: float | None = None
        self.lap_distance: float | None = None
        self.lap_num: int | None = None
        self._dir: tuple[float, float] = (1.0, 0.0)

    # ----- lifecycle -----

    def reset(self) -> None:
        self.zones.clear()
        self.open = None
        self.pending_turnin = None
        self.pending_throttle = None
        self.thr_confirm = 0
        self.last_speak.clear()
        self.cue_last.clear()
        self.cued_this_lap.clear()
        self.lap_distance = None
        self.lap_num = None
        self.stats = AnalyzerStats()
        self.stage.clear()
        self.thr_prof = None
        self._lap_invalid_seen = False
        self.last_lap_ms = None
        self._epoch += 1
        self._prev = self._pf = None
        self.rec = None
        self._call_done.clear()

    def set_track_length(self, length: float | None) -> None:
        self.track_length = float(length) if length and length > 0 else None

    def set_cues(self, cb_brake: Callable[[], None], cb_throttle: Callable[[], None] | None) -> None:
        """The CLI wires Voice.ding functions in here."""
        self.cue_brake = cb_brake
        self.cue_throttle = cb_throttle

    # ----- ana girdi -----

    def on_frame(
        self,
        t: CarTelemetry,
        m: CarMotion,
        sim_time: float,
        *,
        lap_distance: float | None = None,
        lap_num: int | None = None,
    ) -> None:
        self._pf = self._prev
        try:
            self._on_frame(t, m, sim_time, lap_distance=lap_distance, lap_num=lap_num)
        finally:
            self._prev = (lap_distance, t.brake, t.throttle, sim_time)

    def _on_frame(
        self,
        t: CarTelemetry,
        m: CarMotion,
        sim_time: float,
        *,
        lap_distance: float | None = None,
        lap_num: int | None = None,
    ) -> None:
        if self._last_sim_time is not None:
            fdt = sim_time - self._last_sim_time
            if 0.001 < fdt < 0.25:  # frame interval (EMA); ignore flashbacks/pauses
                self._frame_dt = 0.9 * self._frame_dt + 0.1 * fdt
        self._last_sim_time = sim_time
        self.sim_time = sim_time

        if lap_num is not None:
            if self.lap_num is not None and lap_num != self.lap_num:
                self._on_new_lap(forward=lap_num > self.lap_num)
                self._lap_invalid_seen = False
                self._pf = None
            self.lap_num = lap_num
        if self.lap_invalid:
            self._lap_invalid_seen = True
        self.lap_distance = lap_distance
        self._dir = _unit_dir(m.vx, m.vz)

        if self.suppressed:
            # A zone left open during a spin/pit must not produce a wrong sample on return.
            self.open = None
            self._clear_pending()
            self.thr_prof = None
            self.rec = None
            return

        pos = (m.x, m.z)

        # Pre-corner ding: while approaching the brake/throttle point
        if self.cue_brake is not None:
            self._check_ahead_cues(pos, m)
        if self.calls:
            self._check_calls(t, m)

        if self.open is None:
            if t.brake >= self.brake_enter and t.speed_kmh >= self.min_entry_speed:
                self._open_zone(pos, t, m)
        else:
            if t.brake > self.open.max_depth:
                self.open.max_depth = t.brake
            if t.brake <= self.brake_exit:
                self._close_zone()

        # Apex / turn-in detection (after braking ends)
        if self.pending_turnin is not None and self.open is None:
            if t.brake < 0.3 and (abs(t.steer) > 0.25 or abs(m.g_lat) > 0.8):
                self._handle_turnin(pos)

        self._track_throttle_profile(t)
        self._record_profile(t, m)

        # Throttle point detection
        if self.pending_throttle is not None and self.open is None:
            if t.throttle >= self.min_throttle:
                self.thr_confirm += 1
                if self.thr_confirm >= self.throttle_confirm:
                    self._handle_throttle_on(pos)
            elif t.throttle < self.min_throttle * 0.5:
                self.thr_confirm = 0

        if self.open is not None:  # new brake -> older pending ones become invalid
            self._clear_pending()

    def export_refs(self) -> dict:
        """Best (timed, valid) laps' references in a JSON-friendly form."""
        zones = []
        for z in self.zones:
            ranked = [(k, s) for k, s in z.laps if k[0] == 0]
            if ranked:
                zones.append({"laps": [{"ms": k[1], "sample": _jsonable(s)} for k, s in ranked]})
        return {"track_length": self.track_length, "zones": zones}

    def import_refs(self, data: dict | None) -> int:
        """Load references from earlier sessions: calls work from the first lap."""
        if not data or self.zones or not self.track_length:
            return 0
        if abs((data.get("track_length") or 0) - self.track_length) > 15:
            return 0
        n = 0
        for zd in data.get("zones", []):
            z = _Zone()
            for item in zd.get("laps", []):
                sample = dict(item["sample"])
                if "profile" in sample:
                    sample["profile"] = [tuple(f) for f in sample["profile"]]
                for k in ("brake_world", "turnin_world", "throttle_world"):
                    if k in sample:
                        sample[k] = tuple(sample[k])
                z.laps.append(((0, float(item["ms"])), sample))
            z.laps.sort(key=lambda e: e[0])
            del z.laps[self.ref_laps:]
            if z.laps:
                self._rebuild_reference(z)
                self.zones.append(z)
                n += 1
        return n

    def note_lockup(self) -> None:
        """A wheel locked while braking: this corner's reference is marked as 'locked brake'."""
        if self.open is not None:
            self.open.locked = True

    def _on_new_lap(self, forward: bool = True) -> None:
        # Samples of the finished lap: folded in if valid. Going backwards = flashback/restart.
        if forward:
            self._commit_stage(self.last_lap_ms or None, not self._lap_invalid_seen)
        else:
            self.stage.clear()
            self.thr_prof = None
            self.rec = None
        self.cued_this_lap.clear()
        self._call_done.clear()
        self.open = None
        self._clear_pending()

    # ----- per-lap learning (reference = best N valid laps) -----

    def _staging(self) -> bool:
        """If the lap number is known the samples are processed at lap end; otherwise immediately."""
        return self.lap_num is not None

    _FIELDS = (
        "brake_world", "brake_lap", "depth", "turnin_world", "turnin_lap",
        "throttle_world", "throttle_lap", "thr_start_lap", "thr_full_lap", "thr_hold",
    )

    def _onset_dist(self, cur: float, threshold: float, idx: int) -> float | None:
        """Distance at the MOMENT the pedal crossed the threshold (interpolated with the previous frame).

        Frames can be 50-150 ms apart (4-12 m at 300 km/h); taking the first frame above the threshold
        records the learned point systematically late.
        """
        ld = self.lap_distance
        pf = self._pf
        tl = self.track_length
        if ld is None or pf is None or pf[0] is None or not tl:
            return ld
        prev_v = pf[idx]
        if self.sim_time - pf[3] > 0.35 or not (prev_v < threshold <= cur):
            return ld
        fwd = _signed_delta(ld, pf[0], tl)
        if fwd < 0.0 or fwd > 80.0:
            return ld
        frac = (threshold - prev_v) / (cur - prev_v)
        return (pf[0] + frac * fwd) % tl

    def _commit_stage(self, lap_ms: int | None, valid: bool) -> None:
        stage, self.stage = self.stage, {}
        for zid, sample in stage.items():
            if zid >= len(self.zones):
                continue
            z = self.zones[zid]
            self._order += 1
            if not valid:
                # Invalid lap: becomes a reference only if there is no valid lap at all (no phantom corners).
                key = (2, -float(self._order))
            elif lap_ms is None:
                if z.laps:
                    self._gate_unranked(z, sample)
                key = (1, -float(self._order))   # untimed lap: newest first
            else:
                key = (0, float(lap_ms))
            z.laps.append((key, sample))
            z.laps.sort(key=lambda e: e[0])
            del z.laps[self.ref_laps:]
            self._rebuild_reference(z)
        if stage and valid and lap_ms and self.on_commit:
            self.on_commit()

    def _rebuild_reference(self, z: _Zone) -> None:
        for name in self._FIELDS:
            getattr(z, name).clear()
        usable = [e for e in z.laps if e[0][0] < 2] or z.laps   # only valid laps if there are any
        for _key, sample in usable:
            for name in self._FIELDS:
                if name in sample and sample[name] is not None:
                    getattr(z, name).append(sample[name])
        z.profile = self._aggregate_profiles([s["profile"] for _k, s in usable if "profile" in s])
        z.lock_frac = (sum(1 for _k, s in usable if s.get("lock")) / len(usable)) if usable else 0.0
        z.script = self._build_script(z.profile, locked=z.lock_frac >= 0.5)

    # ----- reference pedal profile and call script -----

    @staticmethod
    def _aggregate_profiles(profiles: list[list[tuple[float, ...]]], step: float = 4.0):
        """Resample the laps' profiles into 4 m bins and take the median per bin."""
        profiles = [p for p in profiles if len(p) >= 6]
        if not profiles:
            return []
        span = min(p[-1][0] for p in profiles)
        out: list[tuple[float, float, float, float]] = []
        x = 0.0
        while x <= span:
            vals: list[tuple[float, ...]] = []
            for p in profiles:
                for k in range(1, len(p)):
                    if p[k][0] >= x:
                        a, b = p[k - 1], p[k]
                        w = 0.0 if b[0] == a[0] else (x - a[0]) / (b[0] - a[0])
                        vals.append(tuple(a[j] + w * (b[j] - a[j]) for j in (1, 2, 3, 4)))
                        break
            if vals:
                out.append((x, _median([v[0] for v in vals]), _median([v[1] for v in vals]),
                            _median([v[2] for v in vals]), _median([v[3] for v in vals])))
            x += step
        if out and all(p[-1][2] >= 0.97 for p in profiles) and out[-1][0] < span:
            out.append((span, 0.0, 1.0, out[-1][3], out[-1][4]))      # last full-throttle point
        return out

    def _clip_s(self, text: str) -> float:
        d = self.phrase_duration(text) if self.phrase_duration else None
        return d if d is not None else max(0.45, len(text.split()) * 60.0 / self.speech_rate_wpm)

    def _build_script(
        self, prof: list[tuple[float, ...]], locked: bool = False
    ) -> list[tuple[float, str, str, float]]:
        """Spoken script for one corner: ``Brake 90``, ``50``, ``0``, ``Gas 30``, ``60``, ``Full``.

        The calls are chosen by TIME, not by pedal change: consecutive target moments are at least one
        clip length plus a gap apart, so a call never has to overlap (and kill) the previous one.
        Intermediate numbers are optional; the first call of each phase, the release (``0``) and
        ``Full`` are kept whenever physically possible.
        """
        if len(prof) < 6:
            return []
        xs = [p[0] for p in prof]
        n = len(prof)

        def smooth(a: list[float]) -> list[float]:
            return [(a[max(0, i - 1)] + a[i] + a[min(n - 1, i + 1)]) / 3.0 for i in range(n)]

        br_raw = [p[1] for p in prof]
        th_raw = [p[2] for p in prof]
        br = smooth(br_raw)
        th = smooth(th_raw)
        sp = [max(8.0, p[3]) for p in prof]
        st_ref = smooth([p[4] for p in prof])
        ts = [0.0]                                   # time (s) since brake onset at every bin
        for i in range(1, n):
            ts.append(ts[-1] + (xs[i] - xs[i - 1]) / (0.5 * (sp[i] + sp[i - 1])))

        def idx_at(t: float) -> int:
            return min(n - 1, next((i for i in range(n) if ts[i] >= t), n - 1))

        def d_at(t: float) -> float:
            i = idx_at(t)
            if i == 0 or ts[i] == ts[i - 1]:
                return xs[i]
            f = (t - ts[i - 1]) / (ts[i] - ts[i - 1])
            return xs[i - 1] + f * (xs[i] - xs[i - 1])

        gap = self.call_gap_s
        calls: list[tuple[float, str, str, int, float]] = []   # (d, kind, text, priority, |steering|)
        times: list[float] = []                                 # target time of every call

        def room(t: float, text: str) -> bool:
            return not times or t - times[-1] >= self._clip_s(text) + gap

        def add(t: float, kind: str, text: str, prio: int) -> None:
            calls.append((d_at(t), kind, text, prio, st_ref[idx_at(t)]))
            times.append(t)

        peak = max(br_raw[1:] or br_raw)          # the first frame is synthetic (onset threshold)
        ipk = br.index(max(br))
        braking = peak >= self.call_min_depth
        resume = 0                                 # first bin the throttle phase may start from
        if braking:
            if locked:
                peak = min(peak, 0.85)             # if it locks up every lap, lower the target level
            level = self._pct10(peak)
            add(0.0, "brake", f"Brake {level}", 4)
            i_rel = next((i for i in range(ipk, n) if br[i] < 0.05), None)
            resume = i_rel if i_rel is not None else n
            t_rel = ts[i_rel] if i_rel is not None else ts[-1]
            if level >= 70:                        # a deep stop: one fixed mid-point call, "50"
                i50 = next((i for i in range(ipk, n) if br[i] <= 0.5), None)
                if i50 is not None and ts[i50] <= t_rel - 0.6 and room(ts[i50], "50"):
                    add(ts[i50], "ease", "50", 2)
            if i_rel is not None and room(t_rel, "0"):
                add(t_rel, "ease", "0", 3)
        # throttle phase
        i_g = next((i for i in range(resume, n) if br[i] <= 0.05 and th_raw[i] >= 0.10), None)
        if i_g is not None:
            t_g = ts[i_g]
            ahead = th[min(n - 1, i_g + 1)]
            if ahead >= 0.95:
                if not room(t_g, "Full") and calls and calls[-1][2] == "0":
                    calls.pop()
                    times.pop()
                add(t_g, "full", "Full", 4)
            else:
                lvl_g = 30 if ahead < 0.45 else 60            # fixed throttle steps: Gas 30 / Gas 60 / Full
                if not room(t_g, f"Gas {lvl_g}") and calls and calls[-1][2] == "0":
                    calls.pop()                    # release and throttle are too close: "Gas" implies "0"
                    times.pop()
                add(t_g, "gas", f"Gas {lvl_g}", 4)
                i_full = next((j for j in range(i_g + 1, n) if th_raw[j] >= 0.97 and br[j] <= 0.05), None)
                t_full = ts[i_full] if i_full is not None else None
                if lvl_g < 60:
                    i60 = next((j for j in range(i_g + 1, n) if th[j] >= 0.6), None)
                    late = t_full is not None and ts[i60 or 0] > t_full - 0.4 if i60 is not None else True
                    if not late and room(ts[i60], "60"):
                        add(ts[i60], "gas", "60", 2)
                if t_full is not None:
                    if not room(t_full, "Full") and calls[-1][1] == "gas" and calls[-1][2] == "60":
                        calls.pop()                # "Full" matters more than the "60" before it
                        times.pop()
                    if room(t_full, "Full"):
                        add(t_full, "full", "Full", 4)

        # safety net: if two calls still collide, the less important one goes
        def start_times(items):
            out, t = [], 0.0
            prev_d = 0.0
            for d, _k, text, _p, _s in items:
                seg_speed = sp[min(n - 1, max(0, int(round(d / max(1e-6, xs[1] - xs[0])))))]
                t += (d - prev_d) / seg_speed
                prev_d = d
                out.append(t - self._speech_lead_s(text))
            return out

        calls.sort(key=lambda c: c[0])
        while len(calls) > 1:
            st = start_times(calls)
            clash = next((i for i in range(len(calls) - 1)
                          if st[i] + self._clip_s(calls[i][2]) + 0.04 > st[i + 1]), None)
            if clash is None:
                break
            a_, b_ = calls[clash], calls[clash + 1]
            calls.pop(clash if a_[3] < b_[3] else clash + 1)
        return [(d, k, text, sref) for d, k, text, _p, sref in calls]

    def _zone_calls_script(self, z: _Zone) -> list[tuple[str, float, str, str, float | None]]:
        tgt = _circular_median(z.brake_lap, self.track_length) if z.brake_lap else None
        if tgt is None or not z.script:
            return []
        tl = self.track_length or 0.0
        return [
            (f"call_{idx}", (tgt + d) % tl if tl else tgt + d, text, kind, sref)
            for idx, (d, kind, text, sref) in enumerate(z.script)
        ]

    def _gate_unranked(self, z: _Zone, sample: dict) -> None:
        """Drop outliers on a lap with unknown duration (legacy behaviour)."""
        tl = self.track_length
        if "brake_lap" in sample and z.brake_lap:
            ref = _circular_median(z.brake_lap, tl)
            if ref is not None and abs(_signed_delta(sample["brake_lap"], ref, tl)) >= self.brake_delta_m:
                sample.pop("brake_lap", None)
                sample.pop("brake_world", None)
        if "depth" in sample and z.depth and sample["depth"] - z.depth_avg >= self.depth_delta:
            sample.pop("depth", None)
        if "turnin_lap" in sample and z.turnin_lap:
            ref = _circular_median(z.turnin_lap, tl)
            if ref is not None and abs(_signed_delta(sample["turnin_lap"], ref, tl)) >= self.turnin_delta_m:
                sample.pop("turnin_lap", None)
                sample.pop("turnin_world", None)
        if "throttle_lap" in sample and z.throttle_lap:
            ref = _circular_median(z.throttle_lap, tl)
            dev = None if ref is None else abs(_signed_delta(sample["throttle_lap"], ref, tl))
            if dev is not None and dev >= self.throttle_delta_m:
                for k in ("throttle_lap", "throttle_world", "thr_start_lap", "thr_full_lap", "thr_hold"):
                    sample.pop(k, None)

    # ----- throttle profile: where the pedal lifted, where it hit 100%, how much was held in between -----

    def _track_throttle_profile(self, t: CarTelemetry) -> None:
        p = self.thr_prof
        if p is None:
            return
        if not self._use_lap_mode():
            self.thr_prof = None
            return
        if t.brake > 0.05 or self.open is not None:
            self._finish_throttle_profile()
            return
        ld = self.lap_distance
        assert ld is not None and self.track_length
        travelled = (ld - p["origin"]) % self.track_length
        if p["start"] is None:
            if t.throttle >= 0.15:
                p["start"] = self._onset_dist(t.throttle, 0.15, 2)
            elif travelled > 600.0:
                self.thr_prof = None
            return
        if t.throttle >= 0.98:
            p["full"] = self._onset_dist(t.throttle, 0.98, 2)
            self._finish_throttle_profile()
            return
        p["samples"].append(t.throttle)
        if travelled > 600.0:
            self._finish_throttle_profile()

    def _record_profile(self, t: CarTelemetry, m: CarMotion) -> None:
        r = self.rec
        if r is None:
            return
        if not self._use_lap_mode() or self.lap_distance is None or not self.track_length:
            self.rec = None
            return
        fwd = (self.lap_distance - r["origin"]) % self.track_length
        if fwd > 700.0:
            self._finish_rec()
            return
        frames = r["frames"]
        if fwd > frames[-1][0] + 1.0:
            frames.append((fwd, t.brake, t.throttle, math.hypot(m.vx, m.vz), abs(t.steer)))

    def _finish_rec(self) -> None:
        r, self.rec = self.rec, None
        if r is None or r.get("sample") is None or len(r["frames"]) < 6:
            return
        r["sample"]["profile"] = r["frames"]
        zid = r.get("zid", -1)
        if 0 <= zid < len(self.zones):
            self._rebuild_reference(self.zones[zid])

    def _finish_throttle_profile(self) -> None:
        p, self.thr_prof = self.thr_prof, None
        if p is not None and p.get("full") is not None and self.rec is not None and self.track_length:
            fwd = (p["full"] - self.rec["origin"]) % self.track_length
            if fwd > self.rec["frames"][-1][0]:
                last = self.rec["frames"][-1]
                self.rec["frames"].append((fwd, 0.0, 1.0, last[3], last[4]))
        self._finish_rec()
        if p is None or p["start"] is None or p["full"] is None or not self.track_length:
            return
        span = (p["full"] - p["start"]) % self.track_length
        if span > 500.0:
            return
        samples = p["samples"]
        hold = _median(samples) if len(samples) >= 2 else 1.0
        data = {"thr_start_lap": p["start"], "thr_full_lap": p["full"], "thr_hold": hold}
        sample = p.get("sample")
        if sample is not None:
            sample.update(data)
            if 0 <= p["zid"] < len(self.zones):
                self._rebuild_reference(self.zones[p["zid"]])
        else:
            z = self.zones[p["zid"]]
            for name, v in data.items():
                self._add_sample(getattr(z, name), v)

    # ----- spoken calls -----

    @staticmethod
    def _pct10(value: float) -> int:
        return int(round(max(0.0, min(1.0, value)) * 10)) * 10

    @staticmethod
    def call_phrases() -> list[str]:
        """All call phrases to pre-render (Voice.prepare_phrases)."""
        return (
            [f"Brake {p}" for p in range(10, 101, 10)]
            + [str(p) for p in range(0, 100, 10)]
            + [f"Gas {p}" for p in range(10, 100, 10)] + ["Full", "Easy", "Limits"]
        )

    def _speech_lead_s(self, text: str) -> float:
        """Time needed from the start of the call until the target point."""
        dur = self.phrase_duration(text) if self.phrase_duration else None
        if dur is None:
            words = max(1, len(text.split()))
            dur = words * 60.0 / self.speech_rate_wpm
            latency = self.speech_latency_s            # live 'say': TTS start-up latency
        else:
            latency = self.playback_latency_s          # ready clip: afplay latency
        return dur + latency + self.call_margin_s

    def _zone_calls(self, z: _Zone) -> list[tuple[str, float, str]]:
        tl = self.track_length
        calls: list[tuple[str, float, str]] = []
        if z.brake_lap and z.depth and z.depth_avg >= self.call_min_depth:
            tgt = _circular_median(z.brake_lap, tl)
            if tgt is not None:
                p = self._pct10(z.depth_avg)
                calls.append(("brake", tgt, "Brake max" if p >= 100 or z.depth_avg >= 0.95 else f"Brake {p}"))
        if z.thr_start_lap and z.thr_full_lap:
            st = _circular_median(z.thr_start_lap, tl)
            full = _circular_median(z.thr_full_lap, tl)
            hold = _median(z.thr_hold) if z.thr_hold else 1.0
            if st is not None and full is not None:
                p = self._pct10(hold)
                if hold < 0.93 and 0 < p < 100 and _ahead_signed(full, st, tl) >= 15.0:
                    calls.append(("thr_start", st, f"Gas {p}"))
                    calls.append(("thr_full", full, "Full"))
                else:
                    calls.append(("thr_full", st, "Full"))
        return calls

    def _schedule(self, delay_s: float, fn: Callable[[], None]) -> None:
        epoch, lap = self._epoch, self.lap_num

        def guarded() -> None:
            if epoch == self._epoch and lap == self.lap_num:   # cancelled if the lap/flashback changed
                fn()

        assert self.scheduler is not None
        self.scheduler(max(0.0, delay_s), guarded)

    _GAS_KINDS = ("gas", "full", "thr_start", "thr_full")

    def _check_calls(self, t: CarTelemetry, m: CarMotion) -> None:
        """Graded pedal calls.

        Brake/release: position-based (real data: brake start sd 3-5 m) and fired on time by the timer.
        Throttle/full: STATE-based. On real data the throttle lift and full-throttle points move 15-80 m
        from lap to lap (the driver feeds throttle as steering opens); said by position, it would call "Full"
        while the steering is still closed and the car slides. So a call is spoken only near the reference
        point AND when steering is not more open than the reference; otherwise it waits.
        """
        if not self._use_lap_mode() or self.lap_distance is None:
            return
        speed_ms = math.hypot(m.vx, m.vz)
        if speed_ms < 8.0:
            return
        horizon = speed_ms * self.sched_horizon_s if self.scheduler else 0.0
        best: tuple[float, str, int, str, str, float, bool] | None = None
        for i, z in enumerate(self.zones):
            if z.n < self.learn_n or len(z.laps) < self.call_min_laps:
                continue
            items = self._zone_calls_script(z)
            if not items:
                items = [(f"call_{kind}", tgt, text, kind, None) for kind, tgt, text in self._zone_calls(z)]
            done = self._call_done.get(i, -1)
            for idx, (key, target, text, kind, sref) in enumerate(items):
                if not self._cue_available(key, i):
                    continue
                ahead = _ahead_signed(target, self.lap_distance, self.track_length)
                lead = speed_ms * self._speech_lead_s(text)
                reactive = kind in self._GAS_KINDS
                if reactive:
                    if ahead < -self.gas_window_after_m:
                        # Passed and the window ran out: skip it this lap. Only when the car is really past
                        # the target in this lap's coordinates: across the start/finish line (or half a lap
                        # away) the signed distance wraps around and merely looks "behind".
                        if self.lap_distance > target:
                            self._cue_gate(key, i)
                        continue
                    if ahead > lead:
                        continue
                    if not self._throttle_cue_allowed(z):
                        continue
                    if sref is not None and abs(t.steer) > sref + self.steer_margin:
                        continue                         # steering still more open than the reference: wait
                    if self.rear_slip >= 0.18:
                        continue                         # wheelspin: do not call a throttle increase
                else:
                    if ahead < speed_ms * 0.15 or ahead > lead + horizon:
                        continue  # too early or can no longer make it: do not say it at all
                if idx <= done:
                    continue
                if best is None or ahead < best[0] or reactive:
                    best = (ahead, key, i, text, kind, lead, reactive)
                    if reactive:
                        break
        if best is None:
            return
        ahead, key, zid, text, kind, lead, reactive = best
        if not self._cue_gate(key, zid):
            return
        order = int(key.split("_")[1]) if key.split("_")[1].isdigit() else None
        if order is not None:
            self._call_done[zid] = max(self._call_done.get(zid, -1), order)
            for earlier in range(order):                 # skip earlier ones this lap (keep the order intact)
                self._cue_gate(f"call_{earlier}", zid)

        def fire() -> None:
            if kind in self._GAS_KINDS and self.slide_ratio >= self.slide_block:
                # The car is sliding: no throttle increase; stay calm.
                self.stats.calls += 1
                self.speak("call_easy", "Easy")
                return
            self.stats.calls += 1
            if self.trace:
                self.trace(key, f"z{zid} {text}")
            self.speak(f"call_{kind}", text)

        if not reactive and ahead > lead and self.scheduler is not None:
            self._schedule((ahead - lead) / speed_ms, fire)
        else:
            fire()

    def _clear_pending(self) -> None:
        self.pending_turnin = None
        self.pending_throttle = None
        self.thr_confirm = 0

    # ----- pre-corner ding cues -----

    def _check_ahead_cues(self, pos: tuple[float, float], m: CarMotion) -> None:
        """Play a short beep when approaching the learned brake/throttle point.

        Timing is speed-aware: ~100 m before at 250 km/h, ~40 m before at 100 km/h.
        """
        speed_ms = math.hypot(m.vx, m.vz)
        if speed_ms < 5.0:          # no beep below ~18 km/h
            return

        # + half a frame: pick the frame CLOSEST to the target (do not always be a frame late).
        half_frame = 0.0 if self.scheduler else 0.5 * self._frame_dt   # no frame rounding with the timer
        lead_dist = _clamp(
            speed_ms * (self.cue_lead_s + self.cue_latency_s + half_frame),
            self.cue_min_lead_m,
            self.cue_max_lead_m,
        )
        direction = _unit_dir(m.vx, m.vz)
        horizon = speed_ms * self.sched_horizon_s if self.scheduler else 0.0
        best: tuple[str, int, float] | None = None

        for i, z in enumerate(self.zones):
            if z.n < self.learn_n:
                continue

            targets: list[tuple[str, float]] = []

            # Brake point
            if self._use_lap_mode() and z.brake_lap:
                target = self._zone_lap_dist(z)
                if target is not None:
                    ahead = _ahead_signed(target, self.lap_distance, self.track_length)  # type: ignore[arg-type]
                    targets.append(("brake", ahead))
            elif z.brake_world:
                p = z.pos
                if p is not None:
                    rel = (p[0] - pos[0], p[1] - pos[1])
                    along = _dot(rel, direction)
                    lateral = abs(rel[0] * -direction[1] + rel[1] * direction[0])
                    if along >= -6.0 and lateral <= self.world_lateral_m:
                        targets.append(("brake", along))

            # Throttle point (the moment the pedal first lifted, if known; otherwise the 55% confirm point)
            if self._use_lap_mode() and (z.thr_start_lap or z.throttle_lap):
                target = (
                    _circular_median(z.thr_start_lap, self.track_length)
                    if z.thr_start_lap else self._zone_throttle_dist(z)
                )
                if target is not None:
                    ahead = _ahead_signed(target, self.lap_distance, self.track_length)  # type: ignore[arg-type]
                    targets.append(("throttle", ahead))
            elif z.throttle_world:
                p = z.throttle_pos
                if p is not None:
                    rel = (p[0] - pos[0], p[1] - pos[1])
                    along = _dot(rel, direction)
                    lateral = abs(rel[0] * -direction[1] + rel[1] * direction[0])
                    if along >= -6.0 and lateral <= self.world_lateral_m:
                        targets.append(("throttle", along))

            for kind, ahead in targets:
                # ahead is signed: + ahead, - just passed (6 m tolerance at most).
                if ahead < -6.0 or ahead > lead_dist + horizon:
                    continue
                # If it already played this lap look at the next target; otherwise
                # the brake beep shadows the throttle beep and the throttle beep is late.
                if not self._cue_available(kind, i):
                    continue
                # In short corners the throttle point can be very close to the brake point.
                # Do not play the "throttle" beep before the brakes have been pressed.
                if kind == "throttle" and (self.cue_throttle is None or not self._throttle_cue_allowed(z)):
                    continue
                if best is None or ahead < best[2]:
                    best = (kind, i, ahead)

        if best is None:
            return

        kind, zone_id, ahead = best
        if not self._cue_gate(kind, zone_id):
            return

        def fire() -> None:
            if kind == "brake" and self.open is not None:
                return   # already braking: a beep is pointless
            self.stats.cues += 1
            if self.trace:
                self.trace(f"cue_{kind}", f"z{zone_id}")
            _LOG.debug(
                "cue %s zone=%s ahead=%.1fm speed=%.1fkm/h lap=%s",
                kind, zone_id, ahead, speed_ms * 3.6, self.lap_num,
            )
            if kind == "brake" and self.cue_brake is not None:
                self.cue_brake()
            elif kind == "throttle" and self.cue_throttle is not None:
                self.cue_throttle()

        if ahead > lead_dist and self.scheduler is not None:
            self._schedule((ahead - lead_dist) / speed_ms, fire)
        else:
            fire()

    def _use_lap_mode(self) -> bool:
        return (
            self.lap_distance is not None
            and self.track_length is not None
            and self.track_length > 0
        )

    def _cue_available(self, kind: str, zone_id: int) -> bool:
        key = f"{kind}:{zone_id}"
        if self.lap_num is not None:
            return key not in self.cued_this_lap
        return (self.sim_time - self.cue_last.get(key, -999.0)) >= 12.0

    def _throttle_cue_allowed(self, z: _Zone) -> bool:
        """The throttle beep must not play before reaching that corner's brake point."""
        if not self._use_lap_mode() or not z.brake_lap:
            return True
        target = _circular_median(z.brake_lap, self.track_length)
        if target is None:
            return True
        to_brake = _ahead(target, self.lap_distance, self.track_length)  # type: ignore[arg-type]
        # very close to 0 = we are at the brake point; the throttle beep is now allowed.
        return not (0.5 < to_brake < (self.track_length or 0) / 2.0)

    def _cue_gate(self, kind: str, zone_id: int) -> bool:
        if not self._cue_available(kind, zone_id):
            return False
        key = f"{kind}:{zone_id}"
        if self.lap_num is not None:
            self.cued_this_lap.add(key)
        else:
            self.cue_last[key] = self.sim_time
        return True

    # ----- zone management -----

    def _match(self, pos: tuple[float, float]) -> int | None:
        best: int | None = None
        best_d = 1e18

        for i, z in enumerate(self.zones):
            if self._use_lap_mode() and z.brake_lap:
                target = self._zone_lap_dist(z)
                if target is None:
                    continue
                d = abs(_signed_delta(self.lap_distance, target, self.track_length))  # type: ignore[arg-type]
            elif z.brake_world:
                p = z.pos
                if p is None:
                    continue
                d = _dist(pos, p)
            else:
                continue
            if d < best_d:
                best, best_d = i, d

        return best if best_d <= self.zone_match_m else None

    def _open_zone(self, pos: tuple[float, float], t: CarTelemetry, m: CarMotion) -> None:
        self._finish_throttle_profile()        # the previous corner's profile ends here
        zid = self._match(pos)
        if zid is None:
            self.zones.append(_Zone())
            zid = len(self.zones) - 1
        if self._use_lap_mode() and self._staging():
            origin = self._onset_dist(t.brake, self.brake_enter, 1)
            if origin is not None:
                self.rec = {
                    "origin": origin,
                    "frames": [(0.0, self.brake_enter, 0.0, math.hypot(m.vx, m.vz), abs(t.steer))],
                    "sample": None, "zid": zid,
                }
        self.open = _OpenZone(
            zone_id=zid,
            start_pos=pos,
            start_lap_dist=self._onset_dist(t.brake, self.brake_enter, 1),
            direction=_unit_dir(m.vx, m.vz),
            entry_speed=float(t.speed_kmh),
            max_depth=t.brake,
        )

    def _close_zone(self) -> None:
        if self.open is None:
            return
        oz = self.open
        z = self.zones[oz.zone_id]
        signed = self._brake_signed(z, oz)

        if z.n >= self.learn_n:
            self._speak_brake_pos(z, signed)
            self._speak_depth(z, oz)

        sample: dict | None = None
        if self._staging():
            # When the lap ends, if valid and among the best, it is folded into the reference.
            sample = {"brake_world": oz.start_pos, "depth": oz.max_depth}
            if oz.locked:
                sample["lock"] = 1
            if oz.start_lap_dist is not None:
                sample["brake_lap"] = oz.start_lap_dist
            self.stage[oz.zone_id] = sample
        else:
            # Do not fold an outlier lap into the baseline: it does not drift even if the mistake repeats.
            if z.n < self.learn_n or signed is None or abs(signed) < self.brake_delta_m:
                self._add_sample(z.brake_world, oz.start_pos)
                if oz.start_lap_dist is not None:
                    self._add_sample(z.brake_lap, oz.start_lap_dist)
            if z.n < self.learn_n or (oz.max_depth - z.depth_avg) < self.depth_delta:
                self._add_sample(z.depth, oz.max_depth)

        if self.rec is not None and sample is not None:
            self.rec["sample"] = sample
            self.rec["zid"] = oz.zone_id
        if self._use_lap_mode() and self.lap_distance is not None:
            self.thr_prof = {
                "zid": oz.zone_id, "origin": self.lap_distance, "start": None,
                "full": None, "samples": [], "sample": sample,
            }
        self.pending_turnin = oz.zone_id
        self.pending_throttle = oz.zone_id
        self.thr_confirm = 0
        self.open = None

    def _handle_turnin(self, pos: tuple[float, float]) -> None:
        if self.pending_turnin is None:
            return
        z = self.zones[self.pending_turnin]
        zid = self.pending_turnin
        self.pending_turnin = None

        signed = self._signed_from_zone(
            z.turnin_pos, self._zone_turnin_dist(z), pos, self._dir
        )
        if (
            signed is not None
            and abs(signed) >= self.turnin_delta_m
            and self._gate("apex", zid)
        ):
            d_m = round(abs(signed))
            if signed < 0:
                self.speak("apex", f"Apex {d_m} meters EARLY.")
            else:
                self.speak("apex", f"Apex {d_m} meters LATE.")

        if self._staging():
            st = self.stage.setdefault(zid, {})
            st["turnin_world"] = pos
            if self.lap_distance is not None:
                st["turnin_lap"] = self.lap_distance
        elif (
            len(z.turnin_lap) < self.learn_n
            or signed is None
            or abs(signed) < self.turnin_delta_m
        ):
            self._add_sample(z.turnin_world, pos)
            if self.lap_distance is not None:
                self._add_sample(z.turnin_lap, self.lap_distance)

    def _handle_throttle_on(self, pos: tuple[float, float]) -> None:
        if self.pending_throttle is None:
            return
        z = self.zones[self.pending_throttle]
        zid = self.pending_throttle
        self.pending_throttle = None
        self.thr_confirm = 0

        signed = self._signed_from_zone(
            z.throttle_pos, self._zone_throttle_dist(z), pos, self._dir
        )
        if (
            signed is not None
            and abs(signed) >= self.throttle_delta_m
            and self._gate("throttle", zid)
        ):
            d_m = round(abs(signed))
            if not self.posthoc_throttle:
                pass
            elif signed < 0:
                self.speak("throttle", f"Throttle {d_m} meters EARLY — watch the rear.")
            else:
                self.speak("throttle", f"Throttle {d_m} meters LATE.")

        if self._staging():
            st = self.stage.setdefault(zid, {})
            st["throttle_world"] = pos
            if self.lap_distance is not None:
                st["throttle_lap"] = self.lap_distance
        elif (
            len(z.throttle_lap) < self.learn_n
            or signed is None
            or abs(signed) < self.throttle_delta_m
        ):
            self._add_sample(z.throttle_world, pos)
            if self.lap_distance is not None:
                self._add_sample(z.throttle_lap, self.lap_distance)

    def _signed_from_zone(
        self,
        world_avg: tuple[float, float] | None,
        lap_avg: float | None,
        pos: tuple[float, float],
        direction: tuple[float, float],
    ) -> float | None:
        if self._use_lap_mode() and lap_avg is not None and self.lap_distance is not None:
            return _signed_delta(self.lap_distance, lap_avg, self.track_length)
        if world_avg is not None:
            return _dot((pos[0] - world_avg[0], pos[1] - world_avg[1]), direction)
        return None

    def _zone_lap_dist(self, z: _Zone) -> float | None:
        return _circular_median(z.brake_lap, self.track_length)

    def _zone_turnin_dist(self, z: _Zone) -> float | None:
        return _circular_median(z.turnin_lap, self.track_length)

    def _zone_throttle_dist(self, z: _Zone) -> float | None:
        return _circular_median(z.throttle_lap, self.track_length)

    def _add_sample(self, samples: list, value) -> None:
        samples.append(value)
        if len(samples) > self.sample_window:
            del samples[0]

    # ----- warning kinds -----

    def _brake_signed(self, z: _Zone, oz: _OpenZone) -> float | None:
        target = self._zone_lap_dist(z)
        if self._use_lap_mode() and oz.start_lap_dist is not None and target is not None:
            return _signed_delta(oz.start_lap_dist, target, self.track_length)
        p = z.pos
        if p is None:
            return None
        return _dot((oz.start_pos[0] - p[0], oz.start_pos[1] - p[1]), oz.direction)

    def _speak_brake_pos(self, z: _Zone, signed: float | None) -> None:
        if signed is None or abs(signed) < self.brake_delta_m:
            return
        zid = self._zone_id(z)
        d_m = round(abs(signed))
        if signed < 0:
            if self._gate("brake", zid):
                self.speak("brake", f"Braking {d_m} meters EARLY.")
        else:
            if self._gate("brake", zid):
                self.speak("brake", f"Braking {d_m} meters LATE — watch the entry.")

    def _speak_depth(self, z: _Zone, oz: _OpenZone) -> None:
        if oz.max_depth < self.depth_min:
            return
        if (
            oz.max_depth - z.depth_avg >= self.depth_delta
            and self._gate("hard_brake", self._zone_id(z))
        ):
            self.speak("brake", "Braking too hard — ease it off.")

    # ----- helpers -----

    def _zone_id(self, z: _Zone) -> int:
        for i, x in enumerate(self.zones):
            if x is z:
                return i
        return -1

    def _gate(self, kind: str, zone_id: int) -> bool:
        key = f"{kind}:{zone_id}"
        if (self.sim_time - self.last_speak.get(key, -999.0)) < self.cooldown_s:
            return False
        self.last_speak[key] = self.sim_time
        self.stats.events += 1
        return True
