#!/usr/bin/env bash
# install_macos.sh — iFA one-shot macOS setup
# Detects what's already installed and only installs what's missing.
set -euo pipefail

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; NC='\033[0m'
ok()   { echo -e "${GREEN}✓${NC} $*"; }
warn() { echo -e "${YELLOW}!${NC} $*"; }
die()  { echo -e "${RED}✗${NC} $*"; exit 1; }

echo ""
echo "iFA — macOS setup"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

# ── 1. Homebrew ───────────────────────────────────────────────────────────────
if command -v brew &>/dev/null; then
    ok "Homebrew already installed ($(brew --version | head -1))"
else
    warn "Homebrew not found — installing..."
    /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
    # add brew to PATH for Apple Silicon
    if [[ -f /opt/homebrew/bin/brew ]]; then
        eval "$(/opt/homebrew/bin/brew shellenv)"
    fi
    ok "Homebrew installed"
fi

# ── 2. PostgreSQL 16 ──────────────────────────────────────────────────────────
PG_BIN=""
if command -v pg_ctl &>/dev/null; then
    PG_BIN=$(dirname "$(command -v pg_ctl)")
    ok "PostgreSQL already installed ($("${PG_BIN}/postgres" --version))"
elif brew list postgresql@16 &>/dev/null 2>&1; then
    brew link --force postgresql@16
    PG_BIN="$(brew --prefix postgresql@16)/bin"
    ok "PostgreSQL 16 linked from Homebrew"
else
    warn "PostgreSQL 16 not found — installing via Homebrew..."
    brew install postgresql@16
    brew link --force postgresql@16
    PG_BIN="$(brew --prefix postgresql@16)/bin"
    ok "PostgreSQL 16 installed"
fi

# ensure pg binaries are on PATH
export PATH="${PG_BIN}:$PATH"

# ── 3. uv ─────────────────────────────────────────────────────────────────────
if command -v uv &>/dev/null; then
    ok "uv already installed ($(uv --version))"
else
    warn "uv not found — installing..."
    curl -LsSf https://astral.sh/uv/install.sh | sh
    export PATH="$HOME/.cargo/bin:$HOME/.local/bin:$PATH"
    ok "uv installed"
fi

# ── 4. Python venv + dependencies ────────────────────────────────────────────
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
echo ""
echo "Installing Python dependencies in ${REPO_ROOT}..."
cd "$REPO_ROOT"

uv venv --python 3.12 2>/dev/null || uv venv  # reuse if .venv already exists
uv sync
ok "Python dependencies installed"

# ── 5. secrets/.env ───────────────────────────────────────────────────────────
SECRETS_DIR="$HOME/claude/ifaenv/secrets"
ENV_FILE="${SECRETS_DIR}/.env"

mkdir -p "$SECRETS_DIR"

if [[ -f "$ENV_FILE" ]]; then
    ok ".env already exists at ${ENV_FILE} — skipping"
else
    warn ".env not found — creating from template..."
    cp "$REPO_ROOT/.env.example" "$ENV_FILE"
    chmod 600 "$ENV_FILE"
    warn "Fill in your secrets:"
    warn "  ${ENV_FILE}"
    warn "Then re-run: uv run alembic upgrade head && ifa healthcheck"
fi

# ── 6. Database bootstrap ─────────────────────────────────────────────────────
echo ""
PG_DATA="$HOME/claude/ifaenv/pgdata"

if [[ -d "$PG_DATA/base" ]]; then
    ok "PostgreSQL data directory already initialised at ${PG_DATA}"
else
    warn "Initialising PostgreSQL cluster at ${PG_DATA}..."
    bash "$REPO_ROOT/scripts/postgres-bootstrap.sh"
    ok "PostgreSQL cluster initialised"
fi

# start postgres if not already running
if pg_ctl -D "$PG_DATA" status &>/dev/null; then
    ok "PostgreSQL is running"
else
    warn "Starting PostgreSQL..."
    bash "$REPO_ROOT/scripts/postgres-start.sh"
    sleep 2
    ok "PostgreSQL started"
fi

# ── 7. Alembic migrations ─────────────────────────────────────────────────────
if [[ -f "$ENV_FILE" ]] && grep -qv "^#\|^$\|=your_\|=<\|=\.\.\." "$ENV_FILE" 2>/dev/null; then
    echo ""
    warn "Running database migrations..."
    uv run alembic upgrade head
    ok "Migrations applied"
else
    echo ""
    warn "Skipping migrations — fill in ${ENV_FILE} first, then run:"
    warn "  uv run alembic upgrade head"
fi

# ── 8. Summary ────────────────────────────────────────────────────────────────
echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "Setup complete. Next steps:"
echo ""
echo "  1. Edit ${ENV_FILE}"
echo "     (fill in TUSHARE_TOKEN, LLM_PRIMARY_*, PG_PASSWORD, etc.)"
echo ""
echo "  2. uv run alembic upgrade head   # if skipped above"
echo ""
echo "  3. ifa healthcheck               # verify LLM + TuShare + DB"
echo ""
echo "  4. ifa generate market --slot evening --report-date \$(date +%Y-%m-%d) \\"
echo "         --user default --generate-pdf"
echo ""
