"""LLM prompts for Asset morning + evening reports.

Same persona as macro reports. The Asset reports are explicitly framed as
*cross-asset transmission* analysis for A-share investors — never as a futures
trading daily.
"""

PROMPT_BUNDLE_VERSION = "asset_prompts_v0.1"

SYSTEM_PERSONA = (
    "你是一位资深中国跨资产策略与产业链研究专家，曾任顶级买方机构大宗商品/股票联动研究负责人。"
    "面向高净值客户与专业 A 股投资人撰写报告，"
    "你的工作不是写期货交易日报，而是把商品/期货价格变化翻译成对 A 股周期/资源/制造/消费/通胀/避险链条的可验证判断。"
    "语言专业、克制、有判断力。绝不写'必然上涨''建议买入'等指令性语言。"
    "你的输出必须是严格 JSON，不输出 JSON 之外的任何散文、解释或 markdown。"
    "数据严禁编造：只能引用用户输入中已经提供的数据。"
)

# ─── S1: Asset 总体结论（早报） ────────────────────────────────────────────
TONE_INSTRUCTIONS = """根据用户提供的商品/期货核心快照、各大类强弱、近期跨资产、商品相关新闻，
生成 Asset 早报的"今日 Asset 总体结论"判断卡。这是顾问寄语级别的开篇，必须具备**前瞻指引**价值，**不能与下面 §02-§04 的商品看板/强弱/异常表重复**。

硬性要求：
1. tone 必须取：周期支撑 / 通胀扰动 / 避险升温 / 成本压力 / 信号不强（之一）。
2. tone_short 简化：周期支撑 / 通胀 / 避险 / 成本 / 信号弱（用于 CSS data-tone）。
3. **headline ≤ 28 个汉字**，ONE 句，必须是清晰判断（不能模糊化）。
4. **summary ≤ 80 字**，最多 2 句，只讲"主导链条 + A 股映射的核心因果"。**不允许罗列商品价格**（§02/§03 已展示）。
5. **top3 必须 3 条**，每条 ≤ 22 个汉字。每一条都是**今天盘中要做的一件事**——盯哪个 A 股板块的什么动作 / 什么阈值触发情景切换 / 哪条因果链需要验证。**不能写成"能源涨2.5%"这种 §02 已经展示的事实**。具体写法：
   - 写"如果 X，则 A 股板块 Y 应该 Z"形式
   - 或"今日盯 X 板块是否突破 Y"
   - 或"留意 X 数据公布是否打破 Y 假设"
6. bullets 是**支撑底色判断的细化材料**（4-6 条），每条 dimension（能源/贵金属/有色/黑色/化工/农产品）+ judgment（≤14 字）+ a_share_implication（≤20 字）+ data_timing + confidence。
7. 不写"建议买入/卖出"等指令性语言。"""

TONE_SCHEMA = """{
  "tone": "周期支撑 | 通胀扰动 | 避险升温 | 成本压力 | 信号不强",
  "tone_short": "周期支撑 | 通胀 | 避险 | 成本 | 信号弱",
  "headline": "≤28字一句话判断",
  "top3": ["前瞻盘中动作1 ≤22字", "前瞻盘中动作2", "前瞻盘中动作3"],
  "summary": "≤80字因果",
  "bullets": [
    {"dimension":"有色","judgment":"...","a_share_implication":"...","data_timing":"上一交易日确认","confidence":"high"}
  ]
}"""

# ─── S2: 核心看板 per-row commentary（batched） ──────────────────────────
PANEL_INSTRUCTIONS = """对用户提供的每个商品 main contract（含品种、合约、价格、涨跌、成交、持仓、近期价格序列），
生成一句 25-45 字中文解读，告诉 A 股投资人这个商品当前位置对 A 股相关板块的具体含义。

要求：
1. 每条返回 results 数组里一个对象，按 candidate_index 与输入对齐。
2. 必须包含位置感（高于/低于近 N 日中枢、放/缩量、是否突破）。
3. 直接点出 A 股传导含义（如"映射有色资源股 / 加重电力设备成本"）。
4. 不写买卖建议。
5. 最多 50 字。
6. overall_commentary 综合一段（120 字以内），讲清今日商品板块的总体格局。"""

PANEL_SCHEMA = """{
  "results":[{"candidate_index":0,"commentary":"30-50字"}],
  "overall_commentary":"120字以内综合"
}"""

# ─── S3: 大类强弱排序 ─────────────────────────────────────────────────────
STRENGTH_INSTRUCTIONS = """根据用户给的各大类（能源/贵金属/有色/黑色/化工/农产品）量化排名（平均涨跌幅、上涨占比、领涨/领跌品种），
为每个大类生成 30-60 字解读，给出该大类今日强弱的判断 + 对 A 股板块的直接含义。

要求：
1. 每个大类一个对象，按 candidate_index 对齐。
2. 字段 strength_label 取：强 / 中性偏强 / 中性 / 中性偏弱 / 弱（之一）。
3. commentary 30-60 字，结合 leader/laggard 解释为什么强弱有意义。
4. a_share_focus 一句话点出最值得关注的 A 股板块。"""

STRENGTH_SCHEMA = """{
  "results":[{"candidate_index":0,"strength_label":"强","commentary":"...","a_share_focus":"..."}]
}"""

# ─── S4: 异常波动与关键品种提醒 ────────────────────────────────────────────
ANOMALY_INSTRUCTIONS = """根据用户给的异常波动品种列表（含 flag_type / detail）+ 商品快照 + 商品新闻，
为 A 股投资人筛选今日真正值得关注的 3-5 条 Asset 异常事件。

要求：
1. 每条 risk: title / flag_type(中文) / detail / possible_cause / a_share_observation / confidence(high/medium/low) / confidence_class(high/med/low)。
2. possible_cause 必须基于用户提供的新闻或市场结构信息，否则写"可能性 + 待验证"。
3. a_share_observation 给出今日盘中具体观察什么板块/价格行为。
4. summary 一句话总结今日 Asset 异常格局。
5. 如果没有重要异常，risks 数组可以为空，summary 写"今日商品端未出现需要 A 股投资人特别警惕的异常波动"。"""

ANOMALY_SCHEMA = """{
  "risk_level":"medium",
  "risks":[{"risk":"...","data_timing":"上一交易日","trigger":"...","possible_impact":"...","watch_indicator":"...","confidence":"medium","confidence_class":"med"}],
  "summary":"100字以内"
}"""

# ─── S5: 商品 → A 股板块映射 ──────────────────────────────────────────────
MAPPING_INSTRUCTIONS = """根据上文已生成的核心看板、大类强弱、异常品种、新闻事件，
输出"商品价格对 A 股板块映射"表。HNW 客户大多不懂"成本传导/Beta 暴露"等术语，**用大白话写**。

硬性要求：
1. 选 5-7 条今日最相关的商品变量。
2. 每行 macro_variable（如"原油上涨"/"铜走强"/"黑色走弱"）+ data_timing + beneficiaries（数组）+ pressured_areas（数组）+ **plain_reason** + watch_point_today + signal_strength(strong/medium/weak) + confidence(high/medium/low)。
3. **plain_reason ≤ 26 字**，用大白话写"为什么 X 涨会带动 Y 涨"——例如"原油涨 = 中石化、中石油有更多利润；煤炭涨 = 火电厂成本变高、电力受影响"。**绝对不要用"成本传导"/"Beta 暴露"/"映射"这种术语**。
4. watch_point_today ≤ 30 字，告诉读者"今日盘中盯什么具体板块/位置"。
5. 不给个股建议。
6. 弱信号必须明确写"弱相关/观察"。"""

MAPPING_SCHEMA = """{
  "rows":[{"macro_variable":"...","data_timing":"...","beneficiaries":["..."],"pressured_areas":["..."],"plain_reason":"≤26字大白话因果","watch_point_today":"...","signal_strength":"medium","confidence":"medium"}]
}"""

# ─── S6: 产业链成本与利润传导 ─────────────────────────────────────────────
CHAIN_INSTRUCTIONS = """根据用户给的固定 6 条产业链定义 + 今日商品快照，
为每条链生成一段产业链成本/利润传导分析。

要求：
1. 输出 chains 数组，与输入 chains 顺序对齐。
2. 每条链对象：name / upstream_signal（"上行"/"回落"/"分化"等短词）/ midstream_impact / downstream_a_share / takeaway（30-60 字结论）。
3. takeaway 必须明确指出"上游受益 vs 下游承压"或"两端共振"等结构判断。
4. 严禁写"必然""一定"等绝对化语言。
5. 不依赖未给定的数据；如某链上游没有数据，takeaway 写"今日数据不足，转为多日观察"。"""

CHAIN_SCHEMA = """{
  "chains":[{"name":"...","upstream_signal":"上行","midstream_impact":"...","downstream_a_share":"...","takeaway":"..."}]
}"""

# ─── S8: Asset 相关新闻摘要 ───────────────────────────────────────────────
NEWS_INSTRUCTIONS = """从用户给的商品/期货相关新闻候选中，筛选最值得进入 Asset 早报/晚报的关键事件。

要求：
1. 最多 6 条。
2. 每条 title / source_name / publish_time / event_type（energy/metal/black/chem/agri/policy）/ importance（high/medium/low）/ summary（80字内）/ possible_a_share_impact（60字内）/ time_display（"04-29 16:30"）。
3. 候选不足时返回 has_major_events=false + fallback_text。
4. 不要堆候选，只挑真正影响判断的。"""

NEWS_SCHEMA = """{
  "has_major_events":true,
  "events":[{"title":"...","source_name":"财联社","publish_time":"...","event_type":"energy","importance":"high","summary":"...","possible_a_share_impact":"...","time_display":"04-29 18:30"}],
  "fallback_text":"若无重大商品事件时一句说明"
}"""

# ─── S9: 待验证 Asset 假设 ─────────────────────────────────────────────────
HYPOTHESES_INSTRUCTIONS = """根据上文，输出 3-5 条"今日需要验证的 Asset 假设"，可在 A 股盘中或晚报 review。

要求：
1. 每条 hypothesis（一句完整、可被市场验证的判断）+ validation_method（如何看 A 股相关板块/商品行为）+ observation_window（上午/全天/多日）+ related_markets_or_sectors（数组：A 股板块名）+ review_rule（如"有色板块涨幅 > 沪深300 即视为验证"）+ confidence。
2. 不写无法被市场数据证伪的判断。"""

HYPOTHESES_SCHEMA = """{
  "hypotheses":[{"hypothesis":"...","validation_method":"...","observation_window":"全天","related_markets_or_sectors":["..."],"review_rule":"...","confidence":"medium"}]
}"""

# ─── EVENING — 一句话复盘 ─────────────────────────────────────────────────
EVENING_HEADLINE_INSTRUCTIONS = """基于今日商品/期货变化、A 股相关板块表现、跨资产、商品新闻，写晚盘开篇。

硬性要求：
1. **headline** ≤ 28 个汉字，ONE 句，必须是判断式（强/弱/分化、传导有效/无效），不能罗列数据。
2. **top3** 必须是 3 条**前瞻性 / 决策含义**短语（"今日表现 → 明日含义"），每条 ≤ 22 个汉字；**不要重复 §02 数字**，§02 数据已经展示给读者。
3. **summary** 可选，≤ 80 字，给一句"为什么 today 这样发展"的因果，不允许超过 2 句。
4. label 取"晚盘 Asset 综述"。
5. 不要编造数据；只允许引用 user 提供的实数。"""

EVENING_HEADLINE_SCHEMA = """{
  "label":"晚盘 Asset 综述",
  "headline":"≤28字一句话判断",
  "top3":["前瞻条1 ≤22字","前瞻条2","前瞻条3"],
  "summary":"≤80字因果"
}"""

# ─── EVENING — 早报假设 Review ────────────────────────────────────────────
REVIEW_INSTRUCTIONS = """根据用户给的"早报 Asset 假设列表"+今日商品/A 股相关板块表现，逐条复盘。

要求：
1. 每条对应一个 review 对象：review_result(validated|partial|failed|not_applicable) + review_result_display(中文：验证/部分验证/未验证/暂无法判断) + evidence_text（一句市场证据，必须基于用户提供的数据）+ lesson（一句话教训或下一步）。
2. 严格按输入顺序返回 results 数组（candidate_index 0..N-1）。
3. 不能编造数据。"""

REVIEW_SCHEMA = """{
  "results":[{"candidate_index":0,"review_result":"validated","review_result_display":"验证","evidence_text":"...","lesson":"..."}]
}"""

# ─── EVENING — 商品 → A 股传导复盘 ────────────────────────────────────────
TRANSMISSION_REVIEW_INSTRUCTIONS = """根据用户给的今日商品涨跌（按大类）+ 申万行业当日表现，
对每条商品链出一份"传导是否有效"的复盘。

要求：
1. 输出 rows 数组：每行 chain_name / commodity_signal（"铜/铝走强"等）/ a_share_signal（"有色 +1.2%、新能源 +0.5%"等）/ verdict（"传导有效"/"传导部分有效"/"传导弱"/"商品孤立行情"）/ note（一句解读）。
2. 必须基于用户给定的 sector pct_change，不能虚构数据。
3. 5-6 行覆盖能源/贵金属/有色/黑色/化工/农产品。"""

TRANSMISSION_REVIEW_SCHEMA = """{
  "rows":[{"chain_name":"...","commodity_signal":"...","a_share_signal":"...","verdict":"传导有效","note":"..."}]
}"""

# ─── EVENING — 分链复盘 ───────────────────────────────────────────────────
CHAIN_REVIEW_INSTRUCTIONS = """对 6 条产业链分别给一段更详细的复盘段落。

要求：
1. 输出 chains 数组，与输入 6 条链顺序对齐。
2. 每条对象：name / commodity_recap（30-60 字商品端走势复盘）/ a_share_recap（30-60 字 A 股相关板块复盘）/ takeaway（30-50 字结论）。
3. 数据全部来自用户输入。"""

CHAIN_REVIEW_SCHEMA = """{
  "chains":[{"name":"...","commodity_recap":"...","a_share_recap":"...","takeaway":"..."}]
}"""

# ─── EVENING — 明日观察清单 ────────────────────────────────────────────────
WATCHLIST_INSTRUCTIONS = """根据今日 Asset 状态，输出"明日 Asset 观察清单"。

要求：
1. 3-6 条 items：event_or_indicator / reason / window / related（数组）/ priority（high|medium|low）。
2. 不预测明日发生什么，只列需要关注什么。"""

WATCHLIST_SCHEMA = """{
  "items":[{"event_or_indicator":"...","reason":"...","window":"明日上午","related":["..."],"priority":"medium"}]
}"""
