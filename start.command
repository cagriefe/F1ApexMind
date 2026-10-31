#!/bin/zsh
# ApexMind launcher: double-click in Finder.
# Starts ApexMind and opens the web dashboard in your browser; this window keeps the log running.
# It listens for the game's UDP telemetry and speaks the calls. Recordings go to ./recordings.
cd "$(dirname "$0")" || exit 1
export PYTHONPATH="src"

# Finder's PATH often points at /usr/bin/python3 (3.9); look for 3.10+.
PY=""
for candidate in \
  python3.13 python3.12 python3.11 python3.10 \
  /Library/Frameworks/Python.framework/Versions/3.13/bin/python3 \
  /Library/Frameworks/Python.framework/Versions/3.12/bin/python3 \
  /Library/Frameworks/Python.framework/Versions/3.11/bin/python3 \
  /opt/homebrew/bin/python3 /usr/local/bin/python3 python3
do
  if command -v "$candidate" >/dev/null 2>&1; then
    if "$candidate" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)' >/dev/null 2>&1; then
      PY="$candidate"
      break
    fi
  fi
done

if [ -z "$PY" ]; then
  echo "ApexMind needs Python 3.10 or newer."
  echo "Install Python 3.12 from python.org and try again."
  echo ""
  echo "Press Enter to close..."
  read -r
  exit 1
fi

"$PY" -m apexmind listen --record "$PWD/recordings" --open "$@"
exit_code=$?
# Keep the window open on errors so the message can be read.
if [ "$exit_code" -ne 0 ] && [ "$exit_code" -ne 130 ]; then
  echo ""
  echo "ApexMind exited with error code: $exit_code"
  echo "Press Enter to close..."
  read -r
fi
