#!/bin/bash
# Usage: [PORT=8877] ./run.sh [--demo | path/to/export.zip]
#   --demo  writes a SYNTHETIC export to demo-data/, imports it and serves it
#   path    imports a real export into data/ (or $HEALTHLAB_DATA), then serves
set -e
cd "$(dirname "$0")"
[ -d .venv ] || { "${PYTHON:-python3.11}" -m venv .venv; .venv/bin/pip -q install -r requirements.txt; }
if [ "$1" = "--demo" ]; then
  export HEALTHLAB_DATA="$PWD/demo-data"
  rm -rf demo-data
  .venv/bin/python -m healthlab.synth demo-data/export.zip
  set -- demo-data/export.zip
fi
export HEALTHLAB_DATA="${HEALTHLAB_DATA:-$PWD/data}"
if [ -n "$1" ]; then
  .venv/bin/python -m healthlab.parse "$1" "$HEALTHLAB_DATA" > /dev/null
  .venv/bin/python -W ignore -m healthlab.build "$HEALTHLAB_DATA" > /dev/null
  .venv/bin/python -W ignore -m healthlab.insights > /dev/null
fi
PORT="${PORT:-8877}"
if curl -sf -o /dev/null "http://127.0.0.1:$PORT/api/state"; then
  echo "Health Lab is already running: http://127.0.0.1:$PORT"
  echo "To stop it: pkill -f 'uvicorn healthlab.api'"
  exit 0
fi
echo "Health Lab: http://127.0.0.1:$PORT  (data: $HEALTHLAB_DATA)"
exec .venv/bin/python -W ignore -m uvicorn healthlab.api:app --host 127.0.0.1 --port "$PORT" --no-access-log --log-level warning
