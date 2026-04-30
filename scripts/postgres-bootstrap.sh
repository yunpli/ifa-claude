#!/usr/bin/env bash
# Bootstrap a local PostgreSQL 16 cluster dedicated to iFA.
#
# - Installs postgresql@16 via Homebrew if not already present.
# - Initializes a fresh data dir at /Users/neoclaw/claude/ifaenv/pgdata.
# - Configures it to listen on 127.0.0.1:55432 (loopback only, non-default port
#   so it cannot collide with any other Postgres on this Mac).
# - Creates the `ifa` role and the `ifavr` / `ifavr_test` databases.
#
# Idempotent: re-running after a successful bootstrap is a no-op (skips initdb).
# To start a clean cluster, delete pgdata first.
set -euo pipefail

PGDATA="/Users/neoclaw/claude/ifaenv/pgdata"
PGPORT="55432"
PG_USER="ifa"
PG_DB_PROD="ifavr"
PG_DB_TEST="ifavr_test"
SECRETS_FILE="/Users/neoclaw/claude/ifaenv/secrets/.env"
BREW="/opt/homebrew/bin/brew"
PG_FORMULA="postgresql@16"

# ── 1. install postgresql@16 if missing ──────────────────────────────────────
if ! "$BREW" list --formula | grep -q "^${PG_FORMULA}$"; then
  echo "→ Installing $PG_FORMULA via Homebrew (this can take a few minutes)…"
  "$BREW" install "$PG_FORMULA"
else
  echo "✓ $PG_FORMULA already installed"
fi

PG_PREFIX="$($BREW --prefix $PG_FORMULA)"
INITDB="$PG_PREFIX/bin/initdb"
PG_CTL="$PG_PREFIX/bin/pg_ctl"
PSQL="$PG_PREFIX/bin/psql"
CREATEDB="$PG_PREFIX/bin/createdb"

# ── 2. read PG_PASSWORD from secrets file ────────────────────────────────────
if [[ ! -f "$SECRETS_FILE" ]]; then
  echo "✗ secrets file not found: $SECRETS_FILE"; exit 1
fi
# shellcheck disable=SC2046
export $(grep -E '^(PG_PASSWORD)=' "$SECRETS_FILE" | xargs -0 echo | tr '\n' ' ')
if [[ -z "${PG_PASSWORD:-}" ]]; then
  echo "✗ PG_PASSWORD not found in $SECRETS_FILE"; exit 1
fi

# ── 3. initdb if PGDATA empty ────────────────────────────────────────────────
if [[ -z "$(ls -A "$PGDATA" 2>/dev/null)" ]]; then
  echo "→ Running initdb at $PGDATA"
  "$INITDB" \
    --pgdata="$PGDATA" \
    --username=postgres \
    --auth-local=trust \
    --auth-host=scram-sha-256 \
    --encoding=UTF8 \
    --locale=C \
    --no-instructions

  # Loopback only, custom port
  cat >>"$PGDATA/postgresql.conf" <<EOF

# ── iFA local cluster ───────────────────────────────────────────────────────
listen_addresses = '127.0.0.1'
port = $PGPORT
unix_socket_directories = '$PGDATA'
log_destination = 'stderr'
logging_collector = on
log_directory = '/Users/neoclaw/claude/ifaenv/logs'
log_filename = 'postgres-%Y%m%d.log'
log_min_messages = 'warning'
EOF
else
  echo "✓ pgdata already initialized"
fi

# ── 4. start cluster (if not running) ────────────────────────────────────────
# LC_ALL=C is required on macOS when initdb used locale C, otherwise the
# postmaster "becomes multithreaded during startup" and aborts.
export LC_ALL=C
if ! "$PG_CTL" -D "$PGDATA" status >/dev/null 2>&1; then
  echo "→ Starting cluster on 127.0.0.1:$PGPORT"
  "$PG_CTL" -D "$PGDATA" -l "/Users/neoclaw/claude/ifaenv/logs/pg_ctl.log" start
  sleep 1
else
  echo "✓ cluster already running"
fi

# ── 5. create role + databases (idempotent) ──────────────────────────────────
PSQL_ADMIN=("$PSQL" -h "$PGDATA" -p "$PGPORT" -U postgres -d postgres -v ON_ERROR_STOP=1 -X -q)

"${PSQL_ADMIN[@]}" <<SQL
DO \$\$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '$PG_USER') THEN
    CREATE ROLE $PG_USER LOGIN PASSWORD '$PG_PASSWORD';
  ELSE
    ALTER ROLE $PG_USER WITH LOGIN PASSWORD '$PG_PASSWORD';
  END IF;
END\$\$;
SQL

for db in "$PG_DB_PROD" "$PG_DB_TEST"; do
  exists=$("${PSQL_ADMIN[@]}" -tAc "SELECT 1 FROM pg_database WHERE datname='$db'")
  if [[ "$exists" != "1" ]]; then
    echo "→ Creating database $db"
    "$CREATEDB" -h "$PGDATA" -p "$PGPORT" -U postgres -O "$PG_USER" "$db"
  else
    echo "✓ database $db already exists"
  fi
done

# ── 6. summary ───────────────────────────────────────────────────────────────
echo
echo "PostgreSQL ready:"
echo "  host=127.0.0.1  port=$PGPORT  user=$PG_USER"
echo "  databases: $PG_DB_PROD (prod), $PG_DB_TEST (test)"
echo "  data dir:  $PGDATA"
echo "  start/stop: scripts/postgres-start.sh / postgres-stop.sh"
