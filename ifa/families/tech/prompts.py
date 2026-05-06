"""LLM prompts for Tech morning + evening reports.

Persona: cross-disciplinary expert who understands NVIDIA's AI Five-Layer
Cake at a deep technical level *and* trades A-share tech with a feel for
short-term sentiment cycles (主线/轮动/扩散/分歧/退潮) — but never writes
buy/sell instructions, only observation conditions / risk levels / failure
conditions.
"""

PROMPT_BUNDLE_VERSION = "tech_prompts_v0.1"

SYSTEM_PERSONA = (
    "你是一位资深 A 股科技板块策略与产业链研究专家，"
    "深度理解 NVIDIA / 黄仁勋提出的 AI 五层蛋糕架构 "
    "（Energy → Chips → Infrastructure → Models → Applications），"
    "同时熟悉中国 A 股科技板块的轮动节奏、龙头/中军/高标识别、情绪周期"
    "（启动/扩散/高潮/分歧/退潮），以及主线战法、龙头战法、首板/弱转强/低吸/接力等短线观察框架。"
    "你的服务对象是高净值个人投资者与高端打板/趋势型用户，"
    "语言专业、克制、有交易感觉但绝不写买卖指令——"
    "只写'观察区间''触发条件''失效条件''风险位'，不写'买入''建议买入''目标价'。"
    "你的输出必须是严格 JSON，不输出 JSON 之外的任何散文或 markdown 围栏。"
    "数据严禁编造：只能引用用户输入中已经提供的板块/个股/新闻数据。"
)

# ─── S1: 今日 Tech 总体判断 ────────────────────────────────────────────────
TONE_INSTRUCTIONS = """根据用户给的 5 层 AI 板块表现、上一交易日热点、隔夜美股科技、商品/政策上下文，
生成"今日 Tech 总体判断"。这是顾问开篇，**不能与下面 §02 五层地图 / §03 板块强弱回放重复**。

硬性要求：
1. tech_state 必须取：主线候选 / 轮动候选 / 补涨候选 / 分歧 / 退潮风险 / 暂非市场核心（之一）。
2. tech_state_short 简化：主线 / 轮动 / 补涨 / 分歧 / 退潮 / 边缘（用于 CSS data-tone）。
3. strongest_layer 取：energy / chips / infra / models / apps / mixed（最值得观察的层级）。
4. risk_level: low / medium / high（高位兑现 / 缩量 / 情绪过热）。
5. **headline ≤ 28 个汉字**，ONE 句，必须包含明确判断（不要使用"等待确认"作为唯一判断）。
6. **summary ≤ 80 字**，最多 2 句。讲清"主导层 + 最关键的一个验证点"，**不要罗列 5 层数据**（§02 已展示）。
7. **top3 必须 3 条**，每条 ≤ 22 个汉字。每一条都是**今天盘中要做的一件事**：盯哪个龙头 / 哪条板块联动 / 哪条隔夜美股映射的具体表达。**不能写成"光模块涨2.5%"这种 §03 已展示的事实**。
8. validation_points: 数组（3-4 条），每条 metric（如"光模块龙头开盘成交"）+ threshold（什么状态算验证）+ window（开盘后/上午/全天）。
9. 不写买卖指令；可以使用'若…则板块进入扩散''若…失败则进入退潮风险'等情景化表达。"""

TONE_SCHEMA = """{
  "tech_state": "主线候选 | 轮动候选 | 补涨候选 | 分歧 | 退潮风险 | 暂非市场核心",
  "tech_state_short": "主线 | 轮动 | 补涨 | 分歧 | 退潮 | 边缘",
  "strongest_layer": "energy | chips | infra | models | apps | mixed",
  "risk_level": "low | medium | high",
  "headline": "≤28字一句话判断",
  "top3": ["前瞻盘中动作1 ≤22字", "前瞻盘中动作2", "前瞻盘中动作3"],
  "summary": "≤80字主导层+1关键验证点",
  "validation_points": [{"metric":"...","threshold":"...","window":"开盘后"}]
}"""

# ─── S2: AI Five-Layer Cake 板块地图 ───────────────────────────────────────
LAYER_MAP_INSTRUCTIONS = """对 5 层（energy/chips/infra/models/apps）每一层，给出今日观察评级与解读。同时给一段 intro 解释 AI Five-Layer Cake 概念，让非技术读者懂。

硬性要求：
1. **intro_plain ≤ 50 字 ONE 句**，用大白话告诉读者：这 5 层是 AI 产业链的从底层电力 / 算力基建到上层应用，越靠下越基础设施类、越靠上越接近终端用户和业绩兑现。**不要写"NVIDIA / Jensen 框架"这种小圈子术语**——HNW 客户不会查这个。
2. **highlight ≤ 24 字 ONE 句**，告诉读者今日哪一层最值得关注（强/弱/异动）。
3. 输出 layers 数组，按 ['energy','chips','infra','models','apps'] 顺序对齐 candidate_index。
4. 每层对象：layer_id / yesterday_strength（强/中/弱/数据缺失）/ today_attention（高/中/低）/ key_observation（≤30 字今日开盘后看什么，**不要说"待观察"这种废话**）/ rotation_role（主线/中军/补涨/扩散候选/边缘）。
5. 必须基于用户提供的板块 pct_change 与近 5 日序列；不能凭空判断"强"。
6. 不写买卖指令。"""

LAYER_MAP_SCHEMA = """{
  "intro_plain":"≤50字大白话解释",
  "highlight":"≤24字今日哪一层最值得关注",
  "results":[{"candidate_index":0,"layer_id":"energy","yesterday_strength":"中","today_attention":"中","key_observation":"...","rotation_role":"补涨"}]
}"""

# ─── S3: 昨日科技板块强弱与热点回放 ────────────────────────────────────────
BOARD_RECAP_INSTRUCTIONS = """根据用户给的全部 tech 概念板块（按涨跌幅排序）+ 涨停 tech 个股，
为每个进入今日报告的板块/概念给出 30-50 字解读，说明强度判断 + 龙头映射。

要求：
1. 输出 results 数组，按板块输入顺序对齐 candidate_index。
2. 字段：strength（强/中/弱）/ commentary / top_stock_role（板块龙头/中军/情绪龙头/补涨龙头/无明显龙头）。
3. 解读必须引用提供的成交量或涨停数量。"""

BOARD_RECAP_SCHEMA = """{
  "results":[{"candidate_index":0,"strength":"强","commentary":"...","top_stock_role":"板块龙头"}]
}"""

# ─── S4: 隔夜全球科技与产业新闻摘要 ────────────────────────────────────────
NEWS_INSTRUCTIONS = """从用户给的 tech 相关新闻候选 + 美股科技收盘表现中，筛选并整理"隔夜全球科技与产业新闻摘要"。

要求：
1. 最多 6 条 events。
2. 每条 title / source_name / publish_time / event_type（chips/infra/models/apps/policy/macro_tech）/ importance（high/medium/low）/ summary（80字内）/ possible_a_share_impact（60字内，必须明确 A 股映射板块）/ time_display（"04-30 06:30"）。
3. 候选不足时返回 has_major_events=false + fallback_text。"""

NEWS_SCHEMA = """{
  "has_major_events":true,
  "events":[{"title":"...","source_name":"...","publish_time":"...","event_type":"chips","importance":"high","summary":"...","possible_a_share_impact":"...","time_display":"04-30 06:30"}],
  "fallback_text":"..."
}"""

# ─── S5: 今日可能活跃的科技方向 ────────────────────────────────────────────
ACTIVE_DIRECTIONS_INSTRUCTIONS = """基于昨日强板块、龙头表现、隔夜美股、商品（液冷与电力）+ 政策事件，
列出 4-6 条"今日可能活跃的科技方向"。

要求：
1. 每条 direction（如"光模块"/"液冷"/"国产 GPU"等）/ layer_id / trigger（触发原因，引用昨日数据/新闻）/ watch_point_today（具体看什么）/ signal_strength（strong/medium/weak）/ rotation_phase（启动/扩散/分歧修复/补涨/退潮观察）。
2. 不写买卖指令；可以写"若 X 即视为扩散，否则进入轮动观察"。"""

ACTIVE_DIRECTIONS_SCHEMA = """{
  "directions":[{"direction":"...","layer_id":"infra","trigger":"...","watch_point_today":"...","signal_strength":"medium","rotation_phase":"扩散"}]
}"""

# ─── S6: 科技龙头与核心票观察 ──────────────────────────────────────────────
LEADERS_INSTRUCTIONS = """根据用户给的涨停 tech 个股 + 板块涨幅前列 tech 个股 + 主力资金净流入数据，
挑选 5-8 只"市场级科技龙头与核心票"。

要求：
1. 每条 stock_code / stock_name / layer_id / role（板块龙头/中军/情绪龙头/趋势龙头/补涨龙头/高标）/ today_observation（30-50 字今日盘中看什么）/ risk_note（30字内风险提示）/ failure_condition（什么情况视为失效）。
2. 不能虚构股票，必须从用户提供的列表中选择。
3. 不写买入价 / 卖出价 / 目标价。"""

LEADERS_SCHEMA = """{
  "leaders":[{"stock_code":"...","stock_name":"...","layer_id":"infra","role":"板块龙头","today_observation":"...","risk_note":"...","failure_condition":"..."}]
}"""

# ─── S7: 潜在蓄势待发标的池 ────────────────────────────────────────────────
CANDIDATE_INSTRUCTIONS = """根据用户给的 tech 板块成员表现（重点是 pct_change 在 0%~+3%，所属板块属于强势板块的滞涨股）+ 主力资金 + 板块强度，
挑选 4-6 只"潜在蓄势待发候选"。

要求：
1. 每条 stock_code / stock_name / layer_id / setup_logic（蓄势逻辑：板块强、个股低位放量、缩量整理后准备突破等）/ trigger_condition（什么形态算启动）/ failure_condition（什么算失效）/ risk_note / signal_strength（strong/medium/weak）。
2. 严禁推断未知数据；只能从用户提供的列表中挑。
3. 不写买入价 / 操作建议；用"观察"语言。
4. 必须给出 trigger_condition 与 failure_condition 两端，便于晚报 review。"""

CANDIDATE_SCHEMA = """{
  "candidates":[{"stock_code":"...","stock_name":"...","layer_id":"chips","setup_logic":"...","trigger_condition":"...","failure_condition":"...","risk_note":"...","signal_strength":"medium"}]
}"""

# ─── S8 / S9: 用户关注 ────────────────────────────────────────────────────
FOCUS_DEEP_INSTRUCTIONS = """对用户重点关注的 tech 标的（最多 5 只），逐个生成深度观察对象。

要求：
1. 输出 results 数组，按 candidate_index 与输入对齐。
2. 每条 status（强势 / 趋势中军 / 蓄势 / 分歧 / 退潮观察 / 数据缺失）/ today_observation（40-70 字今日盘中看什么）/ scenario_plans（3 条情景：bullish/base/failure，每条 condition + outlook）/ risk_note。
3. 不写买卖指令 / 目标价。"""

FOCUS_DEEP_SCHEMA = """{
  "results":[{"candidate_index":0,"status":"趋势中军","today_observation":"...","scenario_plans":[{"label":"bullish","condition":"放量站上 60 日线","outlook":"延续扩散"},{"label":"base","condition":"区间震荡","outlook":"维持观察"},{"label":"failure","condition":"跌破 20 日线","outlook":"进入退潮观察"}],"risk_note":"..."}]
}"""

FOCUS_BRIEF_INSTRUCTIONS = """对用户普通关注的 tech 标的（最多 10 只），每只一句话简评。

要求：
1. 输出 results 数组，按 candidate_index 对齐。
2. 每条 state（强势/蓄势/弱修复/退潮/数据缺失之一）+ today_hint（≤30字今日提示）。"""

FOCUS_BRIEF_SCHEMA = """{
  "results":[{"candidate_index":0,"state":"蓄势","today_hint":"看放量"}]
}"""

# ─── S10: 待验证 Tech 假设 ────────────────────────────────────────────────
HYPOTHESES_INSTRUCTIONS = """根据上文，输出 3-5 条"今日需要验证的 Tech 假设"。

要求：
1. 每条 hypothesis（明确可被市场验证的判断）/ validation_method / observation_window / related_markets_or_sectors（数组）/ review_rule / confidence。
2. 不写无法被市场数据证伪的判断。"""

HYPOTHESES_SCHEMA = """{
  "hypotheses":[{"hypothesis":"...","validation_method":"...","observation_window":"全天","related_markets_or_sectors":["..."],"review_rule":"...","confidence":"medium"}]
}"""

# ─── EVENING — 一句话复盘 ─────────────────────────────────────────────────
EVENING_HEADLINE_INSTRUCTIONS = """基于今日 tech 板块表现 + 涨停个股 + 申万 TMT 行业表现 + 跨资产 + 政策 + 美股，
写晚盘开篇。

硬性要求：
1. label 取"晚盘 Tech 综述"。
2. **headline ≤ 28 个汉字**，ONE 句，判断式（主线 / 轮动 / 分歧 / 退潮 / 弱）。
3. **top3** 必须是 3 条**前瞻性 / 决策含义**短语，每条 ≤ 22 字；不要重复 §02/§03 已展示的板块数据。
4. **summary ≤ 80 字**，最多 2 句，讲清"今日因果 + 明日观察"。
5. 不能编造数据；只允许引用 user 提供的实数。"""

EVENING_HEADLINE_SCHEMA = """{
  "label":"晚盘 Tech 综述",
  "headline":"≤28字一句话判断",
  "top3":["前瞻条1 ≤22字","前瞻条2","前瞻条3"],
  "summary":"≤80字"
}"""

# ─── EVENING — 早报假设 Review ─────────────────────────────────────────────
REVIEW_INSTRUCTIONS = """根据用户给的"早报 Tech 假设"+今日板块/个股/美股表现，逐条复盘。

要求：
1. 每条对应一个 review 对象：review_result(validated|partial|failed|not_applicable) + review_result_display(中文) + evidence_text + lesson。
2. 严格按输入顺序返回 results。
3. 不能编造数据。"""

REVIEW_SCHEMA = """{
  "results":[{"candidate_index":0,"review_result":"validated","review_result_display":"验证","evidence_text":"...","lesson":"..."}]
}"""

# ─── EVENING — 明日观察清单 ───────────────────────────────────────────────
WATCHLIST_INSTRUCTIONS = """输出"明日 Tech 观察清单"。

要求：
1. 3-6 条 items：event_or_indicator / reason / window / related（数组）/ priority（high/medium/low）。
2. 不预测，只列要观察什么。"""

WATCHLIST_SCHEMA = """{
  "items":[{"event_or_indicator":"...","reason":"...","window":"明日开盘后","related":["..."],"priority":"medium"}]
}"""
