from ifa.families.sme.analysis.market_structure import (
    add_llm_narrative,
    assess_capital_state,
    build_client_brief,
    build_client_conclusion,
    classify_inflow,
    classify_outflow,
    render_client_brief_html,
    render_client_brief_markdown,
)


def test_classify_outflow_detects_high_low_switch():
    typ, reasons = classify_outflow({
        "sector_return_sw_index": 0.5,
        "main_net_ratio": -0.004,
        "main_net_yuan": -1_000_000_000,
        "return_5d": 5.0,
        "current_state": "distribution",
        "diffusion_phase": "leader_only",
    })
    assert typ == "high_low_switch"
    assert reasons


def test_classify_outflow_detects_panic_sell():
    typ, reasons = classify_outflow({
        "sector_return_sw_index": -2.5,
        "main_net_ratio": -0.03,
        "main_net_yuan": -2_000_000_000,
        "return_5d": -1.0,
        "current_state": "cooldown",
        "diffusion_phase": "diffusion_breakdown",
    })
    assert typ == "panic_sell"
    assert "扩散破坏" in reasons[0]


def test_classify_inflow_detects_absorption_before_defensive_keyword():
    typ, reasons = classify_inflow({
        "l2_name": "银行",
        "sector_return_sw_index": -0.2,
        "main_net_ratio": 0.012,
        "top5_main_net_share": 0.3,
        "flow_breadth_5d": 0.4,
        "current_state": "dormant",
    })
    assert typ == "institutional_absorption"
    assert reasons


def test_classify_inflow_respects_continuous_yaml_threshold_override():
    row = {
        "l2_name": "半导体",
        "sector_return_sw_index": 1.2,
        "main_net_ratio": 0.006,
        "top5_main_net_share": 0.4,
        "flow_breadth_5d": 0.4,
        "current_state": "dormant",
        "inst_net_buy_yuan": 0,
    }
    params = {
        "inflow": {
            "chase_return_min": 1.0,
            "chase_main_net_ratio_min": 0.005,
            "chase_top5_share_min": 0.35,
            "long_config_flow_breadth_5d_min": 0.55,
        }
    }
    typ, _ = classify_inflow(row, params)
    assert typ == "chase_high"


def test_assess_capital_state_detects_defensive_switch():
    state = assess_capital_state(
        breadth={"up_count": 1000, "down_count": 3000},
        inflows=[
            {"inflow_type": "defensive"},
            {"inflow_type": "institutional_absorption"},
            {"inflow_type": "tactical_inflow"},
        ],
        outflows=[{"outflow_type": "active_de_risk"}] * 4,
        strong_return_weak_flow=[],
        suppressed_repair=[],
    )
    assert "risk_appetite_down" in state["state_tags"]
    assert "defensive_switch" in state["state_tags"]


def test_client_conclusion_hides_process_and_keeps_decisions():
    snapshot = {
        "status": "ok",
        "trade_date": "2026-05-06",
        "market_overview": {"interpretation": "放量普涨。"},
        "capital_state": {"state_tags": ["risk_appetite_up", "high_low_switch"]},
        "beneficiary_buckets": {
            "primary_beneficiaries": [{"l2_name": "半导体"}],
            "secondary_beneficiaries": [{"l2_name": "电池"}],
            "desensitized_assets": [{"l2_name": "银行"}],
            "suppressed_repair_candidates": [{"l2_name": "酒店餐饮"}],
        },
        "flow_inflows": [{"l2_name": "半导体"}],
        "flow_outflows": [{"l2_name": "白酒Ⅱ"}],
        "strong_return_weak_flow": [{"l2_name": "游戏Ⅱ"}],
        "external_variables": {"summary": None},
    }
    conclusion = build_client_conclusion(snapshot)
    assert conclusion["focus_now"] == ["半导体"]
    assert conclusion["avoid_or_reduce"] == ["白酒Ⅱ"]
    assert "evidence" not in conclusion
    assert "reasons" not in conclusion


def test_client_brief_is_conclusion_only_markdown():
    snapshot = {
        "status": "ok",
        "trade_date": "2026-05-06",
        "market_overview": {
            "interpretation": "放量普涨。",
            "breadth": {"up_count": 3000, "down_count": 1200, "amount_bn_yuan": 12000},
        },
        "capital_state": {"state_tags": ["risk_appetite_up"]},
        "beneficiary_buckets": {
            "primary_beneficiaries": [{"l2_name": "半导体"}],
            "secondary_beneficiaries": [{"l2_name": "电池"}],
            "desensitized_assets": [{"l2_name": "银行"}],
            "suppressed_repair_candidates": [{"l2_name": "酒店餐饮"}],
        },
        "flow_inflows": [{"l2_name": "半导体", "inflow_type": "event_trade"}],
        "flow_outflows": [{"l2_name": "白酒Ⅱ", "outflow_type": "active_de_risk"}],
        "strong_return_weak_flow": [{"l2_name": "游戏Ⅱ"}],
        "suppressed_repair": [{"l2_name": "酒店餐饮"}],
        "external_variables": {"summary": "美元走强。"},
    }
    brief = build_client_brief(snapshot)
    md = render_client_brief_markdown(brief)
    assert "一句话结论" in md
    assert "一级受益方向" in md
    assert "阈值" not in md
    assert "模型" not in md


def test_client_brief_html_is_standalone_and_conclusion_only():
    snapshot = {
        "status": "ok",
        "trade_date": "2026-05-06",
        "market_overview": {
            "interpretation": "放量普涨。",
            "breadth": {"up_count": 3000, "down_count": 1200, "amount_bn_yuan": 12000},
        },
        "capital_state": {"state_tags": ["risk_appetite_up"]},
        "beneficiary_buckets": {
            "primary_beneficiaries": [{"l2_name": "半导体"}],
            "secondary_beneficiaries": [{"l2_name": "电池"}],
            "desensitized_assets": [{"l2_name": "银行"}],
            "suppressed_repair_candidates": [{"l2_name": "酒店餐饮"}],
        },
        "flow_inflows": [{"l2_name": "半导体", "inflow_type": "event_trade"}],
        "flow_outflows": [{"l2_name": "白酒Ⅱ", "outflow_type": "active_de_risk"}],
        "strong_return_weak_flow": [{"l2_name": "游戏Ⅱ"}],
        "suppressed_repair": [{"l2_name": "酒店餐饮"}],
        "external_variables": {"summary": "美元走强。"},
    }
    html = render_client_brief_html(build_client_brief(snapshot))
    assert "<title>2026年5月6日资金结构简报" in html
    assert "一级受益方向" in html
    assert "{% include" not in html
    assert "ifa/core/render/templates" not in html
    assert "阈值" not in html
    assert "完整免责声明" in html
    assert "Lindenwood Management LLC" in html


def test_llm_narrative_is_optional_and_fact_locked():
    class FakeResponse:
        model = "fake"
        endpoint = "unit"

        def parse_json(self):
            return {"narrative": "今天资金偏进攻，但需要避开拥挤方向。"}

    class FakeClient:
        def chat(self, **kwargs):
            assert kwargs["response_format"] == {"type": "json_object"}
            return FakeResponse()

    conclusion = {
        "date": "2026-05-06",
        "title": "资金结构结论",
        "bottom_line": "今天资金偏进攻。",
        "focus_now": ["半导体"],
        "avoid_or_reduce": ["白酒Ⅱ"],
    }
    enriched = add_llm_narrative(conclusion, llm_client=FakeClient())
    assert enriched["focus_now"] == ["半导体"]
    assert enriched["llm_narrative"]["text"].startswith("今天资金偏进攻")
