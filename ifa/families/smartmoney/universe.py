"""SmartMoney 板块宇宙定义.

板块来源（4 个独立但部分重叠的命名空间）：
  sw   申万 — 行业体系（一级 31 个），最稳定，做量化基础
  dc   东财 — 概念指数 + 板块（地域/行业/概念三类，~1013 个）
  ths  同花顺 — 板块/概念（~408 个）
  kpl  开盘啦 — 炒作主题概念（~3-30 个/天，最敏感于短线主线）

同一只股票通常同时属于多个 source 的多个板块。SmartMoney 的板块层因子按
(sector_code, sector_source) 二元主键存储，下游消费时按 source 选择视角。

设计原则：
  - sw 一级 31：作为"骨架"做长期 / 稳定因子（rolling 60d 等）
  - dc 概念：作为"放大镜"看具体热点 / 资金分级
  - kpl 概念：作为"晴雨表"识别当日主线（z_t_num ranked）
  - ths 概念：作为"补充"，与 dc 跨源校验
"""
from __future__ import annotations

from dataclasses import dataclass

# Source enum (must match sector_source CHECK constraint in DB)
SOURCE_SW = "sw"
SOURCE_DC = "dc"
SOURCE_THS = "ths"
SOURCE_KPL = "kpl"

ALL_SOURCES = (SOURCE_SW, SOURCE_DC, SOURCE_THS, SOURCE_KPL)


@dataclass(frozen=True)
class SectorView:
    """A view onto sector-level data, identified by (code, source)."""

    code: str
    source: str
    name: str | None = None

    @property
    def key(self) -> tuple[str, str]:
        return (self.code, self.source)


# ── 主要宽基指数（report 用，不参与板块因子）─────────────────────────────
MAIN_INDEXES: list[tuple[str, str]] = [
    ("000001.SH", "上证指数"),
    ("399001.SZ", "深证成指"),
    ("399006.SZ", "创业板指"),
    ("000300.SH", "沪深300"),
    ("000905.SH", "中证500"),
    ("000852.SH", "中证1000"),
    ("000688.SH", "科创50"),
    ("899050.BJ", "北证50"),
]


# ── 申万一级行业（31 个，部分实际 28-32 之间随版本变化）─────────────
# 这个列表在 backfill 时通过 fut_basic / index_classify 自动发现，写死只为
# fallback / 文档。
SW_L1_SEED: list[tuple[str, str]] = [
    ("801010.SI", "农林牧渔"),
    ("801030.SI", "基础化工"),
    ("801040.SI", "钢铁"),
    ("801050.SI", "有色金属"),
    ("801080.SI", "电子"),
    ("801110.SI", "家用电器"),
    ("801120.SI", "食品饮料"),
    ("801130.SI", "纺织服饰"),
    ("801140.SI", "轻工制造"),
    ("801150.SI", "医药生物"),
    ("801160.SI", "公用事业"),
    ("801170.SI", "交通运输"),
    ("801180.SI", "房地产"),
    ("801200.SI", "商贸零售"),
    ("801210.SI", "社会服务"),
    ("801230.SI", "综合"),
    ("801710.SI", "建筑材料"),
    ("801720.SI", "建筑装饰"),
    ("801730.SI", "电力设备"),
    ("801740.SI", "国防军工"),
    ("801750.SI", "计算机"),
    ("801760.SI", "传媒"),
    ("801770.SI", "通信"),
    ("801780.SI", "银行"),
    ("801790.SI", "非银金融"),
    ("801880.SI", "汽车"),
    ("801890.SI", "机械设备"),
    ("801950.SI", "煤炭"),
    ("801960.SI", "石油石化"),
    ("801970.SI", "环保"),
    ("801980.SI", "美容护理"),
]


def is_concept_dc(content_type: str | None) -> bool:
    """Filter for DC sectors that are 概念 (theme), not 地域/行业.
    The PRD §2 layer logic only meaningfully applies to themes/industries."""
    return content_type == "概念"


def is_industry_dc(content_type: str | None) -> bool:
    return content_type == "行业"


def is_region_dc(content_type: str | None) -> bool:
    """Region-based DC sectors are excluded from factor computation
    (they don't have AI / cycle semantics)."""
    return content_type == "地域"
