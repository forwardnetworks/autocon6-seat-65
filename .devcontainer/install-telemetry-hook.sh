#!/usr/bin/env bash
# Add the beta activity hook to ~/.bashrc and ~/.zshrc. Safe to re-run. Runs from onCreateCommand, before the
# editor attaches, so the very first terminal already loads it (bootstrap.sh runs it again as a backstop).
set -euo pipefail
root="$(cd "$(dirname "$0")/.." && pwd)"
for rc in "$HOME/.bashrc" "$HOME/.zshrc"; do
  [ -f "$rc" ] || touch "$rc"
  if ! grep -q "AC6_ROOT" "$rc"; then
    printf '\nexport AC6_ROOT=%q\n[ -r "$AC6_ROOT/.devcontainer/telemetry.sh" ] && . "$AC6_ROOT/.devcontainer/telemetry.sh"\n' "$root" >> "$rc"
  fi
done
