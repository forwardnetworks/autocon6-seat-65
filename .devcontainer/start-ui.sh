#!/usr/bin/env bash
# Start "workshop ui" on every Codespace start, detached (setsid) so it outlives the lifecycle
# command that launched it. Port 8765 opens as the "Your lab" preview. Safe to run twice.
cd "$(dirname "$0")/.."
if ss -ltn 2>/dev/null | grep -q ':8765 '; then
  exit 0
fi
setsid -f .venv/bin/workshop ui --port 8765 > /tmp/workshop-ui.log 2>&1 < /dev/null
