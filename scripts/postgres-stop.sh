#!/usr/bin/env bash
set -euo pipefail
PGDATA="/Users/neoclaw/claude/ifaenv/pgdata"
PG_PREFIX="$(/opt/homebrew/bin/brew --prefix postgresql@16)"
exec "$PG_PREFIX/bin/pg_ctl" -D "$PGDATA" -m fast stop
