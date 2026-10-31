# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) ApexMind contributors
"""Socket loop that listens to the F1 game's UDP telemetry stream."""

from __future__ import annotations

import logging
import socket
import time
from collections.abc import Callable

_LOG = logging.getLogger(__name__)


class UdpListener:
    """Listens on a single UDP port; calls ``on_packet(bytes)`` for every datagram."""

    def __init__(
        self,
        port: int,
        host: str = "",
        on_packet: Callable[[bytes], None] | None = None,
        *,
        recv_timeout: float = 0.5,
        rcvbuf: int = 1 << 20,
    ):
        self.on_packet = on_packet
        self.stats: dict[str, int] = {"received": 0, "errors": 0, "bytes": 0}
        self.last_packet_at: float | None = None
        self._stop = False

        try:
            self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
            try:
                self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, rcvbuf)
            except OSError:
                pass
            self.sock.settimeout(recv_timeout)
            self.sock.bind((host, port))
        except OSError as e:  # port busy / permission denied
            raise SystemExit(
                f"⛔ Could not bind UDP port {port}: {e} — "
                "another ApexMind instance may be running; "
                "run pkill -f 'apexmind listen' and try again."
            ) from e

    def run(self) -> None:
        while not self._stop:
            try:
                data, _addr = self.sock.recvfrom(8192)
            except TimeoutError:
                continue
            except OSError:
                return  # socket closed
            self.stats["received"] += 1
            self.stats["bytes"] += len(data)
            self.last_packet_at = time.monotonic()
            if self.on_packet is not None:
                try:
                    self.on_packet(data)
                except Exception as e:  # malformed packet -> keep running
                    self.stats["errors"] += 1
                    if self.stats["errors"] <= 5:
                        print(f"⚠️ packet error: {e}", flush=True)
                    _LOG.debug("packet handling error", exc_info=True)

    def close(self) -> None:
        self._stop = True
        try:
            self.sock.close()
        except OSError:
            pass
