# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) ApexMind contributors
"""ApexMind CLI — `apexmind listen` / `python3 -m apexmind listen`"""

from __future__ import annotations

import argparse
import logging
import logging.handlers
import os
import signal
import socket
import sys
import threading
import webbrowser
import time
from dataclasses import dataclass, field
from pathlib import Path

from . import __version__
from .engineer import (
    Analyzer,
    DeltaTracker,
    LapDebrief,
    LockupCoach,
    SpinCoach,
    TrackLimits,
    TractionCoach,
    TyreCoach,
    Voice,
)
from .engineer import llm as llm_mod
from .telemetry.header import SUPPORTED_FORMATS, UnknownFormat, parse_header
from .telemetry.listener import UdpListener
from .telemetry.packets import (
    CAR_COUNTS,
    CarDamage,
    CarMotion,
    CarTelemetry,
    LapData,
    MotionEx,
    SessionInfo,
    parse_car_damage,
    parse_car_telemetry,
    parse_event_code,
    parse_lap_data,
    parse_motion,
    parse_motion_ex,
    parse_session,
)
from .memory import TrackMemory, track_name
from .recorder import Recorder
from .webserver import LiveHub, start_server

_LOG = logging.getLogger("apexmind")

# Message kinds that skip the LLM polish (contain numbers / are time-critical).
_RAW_KINDS = frozenset({"debrief", "lap_invalid", "limits", "wide", "delta", "lockup", "tyre_temp",
                        "tyre_wear", "damage"})
# Urgent kinds that interrupt speech and go out immediately (short max_age: never spoken if late).
_URGENT_KINDS = frozenset({"spin", "lockup", "wide", "lap_invalid"})
# With pedal calls on, the after-the-fact sentences stay text-only (console, web, recording): they are
# long and would talk over, or right behind, the calls the driver needs to hear.
_TEXT_ONLY_WITH_CALLS = frozenset({"brake", "apex", "throttle"})


def _open_later(url: str, delay_s: float = 0.8) -> None:
    """Open the dashboard in the default browser shortly after the server is up."""
    timer = threading.Timer(delay_s, webbrowser.open, args=(url,))
    timer.daemon = True
    timer.start()


def _schedule(delay_s: float, fn) -> None:
    """Fire exactly on time, independent of the frame interval (daemon timer)."""
    timer = threading.Timer(delay_s, fn)
    timer.daemon = True
    timer.start()


# ---- helpers ---------------------------------------------------------------

def _local_ip() -> str:
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except OSError:
        return "127.0.0.1"


def _default_log_path() -> Path:
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Logs" / "ApexMind" / "apexmind.log"
    base = Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local" / "state"))
    return base / "apexmind" / "apexmind.log"


def _setup_logging(path: str, debug: bool) -> Path | None:
    level = logging.DEBUG if debug else logging.INFO
    root = logging.getLogger()
    root.setLevel(level)
    root.handlers.clear()

    # The console only gets warnings/errors; normal flow is printed with print.
    console = logging.StreamHandler()
    console.setLevel(logging.WARNING)
    console.setFormatter(logging.Formatter("%(levelname)s: %(message)s"))
    root.addHandler(console)

    if path.lower() in {"off", "none", "0", "-"}:
        return None

    target = _default_log_path() if path.lower() in {"auto", "default", ""} else Path(path).expanduser()
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        fh = logging.handlers.RotatingFileHandler(
            target, maxBytes=1_000_000, backupCount=3, encoding="utf-8"
        )
        fh.setLevel(level)
        fh.setFormatter(
            logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
        )
        root.addHandler(fh)
        return target
    except OSError as e:
        print(f"⚠️ could not open log file ({target}): {e}", flush=True)
        return None


def _port(value: str) -> int:
    try:
        p = int(value)
    except ValueError as e:
        raise argparse.ArgumentTypeError("port must be a number") from e
    if not 1 <= p <= 65535:
        raise argparse.ArgumentTypeError("port must be in the range 1-65535")
    return p


def _positive(value: str) -> float:
    try:
        v = float(value)
    except ValueError as e:
        raise argparse.ArgumentTypeError("must be a number") from e
    if v <= 0:
        raise argparse.ArgumentTypeError("must be greater than 0")
    return v


def _nonnegative(value: str) -> float:
    try:
        v = float(value)
    except ValueError as e:
        raise argparse.ArgumentTypeError("must be a number") from e
    if v < 0:
        raise argparse.ArgumentTypeError("must be 0 or greater")
    return v


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="apexmind",
        description=f"ApexMind {__version__} — real-time AI race engineer",
    )
    p.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    sub = p.add_subparsers(dest="command")

    ld = sub.add_parser("listen", help="listen to UDP telemetry and call out mistakes in real time")
    ld.add_argument("--port", type=_port, default=20777, help="UDP port (default 20777)")
    ld.add_argument("--mute", action="store_true", help="silence audio, print to the terminal only")
    ld.add_argument("--no-cues", action="store_true", help="disable the pre-corner beep cues")
    ld.add_argument("--voice", default="auto", help="'say' voice name (auto: best known voice)")
    ld.add_argument(
        "--voice-rate", type=int, default=330, metavar="WPM",
        help="speech rate in words/minute (default 330, max 400)",
    )
    ld.add_argument(
        "--llm", choices=["auto", "on", "off"], default="off",
        help="polish the radio sentence with a local Ollama model (off by default; adds latency)",
    )
    ld.add_argument(
        "--calls", choices=["on", "off"], default="on",
        help="graded pedal calls: 'Brake 90', '50', '0', 'Gas 30', '60', 'Full' (on by default)",
    )
    ld.add_argument(
        "--cue-latency", default="auto", metavar="S|auto",
        help="beep/voice latency compensation: 'auto' (measured at start-up, default) or seconds",
    )
    ld.add_argument(
        "--lock-slip", type=_positive, default=0.30, metavar="R",
        help="wheel lock-up threshold, |slip ratio| (default 0.25)",
    )
    ld.add_argument(
        "--hotspots", default="~/.apexmind/hotspots.json", metavar="FILE",
        help="track-limit violation spots (persistent per track; 'Limits' warning from lap 1)",
    )
    ld.add_argument(
        "--memory", default="~/.apexmind", metavar="DIR",
        help="track memory: what is learned about each circuit is kept here, so calls work from the "
             "first lap of any later session, race or another day (default ~/.apexmind)",
    )
    ld.add_argument(
        "--record", default="", metavar="DIR",
        help="record the session under DIR for calibration (.csv + .samples)",
    )
    ld.add_argument("--debug", action="store_true", help="verbose log and raw packet hints")
    ld.add_argument(
        "--log", default="auto", metavar="PATH",
        help="log file ('auto' default location, 'off' disables)",
    )
    ld.add_argument(
        "--zone-match", type=_positive, default=50.0, metavar="M",
        help="radius for matching the same corner (m, default 50)",
    )
    ld.add_argument("--brake-delta", type=float, default=20.0, help="brake point deviation threshold (m)")
    ld.add_argument("--turnin-delta", type=float, default=18.0, help="turn-in deviation threshold (m)")
    ld.add_argument("--throttle-delta", type=float, default=18.0, help="throttle point deviation (m)")
    ld.add_argument("--depth-delta", type=float, default=0.10, help="brake depth deviation threshold (0-1)")
    ld.add_argument(
        "--cue-lead", type=_nonnegative, default=0.0, metavar="S",
        help="seconds before the target the beep fires (default 0 = exactly on the point)",
    )
    ld.add_argument(
        "--cue-min-m", type=_nonnegative, default=0.0,
        help="shortest beep distance (m, default 0)",
    )
    ld.add_argument("--cue-max-m", type=_positive, default=150.0, help="longest beep distance (m)")

    ld.add_argument(
        "--web", nargs="?", const=8765, type=_port, default=None, metavar="PORT",
        help="serve the live web dashboard and recording analyzer on this port (default 8765)",
    )
    ld.add_argument(
        "--open", action="store_true",
        help="open the web dashboard in your browser when ApexMind starts (implies --web)",
    )
    ld.add_argument(
        "--web-host", default="localhost", metavar="HOST",
        help="interface for --web; use the wildcard address to open it from your phone on the same network",
    )

    ld.set_defaults(func=_cmd_listen)

    rp = sub.add_parser("replay", help="interpret a recorded CSV session offline (no audio)")
    rp.add_argument("rest", nargs=argparse.REMAINDER)
    rp.set_defaults(func=_cmd_replay)
    mp = sub.add_parser("memory", help="list or forget what ApexMind remembers about tracks")
    mp.add_argument("--dir", default="~/.apexmind", metavar="DIR", help="track memory directory")
    mp.add_argument("--forget", type=int, metavar="TRACK_ID", help="forget one track")
    mp.set_defaults(func=_cmd_memory)
    rr = sub.add_parser("report", help="analyse a recorded CSV session and print a JSON report")
    rr.add_argument("rest", nargs=argparse.REMAINDER)
    rr.set_defaults(func=_cmd_report)
    return p


def _fmt_lap(ms: int | None) -> str:
    if not ms:
        return "-"
    return f"{ms // 60000}:{(ms % 60000) / 1000:06.3f}"


def _cmd_memory(args: argparse.Namespace) -> None:
    mem = TrackMemory(args.dir)
    if args.forget is not None:
        done = mem.forget(args.forget)
        print(f"Forgot track {args.forget}." if done else f"Track {args.forget} was not remembered.")
        return
    tracks = mem.tracks()
    if not tracks:
        print(f"Nothing remembered yet ({mem.root}). Drive a lap and ApexMind will remember the track.")
        return
    print(f"Track memory in {mem.root}:")
    for t in tracks:
        saved = time.strftime("%Y-%m-%d", time.localtime(t["saved_at"])) if t["saved_at"] else "imported"
        print(f"  {t['name']} (id {t['track_id']}, {t['length']:.0f} m): {t['corners']} corners, "
              f"best lap {_fmt_lap(t['best_ms'])}, saved {saved}")


def _cmd_report(args: argparse.Namespace) -> None:
    from .report import main as report_main

    sys.exit(report_main(args.rest))


def _cmd_replay(args: argparse.Namespace) -> None:
    from .replay import main as replay_main

    sys.exit(replay_main(args.rest))


def _wheels(values, prefix: str) -> dict:
    """Expand into ``prefix_rl`` ... keys in RL, RR, FL, FR order."""
    if not values:
        return {}
    return {f"{prefix}_{n}": v for n, v in zip(("rl", "rr", "fl", "fr"), values, strict=False)}


def _record_values(t, m, lap, lap_distance, mex, dmg) -> dict:
    v: dict = {
        "lap": None if lap is None else lap.current_lap_num,
        "dist": lap_distance,
        "lap_ms": None if lap is None else lap.current_lap_time_ms,
        "sector": None if lap is None else lap.sector,
        "speed": t.speed_kmh, "throttle": t.throttle, "brake": t.brake, "steer": t.steer,
        "gear": t.gear, "rpm": t.rpm, "g_lat": m.g_lat, "g_long": m.g_long, "vx": m.vx, "vz": m.vz,
        "invalid": None if lap is None else int(lap.current_lap_invalid),
        "cc_warn": None if lap is None else lap.corner_cutting_warnings,
        "warnings": None if lap is None else lap.total_warnings,
        "pit": None if lap is None else lap.pit_status,
    }
    v.update(_wheels(t.tyre_inner_temp, "tin"))
    v.update(_wheels(t.tyre_surface_temp, "tsurf"))
    v.update(_wheels(t.tyre_pressure, "press"))
    v.update(_wheels(t.brakes_temp, "btemp"))
    v.update(_wheels(t.surface_type, "surf"))
    if mex is not None:
        v.update(_wheels(mex.wheel_slip_ratio, "slip"))
        v.update(_wheels(mex.wheel_speed, "wspd"))
    if dmg is not None:
        v.update(_wheels(dmg.tyres_wear, "wear"))
        v.update(_wheels(dmg.tyre_blisters, "blist"))
        v.update({
            "dmg_flwing": dmg.front_left_wing, "dmg_frwing": dmg.front_right_wing,
            "dmg_rwing": dmg.rear_wing, "dmg_floor": dmg.floor, "dmg_diffuser": dmg.diffuser,
            "dmg_sidepod": dmg.sidepod, "dmg_gearbox": dmg.gearbox, "dmg_engine": dmg.engine,
        })
    return v


# ---- runtime state ---------------------------------------------------------

@dataclass
class _State:
    uid: int | None = None
    session: SessionInfo | None = None
    tel: CarTelemetry | None = None
    tel_time: float = 0.0
    motion: CarMotion | None = None
    motion_time: float = 0.0
    lap: LapData | None = None
    lap_time: float = 0.0
    mex: MotionEx | None = None
    mex_time: float = 0.0
    damage: CarDamage | None = None
    frames: int = 0
    last_hud: float = 0.0
    last_pub: float = 0.0
    last_packet_mono: float = field(default_factory=time.monotonic)
    warned_no_packets: bool = False
    warned_no_motion: bool = False
    format_announced: bool = False
    unknown_formats: dict[int, int] = field(default_factory=dict)
    debug_seen: dict[int, int] = field(default_factory=dict)


def _cmd_listen(args: argparse.Namespace) -> None:
    log_path = _setup_logging(args.log, args.debug)

    # If SIGTERM arrives from the terminal/launcher, still print the summary and exit cleanly.
    def _sigterm(_signum, _frame):
        raise KeyboardInterrupt

    try:
        signal.signal(signal.SIGTERM, _sigterm)
    except (ValueError, OSError):
        pass

    voice = Voice(
        enabled=not args.mute,
        name=args.voice,
        rate=args.voice_rate,
    )
    llm_on = args.llm == "on" or (args.llm == "auto" and llm_mod.available())
    polisher = (
        llm_mod.AsyncPolisher(speak_cb=lambda text, urgent=False: voice.speak(text, urgent=urgent))
        if llm_on
        else None
    )

    advice_count = 0
    spin_cue_count = 0
    hub: LiveHub | None = None

    def advise(kind: str, text: str) -> None:
        nonlocal advice_count, spin_cue_count
        advice_count += 1
        debrief.note(kind)
        if kind == "lockup":
            an.note_lockup()
        print(f"⚡ [{kind}] {text}", flush=True)
        if hub is not None:
            hub.publish_event({
                "t": time.time(), "kind": kind, "text": text,
                "lap": an.lap_num, "d": None if an.lap_distance is None else round(an.lap_distance),
            })
        is_call = kind.startswith("call_")
        urgent = kind in _URGENT_KINDS or kind.startswith("spin_") or is_call
        if kind == "spin":
            # Even if TTS is late, the spin is heard at once.
            if voice.enabled:
                spin_cue_count += 1
                voice.cue_spin()
        if recorder is not None:
            recorder.event(f"{kind}@{an.lap_distance:.0f}" if an.lap_distance is not None else kind)
        if args.calls == "on" and kind in _TEXT_ONLY_WITH_CALLS:
            return
        if is_call:
            # Timed call: the ready clip plays instantly; if late it is never spoken.
            voice.call(text)
        elif urgent:
            voice.speak(text, urgent=True, max_age=2.0)
        elif polisher is not None and kind not in _RAW_KINDS:
            if not polisher.submit(kind, text):
                voice.speak(text)
        else:
            voice.speak(text)

    debrief = LapDebrief(speak_cb=advise)
    delta = DeltaTracker(speak_cb=advise)
    limits = TrackLimits(speak_cb=advise)
    limits.on_new_hotspot = lambda: (
        limits.save(args.hotspots, st.session.track_id) if args.hotspots and st.session else None
    )
    lockup = LockupCoach(speak_cb=advise, slip_threshold=args.lock_slip)
    traction = TractionCoach(speak_cb=advise)
    tyres = TyreCoach(speak_cb=advise)
    recorder = Recorder(args.record) if args.record else None

    memory = TrackMemory(args.memory) if args.memory else None

    def load_refs() -> None:
        if memory is None or an.zones or st.session is None or not st.session.track_length:
            return
        data = memory.load(st.session.track_id, st.session.track_length)
        n = an.import_refs(data)
        if n:
            print(
                f"📚 Track memory: {n} corners of {track_name(st.session.track_id)} remembered — "
                "calls work from the first lap", flush=True,
            )

    def save_refs() -> None:
        if memory is None or st.session is None or not an.zones:
            return
        memory.save(st.session.track_id, an.export_refs())

    an = Analyzer(
        speak_cb=advise,
        zone_match_m=args.zone_match,
        brake_delta_m=args.brake_delta,
        turnin_delta_m=args.turnin_delta,
        throttle_delta_m=args.throttle_delta,
        depth_delta=args.depth_delta,
        cue_lead_s=args.cue_lead,
        cue_min_lead_m=args.cue_min_m,
        cue_max_lead_m=args.cue_max_m,
        cue_latency_s=0.12 if args.cue_latency == "auto" else float(args.cue_latency),
        calls=args.calls == "on",
        speech_rate_wpm=args.voice_rate,
        posthoc_throttle=args.calls != "on",
        phrase_duration=voice.phrase_duration,
        scheduler=_schedule,
    )
    an.on_commit = save_refs

    def report_gap(text: str, reason: str) -> None:
        print(f"⚠️ call not spoken: '{text}' ({reason})", flush=True)
        if recorder is not None:
            recorder.event(f"skip:{text}:{reason}")

    an.on_skip = report_gap
    voice.on_call_dropped = report_gap
    if recorder is not None:
        an.trace = lambda k, info: recorder.event(f"{k}:{info}")
    if args.calls == "on" and voice.enabled:
        voice.prepare_phrases(Analyzer.call_phrases())
    if args.cue_latency == "auto" and voice.enabled:
        def _calibrate() -> None:
            voice.phrases_ready.wait(timeout=90)
            lat = voice.measure_latency()
            if lat is None:
                return
            lat = min(0.25, max(0.03, lat))
            an.cue_latency_s = lat
            an.playback_latency_s = lat
            print(f"   Audio latency measured: {lat * 1000:.0f} ms (automatic compensation)", flush=True)
            if recorder is not None:
                recorder.event(f"latency:{lat:.3f}")

        threading.Thread(target=_calibrate, name="apexmind-calibrate", daemon=True).start()
    if not args.no_cues and not args.mute and voice.enabled:
        # No throttle beep while calls are on: the throttle point moves 15-80 m per lap.
        an.set_cues(voice.cue_brake, voice.cue_throttle if args.calls != "on" else None)
    spin = SpinCoach(speak_cb=advise)

    st = _State()

    def reset_learning(reason: str, keep_corners: bool = False) -> None:
        if keep_corners:
            an.rewind()                    # flashback: the corners you learned are still there
        else:
            an.reset()
            an.set_track_length(st.session.track_length if st.session else None)
            load_refs()
            limits.reset()
        spin.reset()
        debrief.reset()
        lockup.reset()
        traction.reset()
        st.tel = st.motion = st.lap = st.mex = None
        st.frames = 0
        st.warned_no_motion = False
        if reason:
            print(reason, flush=True)

    def on_session(info: SessionInfo) -> None:
        new_track = (
            st.session is None
            or st.session.track_id != info.track_id
            or abs(st.session.track_length - info.track_length) > 10
        )
        track_changed = st.session is not None and (
            st.session.track_id != info.track_id
            or (
                st.session.track_length > 0
                and info.track_length > 0
                and abs(info.track_length - st.session.track_length) > 10
            )
        )
        if track_changed:
            debrief.new_session()
            delta.new_session()
            limits.new_session()
            tyres.reset()
            reset_learning("🔄 track changed — personal baseline reset")
        st.session = info
        if info.track_length > 0:
            an.set_track_length(info.track_length)
            limits.track_length = float(info.track_length)
            load_refs()
            if not limits.hotspots and args.hotspots:
                n = limits.load(args.hotspots, info.track_id)
                if n:
                    print(f"⚠️ {n} saved track-limit spots loaded (track {info.track_id})", flush=True)
        if new_track:
            print(f"🗺️ Track {info.track_id} — {info.track_length} m", flush=True)
            _LOG.info(
                "session: track_id=%s track_length=%s type=%s laps=%s",
                info.track_id, info.track_length, info.session_type, info.total_laps,
            )
        else:
            _LOG.debug(
                "session: track_id=%s track_length=%s type=%s laps=%s",
                info.track_id, info.track_length, info.session_type, info.total_laps,
            )

    def on_event(code: str) -> None:
        _LOG.info("event: %s", code)
        if recorder is not None:
            recorder.event(code)
        if code == "SSTA":
            print("🟢 session started", flush=True)
            reset_learning("")
        elif code == "SEND":
            print("🏁 session ended", flush=True)
            save_refs()
        elif code == "FLBK":
            reset_learning("⏪ flashback — corner knowledge kept", keep_corners=True)
        elif code == "CHQF":
            print("🏁 chequered flag", flush=True)

    def process(tel_time: float) -> None:
        t, m = st.tel, st.motion
        if t is None or m is None:
            return

        dt_motion = tel_time - st.motion_time
        if dt_motion > 0.30:
            if not st.warned_no_motion:
                print("⚠️ motion packets are late — packets may be lost", flush=True)
                st.warned_no_motion = True
            return
        st.warned_no_motion = False
        if 0.0 < dt_motion <= 0.15:
            m = m.advanced(dt_motion)

        lap = st.lap
        lap_distance: float | None = None
        lap_num: int | None = None
        if lap is not None:
            lap_distance = lap.lap_distance
            lap_num = lap.current_lap_num
            if st.session is not None and st.session.track_length > 0:
                # If LapData is a bit older than the telemetry, advance it quickly.
                dt_lap = max(0.0, min(tel_time - st.lap_time, 0.20))
                lap_distance = (
                    lap_distance + (t.speed_kmh / 3.6) * dt_lap
                ) % st.session.track_length

        if lap is not None:
            an.last_lap_ms = lap.last_lap_time_ms or None
            an.lap_invalid = lap.current_lap_invalid
        spin.on_frame(t, m, tel_time)
        lockup.on_frame(t, st.mex, st.mex_time, tel_time)
        traction.on_frame(t, st.mex, st.mex_time, tel_time)
        an.rear_slip = traction.rear_slip
        tyres.on_telemetry(t, tel_time)
        an.slide_ratio = spin.ratio
        limits.on_telemetry(t, tel_time, lap_distance)
        in_pit = lap is not None and (lap.pit_status != 0 or lap.driver_status in (0, 2, 3))
        an.suppressed = spin.is_spinning or in_pit
        an.on_frame(
            t, m, tel_time,
            lap_distance=lap_distance,
            lap_num=lap_num,
        )

        st.frames += 1
        if recorder is not None:
            recorder.row(tel_time, _record_values(t, m, lap, lap_distance, st.mex, st.damage))
        if st.frames % 60 == 0:
            _LOG.debug(
                "frame=%s speed=%s gear=%s thr=%.2f brk=%.2f steer=%.2f lap=%s dist=%s",
                st.frames, t.speed_kmh, t.gear, t.throttle, t.brake, t.steer,
                lap_num, None if lap_distance is None else round(lap_distance, 1),
            )

        now = time.monotonic()
        if hub is not None and now - st.last_pub >= 0.1:
            st.last_pub = now
            hub.publish_state({
                "speed": t.speed_kmh, "gear": t.gear, "rpm": t.rpm,
                "throttle": round(t.throttle, 3), "brake": round(t.brake, 3), "steer": round(t.steer, 3),
                "lap": lap_num, "dist": None if lap_distance is None else round(lap_distance),
                "lap_ms": None if lap is None else lap.current_lap_time_ms,
                "last_ms": None if lap is None else lap.last_lap_time_ms,
                "sector": None if lap is None else lap.sector,
                "invalid": bool(lap is not None and lap.current_lap_invalid),
                "spin": spin.is_spinning, "pit": in_pit, "corners": len(an.zones),
                "track_length": None if st.session is None else st.session.track_length,
            })
        if now - st.last_hud >= 2.0:
            st.last_hud = now
            gear = "R" if t.gear < 0 else str(t.gear)
            lap_txt = (
                f"L{lap_num} {lap_distance:4.0f}m"
                if lap_num is not None and lap_distance is not None
                else "L- ---m"
            )
            flags = []
            if spin.is_spinning:
                flags.append("SPIN")
            if in_pit:
                flags.append("PIT")
            flag_txt = ("  ⚠️ " + " ".join(flags)) if flags else ""
            print(
                f"SPD {t.speed_kmh:3d} km/h | v{gear} | "
                f"throttle {t.throttle * 100:3.0f}% brake {t.brake * 100:3.0f}% | "
                f"{lap_txt} | corners {len(an.zones)}{flag_txt}",
                flush=True,
            )

    def on_packet(buf: bytes) -> None:
        st.last_packet_mono = time.monotonic()
        st.warned_no_packets = False

        try:
            h, hsz = parse_header(buf)
        except UnknownFormat:
            fmt = int.from_bytes(buf[:2], "little") if len(buf) >= 2 else -1
            st.unknown_formats[fmt] = st.unknown_formats.get(fmt, 0) + 1
            if st.unknown_formats[fmt] == 1:
                supported = ", ".join(str(x) for x in SUPPORTED_FORMATS)
                print(
                    f"⚠️ unknown packet format: {fmt} (supported: {supported}). "
                    "Set the in-game UDP Format to 2025/2026.",
                    flush=True,
                )
                _LOG.warning("unknown format %s, first 32 bytes: %s", fmt, buf[:32].hex())
            return
        except ValueError:
            _LOG.debug("short/corrupt header: %s", buf[:32].hex())
            return

        if h.session_uid != st.uid:
            if st.uid is not None:
                print("🔁 new session — personal baseline reset", flush=True)
            st.uid = h.session_uid
            st.session = None
            st.format_announced = False
            debrief.new_session()
            delta.new_session()
            limits.new_session()
            tyres.reset()
            reset_learning("")

        if not st.format_announced:
            st.format_announced = True
            print(
                f"📡 Telemetry format: {h.format} (gameYear {h.game_year})",
                flush=True,
            )
            _LOG.info("telemetry format=%s gameYear=%s", h.format, h.game_year)

        if h.player_index >= CAR_COUNTS.get(h.format, 22):
            _LOG.debug("invalid player index: %s", h.player_index)
            return

        if args.debug and st.debug_seen.get(h.packet_id, 0) < 3:
            st.debug_seen[h.packet_id] = st.debug_seen.get(h.packet_id, 0) + 1
            _LOG.debug(
                "pkt id=%s fmt=%s year=%s player=%s t=%.3f size=%s",
                h.packet_id, h.format, h.game_year, h.player_index,
                h.session_time, len(buf),
            )

        payload = memoryview(buf)[hsz:]
        if recorder is not None:
            code = None
            if h.packet_id == 3 and len(payload) >= 4:
                code = bytes(payload[:4]).decode("ascii", errors="replace")
            recorder.packet(buf, h.packet_id, code)
        try:
            if h.packet_id == 1:
                on_session(parse_session(h.format, payload))
            elif h.packet_id == 2:
                st.lap = parse_lap_data(h.format, h.player_index, payload)
                st.lap_time = h.session_time
                limits.on_lap(st.lap, h.session_time)
                debrief.update(st.lap)
                delta.update(st.lap)
            elif h.packet_id == 6:
                st.tel = parse_car_telemetry(h.format, h.player_index, payload)
                st.tel_time = h.session_time
                process(h.session_time)
            elif h.packet_id == 0:
                st.motion = parse_motion(h.format, h.player_index, payload)
                st.motion_time = h.session_time
            elif h.packet_id == 13:
                st.mex = parse_motion_ex(h.format, payload)
                st.mex_time = h.session_time
            elif h.packet_id == 10:
                st.damage = parse_car_damage(h.format, h.player_index, payload)
                tyres.on_damage(st.damage, h.session_time)
            elif h.packet_id == 3:
                code = parse_event_code(h.format, payload)
                if code == "PENA" and len(payload) >= 6:
                    # PENA:<penaltyType>:<infringementType> (meaning still to be mapped)
                    code = f"PENA:{payload[4]}:{payload[5]}"
                on_event(code)
        except ValueError as e:
            _LOG.debug("could not decode packet %s: %s", h.packet_id, e)

    # Bind the port first: if busy, exit without saying "ApexMind ready", with audio off.
    try:
        lst = UdpListener(args.port, on_packet=on_packet)
    except SystemExit:
        if polisher is not None:
            polisher.close()
        voice.close()
        raise

    server = None
    if args.open and not args.web:
        args.web = 8765
    if args.web:
        hub = LiveHub()
        try:
            server = start_server(hub, args.web_host, args.web)
        except OSError as e:
            lst.close()
            voice.close()
            msg = f"⛔ Could not start the web dashboard on {args.web_host}:{args.web}: {e}"
            raise SystemExit(msg) from e

    # Warn the user after 15 s without telemetry.
    stop_watch = threading.Event()

    def watchdog() -> None:
        while not stop_watch.wait(5.0):
            if (
                time.monotonic() - st.last_packet_mono > 15.0
                and not st.warned_no_packets
            ):
                print(
                    "⏳ No telemetry for 15 seconds. F1: UDP Telemetry ON, "
                    f"IP {_local_ip()}, port {args.port}, format 2025/2026.",
                    flush=True,
                )
                st.warned_no_packets = True

    watch_thread = threading.Thread(target=watchdog, name="apexmind-watchdog", daemon=True)
    watch_thread.start()

    ip = _local_ip()
    if server is not None:
        wildcard = args.web_host in ("", "::") or set(args.web_host) <= {"0", "."}
        shown = ip if wildcard else args.web_host
        scheme = "ht" + "tp"
        print(f"🌐 Web dashboard: {scheme}://{shown}:{args.web}", flush=True)
        if args.open:
            _open_later(f"{scheme}://{'localhost' if wildcard else shown}:{args.web}")
    print(f"🏁 ApexMind {__version__} — listening on udp/{args.port}", flush=True)
    print(
        f"   F1 25/26: Options → Telemetry Settings → UDP Telemetry: ON | "
        f"IP: {ip} | Port: {args.port} | Binary | 60 Hz | Format: 2025/2026",
        flush=True,
    )
    if voice.enabled:
        audio = f"ON ({voice.voice or 'system default'}, {voice.rate} wpm)"
    elif args.mute:
        audio = "off (--mute)"
    else:
        audio = "off (macOS 'say' not found)"
    print(f"   Audio: {audio} | LLM: {'ON (async)' if llm_on else 'off'}", flush=True)
    cues_on = not args.no_cues and not args.mute and voice.enabled
    print(
        "   Beeps: " + (
            f"ON — ~{args.cue_lead:g}s before the target, does not wait for speech"
            if cues_on
            else "off"
        ),
        flush=True,
    )
    if log_path is not None:
        print(f"   Log: {log_path}", flush=True)
    if recorder is not None:
        print(f"   Recording: {recorder.csv_path}", flush=True)
    print(
        f"   Calls: {'ON' if args.calls == 'on' else 'off'} | beep latency compensation: "
        f"{'auto' if args.cue_latency == 'auto' else args.cue_latency + ' s'} | speech {voice.rate} wpm",
        flush=True,
    )
    print("   Learning you on the first lap (silent), then the warnings begin.", flush=True)
    if voice.enabled:
        # Verify at once that audio really works; no surprise at the first corner.
        voice.speak("ApexMind ready.", max_age=6.0)
        if args.calls == "on":
            print("   Preparing call clips (~15 s on first run, then cached)...", flush=True)

    _LOG.info("ApexMind %s started: port=%s voice=%s rate=%s llm=%s",
              __version__, args.port, voice.voice, voice.rate, llm_on)

    try:
        lst.run()
    except KeyboardInterrupt:
        save_refs()
        print("\n🏁 Done — summary:", flush=True)
        print(
            f"   packets: {lst.stats['received']} | errors: {lst.stats['errors']} | "
            f"processed: {st.frames} | corners: {len(an.zones)} | "
            f"warnings: {advice_count} | beeps: {an.stats.cues + spin_cue_count} | "
            f"calls not spoken: {an.stats.skipped + voice.dropped_calls}",
            flush=True,
        )
        _LOG.info(
            "summary: received=%s errors=%s frames=%s zones=%s events=%s cues=%s",
            lst.stats["received"], lst.stats["errors"], st.frames,
            len(an.zones), advice_count, an.stats.cues + spin_cue_count,
        )
    finally:
        stop_watch.set()
        if server is not None:
            server.shutdown()
        lst.close()
        if recorder is not None:
            recorder.close()
        if polisher is not None:
            polisher.close()
        voice.close()


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if not getattr(args, "command", None):
        parser.print_help(sys.stderr)
        return 2
    try:
        args.func(args)
    except KeyboardInterrupt:
        print("\n👋 exited", flush=True)
    return 0
