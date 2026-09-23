#!/bin/bash
# Daily brief runner for launchd (Mac only). Generates brief.json, commits, pushes.
# Logs to ~/Library/Logs/bell-block-brief.log. Extra args via BRIEF_ARGS (e.g. --dry-run).
set -u
export PATH="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin"
export LANG="en_US.UTF-8"
REPO="$(cd "$(dirname "$0")/.." && pwd)"
LOG="$HOME/Library/Logs/bell-block-brief.log"
mkdir -p "$(dirname "$LOG")"
{
  echo "=== $(date '+%Y-%m-%d %H:%M:%S %Z') start"
  cd "$REPO" || { echo "repo not found: $REPO"; exit 1; }
  git pull -q --rebase origin main 2>&1 || echo "git pull failed (continuing with local main)"
  /usr/bin/python3 -W ignore brief.py ${BRIEF_ARGS:-} 2>&1
  rc=$?
  echo "=== exit $rc"
  exit $rc
} >> "$LOG" 2>&1
