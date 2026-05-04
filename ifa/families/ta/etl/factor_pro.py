"""TA ETL: fetch stk_factor_pro and store 80 selected fields into ta.factor_pro_daily.

Unit conversions at the fetcher boundary (data-accuracy-guidelines.md Rule 1):
  · amount_yuan  : Tushare 千元 → × 1000 → 元  (TUSHARE_UNITS: stk_factor_pro.amount)
  · total_mv_yuan: Tushare 万元 → × 10000 → 元 (TUSHARE_UNITS: stk_factor_pro.total_mv)
  · circ_mv_yuan : Tushare 万元 → × 10000 → 元
  · turnover_rate_pct / turnover_rate_f_pct: already 0-100, stored as-is
  · vol          : 手, stored as-is (no conversion needed for TA use)
  · prices (close/open/high/low qfq): 元/股, stored as-is
"""
from __future__ import annotations

import logging
from datetime import date
from decimal import Decimal
from typing import Any

import pandas as pd
import tushare as ts
from sqlalchemy import text
from sqlalchemy.engine import Engine
from tenacity import retry, stop_after_attempt, wait_exponential

from ifa.config import get_settings
from ifa.core.units import MoneyUnit, normalize_tushare_value

log = logging.getLogger(__name__)

# 80 fields to pull from stk_factor_pro (Tushare field names)
_FIELDS = ",".join([
    "ts_code,trade_date",
    # OHLCV
    "close_qfq,open_qfq,high_qfq,low_qfq,vol,amount,turnover_rate,turnover_rate_f,volume_ratio",
    # Valuation
    "pe_ttm,pb,ps_ttm,total_mv,circ_mv",
    # MA qfq
    "ma_qfq_5,ma_qfq_10,ma_qfq_20,ma_qfq_30,ma_qfq_60,ma_qfq_90,ma_qfq_250",
    "ema_qfq_5,ema_qfq_10,ema_qfq_20,ema_qfq_30,ema_qfq_60,ema_qfq_90,ema_qfq_250",
    # MACD
    "macd_qfq,macd_dea_qfq,macd_dif_qfq",
    # KDJ
    "kdj_qfq,kdj_d_qfq,kdj_k_qfq",
    # BOLL
    "boll_upper_qfq,boll_mid_qfq,boll_lower_qfq",
    # RSI
    "rsi_qfq_6,rsi_qfq_12,rsi_qfq_24",
    # BIAS
    "bias1_qfq,bias2_qfq,bias3_qfq",
    # Oscillators
    "cci_qfq,wr_qfq,mfi_qfq,obv_qfq,atr_qfq,psy_qfq,mtm_qfq,roc_qfq,trix_qfq",
    # DMI
    "dmi_adx_qfq,dmi_pdi_qfq,dmi_mdi_qfq",
    # Trend count
    "updays,downdays,topdays,lowdays",
    # Channels
    "bbi_qfq,ktn_upper_qfq,ktn_mid_qfq,ktn_down_qfq,expma_12_qfq,expma_50_qfq",
    "taq_up_qfq,taq_mid_qfq,taq_down_qfq",
])

_pro: Any = None


def _get_pro() -> Any:
    global _pro
    if _pro is None:
        settings = get_settings()
        ts.set_token(settings.tushare_token.get_secret_value())
        _pro = ts.pro_api()
    return _pro


@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=2, max=10))
def _pull_factor_pro(trade_date: str) -> pd.DataFrame:
    return _get_pro().stk_factor_pro(trade_date=trade_date, fields=_FIELDS)


def _safe(val: Any) -> Decimal | None:
    if val is None or (isinstance(val, float) and pd.isna(val)):
        return None
    return Decimal(str(val))


def fetch_and_store_factor_pro(engine: Engine, trade_date: date | str) -> int:
    """Pull stk_factor_pro for *trade_date* and upsert into ta.factor_pro_daily.

    Returns number of rows written.
    """
    td = trade_date if isinstance(trade_date, str) else trade_date.strftime("%Y%m%d")
    df = _pull_factor_pro(td)
    if df is None or df.empty:
        log.info("factor_pro: no data for %s", td)
        return 0

    rows = []
    for _, r in df.iterrows():
        ts_code = r["ts_code"]

        def s(col: str) -> Decimal | None:
            return _safe(r.get(col))

        rows.append({
            "trade_date": r["trade_date"],
            "ts_code": ts_code,
            "close_qfq": s("close_qfq"),
            "open_qfq": s("open_qfq"),
            "high_qfq": s("high_qfq"),
            "low_qfq": s("low_qfq"),
            "vol": s("vol"),
            # Unit conversion: 千元 → 元
            "amount_yuan": (
                normalize_tushare_value("stk_factor_pro", "amount", r.get("amount"))
            ),
            "turnover_rate_pct": s("turnover_rate"),
            "turnover_rate_f_pct": s("turnover_rate_f"),
            "volume_ratio": s("volume_ratio"),
            "pe_ttm": s("pe_ttm"),
            "pb": s("pb"),
            "ps_ttm": s("ps_ttm"),
            # Unit conversion: 万元 → 元
            "total_mv_yuan": normalize_tushare_value("stk_factor_pro", "total_mv", r.get("total_mv")),
            "circ_mv_yuan": normalize_tushare_value("stk_factor_pro", "circ_mv", r.get("circ_mv")),
            "ma_qfq_5": s("ma_qfq_5"),
            "ma_qfq_10": s("ma_qfq_10"),
            "ma_qfq_20": s("ma_qfq_20"),
            "ma_qfq_30": s("ma_qfq_30"),
            "ma_qfq_60": s("ma_qfq_60"),
            "ma_qfq_90": s("ma_qfq_90"),
            "ma_qfq_250": s("ma_qfq_250"),
            "ema_qfq_5": s("ema_qfq_5"),
            "ema_qfq_10": s("ema_qfq_10"),
            "ema_qfq_20": s("ema_qfq_20"),
            "ema_qfq_30": s("ema_qfq_30"),
            "ema_qfq_60": s("ema_qfq_60"),
            "ema_qfq_90": s("ema_qfq_90"),
            "ema_qfq_250": s("ema_qfq_250"),
            "macd_qfq": s("macd_qfq"),
            "macd_dea_qfq": s("macd_dea_qfq"),
            "macd_dif_qfq": s("macd_dif_qfq"),
            "kdj_qfq": s("kdj_qfq"),
            "kdj_d_qfq": s("kdj_d_qfq"),
            "kdj_k_qfq": s("kdj_k_qfq"),
            "boll_upper_qfq": s("boll_upper_qfq"),
            "boll_mid_qfq": s("boll_mid_qfq"),
            "boll_lower_qfq": s("boll_lower_qfq"),
            "rsi_qfq_6": s("rsi_qfq_6"),
            "rsi_qfq_12": s("rsi_qfq_12"),
            "rsi_qfq_24": s("rsi_qfq_24"),
            "bias1_qfq": s("bias1_qfq"),
            "bias2_qfq": s("bias2_qfq"),
            "bias3_qfq": s("bias3_qfq"),
            "cci_qfq": s("cci_qfq"),
            "wr_qfq": s("wr_qfq"),
            "mfi_qfq": s("mfi_qfq"),
            "obv_qfq": s("obv_qfq"),
            "atr_qfq": s("atr_qfq"),
            "psy_qfq": s("psy_qfq"),
            "mtm_qfq": s("mtm_qfq"),
            "roc_qfq": s("roc_qfq"),
            "trix_qfq": s("trix_qfq"),
            "dmi_adx_qfq": s("dmi_adx_qfq"),
            "dmi_pdi_qfq": s("dmi_pdi_qfq"),
            "dmi_mdi_qfq": s("dmi_mdi_qfq"),
            "updays": int(r["updays"]) if r.get("updays") is not None and not pd.isna(r["updays"]) else None,
            "downdays": int(r["downdays"]) if r.get("downdays") is not None and not pd.isna(r["downdays"]) else None,
            "topdays": int(r["topdays"]) if r.get("topdays") is not None and not pd.isna(r["topdays"]) else None,
            "lowdays": int(r["lowdays"]) if r.get("lowdays") is not None and not pd.isna(r["lowdays"]) else None,
            "bbi_qfq": s("bbi_qfq"),
            "ktn_upper_qfq": s("ktn_upper_qfq"),
            "ktn_mid_qfq": s("ktn_mid_qfq"),
            "ktn_down_qfq": s("ktn_down_qfq"),
            "expma_12_qfq": s("expma_12_qfq"),
            "expma_50_qfq": s("expma_50_qfq"),
            "taq_up_qfq": s("taq_up_qfq"),
            "taq_mid_qfq": s("taq_mid_qfq"),
            "taq_down_qfq": s("taq_down_qfq"),
        })

    if not rows:
        return 0

    cols = list(rows[0].keys())
    placeholders = ", ".join(f":{c}" for c in cols)
    col_list = ", ".join(cols)
    update_set = ", ".join(
        f"{c} = EXCLUDED.{c}" for c in cols if c not in ("trade_date", "ts_code")
    )

    sql = text(f"""
        INSERT INTO ta.factor_pro_daily ({col_list})
        VALUES ({placeholders})
        ON CONFLICT (trade_date, ts_code) DO UPDATE SET {update_set}
    """)

    with engine.begin() as conn:
        conn.execute(sql, rows)

    log.info("factor_pro: wrote %d rows for %s", len(rows), td)
    return len(rows)
