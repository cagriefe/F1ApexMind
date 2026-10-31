#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) ApexMind contributors
"""Build the static site (for GitHub Pages): the web UI, the Python package as a zip, the sample session.

Usage: python tools/build_site.py [OUTPUT_DIR]   (default: _site)
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from apexmind.webserver import WEBUI_DIR, package_zip_bytes  # noqa: E402


def main(out_dir: str = "_site") -> None:
    out = Path(out_dir)
    shutil.rmtree(out, ignore_errors=True)
    shutil.copytree(WEBUI_DIR, out)
    (out / "apexmind.zip").write_bytes(package_zip_bytes())
    (out / ".nojekyll").write_text("", encoding="utf-8")
    size = sum(f.stat().st_size for f in out.rglob("*") if f.is_file()) // 1024
    print(f"site written to {out} ({size} KiB)")


if __name__ == "__main__":
    main(*sys.argv[1:2])
