# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) ApexMind contributors
import json
import socket
import unittest
from unittest import mock

from apexmind.engineer import llm
from apexmind.telemetry.listener import UdpListener


class ListenerTests(unittest.TestCase):
    def test_busy_port_exits_with_message(self):
        first = UdpListener(0, host="127.0.0.1")
        try:
            port = first.sock.getsockname()[1]
            with self.assertRaises(SystemExit) as ctx:
                UdpListener(port, host="127.0.0.1")
            self.assertIn("Could not bind", str(ctx.exception))
        finally:
            first.close()

    def test_receives_datagram(self):
        got = []
        lst = UdpListener(0, host="127.0.0.1", on_packet=got.append, recv_timeout=0.05)
        try:
            port = lst.sock.getsockname()[1]
            tx = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            tx.sendto(b"hi", ("127.0.0.1", port))
            tx.close()
            data, _ = lst.sock.recvfrom(16)
            self.assertEqual(data, b"hi")
        finally:
            lst.close()


class LlmPayloadTests(unittest.TestCase):
    def test_openai_endpoint_gets_top_level_limits(self):
        captured = {}

        class _Resp:
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def read(self, *a):
                return json.dumps({"choices": [{"message": {"content": '"Box now"'}}]}).encode()

        def fake_urlopen(req, timeout=None):
            captured["body"] = json.loads(req.data)
            return _Resp()

        with mock.patch.object(llm.urllib.request, "urlopen", fake_urlopen):
            out = llm.polish("Braking 20 meters EARLY.")
        self.assertEqual(out, "Box now")
        self.assertEqual(captured["body"]["max_tokens"], 32)
        self.assertNotIn("options", captured["body"])


if __name__ == "__main__":
    unittest.main()
