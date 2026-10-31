# Contributing to ApexMind

Thanks for helping. Bug reports, recordings from other tracks, and pull requests are all welcome.

## Reporting a problem

Open an issue and include:

- game and version (for example F1 25, UDP format 2025), your OS and Python version,
- what you expected and what happened (what was said, where on track),
- if you can, a recording: run with `--record recordings` and attach the `.csv` (or push it with `tools/share-recordings.sh`). The Analyze view and `apexmind replay` make recordings easy to inspect.

## Development setup

```
python3 -m venv .venv && . .venv/bin/activate
pip install -e '.[dev]'
ruff check src tests tools
pytest -q
```

The core must stay dependency-free (standard library only) and support Python 3.10+. The web UI has no build step: edit the files in `src/apexmind/webui/`, then run `apexmind listen --web` or `python tools/build_site.py`.

## Pull requests

- Keep changes focused and add or update tests. Timing changes should be covered by the simulation in `tests/sim.py`; changes to thresholds should cite real data.
- Behaviour you could not verify with the real game should be said so in the pull request.
- `ruff check` and `pytest` must pass (CI runs both).
- Code, comments, identifiers and user-facing text are in English.

## License

By contributing you agree that your work is licensed under the GNU Affero General Public License v3.0 or later, like the rest of the project. Source files carry an `SPDX-License-Identifier: AGPL-3.0-or-later` header; please add it to new files.
