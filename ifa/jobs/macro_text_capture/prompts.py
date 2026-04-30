"""Prompts for macro_text_derived_capture_job.

Adapted from PRD `ifa-macro-v1.txt` §2.1, batched to 5 candidates per call.

Scope after the 2026-04-29 audit (verified via scripts/audit_macro_sources.py):
  - M0/M1/M2 → cn_m (structured)
  - 社融存量/增量 → sf_month (structured)
  - GDP/CPI/PPI/PMI → cn_gdp/cn_cpi/cn_ppi/cn_pmi (structured)
  Therefore this job extracts ONLY the two indicators that have no structured
  source: 新增人民币贷款 and 人民币贷款余额.
"""

PROMPT_VERSION = "macro_text_capture_v0.2"

SYSTEM_PROMPT = (
    "你是一位严谨的中国宏观金融数据抽取专家。"
    "你的任务是从中国财经新闻、政策法规快讯中，"
    "判断每条文本是否明确报告了**新增人民币贷款**或**人民币贷款余额**的具体数值，"
    "并只抽取文本中已经出现的数值，绝不推断、补全或编造。"
    "其他任何宏观/金融指标即使出现也必须忽略。"
    "输出必须是严格的 JSON，符合用户给出的 schema。"
)

INSTRUCTIONS = """对每条候选新闻按下列规则判断：

1. **抽取范围严格只限两个指标**：
   - new_rmb_loans     ：新增人民币贷款（单期或累计的"新增"金额）
   - rmb_loan_balance  ：人民币贷款余额（期末"余额"）

   严禁抽取以下指标，即使它们出现在文中：
   M0、M1、M2、广义货币、社融存量、社融增量、社会融资规模、CPI、PPI、PMI、GDP、外汇储备、外汇占款、利率、汇率、存款余额、住户存款、非金融企业存款、其他存款。
   这些指标已由结构化数据源提供，不进入本任务。

   如果一条候选文本只是提到 M2、社融、CPI 等而没有"新增贷款"或"贷款余额"的具体数值，
   必须返回 has_extractable_data=false，indicators=[]。

2. **区分发布类型** release_type：
   - official_release                       ：官方首次发布（央行/统计局/财政部等）
   - media_report_citing_official_data      ：媒体引用官方数据
   - forecast_or_expectation                ：机构预测或市场预期（不是已发布事实）
   - market_commentary                      ：市场评论/解读
   - unrelated_or_false_positive            ：误判
   - unknown                                ：无法确定

3. **每条数值字段必须保留**：
   - reported_period          ：数据所属期，例如 "2026-03"、"2026Q1"、"2026年1-2月"
   - value（数字字符串）、unit（亿元/万亿元/%）
   - yoy（同比，可为 null）、mom（环比，可为 null）
   - direction_or_comment     ：原文短描述（同比少增/多增/回落/超预期 等），可为 null
   - evidence_sentence        ：原文中提到该数值的中文证据句

4. 即使某条候选 has_extractable_data=false，也必须在 results 数组里返回该候选的对象（保持长度对齐）。

5. confidence：综合判断 high/medium/low。"""

OUTPUT_SCHEMA_HINT = """{
      "candidate_index": 0,
      "has_extractable_data": true,
      "release_type": "official_release | media_report_citing_official_data | forecast_or_expectation | market_commentary | unrelated_or_false_positive | unknown",
      "publisher_or_origin": "中国人民银行 | 国家统计局 | 财政部 | unknown | other",
      "reported_period": "YYYY-MM | YYYY-Qx | YYYY年1-2月 | unknown",
      "indicators": [
        {
          "indicator_name": "new_rmb_loans | rmb_loan_balance",
          "value": "数字字符串，例如 '52271'",
          "unit": "亿元 | 万亿元 | %",
          "yoy": "数字字符串或 null",
          "mom": "数字字符串或 null",
          "direction_or_comment": "原文中文短描述或 null",
          "evidence_sentence": "原文中文证据句"
        }
      ],
      "confidence": "high | medium | low",
      "notes": "可选，简短中文说明"
    }"""
