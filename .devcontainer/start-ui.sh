#!/usr/bin/env bash
# Start "workshop ui" on every Codespace start, detached (setsid) so it outlives the lifecycle
# command that launched it. Port 8765 opens as the "Your lab" preview. Safe to run twice.
#   bash .devcontainer/start-ui.sh --restart   after an update: the page server picks up the new code (reload the page)
cd "$(dirname "$0")/.."
if [ "${1:-}" = "--restart" ]; then
  pkill -f "workshop ui --port 8765" 2>/dev/null || true
  for _ in 1 2 3 4 5 6 7 8 9 10; do ss -ltn 2>/dev/null | grep -q ':8765 ' || break; sleep 0.5; done
fi
if ss -ltn 2>/dev/null | grep -q ':8765 '; then
  exit 0
fi
setsid -f .venv/bin/workshop ui --port 8765 > /tmp/workshop-ui.log 2>&1 < /dev/null
