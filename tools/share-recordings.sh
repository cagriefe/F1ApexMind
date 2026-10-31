#!/bin/sh
# Push your recordings to a separate branch (never to main), e.g. to attach them to an issue.
# Usage: tools/share-recordings.sh [BRANCH] [RECORDINGS_DIR]
set -eu
BRANCH="${1:-session-data}"
DIR="${2:-recordings}"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
REMOTE="$(git -C "$ROOT" remote get-url origin)"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

git init -q "$TMP"
cd "$TMP"
git remote add origin "$REMOTE"
if git fetch -q origin "$BRANCH" 2>/dev/null; then
  git checkout -q -b "$BRANCH" FETCH_HEAD
else
  git checkout -q --orphan "$BRANCH"
fi
mkdir -p recordings
cp "$ROOT/$DIR"/session-* recordings/
git add recordings
if git diff --cached --quiet; then
  echo "Nothing new to share."
  exit 0
fi
git commit -q -m "Add recordings"
git push -q origin "$BRANCH"
echo "Pushed to branch '$BRANCH'."
