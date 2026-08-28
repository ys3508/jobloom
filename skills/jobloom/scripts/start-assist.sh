#!/bin/sh
# Start the assist bridge for this workspace. Run it once; leave it running.
# The token is kept in .jobloom/assist-token and reused, so the panel is configured once.
set -e
cd "$(dirname "$0")/../../.."
exec python3 skills/jobloom/scripts/assist_bridge.py \
  --db .jobloom/jobloom.db \
  --candidate "${JOBLOOM_CANDIDATE:-.jobloom/candidate-v15.json}" \
  "$@"
