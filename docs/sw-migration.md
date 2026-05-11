# SW (申万) Unification

V2.1 unifies all sector-aware logic on the **申万 (SW)** taxonomy. This document records why we did it, how the migration is structured, the canonical SW-driven tables, and the bugs we fixed along the way.

---

## Why SW?

We compared three sector taxonomies that TuShare exposes:

| Source | TuShare endpoint(s) | History | PIT correctness | Verdict |
|---|---|---|---|---|
| **DC** (东财概念) | `dc_index`, `moneyflow_ind_dc`, `dc_member` | ~18 days for `dc_member`; concept history starts 2023+ | No `in_date` / `out_date`, snapshots overwrite | Unusable for any historical study |
| **THS** (同花顺概念) | `ths_index`, `ths_member`, `moneyflow_ind_ths` | Snapshot only — current membership; historical concept lists not retained | Forward-look bias — yesterday's hot list contains today's stars | Cannot backtest |
| **SW** (申万行业) | `index_classify`, `index_member_all`, `sw_daily` | **Full history since 1993** | **`in_date` / `out_date` per (l1, ts_code)** — true PIT | Chosen |

申万 also has a stable three-level hierarchy (L1: ~28 行业, L2: ~120 子行业, L3: ~300 三级行业) that maps cleanly to how Chinese sell-side analysts and institutional desks actually talk about the market. DC and THS use 概念 (themes) which churn — useful for a one-day "what's hot" but useless for any longitudinal signal.

---

## Migration phase plan

| Phase | Scope | Status |
|---|---|---|
| **A** — Raw backfill | `raw_sw_member` ETL, `sw_member_monthly` derived snapshot, full raw backfill 2021-01 → 2026 (877 days, 15.85M rows, 195 min) | Done |
| **B** — Factor refactor | Move `factors/flow.py`, `factors/leader.py`, `factors/candidate.py`, `data.py` from DC paths to SW; new `transition_matrix.py`; `evening.py` per-section refactor; LLM-aug integration; ML §10 dual-model split; run-mode badge | In progress (B1 starting point) |
| **C** — Compute / train / OOS | Run `sector_moneyflow_sw_daily` aggregation; full compute backfill 2021–2026; train RF + XGB; freeze `v2026_05`; OOS validate 2025-11 → 2026-04 | Pending B |

The live B1–B9 / C1–C6 task list is in the root `CLAUDE.md`.

---

## Key SW-driven DB tables

All in the `smartmoney` schema.

### `raw_sw_member`

Full SW membership history. PK `(l1_code, ts_code, in_date)`. ~5,847 rows covering 1993 → present. Columns: `l1_code`, `l1_name`, `l2_code`, `l2_name`, `l3_code`, `l3_name`, `ts_code`, `in_date`, `out_date` (NULL = currently a member).

### `sw_member_monthly`

Monthly snapshot derived from `raw_sw_member`. PK `(snapshot_month, l2_code, ts_code)`. ~327,547 rows over 65 monthly snapshots (2021-01 → 2026-05). The membership rule:

```sql
in_date <= snapshot_month
AND (out_date IS NULL OR out_date > snapshot_month)
```

This is the canonical PIT join surface — every "what stocks were in this sector on this date" query goes through here.

### `sector_moneyflow_sw_daily` (B1 deliverable)

Daily aggregation of individual-stock 资金流 up to SW L2 sector. PK `(trade_date, l2_code)`. Columns include `net_amount`, `buy_elg_amount`, `sell_elg_amount`, `buy_lg_amount`, `sell_lg_amount`, `stock_count`. All amounts in 万元 at storage time.

Aggregation:

```sql
INSERT INTO smartmoney.sector_moneyflow_sw_daily (...)
SELECT m.trade_date, s.l2_code, s.l2_name, s.l1_code, s.l1_name,
       SUM(m.net_mf_amount), SUM(m.buy_elg_amount), SUM(m.sell_elg_amount),
       SUM(m.buy_lg_amount), SUM(m.sell_lg_amount),
       COUNT(DISTINCT m.ts_code)
FROM smartmoney.raw_moneyflow m
JOIN smartmoney.sw_member_monthly s
  ON m.ts_code = s.ts_code
 AND s.snapshot_month = date_trunc('month', m.trade_date)::date
WHERE m.trade_date = ANY(:dates)
GROUP BY m.trade_date, s.l2_code, s.l2_name, s.l1_code, s.l1_name
ON CONFLICT (trade_date, l2_code) DO UPDATE SET ...
```

This is the canonical sector-flow table consumed by Market and SmartMoney.

### `raw_sw_daily`

SW index daily price/volume. **V2.1.1: now backfilled to all ~131 L2 codes** in addition to the 31 L1 codes. Per-L2 close/pct_change/amount/total_mv is queried directly; member-stock aggregation is kept only as a fallback for any missing rows.

Backfill script: `scripts/backfill_sw_l2_daily.py` — one TuShare `sw_daily(ts_code, start, end)` call per L2 code over the full window. Idempotent; safe to re-run for incremental top-up via `--recent-days N`.

---

## The Tech five-layer SW L2 mapping

This is the canonical reference. Any consumer that drifts from this table is wrong.

| Layer | SW L2 codes | Names |
|---|---|---|
| energy (算力·能源) | 801738, 801737, 801735, 801736, 801733, 801731 | 电网设备 / 电池 / 光伏设备 / 风电设备 / 电源设备 / 电机 |
| chips (算力·芯片) | 801081, 801083, 801086, 801082 | 半导体 / 元件 / 电子化学品 / 其他电子 |
| infra (算力·基础设施) | 801102, 801223, 801101 | 通信设备 / 通信服务 / 计算机设备 |
| models (模型层) | 801104, 801103 | 软件开发 / IT服务 |
| apps (应用·终端) | 801085, 801084, 801767, 801764, 801093, 801095 | 消费电子 / 光学光电子 / 数字媒体 / 游戏 / 汽车零部件 / 乘用车 |

---

## The Market main-line dynamic query

Market's 主线 list is no longer a hand-curated config. It is computed each report run from SW L2 资金流, with `close` / `pct_change` taken directly from `raw_sw_daily` (V2.1.1) and falling back to member-stock aggregation when the L2 row is absent:

```sql
WITH ranked AS (
  SELECT l2_code, l2_name, net_amount
  FROM smartmoney.sector_moneyflow_sw_daily
  WHERE trade_date = :rd AND l2_code IS NOT NULL AND net_amount IS NOT NULL
  ORDER BY net_amount DESC LIMIT :n
),
agg AS (
  SELECT s.l2_code, AVG(d.pct_chg) AS pct_change, AVG(d.close) AS close
  FROM smartmoney.sw_member_monthly s
  JOIN smartmoney.raw_daily d
    ON d.ts_code = s.ts_code AND d.trade_date = :rd
  WHERE s.snapshot_month = :sm AND s.l2_code IN (SELECT l2_code FROM ranked)
  GROUP BY s.l2_code
)
SELECT r.l2_code, r.l2_name, r.net_amount,
       COALESCE(sw.close,      a.close)      AS close,
       COALESCE(sw.pct_change, a.pct_change) AS pct_change
FROM ranked r
LEFT JOIN smartmoney.raw_sw_daily sw
       ON sw.ts_code = r.l2_code AND sw.trade_date = :rd
LEFT JOIN agg a USING (l2_code)
ORDER BY r.net_amount DESC;
```

This makes 主线 a deterministic function of (date, top_n) — reproducible and auditable.

### Noon main-line realtime proxy

The A-share main noon report must not use EOD-only SW tables as the primary
source for the observation date. `raw_sw_daily` and `sector_moneyflow_sw_daily`
are settled daily tables; before the close they are normally empty for today,
and any stale fallback would hide the actual morning tape.

For `slot=noon` on the current BJT trading date, `market.fetch_main_lines()`
now ranks SW L2 main lines from constituent realtime `rt_k` snapshots joined to
PIT `sw_member_monthly`:

- `pct_change` / synthetic `close`: MV-weighted member close vs member pre-close.
- `amount_yuan`: sum of member intraday `rt_k.amount`.
- `up_ratio`: advancing constituents divided by all covered constituents.
- rank: sector `pct_change` descending, then `up_ratio`, then `amount_yuan`.

This is explicitly tagged as `source_method=constituent_rt_k_proxy` with
coverage and confidence metadata. It is not presented as an official SW index
quote. If TuShare later exposes a reliable SW L2 intraday index endpoint, that
official path should take precedence and keep the source tag distinct from this
proxy path.

---

## The 千元 bug

A class of bugs that produced 10× inflated 净流入 / 净流出 numbers in early V2 iterations.

**Root cause.** TuShare's `daily.amount` is in 千元, but TuShare's `moneyflow.net_mf_amount` is in 万元. Several aggregation paths historically applied a `× 1e3` or `× 1e4` conversion at the wrong layer (or twice, or not at all), so the same nominal "净流入 50 亿" might mean any of {5亿, 50亿, 500亿} depending on which code path produced it.

**Fix.**

1. Storage layer is canonical: `raw_moneyflow.net_mf_amount` is stored in 万元 verbatim from TuShare. `raw_daily.amount` is stored in 千元 verbatim.
2. All sector aggregations (`SUM(net_mf_amount)` in `sector_moneyflow_sw_daily` etc.) sum in 万元 — no conversion at SQL time.
3. Render layer (`_fmt_amt(scale=1e8)`) is the single place where 万元 → 亿 happens for display.
4. Anywhere that reads `raw_daily.amount` does its own 千元 → 元 / 万元 conversion explicitly with a comment.

The audit trail of which aggregations were affected lives in `docs/audit-pre-b8.md` (historical, do not edit).

---

## V2.1.1 — SW L2 daily price ETL

Added `scripts/backfill_sw_l2_daily.py`. Pulls `sw_daily(ts_code, start_date, end_date)` once per L2 code (one call per code, full date window) and bulk-upserts into `raw_sw_daily`. ~131 codes × 1300 trade days ≈ 170 k rows; ~3 minutes wall time.

Daily incremental top-up: `uv run python scripts/backfill_sw_l2_daily.py --recent-days 5` (cheap; can be added to a nightly cron alongside the existing per-day ETL).

`fetch_main_lines` now uses `COALESCE(raw_sw_daily.{close,pct_change}, member_agg.{close,pct_change})` so L2 prices are taken directly when available, with the member aggregation as a safety net for any backfill gaps.

---

## V2.1.2 — SmartMoney factors use L2 pct_change

Until V2.1.1, `raw_sw_daily` only carried L1 rows, so smartmoney factor SQL joined on `l1_code` and treated the parent L1 pct_change as a per-L2 proxy. With L2 daily prices now backfilled, all 5 join sites have been switched to:

```sql
LEFT JOIN raw_sw_daily sw_l2 ON sw_l2.ts_code = sf.l2_code AND sw_l2.trade_date = sf.trade_date
LEFT JOIN raw_sw_daily sw_l1 ON sw_l1.ts_code = sf.l1_code AND sw_l1.trade_date = sf.trade_date
... COALESCE(sw_l2.pct_change, sw_l1.pct_change) AS pct_change ...
```

Sites updated:
- `factors/flow.py:_load_sw_l2_history` — feeds `_compute_sw_l2_factors` → `factor_daily.pct_change_z` and downstream momentum factors that train the ML models
- `factors/leader.py` — sector_pct_map used for leader scoring
- `data.py` (3 sites) — sector summary, high-quality flow, crowded sectors (display in evening report)

**Why this is a strict improvement.** Sample 2026-04-30, the 6 L2 children of L1 电子:

| L2 | pct_change |
|---|---|
| 半导体 | +4.71% |
| 电子化学品Ⅱ | +1.06% |
| 其他电子Ⅱ | +1.00% |
| 元件 | -0.11% |
| 光学光电子 | -0.39% |
| 消费电子 | -1.02% |

Spread 5.73 percentage points. Pre-V2.1.2 they all read 电子 L1's value (≈ +1%) — the model had zero signal for L2-internal divergence. Now it does.

**Required follow-up — re-compute + retrain.** Because factor inputs changed:

1. `uv run python -m ifa.cli smartmoney compute --start 2021-01-04 --end <today>` — overwrites `factor_daily` / `sector_state_daily` / `stock_signals_daily` with V2.1.2 values
2. `uv run python -m ifa.cli smartmoney train --version v2026_05` — retrain RF + XGB with new factor distributions; persist over v2026_05 (or use a new tag for A/B compare)

Convenience: `bash scripts/recompute_smartmoney_required.sh` runs both. Required before any new SmartMoney report generation.

---

## V2.2 TODO

- **Transition matrix LLM nudge.** `transition_matrix.py` (B5) will support a ±10% LLM hook to bias next-phase probabilities based on macro / news context.
- **Persistent param store v2026_05.** After B/C complete, freeze the first SW-trained baseline.
- **Drop DC paths from `factors/flow.py`.** Currently kept as a `sector_source='dc'` fallback; remove once SW path is fully validated.
