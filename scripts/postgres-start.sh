#!/usr/bin/env bash
set -euo pipefail
PGDATA="/Users/neoclaw/claude/ifaenv/pgdata"
PG_PREFIX="$(/opt/homebrew/bin/brew --prefix postgresql@16)"
# LC_ALL=C avoids the macOS "postmaster became multithreaded" startup failure
# when the cluster was initdb'd with locale C.
export LC_ALL=C
exec "$PG_PREFIX/bin/pg_ctl" -D "$PGDATA" \
  -l "/Users/neoclaw/claude/ifaenv/logs/pg_ctl.log" start
