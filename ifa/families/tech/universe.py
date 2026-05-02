"""Tech report universe — Jensen Huang's AI Five-Layer Cake mapping.

V2.1: Migrated from THS to SW. Five-layer thematic structure preserved;
SW L2 sectors substituted as the sole data source. THS concept boards
(885xxx/886xxx/881xxx) and `ths_daily`/`ths_member` calls have been
removed from the primary data path. Where a thematic concept (e.g.
"CPO", "液冷服务器", "数据要素") has no exact SW analog, the closest
SW L2 industry that *contains* those names is used.

Layer model (五层蛋糕):
  energy  L1 Energy / Power / Cooling      电力设备（电池/电网/光伏/风电）
  chips   L2 Chips / Semiconductors        电子（半导体/元件/电子化学品）
  infra   L3 Infrastructure / Connectivity 通信（通信设备/通信服务）+ 计算机设备
  models  L4 Models / Software / Platforms 计算机（软件开发/IT 服务）
  apps    L5 Applications / Devices        消费电子 + 光学光电子 + 传媒（数字媒体/游戏）
                                            + 汽车（智能驾驶/零部件）

Each layer maps to a curated list of 申万二级行业代码 (SW L2 codes).
SW codes are verified against `smartmoney.raw_sw_member`.
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class LayerSpec:
    layer_id: str               # 'energy' | 'chips' | 'infra' | 'models' | 'apps'
    layer_name: str             # 中文名
    layer_en: str               # 英文名
    narrative: str              # 一句话叙事
    sw_l2_codes: list[str]      # 申万二级行业代码（PIT 通过 sw_member_monthly 解析）
    sw_l2_names: list[str] = field(default_factory=list)  # 与 sw_l2_codes 一一对应的中文名（展示用）


AI_LAYERS: list[LayerSpec] = [
    LayerSpec(
        layer_id="energy",
        layer_name="Energy / 电力与算力基建",
        layer_en="Energy · Power · Cooling",
        narrative="AI 算力扩张的物理基础——电网、电池、光伏/风电、温控与数据中心供电（液冷电源以电力设备 L2 为代理）。",
        sw_l2_codes=[
            "801738.SI",   # 电网设备（特高压/UPS）
            "801737.SI",   # 电池（储能）
            "801735.SI",   # 光伏设备
            "801736.SI",   # 风电设备
            "801733.SI",   # 其他电源设备Ⅱ
            "801731.SI",   # 电机Ⅱ
        ],
        sw_l2_names=["电网设备", "电池", "光伏设备", "风电设备", "其他电源设备Ⅱ", "电机Ⅱ"],
    ),
    LayerSpec(
        layer_id="chips",
        layer_name="Chips / 半导体与算力芯片",
        layer_en="Chips · Semiconductors",
        narrative="AI 算力的核心——半导体（GPU/ASIC/存储/设备/材料/封测）、元件、电子化学品。",
        sw_l2_codes=[
            "801081.SI",   # 半导体
            "801083.SI",   # 元件
            "801086.SI",   # 电子化学品Ⅱ
            "801082.SI",   # 其他电子Ⅱ
        ],
        sw_l2_names=["半导体", "元件", "电子化学品Ⅱ", "其他电子Ⅱ"],
    ),
    LayerSpec(
        layer_id="infra",
        layer_name="Infrastructure / 算力基础设施",
        layer_en="Infrastructure · Data Center · Cloud",
        narrative="AI 数据中心的连接层——通信设备（光模块/CPO/PCB 由通信设备 L2 代理）、通信服务、服务器（计算机设备）。"
                   " # TODO V2.2: needs SW L3 or RPS-based theme detection for CPO/液冷服务器/算力租赁等细分主题。",
        sw_l2_codes=[
            "801102.SI",   # 通信设备
            "801223.SI",   # 通信服务
            "801101.SI",   # 计算机设备（服务器/IDC 设备）
        ],
        sw_l2_names=["通信设备", "通信服务", "计算机设备"],
    ),
    LayerSpec(
        layer_id="models",
        layer_name="Models / 模型与软件基础设施",
        layer_en="Models · AI Platforms · Software",
        narrative="AI 能力载体——大模型、操作系统、数据库、网络安全、行业软件、AI 中台（软件开发 + IT 服务 L2 代理）。",
        sw_l2_codes=[
            "801104.SI",   # 软件开发
            "801103.SI",   # IT服务Ⅱ
        ],
        sw_l2_names=["软件开发", "IT服务Ⅱ"],
    ),
    LayerSpec(
        layer_id="apps",
        layer_name="Applications / AI 应用与端侧",
        layer_en="Applications · Agents · Robotics",
        narrative="AI 落地的需求侧——消费电子、光学光电子（AR/VR/显示）、数字媒体/游戏（AIGC 应用）、汽车智能驾驶。",
        sw_l2_codes=[
            "801085.SI",   # 消费电子
            "801084.SI",   # 光学光电子
            "801767.SI",   # 数字媒体
            "801764.SI",   # 游戏Ⅱ
            "801093.SI",   # 汽车零部件（智能驾驶/三电）
            "801095.SI",   # 乘用车（新能源车/智能驾驶整车）
        ],
        sw_l2_names=["消费电子", "光学光电子", "数字媒体", "游戏Ⅱ", "汽车零部件", "乘用车"],
    ),
]


def layer_by_id(layer_id: str) -> LayerSpec | None:
    for l in AI_LAYERS:
        if l.layer_id == layer_id:
            return l
    return None


def all_tech_sector_codes() -> list[str]:
    """Return every SW L2 code referenced by the 5 layers, dedup'd, in order."""
    out: list[str] = []
    for l in AI_LAYERS:
        out.extend(l.sw_l2_codes)
    seen: set[str] = set()
    return [c for c in out if not (c in seen or seen.add(c))]


def sector_to_layer() -> dict[str, str]:
    """SW L2 code -> layer_id reverse map."""
    out: dict[str, str] = {}
    for l in AI_LAYERS:
        for c in l.sw_l2_codes:
            out[c] = l.layer_id
    return out


def sector_name(code: str) -> str:
    """Display name for a SW L2 code (best effort)."""
    for l in AI_LAYERS:
        for c, n in zip(l.sw_l2_codes, l.sw_l2_names):
            if c == code:
                return n
    return code


# ─── Backward-compatibility shims (deprecated) ─────────────────────────────
# The morning/evening orchestrators historically called these names. They
# now resolve to SW-based equivalents so consumers do not break.

def all_tech_board_codes() -> list[str]:  # pragma: no cover - shim
    """Deprecated: use all_tech_sector_codes(). Retained so consumers built
    against the THS naming continue to work after the V2.1 SW migration."""
    return all_tech_sector_codes()


def board_to_layer() -> dict[str, str]:  # pragma: no cover - shim
    """Deprecated: use sector_to_layer()."""
    return sector_to_layer()
