"""Tech report universe — Jensen Huang's AI Five-Layer Cake mapping.

Layer model:
  L1 Energy / Power / Physical Base   电力、储能、液冷、温控、特高压、数据中心供电
  L2 Chips / Semiconductors           AI 芯片、存储、设备、材料、封测、国产替代
  L3 Infrastructure / Data Center     光模块、CPO、PCB、服务器、IDC、液冷服务器
  L4 Models / AI Platforms            大模型、数据要素、AI 中台、网络安全、操作系统
  L5 Applications / Agents / Robotics AI 应用、智能体、机器人、智能驾驶、端侧 AI

Each layer maps to a curated list of 同花顺概念板块 (THS concepts) — selected
from the live audit on 2026-04-30 (`pro.ths_index()` matched against keyword
patterns). We prefer 885xxx / 886xxx concept boards over 700xxx 行业分类
because concept boards are tighter to AI thematics.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class LayerSpec:
    layer_id: str               # 'energy' | 'chips' | 'infra' | 'models' | 'apps'
    layer_name: str             # 中文名
    layer_en: str               # 英文名
    narrative: str              # 一句话叙事
    ths_board_codes: list[str]  # THS 概念板块代码（ts_code），按相关性排序
    sw_l2_codes: list[str]      # 申万二级行业代码（作为补充与回退）


AI_LAYERS: list[LayerSpec] = [
    LayerSpec(
        layer_id="energy",
        layer_name="Energy / 电力与算力基建",
        layer_en="Energy · Power · Cooling",
        narrative="AI 算力扩张的物理基础——电力、电网、储能、液冷、UPS、数据中心供电与温控。",
        ths_board_codes=[
            "871063.TI",   # 电力
            "884089.TI",   # 线缆部件及其他
            "884263.TI",   # 通信线缆及配套
            "861289.TI",   # 数据中心REIT
            # 储能 / 液冷 / 充电桩（运行时如果 ths_index 找到匹配名称会被动态补充）
        ],
        sw_l2_codes=["801160.SI"],  # 公用事业（含电力）
    ),
    LayerSpec(
        layer_id="chips",
        layer_name="Chips / 半导体与算力芯片",
        layer_en="Chips · Semiconductors",
        narrative="AI 算力的核心——GPU/ASIC/CPU/存储芯片、半导体设备、材料、EDA、封测、国产替代。",
        ths_board_codes=[
            "881121.TI",   # 半导体
            "884229.TI",   # 半导体设备
            "884091.TI",   # 半导体材料
            "884228.TI",   # 集成电路封测
            "861100.TI",   # 半导体产品与设备Ⅱ
        ],
        sw_l2_codes=["801080.SI"],  # 电子（含半导体）
    ),
    LayerSpec(
        layer_id="infra",
        layer_name="Infrastructure / 算力基础设施",
        layer_en="Infrastructure · Data Center · Cloud",
        narrative="AI 数据中心的连接层——光模块、CPO、PCB、服务器、液冷、IDC、算力租赁。",
        ths_board_codes=[
            "885957.TI",   # 东数西算 (算力)
            "886033.TI",   # 共封装光学 CPO
            "886044.TI",   # 液冷服务器
            "886050.TI",   # 算力租赁
            "885959.TI",   # PCB 概念
            "885362.TI",   # 云计算
            "884262.TI",   # 通信网络设备及器件
        ],
        sw_l2_codes=["801770.SI"],  # 通信
    ),
    LayerSpec(
        layer_id="models",
        layer_name="Models / 模型与软件基础设施",
        layer_en="Models · AI Platforms · Software",
        narrative="AI 能力载体——大模型、数据要素、AI 中台、操作系统、数据库、网络安全。",
        ths_board_codes=[
            "886041.TI",   # 数据要素
            "885844.TI",   # 国产操作系统
            "885459.TI",   # 网络安全
        ],
        sw_l2_codes=["801750.SI"],  # 计算机
    ),
    LayerSpec(
        layer_id="apps",
        layer_name="Applications / AI 应用与端侧",
        layer_en="Applications · Agents · Robotics",
        narrative="AI 落地的需求侧——AI 应用、智能体、机器人、智能驾驶、端侧 AI、消费电子。",
        ths_board_codes=[
            "886108.TI",   # AI 应用
            "886099.TI",   # AI 智能体
            "886069.TI",   # 人形机器人
            "885517.TI",   # 机器人概念
            "881124.TI",   # 消费电子
            "884098.TI",   # 消费电子零部件及组装
            "884218.TI",   # 机器人
        ],
        sw_l2_codes=["801760.SI"],  # 传媒
    ),
]


def layer_by_id(layer_id: str) -> LayerSpec | None:
    for l in AI_LAYERS:
        if l.layer_id == layer_id:
            return l
    return None


def all_tech_board_codes() -> list[str]:
    out: list[str] = []
    for l in AI_LAYERS:
        out.extend(l.ths_board_codes)
    # dedupe while preserving order
    seen: set[str] = set()
    return [c for c in out if not (c in seen or seen.add(c))]


def board_to_layer() -> dict[str, str]:
    out: dict[str, str] = {}
    for l in AI_LAYERS:
        for c in l.ths_board_codes:
            out[c] = l.layer_id
    return out
