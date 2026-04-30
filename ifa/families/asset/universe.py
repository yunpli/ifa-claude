"""China Asset Radar Universe — the fixed set of commodities the Asset
report tracks each day, plus their A-share sector / industry-chain semantics.

Two design notes worth remembering:
  - The report's display uses *logical symbols* (CU, AU, RB, ...). The actual
    futures contract (CU2606.SHF) is resolved fresh every run via volume rank.
  - CZCE products (TA, SA, FG, CF, SR, AP, RM, OI, MA) appear in fut_basic but
    fut_daily returns no rows under the current account permission — these are
    kept in the universe but tagged `data_status='czce_unavailable'`. The
    renderer will gracefully display them as "数据未启用" instead of dropping
    them silently.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class AssetSpec:
    logical_symbol: str          # e.g. 'CU'
    display_name: str            # e.g. '铜'
    category: str                # 能源 / 贵金属 / 有色 / 黑色 / 化工 / 农产品
    exchange: str                # SHF / DCE / INE / CZC / GFE
    a_share_sectors: list[str]   # primary A-share sectors driven by this asset
    transmission_role: str       # 'cost_input' | 'demand_proxy' | 'safe_haven'
    notes: str = ""


CHINA_ASSET_UNIVERSE: list[AssetSpec] = [
    # ── 能源 ───────────────────────────────────────────────────────────────
    AssetSpec("SC",  "原油",   "能源",   "INE",
              ["石油石化", "油服", "化工", "航空运输"], "cost_input",
              "通胀预期 + 化工成本输入 + 航空运输燃料成本"),
    AssetSpec("FU",  "燃料油", "能源",   "SHF",
              ["航运", "石化"], "cost_input",
              "船用燃料；航运景气与油价同步"),
    AssetSpec("PG",  "LPG",   "能源",   "DCE",
              ["燃气", "化工"], "cost_input",
              "居民能源 + 化工原料"),

    # ── 贵金属 ─────────────────────────────────────────────────────────────
    AssetSpec("AU",  "黄金",   "贵金属", "SHF",
              ["贵金属股", "避险"], "safe_haven",
              "实际利率倒数 + 风险偏好"),
    AssetSpec("AG",  "白银",   "贵金属", "SHF",
              ["贵金属股", "电子", "光伏"], "safe_haven",
              "兼具贵金属与工业属性"),

    # ── 有色金属 ──────────────────────────────────────────────────────────
    AssetSpec("CU",  "铜",     "有色",   "SHF",
              ["有色", "新能源", "电网"], "demand_proxy",
              "全球工业需求晴雨表"),
    AssetSpec("AL",  "铝",     "有色",   "SHF",
              ["电解铝", "汽车", "制造"], "demand_proxy",
              "电解铝产能 + 地产/汽车需求"),
    AssetSpec("ZN",  "锌",     "有色",   "SHF",
              ["基建", "地产", "工业"], "demand_proxy",
              "镀锌钢需求 → 地产/基建"),
    AssetSpec("NI",  "镍",     "有色",   "SHF",
              ["新能源电池", "不锈钢"], "demand_proxy",
              "三元锂电正极 + 不锈钢"),
    AssetSpec("SN",  "锡",     "有色",   "SHF",
              ["半导体", "电子焊料"], "demand_proxy",
              "电子焊料 → 半导体周期"),
    AssetSpec("PB",  "铅",     "有色",   "SHF",
              ["电池", "工业"], "demand_proxy",
              "传统铅酸电池"),

    # ── 黑色链 ────────────────────────────────────────────────────────────
    AssetSpec("I",   "铁矿石", "黑色",   "DCE",
              ["钢铁", "地产", "基建"], "cost_input",
              "钢铁主要原料；地产/基建需求映射"),
    AssetSpec("RB",  "螺纹钢", "黑色",   "SHF",
              ["地产", "基建"], "demand_proxy",
              "建筑钢材；地产施工与基建景气"),
    AssetSpec("HC",  "热卷",   "黑色",   "SHF",
              ["制造", "汽车", "家电"], "demand_proxy",
              "板材 → 制造业 + 出口链"),
    AssetSpec("JM",  "焦煤",   "黑色",   "DCE",
              ["煤炭", "钢铁"], "cost_input",
              "焦化原料"),
    AssetSpec("J",   "焦炭",   "黑色",   "DCE",
              ["钢铁", "黑色"], "cost_input",
              "钢铁炼铁还原剂"),

    # ── 化工 / 建材（部分） ─────────────────────────────────────────────────
    AssetSpec("L",   "塑料(LLDPE)", "化工", "DCE",
              ["石化", "包装"], "cost_input",
              "聚乙烯薄膜"),
    AssetSpec("PP",  "PP",     "化工",   "DCE",
              ["石化", "消费制造"], "cost_input",
              "聚丙烯"),
    AssetSpec("V",   "PVC",    "化工",   "DCE",
              ["地产", "建材"], "cost_input",
              "氯碱 → 地产管材/型材"),
    AssetSpec("RU",  "天然橡胶", "化工", "SHF",
              ["轮胎", "汽车"], "cost_input",
              "轮胎主原料"),
    AssetSpec("BU",  "沥青",   "化工",   "SHF",
              ["基建", "道路"], "cost_input",
              "道路基建"),
    AssetSpec("TA",  "PTA",    "化工",   "CZC",
              ["化纤", "纺服", "油化工"], "cost_input",
              "数据源限制：CZCE fut_daily 不可用"),
    AssetSpec("MA",  "甲醇",   "化工",   "CZC",
              ["煤化工", "基础化工"], "cost_input",
              "数据源限制：CZCE fut_daily 不可用"),
    AssetSpec("SA",  "纯碱",   "化工",   "CZC",
              ["光伏玻璃", "玻璃", "地产"], "cost_input",
              "数据源限制：CZCE fut_daily 不可用"),
    AssetSpec("FG",  "玻璃",   "化工",   "CZC",
              ["地产竣工", "建材"], "cost_input",
              "数据源限制：CZCE fut_daily 不可用"),

    # ── 农产品 ────────────────────────────────────────────────────────────
    AssetSpec("M",   "豆粕",   "农产品", "DCE",
              ["饲料", "养殖"], "cost_input",
              "蛋白饲料 → 养殖成本"),
    AssetSpec("Y",   "豆油",   "农产品", "DCE",
              ["油脂", "食品"], "cost_input",
              "食用油"),
    AssetSpec("C",   "玉米",   "农产品", "DCE",
              ["饲料", "粮价", "通胀"], "cost_input",
              "粮价 + 饲料原料"),
    AssetSpec("CS",  "玉米淀粉", "农产品", "DCE",
              ["食品加工"], "cost_input",
              "玉米深加工"),
    AssetSpec("RM",  "菜粕",   "农产品", "CZC",
              ["水产饲料"], "cost_input",
              "数据源限制：CZCE fut_daily 不可用"),
    AssetSpec("OI",  "菜油",   "农产品", "CZC",
              ["油脂"], "cost_input",
              "数据源限制：CZCE fut_daily 不可用"),
    AssetSpec("SR",  "白糖",   "农产品", "CZC",
              ["食品饮料", "消费"], "cost_input",
              "数据源限制：CZCE fut_daily 不可用"),
    AssetSpec("CF",  "棉花",   "农产品", "CZC",
              ["纺织服装"], "cost_input",
              "数据源限制：CZCE fut_daily 不可用"),
    AssetSpec("AP",  "苹果",   "农产品", "CZC",
              ["农产品", "消费"], "cost_input",
              "数据源限制：CZCE fut_daily 不可用"),
]

# Dataset of which exchanges currently lack daily data on this account.
EXCHANGES_UNAVAILABLE = {"CZC"}


def by_category() -> dict[str, list[AssetSpec]]:
    out: dict[str, list[AssetSpec]] = {}
    for s in CHINA_ASSET_UNIVERSE:
        out.setdefault(s.category, []).append(s)
    return out


# ─── Industry chain definitions for S6 transmission analysis ───────────────

@dataclass(frozen=True)
class IndustryChain:
    name: str
    upstream_symbols: list[str]    # logical symbols whose price = cost input upstream
    midstream_symbols: list[str]   # processing/middle stage
    downstream_a_share: list[str]  # A-share sectors that absorb the impact
    narrative: str


INDUSTRY_CHAINS: list[IndustryChain] = [
    IndustryChain(
        name="油 — 化工链",
        upstream_symbols=["SC", "FU"],
        midstream_symbols=["TA", "MA", "L", "PP", "BU"],
        downstream_a_share=["纺织服装", "包装", "运输", "化工制造"],
        narrative="原油上行 → 上游石化利润扩张；中游化工成本上升；下游纺织/包装/运输承压。",
    ),
    IndustryChain(
        name="黑色 — 地产/基建链",
        upstream_symbols=["I", "JM", "J"],
        midstream_symbols=["RB", "HC"],
        downstream_a_share=["地产开发", "基建", "钢铁", "建材"],
        narrative="铁矿/双焦走强 → 钢厂利润压缩；螺纹/热卷价格反映地产+制造需求强弱。",
    ),
    IndustryChain(
        name="有色 — 制造/新能源链",
        upstream_symbols=["CU", "AL", "NI", "SN"],
        midstream_symbols=[],
        downstream_a_share=["有色金属", "新能源", "电网设备", "汽车", "家电"],
        narrative="铜铝走强 → 上游资源股受益；中下游成本承压；镍/锡映射新能源/半导体。",
    ),
    IndustryChain(
        name="贵金属 — 避险链",
        upstream_symbols=["AU", "AG"],
        midstream_symbols=[],
        downstream_a_share=["贵金属股", "避险资产"],
        narrative="黄金/白银上行 → 实际利率/避险需求驱动；与高 beta 风险偏好通常负相关。",
    ),
    IndustryChain(
        name="农产品 — 食品/养殖链",
        upstream_symbols=["M", "C", "RM", "Y", "OI"],
        midstream_symbols=["CS"],
        downstream_a_share=["养殖", "饲料", "食品饮料", "农业"],
        narrative="豆粕/玉米走强 → 饲料成本上行；养殖利润压缩，食品端转嫁能力分化。",
    ),
    IndustryChain(
        name="建材 — 地产竣工链",
        upstream_symbols=["SA", "FG", "V"],
        midstream_symbols=[],
        downstream_a_share=["光伏玻璃", "建材", "地产竣工"],
        narrative="纯碱/玻璃/PVC 反映地产竣工 + 光伏装机；价格走强通常意味着竣工预期改善。",
    ),
]
