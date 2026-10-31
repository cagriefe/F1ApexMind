# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) ApexMind contributors
"""Optional LOCAL LLM radio polish via Ollama: free, no API key.

IMPORTANT: during a live race the LLM **never** blocks the UDP loop. ``AsyncPolisher``
runs in a separate thread; if the model has not answered within a second the raw short
message is spoken. Disabled by default (``--llm off``); enabling it adds latency.

One-time setup:
  brew install ollama && ollama serve &
  ollama pull qwen2.5:3b
"""

from __future__ import annotations

import json
import queue
import threading
import time
import urllib.request
from collections.abc import Callable

BASE = "http://127.0.0.1:11434"
SYSTEM = (
    "You are an F1 race engineer giving short radio calls to your driver. "
    "Rephrase the given advice into one punchy radio line, max 8 words, plain English. "
    "Keep every number and unit exactly as given. Output only the radio line."
)

def available(timeout: float = 0.4) -> bool:
    """Is Ollama running? (short timeout, silently False)"""
    try:
        with urllib.request.urlopen(f"{BASE}/api/tags", timeout=timeout) as r:
            return r.status == 200
    except Exception:
        return False


def polish(text: str, model: str = "qwen2.5:3b", timeout: float = 1.0) -> str | None:
    """Rewrite the advice in more natural radio language with the local model.

    On error / timeout returns ``None`` (the caller falls back to the raw text).
    """
    body = json.dumps(
        {
            "model": model,
            "messages": [
                {"role": "system", "content": SYSTEM},
                {"role": "user", "content": text},
            ],
            "stream": False,
            "temperature": 0.2,
            "max_tokens": 32,
        }
    ).encode()
    req = urllib.request.Request(
        f"{BASE}/v1/chat/completions",
        data=body,
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            j = json.load(r)
        out = (j.get("choices") or [{}])[0].get("message", {}).get("content", "").strip()
        # The model sometimes adds quotes or a heading; strip them.
        out = out.strip().strip('"').strip()
        return out or None
    except Exception:
        return None


class AsyncPolisher:
    """Polishes radio messages in the background; speaks the raw text if it is late."""

    def __init__(
        self,
        speak_cb: Callable[..., None],
        *,
        model: str = "qwen2.5:3b",
        timeout: float = 1.0,
        max_age: float = 1.5,
    ):
        self.speak = speak_cb
        self.model = model
        self.timeout = timeout
        self.max_age = max_age
        self._q: queue.Queue[tuple[float, str, str] | None] = queue.Queue(maxsize=2)
        self._stop = threading.Event()
        self._t = threading.Thread(
            target=self._worker, name="apexmind-llm", daemon=True
        )
        self._t.start()

    def submit(self, kind: str, text: str) -> bool:
        """Queue a message. Returns False if the queue is full (the raw message already went out)."""
        try:
            self._q.put_nowait((time.monotonic(), kind, text))
            return True
        except queue.Full:
            return False

    def _worker(self) -> None:
        while not self._stop.is_set():
            try:
                item = self._q.get(timeout=0.5)
            except queue.Empty:
                continue
            if item is None:
                return
            queued_at, kind, text = item
            if time.monotonic() - queued_at > self.max_age:
                continue

            polished = polish(text, model=self.model, timeout=self.timeout)
            if time.monotonic() - queued_at > self.max_age:
                continue
            self.speak(polished or text, urgent=(kind == "spin"))

    def close(self) -> None:
        self._stop.set()
        try:
            self._q.put_nowait(None)
        except queue.Full:
            pass
        if self._t.is_alive():
            self._t.join(timeout=1.0)
