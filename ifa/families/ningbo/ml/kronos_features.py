"""Phase 3.C — Kronos pre-trained OHLCV embeddings as ML features.

Uses the vendored Kronos tokenizer (KronosTokenizer-2k from HF) to extract
256-d continuous embeddings from a 128-day OHLCV window for each candidate.

Why tokenizer not full transformer:
  - Tokenizer's encoder produces continuous z (pre-quantization) which
    captures K-line patterns. The Kronos transformer is autoregressive
    over discrete tokens — useful for *generation* but our task is
    *classification* so the continuous z is what we want.
  - Smaller, faster (~600/sec on M1 MPS at batch 32).
  - Uses 256-d embedding which is the right size to add to our 39 base feats.

Pipeline:
  1. Bulk-load raw_daily for ALL unique ts_codes in candidates table once.
  2. Group by ts_code, build 128-day windows ending at each rec_date.
  3. Batched Kronos inference (B=32) on M1 MPS.
  4. Persist as Parquet keyed by (rec_date, ts_code).
  5. attach_kronos_embeddings() joins them into the feature matrix.

Cache layout:
    {output_root.parent}/embeddings/ningbo/kronos_small_v1/
        emb_2024.parquet
        emb_2025.parquet
        emb_2026.parquet
        meta.json
"""
from __future__ import annotations

import datetime as dt
import json
import time
from pathlib import Path
from typing import Callable

import numpy as np
import pandas as pd
from sqlalchemy import Engine, text


KRONOS_TOKENIZER_ID = "NeoQuasar/Kronos-Tokenizer-2k"
LOOKBACK_BARS = 128       # 128 trading days ≈ 6 months — enough for K-line context
EMBEDDING_DIM = 256       # confirmed via tokenizer probe


def _cache_root() -> Path:
    from ifa.config import get_settings
    s = get_settings()
    root = Path(s.output_root).parent / "embeddings" / "ningbo" / "kronos_small_v1"
    root.mkdir(parents=True, exist_ok=True)
    return root


def _load_kronos_tokenizer():
    """Load KronosTokenizer-2k on the best available device (MPS > CUDA > CPU)."""
    import torch
    from ifa.families.ningbo.ml.kronos_lib.kronos import KronosTokenizer

    if torch.backends.mps.is_available():
        device = torch.device("mps")
        backend = "MPS"
    elif torch.cuda.is_available():
        device = torch.device("cuda")
        backend = "CUDA"
    else:
        device = torch.device("cpu")
        backend = "CPU"

    print(f"  → Loading {KRONOS_TOKENIZER_ID} on {backend}…")
    t0 = time.time()
    tok = KronosTokenizer.from_pretrained(KRONOS_TOKENIZER_ID).to(device).eval()
    print(f"  ✓ Loaded in {time.time()-t0:.1f}s")
    return tok, device


def _normalize_window(arr: np.ndarray) -> np.ndarray:
    """Per-window z-score normalization (Kronos was trained on normalized inputs)."""
    mu = arr.mean(axis=0, keepdims=True)
    sd = arr.std(axis=0, keepdims=True) + 1e-8
    return (arr - mu) / sd


def precompute_kronos_embeddings(
    engine: Engine,
    rec_date_start: dt.date,
    rec_date_end: dt.date,
    *,
    batch_size: int = 32,
    on_log: Callable[[str], None] = print,
    write_yearly: bool = True,
) -> dict:
    """Extract Kronos embeddings for every (rec_date, ts_code) in candidates_daily.

    Strategy: bulk-load raw_daily once (all candidates' ts_codes, full history
    needed for trailing windows), build per-(ts_code, rec_date) windows in
    pandas, batched MPS inference.
    """
    import torch

    on_log(f"\n[Kronos precompute] {rec_date_start} → {rec_date_end}  batch={batch_size}")

    # ── 1. Get all unique (ts_code, rec_date) pairs ──────────────────────────
    pairs = pd.read_sql(text("""
        SELECT DISTINCT ts_code, rec_date FROM ningbo.candidates_daily
        WHERE rec_date BETWEEN :s AND :e
        ORDER BY rec_date, ts_code
    """), engine, params={"s": rec_date_start, "e": rec_date_end})
    pairs["rec_date"] = pd.to_datetime(pairs["rec_date"]).dt.date
    n_pairs = len(pairs)
    n_unique_codes = pairs["ts_code"].nunique()
    on_log(f"  {n_pairs:,} (ts_code, rec_date) pairs across {n_unique_codes:,} unique stocks")

    # ── 2. Bulk-load raw_daily for ALL ts_codes in one query ─────────────────
    # Need lookback_bars trading days BEFORE earliest rec_date
    bulk_start = rec_date_start - dt.timedelta(days=LOOKBACK_BARS * 2)  # safety buffer
    on_log(f"  Bulk-loading raw_daily {bulk_start} → {rec_date_end}…")
    t0 = time.time()
    daily = pd.read_sql(text("""
        SELECT ts_code, trade_date, open, high, low, close, vol, amount
        FROM smartmoney.raw_daily
        WHERE ts_code = ANY(:codes)
          AND trade_date BETWEEN :s AND :e
        ORDER BY ts_code, trade_date
    """), engine, params={
        "codes": pairs["ts_code"].unique().tolist(),
        "s": bulk_start, "e": rec_date_end,
    })
    daily["trade_date"] = pd.to_datetime(daily["trade_date"]).dt.date
    on_log(f"  Loaded {len(daily):,} OHLCV rows in {time.time()-t0:.1f}s "
           f"(~{daily.memory_usage(deep=True).sum()/1e6:.0f} MB)")

    # Group by ts_code for fast lookup
    daily_by_code = {code: g.reset_index(drop=True) for code, g in daily.groupby("ts_code")}

    # ── 3. Load Kronos tokenizer ─────────────────────────────────────────────
    tok, device = _load_kronos_tokenizer()

    # ── 4. Build windows + batched inference ─────────────────────────────────
    cache_root = _cache_root()
    rows_per_year: dict[int, list[dict]] = {}
    n_embedded = n_skipped_short = n_skipped_missing = 0

    OHLCV_COLS = ["open", "high", "low", "close", "vol", "amount"]
    t_start = time.time()
    n_total = n_pairs

    # Process in batches
    pending_windows: list[np.ndarray] = []
    pending_meta:    list[tuple[dt.date, str]] = []

    def flush_batch():
        nonlocal n_embedded, pending_windows, pending_meta
        if not pending_windows:
            return
        with torch.no_grad():
            x = torch.tensor(np.stack(pending_windows), device=device, dtype=torch.float32)
            z = tok.embed(x)
            for layer in tok.encoder:
                z = layer(z)
            emb = z.mean(dim=1)
            if device.type == "mps":
                torch.mps.synchronize()
            emb_np = emb.cpu().numpy().astype(np.float32)
        for k, (rd, ts) in enumerate(pending_meta):
            yr = rd.year
            rec = {"rec_date": rd, "ts_code": ts}
            for d in range(EMBEDDING_DIM):
                rec[f"kronos_emb_{d}"] = float(emb_np[k, d])
            rows_per_year.setdefault(yr, []).append(rec)
            n_embedded += 1
        pending_windows.clear()
        pending_meta.clear()

    for i, row in enumerate(pairs.itertuples(index=False)):
        ts_code = row.ts_code
        rec_date = row.rec_date

        code_df = daily_by_code.get(ts_code)
        if code_df is None:
            n_skipped_missing += 1
            continue

        # Window: last LOOKBACK_BARS rows where trade_date <= rec_date
        mask = code_df["trade_date"] <= rec_date
        sub = code_df[mask]
        if len(sub) < LOOKBACK_BARS:
            n_skipped_short += 1
            continue

        window = sub.iloc[-LOOKBACK_BARS:][OHLCV_COLS].values.astype(np.float32)
        window = _normalize_window(window)
        pending_windows.append(window)
        pending_meta.append((rec_date, ts_code))

        if len(pending_windows) >= batch_size:
            flush_batch()

        if (i + 1) % 5000 == 0:
            elapsed = time.time() - t_start
            rate = n_embedded / max(elapsed, 1e-3)
            eta_min = (n_total - i - 1) / max(rate, 1e-3) / 60
            on_log(
                f"  [{i+1:>6,}/{n_total:,}]  embedded={n_embedded:,}  "
                f"skipped(short={n_skipped_short},miss={n_skipped_missing})  "
                f"rate={rate:.0f}/s  ETA={eta_min:.1f}m"
            )

    flush_batch()

    # ── 5. Write yearly Parquet (fallback to pickle if engine missing) ──────
    on_log(f"  Writing files…")
    paths_written = []
    for yr, rows in rows_per_year.items():
        df_yr = pd.DataFrame(rows)
        path = cache_root / f"emb_{yr}.parquet"
        try:
            df_yr.to_parquet(path, index=False, compression="snappy")
        except ImportError as exc:
            # No pyarrow/fastparquet — write pickle, attach_kronos_embeddings handles both
            path = cache_root / f"emb_{yr}.pkl"
            df_yr.to_pickle(path)
            on_log(f"    ⚠️  parquet engine missing ({exc}), wrote pickle instead")
        paths_written.append(path)
        size_mb = path.stat().st_size / 1e6
        on_log(f"    {path.name}: {len(df_yr):,} rows, {size_mb:.1f} MB")

    meta = {
        "model_id": KRONOS_TOKENIZER_ID,
        "embedding_dim": EMBEDDING_DIM,
        "lookback_bars": LOOKBACK_BARS,
        "n_embedded": n_embedded,
        "n_skipped_short": n_skipped_short,
        "n_skipped_missing": n_skipped_missing,
        "rec_date_start": str(rec_date_start),
        "rec_date_end": str(rec_date_end),
        "computed_at": dt.datetime.now().isoformat(),
        "device": str(device),
    }
    (cache_root / "meta.json").write_text(json.dumps(meta, indent=2, default=str))

    elapsed = time.time() - t_start
    on_log(
        f"\n  Done in {elapsed:.0f}s ({elapsed/60:.1f}m).  "
        f"embedded={n_embedded:,}  skipped_short={n_skipped_short}  skipped_missing={n_skipped_missing}"
    )
    return {
        "n_embedded": n_embedded, "n_skipped_short": n_skipped_short,
        "n_skipped_missing": n_skipped_missing, "paths": paths_written, "meta": meta,
    }


def attach_kronos_embeddings(engine: Engine, feat_df: pd.DataFrame) -> pd.DataFrame:
    """Join cached Kronos embeddings into a feature DataFrame.

    Adds kronos_emb_0 ... kronos_emb_255 columns. Rows without embeddings get NaN
    (downstream imputer handles).
    """
    cache_root = _cache_root()
    parquet_files = sorted(cache_root.glob("emb_*.parquet"))
    pickle_files  = sorted(cache_root.glob("emb_*.pkl"))
    files = parquet_files + pickle_files
    if not files:
        raise FileNotFoundError(
            f"No Kronos cache at {cache_root}. Run precompute_kronos_embeddings() first."
        )
    emb_df = pd.concat(
        [pd.read_parquet(p) if p.suffix == ".parquet" else pd.read_pickle(p) for p in files],
        ignore_index=True,
    )
    emb_df["rec_date"] = pd.to_datetime(emb_df["rec_date"]).dt.date
    print(f"  Loaded {len(emb_df):,} cached Kronos embeddings from {len(parquet_files)} files")

    merged = feat_df.merge(emb_df, on=["rec_date", "ts_code"], how="left")
    emb_cols = [c for c in merged.columns if c.startswith("kronos_emb_")]
    if emb_cols:
        n_with = merged[emb_cols[0]].notna().sum()
        print(f"  Merge: {n_with:,}/{len(merged):,} rows have embeddings "
              f"({n_with/len(merged)*100:.1f}%)")
    return merged
