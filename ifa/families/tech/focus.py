"""Default user focus list — Tech-relevant A-share holdings / watchlist seed.

Why hardcoded for v1:
  - There is no user-management system yet (out of P0 scope).
  - "default" user must still produce useful Tech reports.
  - Final shape of the focus list will eventually come from a periodic seed
    job that filters the broad market by recent leader strength + AI 5-layer
    coverage. For now we curate a representative basket that covers each
    layer with well-known leaders + 1–2 follow-up tickers.

Composition:
  - 重点关注 (deep view, ≤5 in any single Tech report): 10 stocks
  - 普通关注 (brief view, ≤10):                          20 stocks
  - Roughly half tech, half macro/policy-driven (so the same default user can
    feed the main A-share report later without rebuilding).

Each entry tags its primary AI layer so the Tech report can deep-link the
stock to its 5-layer position. Non-tech stocks are tagged `non_tech`.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class FocusStock:
    ts_code: str
    display_name: str
    layer: str          # 'energy' | 'chips' | 'infra' | 'models' | 'apps' | 'non_tech'
    sub_theme: str      # 子方向描述
    note: str = ""


# ── 重点关注（10）— Tech 占 8 + 非 Tech 2 ───────────────────────────────
DEFAULT_IMPORTANT: list[FocusStock] = [
    FocusStock("300308.SZ", "中际旭创",   "infra",     "光模块龙头",
               "全球高速光模块（800G/1.6T）核心供应商；NVDA AI 数据中心直接受益标的"),
    FocusStock("002463.SZ", "沪电股份",   "infra",     "AI 服务器 PCB",
               "高端 PCB（AI 服务器 / 800G 光模块 PCB）领先供应商"),
    FocusStock("688256.SH", "寒武纪",     "chips",     "国产 AI 芯片",
               "国产 GPU/ASIC 训推芯片代表"),
    FocusStock("002371.SZ", "北方华创",   "chips",     "半导体设备龙头",
               "刻蚀/薄膜/清洗设备国产化领跑"),
    FocusStock("688041.SH", "海光信息",   "chips",     "CPU + 加速卡",
               "国产 x86 CPU + DCU 算力加速卡"),
    FocusStock("002230.SZ", "科大讯飞",   "apps",      "AI 大模型 / 语音",
               "讯飞星火大模型 + 教育 / 医疗 / 办公 AI 应用"),
    FocusStock("002050.SZ", "三花智控",   "apps",      "人形机器人核心部件",
               "执行器 / 减速器 / 热管理；特斯拉 Optimus 供应链"),
    FocusStock("600406.SH", "国电南瑞",   "energy",    "电网 / 特高压 / 储能",
               "电力调度自动化 + 储能 + 数据中心 UPS"),
    # 非 Tech 占位 ─────────────────────────────────────────────
    FocusStock("600519.SH", "贵州茅台",   "non_tech",  "消费白酒龙头", "高端白酒、防御 / 高股息基准"),
    FocusStock("601318.SH", "中国平安",   "non_tech",  "金融", "保险 / 综合金融，受利率与资本市场政策影响"),
]


# ── 普通关注（20）— Tech 占 14 + 非 Tech 6 ─────────────────────────────
DEFAULT_REGULAR: list[FocusStock] = [
    FocusStock("300502.SZ", "新易盛",     "infra",     "光模块",       "1.6T/800G 第二梯队"),
    FocusStock("002463.SZ", "沪电股份",   "infra",     "AI PCB",       "（重点关注重叠示例）"),
    FocusStock("000977.SZ", "浪潮信息",   "infra",     "AI 服务器",    "国产 AI 服务器主力"),
    FocusStock("000938.SZ", "紫光股份",   "infra",     "服务器 / 网络", "新华三体系；服务器 + 交换机"),
    FocusStock("002049.SZ", "紫光国微",   "chips",     "FPGA / 特种芯片", "国产 FPGA + 安全芯片"),
    FocusStock("603501.SH", "韦尔股份",   "chips",     "CIS 图像传感器", "智能手机 / 汽车 CIS 龙头"),
    FocusStock("688012.SH", "中微公司",   "chips",     "刻蚀设备",     "等离子刻蚀全球第二梯队"),
    FocusStock("603986.SH", "兆易创新",   "chips",     "存储 + MCU",   "国产 NOR Flash / DRAM / MCU"),
    FocusStock("300782.SZ", "卓胜微",     "chips",     "射频前端",     "国产 5G RF 前端"),
    FocusStock("002415.SZ", "海康威视",   "apps",      "智能视觉",     "AI 视觉 + 端侧 AI 应用"),
    FocusStock("002241.SZ", "歌尔股份",   "apps",      "AR / VR / 端侧 AI", "Meta / 苹果 AR/VR 供应链"),
    FocusStock("002475.SZ", "立讯精密",   "apps",      "消费电子 / 端侧 AI", "苹果 AI 终端核心供应商"),
    FocusStock("300229.SZ", "拓尔思",     "models",    "数据要素 / NLP", "知识图谱 + 大模型 + 数据要素"),
    FocusStock("000938.SZ", "紫光股份",   "infra",     "服务器（重叠）", "（示意）"),
    FocusStock("300274.SZ", "阳光电源",   "energy",    "光储逆变",     "光伏逆变 + 储能"),
    # 非 Tech ─────────────────────────────────────────────────
    FocusStock("601012.SH", "隆基绿能",   "non_tech",  "光伏",         "光伏组件 + 硅片，新能源链"),
    FocusStock("600276.SH", "恒瑞医药",   "non_tech",  "医药龙头",     "创新药 / 防御性配置"),
    FocusStock("000333.SZ", "美的集团",   "non_tech",  "家电",         "白色家电出口链"),
    FocusStock("601398.SH", "工商银行",   "non_tech",  "银行",         "国有大行 / 高股息"),
    FocusStock("601899.SH", "紫金矿业",   "non_tech",  "有色矿业",     "黄金 + 铜矿，跨资产联动"),
]


def tech_only(stocks: list[FocusStock], limit: int) -> list[FocusStock]:
    """Filter to Tech-relevant stocks (any AI layer != non_tech) up to `limit`."""
    return [s for s in stocks if s.layer != "non_tech"][:limit]


def get_default_focus() -> tuple[list[FocusStock], list[FocusStock]]:
    """Return (important, regular) focus lists for the default user."""
    return DEFAULT_IMPORTANT, DEFAULT_REGULAR


def get_focus_for(user: str = "default") -> tuple[list[FocusStock], list[FocusStock]]:
    """Resolve focus lists for a user. v1 only supports 'default'."""
    if user == "default":
        return get_default_focus()
    # Future: pluggable users
    return get_default_focus()
