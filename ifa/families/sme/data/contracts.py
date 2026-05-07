"""SME data contract checks."""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass

from sqlalchemy import text


@dataclass(frozen=True)
class CheckResult:
    name: str
    status: str
    message: str


def run_basic_contracts(engine, trade_date: dt.date | None = None) -> list[CheckResult]:
    checks: list[CheckResult] = []
    params = {"d": trade_date} if trade_date else {}
    date_filter = "WHERE trade_date = :d" if trade_date else ""
    with engine.connect() as conn:
        for table in [
            "sme_stock_orderflow_daily",
            "sme_sector_orderflow_daily",
            "sme_sector_diffusion_daily",
            "sme_sector_state_daily",
        ]:
            count = conn.execute(text(f"SELECT COUNT(*) FROM sme.{table} {date_filter}"), params).scalar_one()
            checks.append(CheckResult(table, "ok" if count else "degraded", f"{count} rows"))

        source_stock = conn.execute(text(f"""
            SELECT COUNT(*)
            FROM smartmoney.raw_moneyflow
            {date_filter}
        """), params).scalar_one()
        derived_stock = conn.execute(text(f"""
            SELECT COUNT(*)
            FROM sme.sme_stock_orderflow_daily
            {date_filter}
        """), params).scalar_one()
        stock_status = "ok" if int(derived_stock) >= int(source_stock) and int(source_stock) > 0 else "blocked"
        checks.append(CheckResult("source_to_stock_coverage", stock_status, f"source={int(source_stock)} derived={int(derived_stock)}"))

        bad = conn.execute(text(f"""
            SELECT COUNT(*) FROM sme.sme_stock_orderflow_daily
            {date_filter}
            {"AND" if trade_date else "WHERE"} (
                amount_yuan < 0
                OR main_net_ratio < -2.5 OR main_net_ratio > 2.5
                OR retail_net_ratio < -2.5 OR retail_net_ratio > 2.5
                OR elg_net_ratio < -2.5 OR elg_net_ratio > 2.5
            )
        """), params).scalar_one()
        checks.append(CheckResult("stock_orderflow_ratio_ranges", "ok" if bad == 0 else "blocked", f"{bad} bad rows"))

        balance_bad = conn.execute(text(f"""
            SELECT COUNT(*) FROM sme.sme_stock_orderflow_daily
            {date_filter}
            {"AND" if trade_date else "WHERE"} ABS(COALESCE(reconciliation_error_yuan, 0)) > 100000
        """), params).scalar_one()
        stock_total = conn.execute(text(f"SELECT COUNT(*) FROM sme.sme_stock_orderflow_daily {date_filter}"), params).scalar_one()
        balance_rate = (int(balance_bad) / int(stock_total)) if stock_total else 0.0
        balance_status = "ok" if balance_rate <= 0.01 else "degraded"
        checks.append(CheckResult("stock_orderflow_balance_error", balance_status, f"{int(balance_bad)} bad rows / {int(stock_total)} total ({balance_rate:.2%})"))

        quality_rows = conn.execute(text(f"""
            SELECT quality_flag, COUNT(*)
            FROM sme.sme_stock_orderflow_daily
            {date_filter}
            GROUP BY quality_flag
        """), params).fetchall()
        quality_counts = {str(flag): int(count) for flag, count in quality_rows}
        degraded_count = quality_counts.get("degraded", 0)
        quality_rate = degraded_count / int(stock_total) if stock_total else 0.0
        quality_status = "ok" if quality_rate <= 0.001 else ("degraded" if quality_rate <= 0.01 else "blocked")
        checks.append(CheckResult("stock_orderflow_quality", quality_status, f"{quality_counts}"))

        bad_cov = conn.execute(text(f"""
            SELECT COUNT(*) FROM sme.sme_sector_orderflow_daily
            {date_filter}
            {"AND" if trade_date else "WHERE"} (coverage_ratio < 0 OR coverage_ratio > 1)
        """), params).scalar_one()
        checks.append(CheckResult("sector_coverage_ranges", "ok" if bad_cov == 0 else "blocked", f"{bad_cov} bad rows"))

        low_cov = conn.execute(text(f"""
            SELECT COUNT(*) FROM sme.sme_sector_orderflow_daily
            {date_filter}
            {"AND" if trade_date else "WHERE"} coverage_ratio < 0.80
        """), params).scalar_one()
        sector_total = conn.execute(text(f"SELECT COUNT(*) FROM sme.sme_sector_orderflow_daily {date_filter}"), params).scalar_one()
        low_cov_rate = int(low_cov) / int(sector_total) if sector_total else 0.0
        cov_status = "ok" if low_cov_rate <= 0.05 else ("degraded" if low_cov_rate <= 0.20 else "blocked")
        checks.append(CheckResult("sector_coverage_quality", cov_status, f"{int(low_cov)} low-coverage sectors / {int(sector_total)} total ({low_cov_rate:.2%})"))

        align_rows = conn.execute(text(f"""
            WITH dates AS (
                SELECT DISTINCT trade_date FROM sme.sme_sector_orderflow_daily {date_filter}
            ),
            counts AS (
                SELECT d.trade_date,
                       (SELECT COUNT(*) FROM sme.sme_sector_orderflow_daily x WHERE x.trade_date = d.trade_date) AS sector_rows,
                       (SELECT COUNT(*) FROM sme.sme_sector_diffusion_daily x WHERE x.trade_date = d.trade_date) AS diffusion_rows,
                       (SELECT COUNT(*) FROM sme.sme_sector_state_daily x WHERE x.trade_date = d.trade_date) AS state_rows
                FROM dates d
            )
            SELECT COUNT(*)
            FROM counts
            WHERE sector_rows <> diffusion_rows OR sector_rows <> state_rows
        """), params).scalar_one()
        checks.append(CheckResult("sector_diffusion_state_alignment", "ok" if int(align_rows) == 0 else "blocked", f"{int(align_rows)} mismatched dates"))

        label_row = conn.execute(text("""
            SELECT
                (SELECT MAX(trade_date) FROM sme.sme_sector_orderflow_daily) AS feature_latest,
                (SELECT MAX(trade_date) FROM sme.sme_labels_daily) AS label_latest,
                (SELECT COUNT(*) FROM sme.sme_labels_daily) AS label_rows,
                (SELECT COUNT(*) FROM sme.sme_labels_daily
                  WHERE future_return IS NULL
                     OR future_excess_return_vs_market IS NULL
                     OR future_excess_return_vs_l1 IS NULL) AS null_label_rows
        """)).one()
        feature_latest, label_latest, label_rows, null_label_rows = label_row
        label_status = "ok" if label_rows and label_latest and feature_latest and label_latest <= feature_latest and int(null_label_rows or 0) == 0 else "degraded"
        checks.append(CheckResult("labels_maturity", label_status, f"feature_latest={feature_latest} label_latest={label_latest} rows={int(label_rows or 0)} null_labels={int(null_label_rows or 0)}"))
    return checks
