# Stock Edge Staged Validation Standard

Updated: 2026-05-08

## Correction

`pit8`, `top30`, or any several-day replay panel is smoke only. It can verify that
the code path runs, cache keys are isolated, and outputs are shaped correctly. It
cannot validate a parameter set, algorithm, horizon edge, or YAML promotion.

Do not jump from smoke directly to a five-year hard run. Five-year history is
available, but older market structures can overfit stale regimes or dilute the
current edge. The validation sequence must separate recent OOS evidence from
historical regime robustness:

1. Recent 6m validation first: roughly the latest six months of trading dates,
   PIT-local or stratified PIT universe, enough dates and stock-date rows to
   validate pipeline behavior, parameter direction, and 5/10/20d horizon
   stability.
2. Multiple 6m regime windows next: recent 6m, prior 6m, and representative
   2022/2023/2024/2025 windows. Report these as regime robustness, not as a
   substitute for current edge.
3. Full 2021-2026 walk-forward / purged CV only as the final promotion gate for
   baseline YAML changes.

If production replay is too slow, the fix is engineering: batch feature build,
precomputed labels, cache redesign, cheap proxy pre-screen, and stratified replay
sampling. The answer is not shrinking validation to a few PIT dates.

## Current Inventory

Command:

```bash
uv run python scripts/stock_edge_full_history_diagnose.py \
  --start 2021-01-01 \
  --output /Users/neoclaw/claude/ifaenv/manifests/stock_edge_full_history_validation
```

Latest artifact:

```text
/Users/neoclaw/claude/ifaenv/manifests/stock_edge_full_history_validation/stock_edge_full_history_validation_20260508T163031Z.json
```

Window: `2021-01-01` through `2026-05-08`.

| Year | raw_daily rows | core input rows | core intersection rate |
|---|---:|---:|---:|
| 2021 | 1,085,445 | 1,058,567 | 97.52% |
| 2022 | 1,179,072 | 1,146,019 | 97.20% |
| 2023 | 1,258,734 | 1,209,410 | 96.08% |
| 2024 | 1,293,893 | 1,233,188 | 95.31% |
| 2025 | 1,313,898 | 1,248,108 | 94.99% |
| 2026 | 438,077 | 414,279 | 94.57% |

Core input rows mean same `(trade_date, ts_code)` exists in `raw_daily`,
`raw_daily_basic`, and `raw_moneyflow`.

Mature forward label capacity:

| Horizon | Mature rows |
|---|---:|
| 5d | 6,540,568 |
| 10d | 6,512,036 |
| 20d | 6,455,056 |

Estimated full validation panel after core-input and 20d maturity constraints:
`6,309,571` stock-date rows.

## Required Pipeline

1. Inventory first: run `scripts/stock_edge_full_history_diagnose.py` and inspect
   input coverage, sector/market state coverage, and mature label capacity.
2. Build a recent-6m cheap proxy panel from SQL/precomputed features for mature
   stock-date rows across 5/10/20d horizons.
3. Run recent-6m PIT-local / stratified PIT validation before any five-year
   replay. This is the first real evidence tier; `pit8/top30` remains smoke.
4. Repeat the same cheap proxy and stratified replay process over multiple 6m
   windows, including recent, prior, and known different regimes.
5. Use walk-forward splits by year or contiguous quarter blocks only for the
   final full-history gate. Embargo must be
   at least the max target horizon, currently 20 trading days.
6. Use purged CV. Validation folds must not overlap forward-label windows with
   training rows.
7. Require horizon-specific OOS/OOC lift. A 20d improvement does not validate 5d
   or 10d parameters.
8. Bucket results by regime, liquidity, size, SW L1 industry, and volatility.
   Promotion requires robustness across buckets, not just aggregate IC.
9. Use expensive production replay only as the second stage inside each window:
   stratified PIT samples, cached by date/universe/base-param hash, then re-rank
   candidates that passed the cheap proxy.
10. Change YAML only after staged OOS/OOC gates pass. Engineering tools and
   diagnostics may be committed earlier; production parameters may not.

## Bottlenecks

The first run of the diagnostic script spends most time computing mature forward
label capacity over the full `raw_daily` history. That is acceptable for an
inventory script but too expensive for repeated tuning loops. Next engineering
work should persist a Stock Edge label/proxy table or parquet cache with:

- PIT `(trade_date, ts_code)` keys.
- 5/10/20d forward returns.
- 5/10/20d target-first, stop-first, MFE, MAE/path labels.
- Liquidity, market-cap, SW L1/L2, volatility, and market/TA regime buckets.
- Feature logic version and source coverage flags.

This cache is the bridge between recent-6m validation, multi-window regime
robustness, and the final five-year promotion gate.
