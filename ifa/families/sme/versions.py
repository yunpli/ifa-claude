"""Version identifiers for SME data and logic contracts.

These are intentionally separate from package versions. SME features are used
for backtests and tuning, so a semantic change in a SQL formula must be
traceable even when the Python package version stays the same.
"""
from __future__ import annotations


SME_SCHEMA_VERSION = "sme_mvp1_schema_v1"
SME_STOCK_FLOW_LOGIC_VERSION = "stock_orderflow_v1_3"
SME_SECTOR_FLOW_LOGIC_VERSION = "sector_orderflow_v1_1"
SME_DIFFUSION_LOGIC_VERSION = "diffusion_v1_2"
SME_STATE_LOGIC_VERSION = "state_machine_v1_1"
SME_LABEL_LOGIC_VERSION = "labels_forward_v1_1"
SME_MARKET_STRUCTURE_LOGIC_VERSION = "market_structure_v1_2"
SME_STRATEGY_EVAL_LOGIC_VERSION = "strategy_eval_v1_0"


def logic_versions() -> dict[str, str]:
    return {
        "schema": SME_SCHEMA_VERSION,
        "stock_orderflow": SME_STOCK_FLOW_LOGIC_VERSION,
        "sector_orderflow": SME_SECTOR_FLOW_LOGIC_VERSION,
        "diffusion": SME_DIFFUSION_LOGIC_VERSION,
        "state": SME_STATE_LOGIC_VERSION,
        "labels": SME_LABEL_LOGIC_VERSION,
        "market_structure": SME_MARKET_STRUCTURE_LOGIC_VERSION,
        "strategy_eval": SME_STRATEGY_EVAL_LOGIC_VERSION,
    }
