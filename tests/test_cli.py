# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) ApexMind contributors
import unittest

from apexmind.cli import build_parser


class CliParserTest(unittest.TestCase):
    def test_defaults(self):
        args = build_parser().parse_args(["listen"])
        self.assertEqual(args.command, "listen")
        self.assertEqual(args.port, 20777)
        self.assertAlmostEqual(args.cue_lead, 0.0)
        self.assertAlmostEqual(args.cue_min_m, 0.0)
        self.assertEqual(args.llm, "off")
        self.assertEqual(args.log, "auto")
        self.assertFalse(args.mute)
        self.assertFalse(args.no_cues)

    def test_options(self):
        args = build_parser().parse_args(
            [
                "listen",
                "--port", "20779",
                "--cue-lead", "2.2",
                "--no-cues",
                "--mute",
                "--llm", "auto",
                "--voice-rate", "240",
            ]
        )
        self.assertEqual(args.port, 20779)
        self.assertAlmostEqual(args.cue_lead, 2.2)
        self.assertTrue(args.no_cues)
        self.assertTrue(args.mute)
        self.assertEqual(args.llm, "auto")
        self.assertEqual(args.voice_rate, 240)

    def test_invalid_port(self):
        with self.assertRaises(SystemExit):
            build_parser().parse_args(["listen", "--port", "70000"])


if __name__ == "__main__":
    unittest.main()
