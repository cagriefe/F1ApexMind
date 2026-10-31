# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) ApexMind contributors
"""A browser that drops its connection must not spray tracebacks into the terminal."""

import contextlib
import io
import socket
import struct
import time
import unittest

from apexmind.webserver import LiveHub, start_server


class QuietOnDisconnect(unittest.TestCase):
    def test_reset_connections_print_nothing(self):
        hub = LiveHub()
        server = start_server(hub, "127.0.0.1", 0)
        self.addCleanup(server.shutdown)
        port = server.server_address[1]
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            for _ in range(5):
                s = socket.create_connection(("127.0.0.1", port))
                s.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER, struct.pack("ii", 1, 0))
                s.close()                                  # RST instead of FIN: ConnectionResetError
            time.sleep(0.5)
        self.assertEqual(err.getvalue(), "")

    def test_real_requests_still_work(self):
        server = start_server(LiveHub(), "127.0.0.1", 0)
        self.addCleanup(server.shutdown)
        s = socket.create_connection(("127.0.0.1", server.server_address[1]))
        s.sendall(b"GET /api/live HTTP/1.0\r\n\r\n")
        data = b""
        while chunk := s.recv(4096):
            data += chunk
        self.assertTrue(b" 200 " in data.split(b"\r\n")[0], data[:40])


if __name__ == "__main__":
    unittest.main()
