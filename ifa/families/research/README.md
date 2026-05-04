# Research Family — V2.2

Per-stock equity research reports built from Tushare fundamentals.

## TL;DR

```bash
# Single stock report
uv run ifa research report 智微智能
uv run ifa research report 600519 --llm                      # add LLM narratives
uv run ifa research report 智微智能 --tier quick --pdf       # rules-only + PDF
uv run ifa research report 智微智能 --tier deep --llm        # all sections + watchpoints

# Peer cohort (one anchor's L2)
uv run ifa research peer-scan 智微智能 --max-peers 20
uv run ifa research peer-scan 智微智能 --full                # full cohort (~30min)

# Multi-L2 production scan (used by full-market job)
uv run ifa research scan-universe --full --concurrency 3
uv run ifa research scan-universe --l2 801101.SI --dry-run
uv run ifa research scan-status --hours 24 --failures
uv run ifa research scan-cleanup                             # reset stale 'running' rows
uv run ifa research peer-rank-refresh                        # MUST run after scan-universe
                                                              # (recomputes peer ranks ~12s for full market)

# Cross-stock analytics (after scan + peer-rank-refresh)
uv run ifa research rank --top 20                            # top 20 by overall score
uv run ifa research rank --factor ROE --top 10               # top 10 by ROE
uv run ifa research rank --factor ROE --l2 801101.SI         # within 计算机设备
uv run ifa research rank --factor FCF --status red --bottom  # worst FCF in RED status
uv run ifa research industry-view 计算机设备                  # one-page L2 dashboard
uv run ifa research industry-view 801101.SI

# Event memory (LLM job, optional)
uv run ifa research extract-events 智微智能 --max-per-source 15
```

## Pipeline

```
resolve(name|code)
    │
    ▼
fetch_all (Tushare → research.api_cache, TTL'd per API)
    │
    ▼
load_company_snapshot — single conversion boundary; everything in 元/股/% from here
    │
    ▼
compute_<family> × 5 → 28 FactorResult (status: GREEN/YELLOW/RED/UNKNOWN)
    │
    ▼
persist_factor_results → research.factor_value
    │
    ▼
attach_peer_ranks (research.factor_value × smartmoney.sw_member_monthly PIT JOIN)
    │
    ▼
score_results — 5-dim weighted blend of `0.5 × status_base + 0.5 × peer_pct`
    │
    ▼
build_research_report → ResearchReport (typed dict)
    │       │
    │       ├── markdown.render(report) → str
    │       └── HtmlRenderer().render(report=report) → str (inline CSS + SVG sparklines)
```

## Layout

```
families/research/
├── resolver.py             — name/code → CompanyRef (fuzzy + bootstrap)
├── peer_scan.py            — production batch worker for SW L2 cohorts
├── fetcher/
│   ├── client.py           — 23 Tushare endpoints (tenacity retry; cached)
│   ├── cache.py            — api_cache + computed_cache (NaN-sanitized JSONB)
│   └── pdf.py              — PDF text extraction for disclosures
├── analyzer/
│   ├── data.py             — CompanyFinancialSnapshot + load_company_snapshot
│   ├── factors.py          — FactorSpec / FactorResult / classify_* helpers
│   ├── profitability.py    — A: GPM/NPM/NPM_DEDT/ROE/ROIC/DUPONT (6 factors)
│   ├── growth.py           — B: REVENUE_YOY/N_INCOME_YOY/CAGR/FORECAST_ACH (4)
│   ├── cash_quality.py     — C: CFO_TO_NI/FCF/AR-INV growth/CCC (5)
│   ├── balance.py          — D: DEBT/CURRENT/QUICK/GOODWILL/PLEDGE/IBD (6)
│   ├── governance.py       — E: HOLDERTRADE/AUDIT/MGR/IRM/DISCLOSURE (7)
│   ├── trends.py           — 5-level trend classifier (rapid_up...rapid_down)
│   ├── peer.py             — SW L2 percentile rank (with L1 fallback)
│   ├── scoring.py          — 5-dim radar aggregation + overall verdict
│   ├── timeline.py         — chronological event merger
│   └── persistence.py      — upsert FactorResult → research.factor_value
├── report/
│   ├── builder.py          — assembles ResearchReport dict
│   ├── markdown.py         — terminal-friendly preview
│   ├── html.py             — Jinja renderer with inline CSS
│   ├── sparkline.py        — pure-Python inline SVG mini line charts
│   ├── llm_aug.py          — optional 6-paragraph narratives (parallel + cached)
│   └── templates/          — Jinja master + 6 partials + styles.css
└── params/
    └── research_v2.2.yaml  — thresholds + 5-dim scoring weights
```

## Database tables (`research` schema)

| Table | Purpose | Volume estimate |
|---|---|---|
| `company_identity` | resolver lookup | ~6000 stocks |
| `api_cache` | Tushare responses (TTL'd) | ~22 rows × stocks scanned |
| `computed_cache` | LLM narrative cache (SHA256-keyed) | ~6 rows / stock with --llm |
| `factor_value` | persisted FactorResult; backs peer rank | 28 rows × stocks scanned |
| `scan_run` | per-L2 audit trail (run_id, status, durations) | ~1 row / L2 / day |

## Key design decisions

### Status + peer_pct blend (scoring.py)
`final_score = 0.5 × status_base + 0.5 × peer_percentile`. Status carries an
absolute floor (a RED can't be hidden by being best-in-class), peer_pct adds
industry context (so steel & software don't share GPM thresholds).
Demonstrated insight: 智微智能 ROE 4.73% (absolute RED) but rank 7/89 (P93)
in 计算机设备 reveals "industry-wide capital-return slump, this stock leading recovery".

### Factor families that need multi-period series
AR_GROWTH_REV, INV_GROWTH_COST, CCC_CHANGE, IBD_SHARE_YOY all need balance-
sheet YoY. data.py builds `accounts_receiv_series` / `inventories_series` /
`total_liab_series` / `total_assets_series` from the same `balancesheet` rows.

### profit_dedt source
`profit_dedt` is in `fina_indicator`, NOT `income`. Tushare silently drops it
from `income.fields` requests. data.py reads it from fina_indicator.

### Fail-soft in narrative path
Builder accepts optional `augmenter`. None → fully deterministic markdown/HTML.
Augmenter failure → narrative=""; the report always renders.

## Production runbook

### Daily / scheduled scan
```bash
bash scripts/run_full_market_scan.sh                  # default concurrency=3
bash scripts/run_full_market_scan.sh --concurrency 5  # if Tushare quota allows
bash scripts/run_full_market_scan.sh --no-skip-fresh  # force recompute
# Then immediately:
uv run ifa research peer-rank-refresh                 # 12s, no Tushare hits
```
Idempotent. Ctrl-C safe — re-running picks up where it stopped via the
24h freshness check. ~5-8h cold, ~10-15min warm.

**Important: scan-universe persists factor values WITHOUT peer rank** (peer rank
requires the full cohort to be present, which it isn't until each L2 finishes).
Always run `peer-rank-refresh` after a fresh scan to populate peer_rank /
peer_percentile columns. The refresh is pure SQL + arithmetic, ~12s for the
whole market.

### Production cron / launchd snippet
```cron
# Linux crontab — daily at 17:30 BJT (post market + filings settled)
30 17 * * 1-5 cd /opt/ifa-claude && \
  bash scripts/run_full_market_scan.sh --concurrency 3 && \
  uv run ifa research peer-rank-refresh \
  >> /var/log/ifa-research-scan.log 2>&1
```

### Monitoring
```bash
uv run ifa research scan-status --hours 24 --failures
```
Healthy state:
- All recent rows have status `succeeded` or `partial`
- `partial` is OK if the only "failures" are delisted ts_codes (counted separately)
- Latest `succeeded` for each L2 ≤ 25h old (for daily cron)

### Recovering from corrupt or stale data
```sql
-- Force re-fetch on a stock (clear api_cache + factor_value)
DELETE FROM research.api_cache WHERE ts_code = '001339.SZ';
DELETE FROM research.factor_value WHERE ts_code = '001339.SZ';
```
Then `ifa research report 001339.SZ` will re-fetch and re-compute.

## Testing

```bash
uv run python -m pytest tests/research/ -v
```
- Pure-function tests (factors / trends / sparkline / scoring)
- Integration smoke tests (skip cleanly if DB or fixture data unavailable)
- Tier filtering tests (quick / standard / deep)
- Total: ~1s, 65 tests as of last commit

## Future enhancements (deferred)

The following are recognized as meaningful but intentionally not implemented
in V2.2.0 — capturing the rationale here so it surfaces during planning.

### Watchpoints ↔ company_event_memory linkage
Today watchpoints are factor-driven (e.g. "治理透明度不足: IRM 100% no-reply"),
and the timeline shows extracted events with polarity/importance. The two
are not cross-referenced. Linking them would let watchpoints cite specific
events as evidence (e.g. "治理 watchpoint backed by 2026-04-15 management
change announcement"), saving the analyst a manual cross-check step.

**Estimated value:** medium — saves ~30s/stock for a careful analyst.
**Estimated cost:** moderate — either (a) feed event_memory rows into the
watchpoints LLM prompt (+30% tokens, $X/report), or (b) post-process keyword
matching (brittle).
**Trigger to revisit:** if reviewer feedback consistently asks "why is X
flagged?" without enough evidence in the watchpoint description.

### Three-tier output quality differentiation
Currently `quick / standard / deep` differ by section count and LLM usage.
A more sophisticated split would also vary LLM model (cheaper for quick,
gpt-5.4 for deep) and sentence count per narrative. Today everything uses
the default LLMClient model with the same prompts.

**Estimated value:** medium — cost optimization once volume scales.
**Trigger to revisit:** when daily LLM spend matters.

### Event memory at scale
`extract_events_for_company` is on-demand. A scheduled job that periodically
extracts events for the entire universe (5800+ stocks × ~20 events/each =
116k LLM calls per refresh) would unlock cross-company event analytics and
make watchpoint enrichment instant. Cost-controlled approach: extract only
high-importance events first (`importance == 'high'` threshold).

**Estimated value:** high — enables industry event roll-ups, sector mood,
catalyst-driven candidate ranking.
**Trigger to revisit:** when starting Stock Intel family (SI-M2.3 catalysts).
