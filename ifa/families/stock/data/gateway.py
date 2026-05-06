"""Local-first Stock Edge data gateway.

Phase B starts with local PostgreSQL reads only. Tushare backfill adapters and
DuckDB 5min loaders are added behind this same contract so strategy/report code
does not care where data was sourced.
"""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from typing import Any

import pandas as pd
from sqlalchemy import text
from sqlalchemy.engine import Engine

from ifa.families.research.memory import load_fundamental_lineup
from ifa.families.ta.report.labels import setup_family, setup_label

from .availability import LoadResult


def _decorate_ta_row(row: dict[str, Any]) -> dict[str, Any]:
    setup_name = row.get("setup_name")
    if setup_name:
        row["setup_label"] = setup_label(str(setup_name))
        row["setup_family"] = setup_family(str(setup_name))
        row["setup_family_code"] = str(setup_name)[0]
    return row


def _build_sector_leaders(peers: list[dict[str, Any]], target_ts_code: str) -> dict[str, Any]:
    decorated = [_decorate_peer(row, target_ts_code) for row in peers if _is_active_listing_peer(row, target_ts_code)]
    return {
        "size": _top_peers(decorated, "total_mv", target_ts_code=target_ts_code),
        "momentum": _top_peers(decorated, "return_5d_pct", target_ts_code=target_ts_code),
        "moneyflow": _top_peers(decorated, "net_mf_amount_7d", target_ts_code=target_ts_code),
        "ta": _top_peers(decorated, "ta_score", target_ts_code=target_ts_code),
    }


def _decorate_peer(row: dict[str, Any], target_ts_code: str) -> dict[str, Any]:
    out = dict(row)
    out["is_target"] = out.get("ts_code") == target_ts_code
    if out.get("setup_name"):
        out["setup_label"] = setup_label(str(out["setup_name"]))
    for key in [
        "close",
        "pct_chg",
        "amount",
        "return_5d_pct",
        "return_10d_pct",
        "return_15d_pct",
        "total_mv",
        "circ_mv",
        "pe_ttm",
        "pb",
        "net_mf_amount_7d",
        "ta_score",
    ]:
        out[key] = _float_or_none(out.get(key))
    out["daily_returns_15d"] = [
        {
            "trade_date": item.get("trade_date"),
            "pct_chg": _float_or_none(item.get("pct_chg")),
        }
        for item in (out.get("daily_returns_15d") or [])
        if isinstance(item, dict)
    ]
    return out


def _top_peers(peers: list[dict[str, Any]], key: str, *, target_ts_code: str, limit: int = 5) -> list[dict[str, Any]]:
    rows = [row for row in peers if row.get(key) is not None]
    rows.sort(key=lambda row: float(row[key]), reverse=True)
    top = rows[:limit]
    if any(row.get("ts_code") == target_ts_code for row in top):
        return top
    target = next((row for row in rows if row.get("ts_code") == target_ts_code), None)
    if target is None:
        target = next((row for row in peers if row.get("ts_code") == target_ts_code), None)
    if target is None:
        return top
    return [*top[: max(limit - 1, 0)], target]


def _is_active_listing_peer(row: dict[str, Any], target_ts_code: str | None = None) -> bool:
    ts_code = str(row.get("ts_code") or "")
    if target_ts_code and ts_code == target_ts_code:
        return True
    list_status = str(row.get("list_status") or "").strip().upper()
    if list_status and list_status != "L":
        return False
    name = str(row.get("name") or "").replace(" ", "")
    if "退市" in name or "退(" in name or name.endswith("退"):
        return False
    return True


def _float_or_none(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


@dataclass(frozen=True)
class LocalDataGateway:
    engine: Engine

    def load_daily_bars(
        self,
        ts_code: str,
        as_of_trade_date: dt.date,
        *,
        lookback_rows: int = 60,
        min_rows: int = 20,
        required: bool = True,
    ) -> LoadResult[pd.DataFrame]:
        sql = text("""
            SELECT ts_code, trade_date, open, high, low, close, pre_close,
                   change_, pct_chg, vol, amount
            FROM smartmoney.raw_daily
            WHERE ts_code = :ts_code AND trade_date <= :as_of
            ORDER BY trade_date DESC
            LIMIT :limit
        """)
        return self._load_frame(
            "daily_bars",
            sql,
            {"ts_code": ts_code, "as_of": as_of_trade_date, "limit": lookback_rows},
            date_col="trade_date",
            min_rows=min_rows,
            required=required,
        )

    def load_daily_basic(
        self,
        ts_code: str,
        as_of_trade_date: dt.date,
        *,
        lookback_rows: int = 20,
        min_rows: int = 5,
        required: bool = True,
    ) -> LoadResult[pd.DataFrame]:
        sql = text("""
            SELECT ts_code, trade_date, close, turnover_rate, turnover_rate_f,
                   volume_ratio, pe, pe_ttm, pb, ps, ps_ttm, total_mv, circ_mv
            FROM smartmoney.raw_daily_basic
            WHERE ts_code = :ts_code AND trade_date <= :as_of
            ORDER BY trade_date DESC
            LIMIT :limit
        """)
        return self._load_frame(
            "daily_basic",
            sql,
            {"ts_code": ts_code, "as_of": as_of_trade_date, "limit": lookback_rows},
            date_col="trade_date",
            min_rows=min_rows,
            required=required,
        )

    def load_moneyflow(
        self,
        ts_code: str,
        as_of_trade_date: dt.date,
        *,
        lookback_rows: int = 20,
        min_rows: int = 3,
        required: bool = False,
    ) -> LoadResult[pd.DataFrame]:
        sql = text("""
            SELECT ts_code, trade_date, buy_lg_amount, sell_lg_amount,
                   buy_elg_amount, sell_elg_amount, net_mf_amount
            FROM smartmoney.raw_moneyflow
            WHERE ts_code = :ts_code AND trade_date <= :as_of
            ORDER BY trade_date DESC
            LIMIT :limit
        """)
        return self._load_frame(
            "moneyflow",
            sql,
            {"ts_code": ts_code, "as_of": as_of_trade_date, "limit": lookback_rows},
            date_col="trade_date",
            min_rows=min_rows,
            required=required,
        )

    def load_event_context(self, ts_code: str, as_of_trade_date: dt.date) -> LoadResult[dict[str, Any]]:
        """Load recent event-driven trading context for one stock.

        No rows is an ordinary neutral state, not a missing-data failure:
        龙虎榜/涨停池 only exist when the stock triggered those market events.
        """
        with self.engine.connect() as conn:
            top_list = [dict(r) for r in conn.execute(
                text("""
                    SELECT trade_date, reason, name, close, pct_change,
                           turnover_rate, amount, l_sell, l_buy, l_amount,
                           net_amount, net_rate, amount_rate, float_values
                    FROM smartmoney.raw_top_list
                    WHERE ts_code = :ts_code
                      AND trade_date <= :as_of
                      AND trade_date >= (:as_of - INTERVAL '30 days')
                    ORDER BY trade_date DESC
                    LIMIT 20
                """),
                {"ts_code": ts_code, "as_of": as_of_trade_date},
            ).mappings()]
            top_inst = [dict(r) for r in conn.execute(
                text("""
                    SELECT trade_date, exalter, buy, buy_rate, sell, sell_rate,
                           net_buy, side, reason
                    FROM smartmoney.raw_top_inst
                    WHERE ts_code = :ts_code
                      AND trade_date <= :as_of
                      AND trade_date >= (:as_of - INTERVAL '30 days')
                    ORDER BY trade_date DESC, net_buy DESC NULLS LAST
                    LIMIT 50
                """),
                {"ts_code": ts_code, "as_of": as_of_trade_date},
            ).mappings()]
            kpl = [dict(r) for r in conn.execute(
                text("""
                    SELECT trade_date, name, lu_time, ld_time, open_time,
                           last_time, lu_desc, tag, theme, net_change,
                           bid_amount, status, bid_change, bid_turnover,
                           lu_bid_vol, pct_chg, bid_pct_chg, rt_pct_chg,
                           limit_order, amount, turnover_rate, free_float,
                           lu_limit_order
                    FROM smartmoney.raw_kpl_list
                    WHERE ts_code = :ts_code
                      AND trade_date <= :as_of
                      AND trade_date >= (:as_of - INTERVAL '30 days')
                    ORDER BY trade_date DESC
                    LIMIT 20
                """),
                {"ts_code": ts_code, "as_of": as_of_trade_date},
            ).mappings()]
            limit_rows = [dict(r) for r in conn.execute(
                text("""
                    SELECT trade_date, industry, name, close, pct_chg, amount,
                           limit_amount, fc_ratio, fl_ratio, fd_amount,
                           first_time, last_time, open_times, up_stat,
                           limit_times, limit_
                    FROM smartmoney.raw_limit_list_d
                    WHERE ts_code = :ts_code
                      AND trade_date <= :as_of
                      AND trade_date >= (:as_of - INTERVAL '30 days')
                    ORDER BY trade_date DESC
                    LIMIT 20
                """),
                {"ts_code": ts_code, "as_of": as_of_trade_date},
            ).mappings()]
            block_trades = [dict(r) for r in conn.execute(
                text("""
                    SELECT trade_date, price, vol, amount, buyer, seller
                    FROM smartmoney.raw_block_trade
                    WHERE ts_code = :ts_code
                      AND trade_date <= :as_of
                      AND trade_date >= (:as_of - INTERVAL '60 days')
                    ORDER BY trade_date DESC, amount DESC NULLS LAST
                    LIMIT 30
                """),
                {"ts_code": ts_code, "as_of": as_of_trade_date},
            ).mappings()]
            market_margin = [dict(r) for r in conn.execute(
                text("""
                    SELECT trade_date,
                           SUM(rzye) AS rzye,
                           SUM(rzmre) AS rzmre,
                           SUM(rzche) AS rzche,
                           SUM(rqye) AS rqye,
                           SUM(rzrqye) AS rzrqye
                    FROM smartmoney.raw_margin
                    WHERE trade_date <= :as_of
                      AND trade_date >= (:as_of - INTERVAL '60 days')
                    GROUP BY trade_date
                    ORDER BY trade_date DESC
                    LIMIT 45
                """),
                {"as_of": as_of_trade_date},
            ).mappings()]
            northbound = [dict(r) for r in conn.execute(
                text("""
                    SELECT trade_date, hgt, sgt, north_money, south_money
                    FROM smartmoney.raw_moneyflow_hsgt
                    WHERE trade_date <= :as_of
                      AND trade_date >= (:as_of - INTERVAL '60 days')
                    ORDER BY trade_date DESC
                    LIMIT 45
                """),
                {"as_of": as_of_trade_date},
            ).mappings()]
            company_events = [dict(r) for r in conn.execute(
                text("""
                    SELECT capture_date, event_type, title, summary, polarity,
                           importance, source_type, source_url, publish_time,
                           extraction_model
                    FROM research.company_event_memory
                    WHERE ts_code = :ts_code
                      AND capture_date <= :as_of
                      AND capture_date >= (:as_of - INTERVAL '120 days')
                    ORDER BY capture_date DESC, importance DESC NULLS LAST
                    LIMIT 20
                """),
                {"ts_code": ts_code, "as_of": as_of_trade_date},
            ).mappings()]
            catalyst_events = [dict(r) for r in conn.execute(
                text("""
                    SELECT capture_date, event_type, title, summary, polarity,
                           importance, source_url, publish_time,
                           extraction_model, target_ts_codes, target_sectors
                    FROM ta.catalyst_event_memory
                    WHERE :ts_code = ANY(target_ts_codes)
                      AND capture_date <= :as_of
                      AND capture_date >= (:as_of - INTERVAL '120 days')
                    ORDER BY capture_date DESC, importance DESC NULLS LAST
                    LIMIT 20
                """),
                {"ts_code": ts_code, "as_of": as_of_trade_date},
            ).mappings()]
        rows = len(top_list) + len(top_inst) + len(kpl) + len(limit_rows) + len(block_trades) + len(market_margin) + len(northbound) + len(company_events) + len(catalyst_events)
        latest_dates = [
            row.get("trade_date")
            for row in [
                *(top_list or []),
                *(top_inst or []),
                *(kpl or []),
                *(limit_rows or []),
                *(block_trades or []),
                *(market_margin or []),
                *(northbound or []),
            ]
            if row.get("trade_date")
        ]
        latest_dates.extend(
            row.get("capture_date")
            for row in [*(company_events or []), *(catalyst_events or [])]
            if row.get("capture_date")
        )
        return LoadResult(
            name="event_context",
            data={
                "top_list": top_list,
                "top_inst": top_inst,
                "kpl": kpl,
                "limit_list": limit_rows,
                "block_trade": block_trades,
                "market_margin": market_margin,
                "northbound": northbound,
                "company_events": company_events,
                "catalyst_events": catalyst_events,
            },
            source="postgres",
            status="ok",
            rows=rows,
            as_of=max(latest_dates) if latest_dates else None,
            required=False,
            message=None if rows else "近30日无龙虎榜/涨停事件，按中性处理。",
        )

    def load_sector_membership(
        self,
        ts_code: str,
        as_of_trade_date: dt.date,
    ) -> LoadResult[dict[str, Any]]:
        snapshot_month = as_of_trade_date.replace(day=1)
        with self.engine.connect() as conn:
            row = conn.execute(
                text("""
                    SELECT snapshot_month, l1_code, l1_name, l2_code, l2_name, name
                    FROM smartmoney.sw_member_monthly
                    WHERE ts_code = :ts_code AND snapshot_month <= :snapshot_month
                    ORDER BY snapshot_month DESC
                    LIMIT 1
                """),
                {"ts_code": ts_code, "snapshot_month": snapshot_month},
            ).mappings().fetchone()
            if row is None:
                sector_flow = []
                sector_state = None
                sector_factor = None
            else:
                l2_code = row["l2_code"]
                sector_flow = [
                    dict(r)
                    for r in conn.execute(
                        text("""
                            SELECT trade_date, l2_code, l2_name, l1_code, l1_name,
                                   net_amount, buy_elg_amount, sell_elg_amount,
                                   buy_lg_amount, sell_lg_amount, stock_count
                            FROM smartmoney.sector_moneyflow_sw_daily
                            WHERE l2_code = :l2_code AND trade_date <= :as_of
                            ORDER BY trade_date DESC
                            LIMIT 7
                        """),
                        {"l2_code": l2_code, "as_of": as_of_trade_date},
                    ).mappings()
                ]
                sector_state = conn.execute(
                    text("""
                        SELECT trade_date, sector_code, sector_source, sector_name,
                               role, cycle_phase, role_confidence, phase_confidence,
                               evidence_json
                        FROM smartmoney.sector_state_daily
                        WHERE sector_code = :l2_code AND trade_date <= :as_of
                        ORDER BY trade_date DESC
                        LIMIT 1
                    """),
                    {"l2_code": l2_code, "as_of": as_of_trade_date},
                ).mappings().fetchone()
                sector_factor = conn.execute(
                    text("""
                        SELECT trade_date, sector_code, sector_source, sector_name,
                               heat_score, trend_score, persistence_score,
                               crowding_score, derived_json
                        FROM smartmoney.factor_daily
                        WHERE sector_code = :l2_code AND trade_date <= :as_of
                        ORDER BY trade_date DESC
                        LIMIT 1
                    """),
                    {"l2_code": l2_code, "as_of": as_of_trade_date},
                ).mappings().fetchone()
                sector_peers = [
                    dict(r)
                    for r in conn.execute(
                        text("""
                            WITH members AS (
                                SELECT m.ts_code, m.name, ci.list_status
                                FROM smartmoney.sw_member_monthly m
                                LEFT JOIN research.company_identity ci ON ci.ts_code = m.ts_code
                                WHERE m.snapshot_month = :snapshot_month
                                  AND m.l2_code = :l2_code
                                  AND COALESCE(ci.list_status, 'L') = 'L'
                                  AND m.name NOT LIKE '%退市%'
                                  AND m.name NOT LIKE '%退(%'
                                  AND m.name NOT LIKE '%退'
                            ),
                            latest_daily AS (
                                SELECT DISTINCT ON (d.ts_code)
                                       d.ts_code, d.trade_date, d.close, d.pct_chg, d.amount
                                FROM smartmoney.raw_daily d
                                JOIN members m ON m.ts_code = d.ts_code
                                WHERE d.trade_date <= :as_of
                                ORDER BY d.ts_code, d.trade_date DESC
                            ),
                            daily_ranked AS (
                                SELECT d.ts_code, d.close,
                                       ROW_NUMBER() OVER (PARTITION BY d.ts_code ORDER BY d.trade_date DESC) AS rn
                                FROM smartmoney.raw_daily d
                                JOIN members m ON m.ts_code = d.ts_code
                                WHERE d.trade_date <= :as_of
                            ),
                            returns AS (
                                SELECT now.ts_code,
                                       CASE WHEN prev.close IS NOT NULL AND prev.close <> 0
                                            THEN (now.close / prev.close - 1) * 100
                                            ELSE NULL END AS return_5d_pct,
                                       CASE WHEN prev10.close IS NOT NULL AND prev10.close <> 0
                                            THEN (now.close / prev10.close - 1) * 100
                                            ELSE NULL END AS return_10d_pct,
                                       CASE WHEN prev15.close IS NOT NULL AND prev15.close <> 0
                                            THEN (now.close / prev15.close - 1) * 100
                                            ELSE NULL END AS return_15d_pct
                                FROM daily_ranked now
                                LEFT JOIN daily_ranked prev ON prev.ts_code = now.ts_code AND prev.rn = 6
                                LEFT JOIN daily_ranked prev10 ON prev10.ts_code = now.ts_code AND prev10.rn = 11
                                LEFT JOIN daily_ranked prev15 ON prev15.ts_code = now.ts_code AND prev15.rn = 16
                                WHERE now.rn = 1
                            ),
                            latest_basic AS (
                                SELECT DISTINCT ON (b.ts_code)
                                       b.ts_code, b.total_mv, b.circ_mv, b.pe_ttm, b.pb
                                FROM smartmoney.raw_daily_basic b
                                JOIN members m ON m.ts_code = b.ts_code
                                WHERE b.trade_date <= :as_of
                                ORDER BY b.ts_code, b.trade_date DESC
                            ),
                            flow7 AS (
                                SELECT mf.ts_code, SUM(mf.net_mf_amount) AS net_mf_amount_7d
                                FROM smartmoney.raw_moneyflow mf
                                JOIN members m ON m.ts_code = mf.ts_code
                                WHERE mf.trade_date <= :as_of
                                  AND mf.trade_date >= (:as_of - INTERVAL '20 days')
                                GROUP BY mf.ts_code
                            ),
                            ta_latest AS (
                                SELECT DISTINCT ON (c.ts_code)
                                       c.ts_code, c.setup_name, c.final_score, c.star_rating
                                FROM ta.candidates_daily c
                                JOIN members m ON m.ts_code = c.ts_code
                                WHERE c.trade_date <= :as_of
                                ORDER BY c.ts_code, c.trade_date DESC, c.final_score DESC NULLS LAST
                            )
                            SELECT m.ts_code, m.name, m.list_status,
                                   ld.close, ld.pct_chg, ld.amount,
                                   r.return_5d_pct, r.return_10d_pct, r.return_15d_pct,
                                   lb.total_mv, lb.circ_mv, lb.pe_ttm, lb.pb,
                                   f.net_mf_amount_7d,
                                   t.setup_name, t.final_score AS ta_score, t.star_rating
                            FROM members m
                            LEFT JOIN latest_daily ld ON ld.ts_code = m.ts_code
                            LEFT JOIN returns r ON r.ts_code = m.ts_code
                            LEFT JOIN latest_basic lb ON lb.ts_code = m.ts_code
                            LEFT JOIN flow7 f ON f.ts_code = m.ts_code
                            LEFT JOIN ta_latest t ON t.ts_code = m.ts_code
                        """),
                        {"l2_code": l2_code, "snapshot_month": row["snapshot_month"], "as_of": as_of_trade_date},
                    ).mappings()
                ]
                peer_codes = [r["ts_code"] for r in sector_peers if r.get("ts_code")]
                peer_daily_returns: dict[str, list[dict[str, Any]]] = {}
                if peer_codes:
                    rows_15d = [
                        dict(r)
                        for r in conn.execute(
                            text("""
                                WITH ranked AS (
                                    SELECT d.ts_code, d.trade_date, d.pct_chg,
                                           ROW_NUMBER() OVER (PARTITION BY d.ts_code ORDER BY d.trade_date DESC) AS rn
                                    FROM smartmoney.raw_daily d
                                    WHERE d.ts_code = ANY(:peer_codes)
                                      AND d.trade_date <= :as_of
                                )
                                SELECT ts_code, trade_date, pct_chg
                                FROM ranked
                                WHERE rn <= 15
                                ORDER BY ts_code, trade_date
                            """),
                            {"peer_codes": peer_codes, "as_of": as_of_trade_date},
                        ).mappings()
                    ]
                    for daily_row in rows_15d:
                        peer_daily_returns.setdefault(str(daily_row["ts_code"]), []).append(
                            {
                                "trade_date": daily_row.get("trade_date"),
                                "pct_chg": _float_or_none(daily_row.get("pct_chg")),
                            }
                        )
                peer_fundamentals = []
                if peer_codes:
                    peer_fundamentals = [
                        dict(r)
                        for r in conn.execute(
                            text("""
                                WITH latest AS (
                                    SELECT ts_code, period_type, MAX(period) AS period
                                    FROM research.period_factor_decomposition
                                    WHERE ts_code = ANY(:peer_codes)
                                      AND factor_name = ANY(:factor_names)
                                      AND period_type IN ('annual', 'quarterly')
                                    GROUP BY ts_code, period_type
                                )
                                SELECT p.ts_code, p.factor_family, p.factor_name,
                                       p.period, p.period_type, p.value, p.unit
                                FROM research.period_factor_decomposition p
                                JOIN latest l
                                  ON l.ts_code = p.ts_code
                                 AND l.period_type = p.period_type
                                 AND l.period = p.period
                                WHERE p.factor_name = ANY(:factor_names)
                                ORDER BY p.ts_code, p.period_type, p.factor_family, p.factor_name
                            """),
                            {
                                "peer_codes": peer_codes,
                                "factor_names": ["ROE", "营收同比增速", "CFO/NI", "资产负债率", "审计意见稳定性"],
                            },
                        ).mappings()
                    ]
        if row is None:
            return LoadResult(
                name="sector_membership",
                data=None,
                source="missing",
                status="missing",
                rows=0,
                required=False,
                message=f"No SW membership found locally for {ts_code} as of {as_of_trade_date}.",
            )
        data = dict(row)
        data["sector_flow_7d"] = list(reversed(sector_flow))
        data["sector_state"] = dict(sector_state) if sector_state else None
        data["sector_factor"] = dict(sector_factor) if sector_factor else None
        decorated_peers = [
            _decorate_peer({**r, "daily_returns_15d": peer_daily_returns.get(str(r.get("ts_code")), [])}, ts_code)
            for r in sector_peers
            if _is_active_listing_peer(r, ts_code)
        ]
        data["sector_peers"] = decorated_peers
        data["sector_leaders"] = _build_sector_leaders(decorated_peers, ts_code)
        data["peer_fundamentals"] = peer_fundamentals
        return LoadResult(
            name="sector_membership",
            data=data,
            source="postgres",
            status="ok",
            rows=1,
            as_of=data.get("snapshot_month"),
            required=False,
        )

    def load_ta_context(
        self,
        ts_code: str,
        as_of_trade_date: dt.date,
    ) -> LoadResult[dict[str, Any]]:
        with self.engine.connect() as conn:
            candidates = conn.execute(
                text("""
                    SELECT trade_date, setup_name, rank, final_score, star_rating,
                           regime_at_gen, evidence_json, validation_json,
                           invalidation_json, entry_price, stop_loss, target_price,
                           rr_ratio, price_basis
                    FROM ta.candidates_daily
                    WHERE ts_code = :ts_code AND trade_date <= :as_of
                    ORDER BY trade_date DESC, rank NULLS LAST
                    LIMIT 30
                """),
                {"ts_code": ts_code, "as_of": as_of_trade_date},
            ).mappings().all()
            warnings = conn.execute(
                text("""
                    SELECT trade_date, setup_name, score, triggers, evidence,
                           regime_at_gen, sector_role, sector_cycle_phase
                    FROM ta.warnings_daily
                    WHERE ts_code = :ts_code AND trade_date <= :as_of
                    ORDER BY trade_date DESC, score DESC
                    LIMIT 30
                """),
                {"ts_code": ts_code, "as_of": as_of_trade_date},
            ).mappings().all()
            regime = conn.execute(
                text("""
                    SELECT trade_date, regime, confidence, evidence_json, transitions_json
                    FROM ta.regime_daily
                    WHERE trade_date <= :as_of
                    ORDER BY trade_date DESC
                    LIMIT 1
                """),
                {"as_of": as_of_trade_date},
            ).mappings().fetchone()
            setup_names = sorted(
                {
                    str(row["setup_name"])
                    for row in [*candidates, *warnings]
                    if row.get("setup_name")
                }
            )
            setup_metrics = []
            if setup_names:
                setup_metrics = [
                    dict(row)
                    for row in conn.execute(
                        text("""
                            SELECT DISTINCT ON (setup_name)
                                   setup_name, trade_date, triggers_count,
                                   winrate_60d, avg_return_60d, pl_ratio_60d,
                                   winrate_250d, decay_score, combined_score_60d
                            FROM ta.setup_metrics_daily
                            WHERE setup_name = ANY(:setup_names)
                              AND trade_date <= :as_of
                            ORDER BY setup_name, trade_date DESC
                        """),
                        {"setup_names": setup_names, "as_of": as_of_trade_date},
                    ).mappings()
                ]

        data = {
            "candidates": [_decorate_ta_row(dict(r)) for r in candidates],
            "warnings": [_decorate_ta_row(dict(r)) for r in warnings],
            "regime": dict(regime) if regime else None,
            "setup_metrics": [_decorate_ta_row(r) for r in setup_metrics],
        }
        rows = len(data["candidates"]) + len(data["warnings"]) + (1 if regime else 0) + len(setup_metrics)
        status = "ok" if rows else "missing"
        return LoadResult(
            name="ta_context",
            data=data if rows else None,
            source="postgres" if rows else "missing",
            status=status,
            rows=rows,
            as_of=as_of_trade_date if rows else None,
            required=False,
            message=None if rows else f"No TA context found locally for {ts_code} as of {as_of_trade_date}.",
        )

    def load_research_lineup(self, ts_code: str) -> LoadResult[dict[str, Any]]:
        data = load_fundamental_lineup(self.engine, ts_code)
        rows = (
            len(data.get("annual_factors") or [])
            + len(data.get("quarterly_factors") or [])
            + len(data.get("recent_research_reports") or [])
        )
        return LoadResult(
            name="research_lineup",
            data=data if rows else None,
            source="postgres" if rows else "missing",
            status="ok" if rows else "missing",
            rows=rows,
            required=False,
            message=None if rows else f"No Research lineup found locally for {ts_code}.",
        )

    def load_model_context(
        self,
        ts_code: str,
        as_of_trade_date: dt.date,
        sector_data: dict[str, Any] | None,
    ) -> LoadResult[dict[str, Any]]:
        from ifa.families.stock.ml import load_reused_model_context

        data = load_reused_model_context(
            self.engine,
            ts_code=ts_code,
            as_of_trade_date=as_of_trade_date,
            sector_data=sector_data,
        )
        available = sum(1 for item in data.values() if isinstance(item, dict) and item.get("available"))
        return LoadResult(
            name="model_context",
            data=data,
            source="postgres" if available else "missing",
            status="ok" if available else "missing",
            rows=available,
            as_of=as_of_trade_date if available else None,
            required=False,
            message=None if available else "SmartMoney / Ningbo / Kronos 既有模型均未命中目标股。",
        )

    def _load_frame(
        self,
        name: str,
        sql,
        params: dict[str, Any],
        *,
        date_col: str,
        min_rows: int,
        required: bool,
    ) -> LoadResult[pd.DataFrame]:
        with self.engine.connect() as conn:
            df = pd.read_sql_query(sql, conn, params=params)
        if df.empty:
            return LoadResult(
                name=name,
                data=None,
                source="missing",
                status="missing",
                rows=0,
                required=required,
                message=f"No local {name} rows for {params.get('ts_code')} as of {params.get('as_of')}.",
            )

        df[date_col] = pd.to_datetime(df[date_col]).dt.date
        df = df.sort_values(date_col).reset_index(drop=True)
        status = "ok" if len(df) >= min_rows else "partial"
        latest = df[date_col].iloc[-1]
        return LoadResult(
            name=name,
            data=df,
            source="postgres",
            status=status,
            rows=len(df),
            as_of=latest,
            required=required,
            message=None if status == "ok" else f"Only {len(df)} local {name} rows; expected at least {min_rows}.",
        )
