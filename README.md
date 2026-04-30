# ifa-claude — iFA China Market Report System

AI-native, structured, auditable daily market intelligence for China A-share investors.
Customer-facing reports are **Chinese**; this engineering spec is **English**.

---

## What this repo is

A report-generation system that turns market data, news, policy, macro variables,
commodity prices, and sector behavior into **structured, source-anchored, reviewable
investment judgments** — and renders them as professional-grade HTML reports.

It is **not** a chatbot. It is **not** a market data terminal. Every report is a
deterministic *report run* with persisted facts, signals, judgments, prompts,
LLM I/O, and source references. Every claim is later reviewable against the market.

Product spec & rationale: see [`docs/`](docs/) and the local design notes in
`/Users/neoclaw/claude/local/` (`ifa-overall.txt`, `ifa-macro-v1.txt`,
`project-joblist.txt`, `tushare-probe.txt`, `tushare-landscapte.txt`).

---

## Scope of this iteration

The full product spans ~21 P0 templates (long reports + intraday briefings + weekend),
plus P1/P2 layers. **This iteration delivers a slice:**

| Area                      | In scope                             | Out of scope (later) |
|---------------------------|--------------------------------------|----------------------|
| Report families           | Macro morning, Macro evening         | Main A-share, Tech, Asset, weekend, briefings |
| Pre-jobs                  | `macro_text_derived_capture_job`, `macro_policy_event_memory_job` (scaffolding) | OCR / PDF research extraction |
| Common tools              | LLM client (with fallback), TuShare client, DB layer, HTML renderer | — |
| Output formats            | HTML (institutional style)           | PDF, Markdown KB, Telegram |
| User system               | None                                 | Subscriptions, watchlists, entitlements |
| Database                  | Local PostgreSQL `ifavr` / `ifavr_test` | Cloud, replication |

The reporting database `IFAVR` is shared across **all** future report families
(`report_runs`, `report_facts`, `report_signals`, `report_judgments`, …) so the
schema we build now serves the whole P0 → P2 roadmap, not only Macro.

---

## Repo layout

```
ifa-claude/
├── README.md
├── pyproject.toml                # uv-managed, Python 3.12
├── .python-version
├── .env.example                  # documents env vars (no secrets)
├── alembic.ini                   # (added once schema is approved)
├── alembic/                      # migrations
├── docs/
│   ├── architecture.md
│   ├── database-schema.md        # IFAVR table-by-table design
│   └── run-modes.md
├── ifa/                          # main package
│   ├── config.py                 # Pydantic Settings, loads ifaenv/secrets/.env
│   ├── runtime.py                # RunContext: mode, run_id, output paths
│   ├── core/
│   │   ├── llm/                  # OpenAI-compatible client w/ primary→fallback
│   │   ├── tushare/              # token-aware data wrapper
│   │   ├── db/                   # SQLAlchemy engine + ORM
│   │   ├── render/               # Jinja2 HTML templates
│   │   └── report/               # ReportRun lifecycle, Section base, FSJ helpers
│   ├── families/
│   │   └── macro/
│   │       ├── morning/          # 12 sections per ifa-macro-v1
│   │       ├── evening/          # 11 sections
│   │       └── prompts/          # versioned prompt YAML
│   ├── jobs/
│   │   ├── macro_text_capture.py
│   │   └── macro_policy_memory.py
│   └── cli/                      # `ifa healthcheck`, `ifa generate macro ...`
├── scripts/
│   ├── postgres-bootstrap.sh     # brew install + initdb at ifaenv/pgdata
│   ├── postgres-start.sh
│   └── postgres-stop.sh
└── tests/
```

External (not in repo, gitignored anyway):

```
/Users/neoclaw/claude/ifaenv/
├── secrets/.env                  # all API keys & DB password (chmod 600)
├── pgdata/                       # PostgreSQL data directory
├── out/{test,manual,production}/ # rendered reports + JSON snapshots
└── logs/
```

---

## Run modes

Every report run records the mode it was triggered under. Set via `--mode` CLI flag
or `IFA_RUN_MODE` env. Three modes:

| Mode         | When                                    | DB                          | Output dir                              |
|--------------|-----------------------------------------|-----------------------------|-----------------------------------------|
| `test`       | Developer or CI testing                 | `ifavr_test` (separate DB)  | `ifaenv/out/test/<date>/<run-id>/`      |
| `manual`     | Operator triggers a re-run after deploy | `ifavr`                     | `ifaenv/out/manual/<date>/<run-id>/`    |
| `production` | Cron-scheduled run                      | `ifavr`                     | `ifaenv/out/production/<date>/<run-id>/`|

`report_runs.run_mode` is recorded so we can filter/exclude test runs from any
review/aggregation query. See [`docs/run-modes.md`](docs/run-modes.md).

---

## Setup

### 1. Bootstrap the Python env
```bash
cd /Users/neoclaw/claude/ifa-claude
uv venv --python 3.12
uv sync
```

### 2. Install local PostgreSQL (one-time)
```bash
./scripts/postgres-bootstrap.sh    # brew install postgresql@16, initdb to ifaenv/pgdata
./scripts/postgres-start.sh        # starts cluster on 127.0.0.1:55432
```

### 3. Verify everything is wired
```bash
uv run ifa healthcheck
```
Pings primary LLM, fallback LLM, TuShare, and the database.

### 4. Generate a Macro report (once schema & sections are landed)
```bash
uv run ifa generate macro \
  --slot morning \
  --report-date 2026-04-29 \
  --data-cutoff "2026-04-29T08:45:00+08:00" \
  --mode manual
```

---

## Secrets

API keys, DB passwords, and tokens **never** enter this repo. They live in
`/Users/neoclaw/claude/ifaenv/secrets/.env` (chmod 600). `ifa.config` reads that
file via `IFA_SECRETS_FILE` env. `.env.example` in the repo only documents the
variable names and non-secret defaults.

---

## Compliance footer

All formal reports include the `Lindenwood Management LLC` disclaimer (English short
header + full English/Chinese long-form). Reports are informational and research
only — never investment advice.
