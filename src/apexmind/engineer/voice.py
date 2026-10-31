# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) ApexMind contributors
"""macOS audio engine.

Design rules:
  - **Beeps never enter the speech queue.** A separate priority worker plays them instantly with
    ``afplay``; they are audible even while the radio is talking.
  - The speech queue drops stale messages. Saying nothing is better than saying "brake"
    after the corner has passed.
  - Urgent messages such as a spin interrupt the current speech.
  - The ``say`` rate defaults to 220 wpm: faster than normal, still clear.
  - Beep WAVs are generated at start-up so the first beep is not delayed.
"""

from __future__ import annotations

import hashlib
import logging
import math
import os
import queue
import re
import shutil
import struct
import subprocess
import sys
import tempfile
import threading
import time
import wave
from collections.abc import Callable

_LOG = logging.getLogger(__name__)

# An English voice is preferred (installed on every macOS); override with --voice.
_PREFERRED = ("Samantha", "Daniel", "Karen", "Moira", "Nicky", "Tessa", "Ava", "Serena", "Allison")


def _has(cmd: str) -> bool:
    return shutil.which(cmd) is not None


def detect_voice() -> str | None:
    """Find an installed, known 'say' voice; None if there is none (system default)."""
    if not _has("say"):
        return None
    try:
        out = subprocess.run(
            ["say", "-v", "?"], capture_output=True, text=True, timeout=5
        ).stdout
    except Exception:
        return None

    available: set[str] = set()
    for line in out.splitlines():
        # "Samantha            en_US    # ..." or "Eddy (English (UK))  en_GB ..."
        m = re.match(r"^(.+?)\s+([a-z]{2}_[A-Z]{2})\s", line)
        if m:
            available.add(m.group(1).strip())
    for cand in _PREFERRED:
        if cand in available:
            return cand
    return None


def _beep_path(
    kind: str,
    freq: int,
    dur_ms: int,
    *,
    gap_ms: int = 0,
    double: bool = False,
    volume: float = 0.65,
) -> str | None:
    """Generate a short 'ding' WAV and return its path (cached)."""
    name = f"apexmind_{kind}_{freq}_{dur_ms}_{gap_ms}_{int(double)}_{int(volume * 100)}.wav"
    path = os.path.join(tempfile.gettempdir(), name)
    if os.path.exists(path) and os.path.getsize(path) > 44:
        return path

    sr = 22050
    n = max(1, dur_ms * sr // 1000)
    gap = max(0, gap_ms * sr // 1000)
    total = n + gap + (n if double else 0)
    frames = [0.0] * total
    fade = max(1, int(0.004 * sr))

    def add_pulse(start: int, f: float) -> None:
        for i in range(n):
            amp = volume
            if i < fade:
                amp *= i / fade
            if i >= n - fade:
                amp *= (n - i) / fade
            frames[start + i] += amp * math.sin(2 * math.pi * f * i / sr)

    add_pulse(0, freq)
    if double:
        add_pulse(n + gap, freq * 1.25)  # the second beep is slightly higher

    buf = bytearray()
    for v in frames:
        s = int(max(-1.0, min(1.0, v)) * 32767)
        buf += struct.pack("<h", s)

    tmp = f"{path}.{os.getpid()}.{threading.get_ident()}.tmp"
    try:
        with wave.open(tmp, "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(sr)
            w.writeframes(bytes(buf))
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        _LOG.debug("could not create beep file: %s", kind, exc_info=True)
        return None
    return path


class Voice:
    """Radio speech + instant beeps; never blocks the main thread."""

    def __init__(
        self,
        enabled: bool = True,
        name: str = "auto",
        *,
        rate: int = 220,
        min_gap_s: float = 1.5,
        max_queue: int = 6,
    ):
        self.enabled = bool(enabled) and sys.platform == "darwin" and _has("say")
        if enabled and not self.enabled:
            _LOG.warning("voice disabled: macOS 'say' command not found")
        self.rate = max(120, min(400, int(rate)))
        self.min_gap_s = min_gap_s
        self.voice = (
            (name if name != "auto" else detect_voice()) if self.enabled else None
        )
        self._speech_q: queue.Queue[tuple[float, str, float, bool] | None] = queue.Queue(
            maxsize=max(1, max_queue)
        )
        self._cue_q: queue.Queue[str | None] = queue.Queue(maxsize=8)
        self._lock = threading.Lock()
        self._proc: subprocess.Popen | None = None
        self._proc_urgent = False
        self._stop = threading.Event()
        self._last_text = ""
        self._last_time = 0.0
        self._speak_lock = threading.Lock()
        self._cue_files: dict[str, str] = {}
        # Pre-rendered short calls: text -> (wav path, duration in s)
        self._phrase_files: dict[str, tuple[str, float]] = {}
        self._phrase_q: queue.Queue[tuple[float, str] | None] = queue.Queue(maxsize=4)
        self._phrase_thread: threading.Thread | None = None
        self._render_thread: threading.Thread | None = None
        self.phrases_ready = threading.Event()
        self.measured_latency: float | None = None
        self._clip_procs: list[subprocess.Popen] = []   # call clips currently playing
        self.dropped_calls = 0
        self.on_call_dropped: Callable[[str, str], None] | None = None   # (text, reason)

        self._speech_thread: threading.Thread | None = None
        self._cue_thread: threading.Thread | None = None

        if self.enabled:
            # Generate at start-up so the first beep is not delayed.
            brake = _beep_path("cue_brake", 820, 90, gap_ms=55, double=True)
            throttle = _beep_path("cue_throttle", 1380, 120)
            spin = _beep_path("cue_spin", 1500, 65, gap_ms=40, double=True)
            if brake:
                self._cue_files["brake"] = brake
            if throttle:
                self._cue_files["throttle"] = throttle
            if spin:
                self._cue_files["spin"] = spin

            self._speech_thread = threading.Thread(
                target=self._speech_worker, name="apexmind-speech", daemon=True
            )
            self._cue_thread = threading.Thread(
                target=self._cue_worker, name="apexmind-cues", daemon=True
            )
            self._phrase_thread = threading.Thread(
                target=self._phrase_worker, name="apexmind-phrases", daemon=True
            )
            self._speech_thread.start()
            self._cue_thread.start()
            self._phrase_thread.start()

    # ----- radio speech -----

    def speak(self, text: str, *, urgent: bool = False, max_age: float = 2.5) -> None:
        if not self.enabled:
            return
        text = " ".join(text.split())
        if not text:
            return

        now = time.monotonic()
        with self._speak_lock:
            if text == self._last_text and (now - self._last_time) < self.min_gap_s:
                return
            self._last_text, self._last_time = text, now

        if urgent:
            self._drop_normal_speech()
            with self._lock:
                proc = self._proc
                if proc is not None and not self._proc_urgent and proc.poll() is None:
                    try:
                        proc.terminate()
                    except OSError:
                        pass

        item = (now, text, max_age, urgent)
        try:
            self._speech_q.put_nowait(item)
        except queue.Full:
            # Drop the oldest message, put the new one in.
            try:
                self._speech_q.get_nowait()
            except queue.Empty:
                pass
            try:
                self._speech_q.put_nowait(item)
            except queue.Full:
                pass

    def _drop_normal_speech(self) -> None:
        kept: list[tuple[float, str, float, bool]] = []
        while True:
            try:
                item = self._speech_q.get_nowait()
            except queue.Empty:
                break
            if item is not None and item[3]:
                kept.append(item)
        for item in kept:
            try:
                self._speech_q.put_nowait(item)
            except queue.Full:
                break

    # ----- pre-rendered calls ("Brake 80", "Gas 40", "Full") -----

    def prepare_phrases(self, phrases: list[str], *, background: bool = True) -> None:
        """Render short calls to WAV with `say -o`; they then play via `afplay` as fast as a beep.

        Live `say` carries synthesis + voice-loading latency every time (hundreds of ms, variable);
        a ready clip has constant latency and a known duration (essential for timing).
        """
        if not self.enabled or not _has("afplay"):
            self.phrases_ready.set()
            return
        if background:
            self._render_thread = threading.Thread(
                target=self._render_phrases, args=(list(phrases),),
                name="apexmind-render", daemon=True,
            )
            self._render_thread.start()
        else:
            self._render_phrases(list(phrases))

    def _render_phrases(self, phrases: list[str]) -> None:
        cache = os.path.join(tempfile.gettempdir(), "apexmind_phrases")
        try:
            os.makedirs(cache, exist_ok=True)
        except OSError:
            self.phrases_ready.set()
            return
        for text in phrases:
            if self._stop.is_set():
                break
            key = hashlib.sha1(f"{self.voice}|{self.rate}|{text}".encode()).hexdigest()[:16]
            path = os.path.join(cache, f"{key}.wav")
            if not (os.path.exists(path) and os.path.getsize(path) > 44):
                tmp = f"{path}.{os.getpid()}.tmp.wav"
                cmd = ["say", "-o", tmp, "--data-format=LEI16@22050", "-r", str(self.rate)]
                if self.voice:
                    cmd += ["-v", self.voice]
                cmd.append(text)
                try:
                    subprocess.run(
                        cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=15, check=True
                    )
                    os.replace(tmp, path)
                except Exception:
                    _LOG.debug("could not render clip: %s", text, exc_info=True)
                    try:
                        os.unlink(tmp)
                    except OSError:
                        pass
                    continue
            try:
                with wave.open(path, "rb") as w:
                    dur = w.getnframes() / float(w.getframerate())
            except (wave.Error, OSError, EOFError):
                continue
            self._phrase_files[text] = (path, dur)
        self.phrases_ready.set()

    def measure_latency(self, runs: int = 5) -> float | None:
        """Measure the `afplay` start-up latency (silent: ``-v 0``): time difference = open + close latency.

        A beep WAV of known length is played at zero volume; the file duration is subtracted from the
        measured total. The median is taken; beeps/clips are fired this much earlier.
        """
        if not self.enabled or not _has("afplay"):
            return None
        path = self._cue_files.get("brake") or next(iter(self._cue_files.values()), None)
        if not path:
            return None
        try:
            with wave.open(path, "rb") as w:
                dur = w.getnframes() / float(w.getframerate())
        except (wave.Error, OSError, EOFError):
            return None
        samples: list[float] = []
        for _ in range(max(3, runs)):
            t0 = time.monotonic()
            try:
                subprocess.run(
                    ["afplay", "-v", "0", path], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                    timeout=5, check=True,
                )
            except Exception:
                continue
            samples.append(max(0.0, time.monotonic() - t0 - dur))
        if len(samples) < 3:
            return None
        samples.sort()
        self.measured_latency = samples[0]   # the minimum is the least inflated by one-off slow runs
        return self.measured_latency

    def phrase_duration(self, text: str) -> float | None:
        """Duration (s) of a ready clip; None if there is no clip."""
        item = self._phrase_files.get(text)
        return None if item is None else item[1]

    def call(self, text: str, *, max_age: float = 0.6) -> None:
        """Timed call: play the ready clip instantly if there is one; otherwise live `say` (urgent)."""
        if not self.enabled:
            return
        if text in self._phrase_files:
            self._drop_normal_speech()
            with self._lock:
                proc = self._proc
                if proc is not None and not self._proc_urgent and proc.poll() is None:
                    try:
                        proc.terminate()   # a long radio sentence must not delay the call
                    except OSError:
                        pass
            item = (time.monotonic(), text, max_age)
            try:
                self._phrase_q.put_nowait(item)  # type: ignore[arg-type]
            except queue.Full:
                try:
                    old = self._phrase_q.get_nowait()
                    if old is not None:
                        self._dropped(old[1], "the call queue was full")  # type: ignore[index]
                except queue.Empty:
                    pass
                try:
                    self._phrase_q.put_nowait(item)  # type: ignore[arg-type]
                except queue.Full:
                    pass
            return
        self.speak(text, urgent=True, max_age=max_age + 0.4)

    def _phrase_worker(self) -> None:
        while not self._stop.is_set():
            try:
                item = self._phrase_q.get(timeout=0.5)
            except queue.Empty:
                continue
            if item is None:
                return
            queued, text, max_age = item  # type: ignore[misc]
            if time.monotonic() - queued > max_age:
                self._dropped(text, "it was queued too long to be useful")
                continue
            entry = self._phrase_files.get(text)
            if entry is None:
                continue
            self._play_clip(entry[0], text)

    def _dropped(self, text: str, reason: str) -> None:
        self.dropped_calls += 1
        _LOG.warning("call dropped: %s (%s)", text, reason)
        if self.on_call_dropped:
            self.on_call_dropped(text, reason)

    def _play_clip(self, path: str, text: str) -> None:
        """Start a call clip without waiting for it: clips never queue behind each other.

        The newest call wins: a clip that is still playing is cut (two overlapping numbers are
        unintelligible), but the script spacing makes that rare.
        """
        try:
            proc = subprocess.Popen(["afplay", path], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except OSError:
            self._dropped(text, "afplay could not be started")
            return
        with self._lock:
            for old in self._clip_procs:
                if old.poll() is None:
                    try:
                        old.terminate()
                    except OSError:
                        pass
            self._clip_procs = [proc]

    # ----- beep cues -----

    def cue_brake(self) -> None:
        """Double low beep: BRAKE point."""
        self._enqueue_cue("brake")

    def cue_throttle(self) -> None:
        """Single high beep: THROTTLE point."""
        self._enqueue_cue("throttle")

    def cue_spin(self) -> None:
        """Double very high beep: SPIN! (comes before the radio)."""
        self._enqueue_cue("spin")

    def _enqueue_cue(self, kind: str) -> None:
        if not self.enabled:
            return
        _LOG.debug("cue queued: %s", kind)
        if kind == "spin":
            # Spin must not be stuck behind pending brake/throttle beeps.
            while True:
                try:
                    self._cue_q.get_nowait()
                except queue.Empty:
                    break
        try:
            self._cue_q.put_nowait(kind)
        except queue.Full:
            # Drop the old beep(s), put the new one first.
            try:
                self._cue_q.get_nowait()
            except queue.Empty:
                pass
            try:
                self._cue_q.put_nowait(kind)
            except queue.Full:
                pass

    # ----- workers -----

    def _run_say(self, cmd: list[str], urgent: bool) -> int | None:
        """Run the ``say`` process; can be interrupted by an urgent message or shutdown."""
        try:
            proc = subprocess.Popen(
                cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
            )
        except Exception:
            _LOG.debug("could not run say", exc_info=True)
            return None

        with self._lock:
            self._proc = proc
            self._proc_urgent = urgent
        try:
            proc.wait(timeout=20)
            return proc.returncode
        except subprocess.TimeoutExpired:
            proc.kill()
            return None
        except Exception:
            _LOG.debug("say error", exc_info=True)
            return None
        finally:
            with self._lock:
                if self._proc is proc:
                    self._proc = None
                    self._proc_urgent = False

    def _speech_worker(self) -> None:
        while not self._stop.is_set():
            try:
                item = self._speech_q.get(timeout=0.5)
            except queue.Empty:
                continue
            if item is None:
                return

            enqueued, text, max_age, urgent = item
            if time.monotonic() - enqueued > max_age:
                _LOG.debug("stale radio message dropped: %s", text)
                continue

            cmd = ["say", "-r", str(self.rate)]
            if self.voice:
                cmd += ["-v", self.voice]
            cmd.append(text)

            rc = self._run_say(cmd, urgent)
            # Negative returncode = we cut it for an urgent message / shutdown; do not read it again.
            if (
                rc is not None
                and rc > 0
                and self.voice
                and not self._stop.is_set()
            ):
                # The selected voice may have been removed; retry once with the system voice.
                self._run_say(["say", "-r", str(self.rate), text], urgent)

    def _cue_worker(self) -> None:
        while not self._stop.is_set():
            try:
                kind = self._cue_q.get(timeout=0.5)
            except queue.Empty:
                continue
            if kind is None:
                return

            path = self._cue_files.get(kind)
            _LOG.debug("cue playing: %s (%s)", kind, path)
            if path and _has("afplay"):
                try:
                    result = subprocess.run(
                        ["afplay", path],
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                        timeout=5,
                    )
                    if result.returncode == 0:
                        continue
                    _LOG.debug("afplay failed: returncode=%s", result.returncode)
                except Exception:
                    _LOG.debug("afplay error", exc_info=True)
            self._bell()

    @staticmethod
    def _bell() -> None:
        try:
            sys.stdout.write("\a")
            sys.stdout.flush()
        except Exception:
            pass

    # ----- shutdown -----

    def close(self) -> None:
        self._stop.set()
        with self._lock:
            for proc in [self._proc, *self._clip_procs]:
                if proc is not None and proc.poll() is None:
                    try:
                        proc.terminate()
                    except OSError:
                        pass
        for q in (self._speech_q, self._cue_q, self._phrase_q):
            try:
                q.put_nowait(None)
            except queue.Full:
                pass
        for t in (self._speech_thread, self._cue_thread, self._phrase_thread):
            if t is not None and t.is_alive():
                t.join(timeout=1.0)
