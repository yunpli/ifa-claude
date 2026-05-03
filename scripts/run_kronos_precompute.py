#!/usr/bin/env python3
"""Run Kronos embedding precompute for all candidates.

Uses M1 MPS for inference (~600 emb/sec at batch=32).
Total time for ~233k candidates: estimated ~7-10 minutes.

Output: yearly Parquet files in {output_root}/../embeddings/ningbo/kronos_small_v1/

Usage:
    uv run python scripts/run_kronos_precompute.py
    uv run python scripts/run_kronos_precompute.py --start 2024-01-02 --end 2026-04-30
"""
from __future__ import annotations

import argparse
import datetime as dt
import time

from rich.console import Console


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--start", default="2024-01-02")
    parser.add_argument("--end",   default="2026-04-30")
    parser.add_argument("--batch-size", type=int, default=32)
    args = parser.parse_args()

    console = Console()
    console.print(f"[bold cyan]Kronos Embedding Precompute[/bold cyan]")
    console.print(f"  Range: {args.start} → {args.end}")
    console.print(f"  Batch: {args.batch_size}")

    from ifa.core.db import get_engine
    from ifa.config import get_settings
    from ifa.families.ningbo.ml.kronos_features import precompute_kronos_embeddings

    engine = get_engine(get_settings())

    t0 = time.time()
    result = precompute_kronos_embeddings(
        engine,
        dt.date.fromisoformat(args.start),
        dt.date.fromisoformat(args.end),
        batch_size=args.batch_size,
        on_log=lambda m: console.print(m),
    )
    console.print(
        f"\n[bold green]Done[/bold green]  embedded={result['n_embedded']:,}  "
        f"skipped(short={result['n_skipped_short']}, missing={result['n_skipped_missing']})  "
        f"elapsed={time.time()-t0:.0f}s"
    )


if __name__ == "__main__":
    main()
