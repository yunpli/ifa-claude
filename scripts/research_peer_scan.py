"""Run peer universe scan for the 3 test stocks.

After this script completes, research.factor_value will have ~10-20 stocks
per L2 (computer hardware / electronic components / consumer electronics) so
peer.py can return real percentile ranks on the next smoketest run.

Usage:
    uv run python scripts/research_peer_scan.py [--max-peers 20]

Idempotent: rerunning skips stocks computed within the last 24h.
"""
from __future__ import annotations

import argparse
import logging

from ifa.core.db import get_engine
from ifa.families.research.peer_scan import scan_l2_universe

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("peer_scan")

TARGETS = [
    "001339.SZ",   # 智微智能 — 计算机设备
    "301486.SZ",   # 致尚科技 — 消费电子
    "002938.SZ",   # 鹏鼎控股 — 元件
]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--max-peers", type=int, default=20,
                        help="Cap peers per L2 (default 20)")
    parser.add_argument("--no-skip-fresh", action="store_true",
                        help="Recompute even if data is fresh")
    args = parser.parse_args()

    engine = get_engine()
    for ts_code in TARGETS:
        log.info("════════════════════════════════════════════════════")
        log.info("anchor: %s", ts_code)
        result = scan_l2_universe(
            engine, ts_code,
            max_peers=args.max_peers,
            skip_fresh=not args.no_skip_fresh,
        )
        log.info(result.summary())
        if result.failures:
            log.warning("failures (first 5): %s", result.failures[:5])


if __name__ == "__main__":
    main()
