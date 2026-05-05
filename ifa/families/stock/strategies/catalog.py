"""Stock Edge strategy catalog.

The catalog is the source of truth for what the current V2.2 strategy matrix
can consume. It deliberately separates implemented signals from future research
ideas so handover does not confuse roadmap ambition with production behavior.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Literal

StrategyCategory = Literal["rule", "statistical", "ta", "smartmoney", "ml", "dl", "llm", "execution"]


@dataclass(frozen=True)
class StrategyCatalogItem:
    key: str
    label: str
    category: StrategyCategory
    implemented: bool
    data_sources: tuple[str, ...]
    prediction_role: str
    training_scope: str

    def to_dict(self) -> dict:
        return asdict(self)


IMPLEMENTED_STRATEGIES: tuple[StrategyCatalogItem, ...] = (
    StrategyCatalogItem("trend_following", "趋势延续", "rule", True, ("smartmoney.raw_daily",), "入场方向/追涨约束", "single-stock + global"),
    StrategyCatalogItem("support_pullback", "支撑回踩", "rule", True, ("smartmoney.raw_daily", "support_resistance"), "今日/未来5日回踩买点", "single-stock"),
    StrategyCatalogItem("breakout_pressure", "压力突破", "rule", True, ("support_resistance",), "突破回踩买点", "single-stock"),
    StrategyCatalogItem("momentum_5d", "5日动量", "statistical", True, ("smartmoney.raw_daily",), "短线惯性/过热判断", "single-stock + global"),
    StrategyCatalogItem("volume_confirmation", "量能确认", "statistical", True, ("smartmoney.raw_daily",), "入场确认", "single-stock"),
    StrategyCatalogItem("volatility_structure", "波动结构", "statistical", True, ("smartmoney.raw_daily",), "止损宽度/仓位风险", "single-stock"),
    StrategyCatalogItem("liquidity_slippage", "流动性滑点风险", "statistical", True, ("smartmoney.raw_daily", "smartmoney.raw_daily_basic"), "容量/滑点/可执行性风险", "single-stock + global"),
    StrategyCatalogItem("range_position", "区间位置", "statistical", True, ("smartmoney.raw_daily",), "强势但未极端的买点质量", "single-stock"),
    StrategyCatalogItem("volatility_contraction", "波动收敛", "statistical", True, ("smartmoney.raw_daily",), "蓄势/突破前结构", "single-stock"),
    StrategyCatalogItem("drawdown_recovery", "回撤修复", "statistical", True, ("smartmoney.raw_daily",), "反转修复质量", "single-stock"),
    StrategyCatalogItem("gap_risk", "跳空风险", "statistical", True, ("smartmoney.raw_daily",), "执行质量/不追高约束", "single-stock"),
    StrategyCatalogItem("gap_risk_open_model", "开盘跳空风险模型", "ml", True, ("smartmoney.raw_daily", "sklearn.RandomForestClassifier"), "次日开盘不利跳空概率与执行 veto", "single-stock overlay"),
    StrategyCatalogItem("auction_imbalance_proxy", "集合竞价失衡代理", "execution", True, ("smartmoney.raw_daily",), "开盘跳空和承接质量对当日执行的修正", "event-driven"),
    StrategyCatalogItem("trend_quality_r2", "趋势质量R2", "statistical", True, ("smartmoney.raw_daily",), "趋势斜率与趋势纯度", "single-stock"),
    StrategyCatalogItem("candle_reversal_structure", "K线反转结构", "rule", True, ("smartmoney.raw_daily",), "下影反转/上影派发对买点的修正", "single-stock"),
    StrategyCatalogItem("volume_price_divergence", "量价背离", "statistical", True, ("smartmoney.raw_daily",), "上涨缩量/下跌放量的执行风险", "single-stock"),
    StrategyCatalogItem("moneyflow_7d", "7日主力净流", "smartmoney", True, ("smartmoney.raw_moneyflow",), "资金确认", "single-stock + sector"),
    StrategyCatalogItem("orderflow_mix", "大单结构", "smartmoney", True, ("smartmoney.raw_moneyflow",), "机构参与代理", "single-stock + sector"),
    StrategyCatalogItem("northbound_regime", "北向资金体制", "smartmoney", True, ("smartmoney.raw_moneyflow_hsgt",), "外资顺逆风与市场风险偏好", "global prior"),
    StrategyCatalogItem("market_margin_impulse", "两融杠杆脉冲", "smartmoney", True, ("smartmoney.raw_margin",), "融资余额扩张/收缩对风险预算的约束", "global prior"),
    StrategyCatalogItem("block_trade_pressure", "大宗交易压力", "smartmoney", True, ("smartmoney.raw_block_trade", "smartmoney.raw_daily"), "折溢价大宗交易对承接/派发的事件修正", "event-driven"),
    StrategyCatalogItem("lhb_institution_hotmoney_divergence", "龙虎榜机构游资分歧", "smartmoney", True, ("smartmoney.raw_top_list", "smartmoney.raw_top_inst"), "机构/游资事件资金是否共振", "event-driven"),
    StrategyCatalogItem("flow_persistence_decay", "资金持续性衰减", "smartmoney", True, ("smartmoney.raw_moneyflow",), "主力净流是否持续、转弱或反转", "single-stock + global"),
    StrategyCatalogItem("limit_up_microstructure", "涨停微结构", "rule", True, ("smartmoney.raw_kpl_list", "smartmoney.raw_limit_list_d"), "封单/炸板/连板结构对买点的约束", "event-driven"),
    StrategyCatalogItem("limit_up_event_path_model", "涨停事件路径模型", "statistical", True, ("smartmoney.raw_kpl_list", "smartmoney.raw_limit_list_d"), "涨停/炸板/连板后的延续或衰减路径", "event-driven"),
    StrategyCatalogItem("smartmoney_sw_l2", "SW L2 板块资金", "smartmoney", True, ("smartmoney.sector_moneyflow_sw_daily", "smartmoney.sector_state_daily", "smartmoney.factor_daily"), "板块顺逆风", "global prior"),
    StrategyCatalogItem("sector_diffusion_breadth", "SW L2 扩散宽度", "smartmoney", True, ("smartmoney.sector_moneyflow_sw_daily", "smartmoney.factor_daily", "smartmoney.sw_member_monthly"), "板块资金是否扩散、是否拥挤", "sector"),
    StrategyCatalogItem("same_sector_leadership", "同板块龙头位置", "smartmoney", True, ("smartmoney.sw_member_monthly", "smartmoney.raw_daily_basic", "ta.candidates_daily"), "同行位置/基本面触发", "sector"),
    StrategyCatalogItem("peer_relative_momentum", "同行相对强弱", "statistical", True, ("smartmoney.raw_daily", "smartmoney.sw_member_monthly"), "同行5/10/15日相对强弱", "sector"),
    StrategyCatalogItem("peer_leader_fundamental_spread", "同行财报质量价差", "statistical", True, ("research.memory", "smartmoney.raw_daily_basic"), "同行财报质量/估值为主，市值动量为辅", "sector"),
    StrategyCatalogItem("peer_financial_alpha_model", "同行财务Alpha模型", "statistical", True, ("research.memory", "smartmoney.sw_member_monthly"), "同行财务质量、估值与价格滞后的 alpha", "sector + single-stock"),
    StrategyCatalogItem("hierarchical_sector_shrinkage", "行业层级收缩", "statistical", True, ("smartmoney.factor_daily", "smartmoney.sw_member_monthly"), "个股样本不足时向SW L2/同行先验收缩", "global + per-stock"),
    StrategyCatalogItem("daily_basic_style", "交易质量/估值风格", "statistical", True, ("smartmoney.raw_daily_basic",), "流动性/估值拥挤度", "single-stock"),
    StrategyCatalogItem("historical_replay_edge", "单股历史相似形态Replay", "statistical", True, ("smartmoney.raw_daily",), "相似历史片段后的目标命中/止损统计", "single-stock overlay"),
    StrategyCatalogItem("target_stop_replay", "目标/止损路径Replay", "statistical", True, ("smartmoney.raw_daily",), "目标价或止损价谁先触发与用时", "single-stock overlay"),
    StrategyCatalogItem("entry_fill_replay", "入场成交Replay", "statistical", True, ("smartmoney.raw_daily",), "未来5日买入区间成交与先破位概率", "single-stock overlay"),
    StrategyCatalogItem("entry_fill_classifier", "入场成交概率分类器", "ml", True, ("smartmoney.raw_daily", "stock.replay_labels"), "未来5日能否成交和成交质量", "single-stock overlay"),
    StrategyCatalogItem("quantile_return_forecaster", "20/40日收益分位预测", "ml", True, ("smartmoney.raw_daily",), "p10/p50/p90收益分布", "single-stock overlay"),
    StrategyCatalogItem("conformal_return_band", "保序置信收益带", "statistical", True, ("smartmoney.raw_daily",), "收益预测区间与不确定性", "single-stock overlay"),
    StrategyCatalogItem("stop_first_classifier", "先止损概率", "ml", True, ("smartmoney.raw_daily",), "风险优先级", "single-stock overlay"),
    StrategyCatalogItem("isotonic_score_calibrator", "单调概率校准", "statistical", True, ("smartmoney.raw_daily", "stock.replay_labels"), "矩阵分数到真实概率的校准", "single-stock overlay"),
    StrategyCatalogItem("right_tail_meta_gbm", "右尾收益GBM元模型", "ml", True, ("smartmoney.raw_daily", "sklearn.HistGradientBoostingClassifier"), "20-40日右尾概率", "single-stock overlay"),
    StrategyCatalogItem("target_stop_survival_model", "目标止损生存模型", "ml", True, ("smartmoney.raw_daily", "sklearn.RandomForestClassifier"), "目标先到/止损先到概率", "single-stock overlay"),
    StrategyCatalogItem("stop_loss_hazard_model", "止损危险率模型", "ml", True, ("smartmoney.raw_daily", "sklearn.RandomForestClassifier"), "止损先到概率与风险 veto", "single-stock overlay"),
    StrategyCatalogItem("multi_horizon_target_classifier", "多目标周期分类器", "ml", True, ("smartmoney.raw_daily", "sklearn.HistGradientBoostingClassifier"), "15日20%/25日30%/40日50%目标概率", "single-stock overlay"),
    StrategyCatalogItem("target_ladder_probability_model", "目标阶梯概率模型", "ml", True, ("smartmoney.raw_daily", "sklearn.HistGradientBoostingClassifier"), "15/25/40日目标概率、止损先到和命中时间", "single-stock overlay"),
    StrategyCatalogItem("path_shape_mixture_model", "路径形态混合模型", "ml", True, ("smartmoney.raw_daily", "sklearn.GaussianMixture"), "当前路径簇的未来收益/目标/止损分布", "single-stock overlay"),
    StrategyCatalogItem("mfe_mae_surface_model", "MFE/MAE收益风险面", "ml", True, ("smartmoney.raw_daily", "sklearn.HistGradientBoostingRegressor"), "预测最大上行/最大不利回撤与收益风险比", "single-stock overlay"),
    StrategyCatalogItem("position_sizing_model", "连续仓位模型", "ml", True, ("strategy_matrix", "smartmoney.raw_daily"), "由概率、风险、流动性推导建议仓位", "report-time ensemble"),
    StrategyCatalogItem("forward_entry_timing_model", "未来5日择时模型", "ml", True, ("smartmoney.raw_daily", "sklearn.RandomForestClassifier"), "今天买与等待回踩的择时概率", "single-stock overlay"),
    StrategyCatalogItem("entry_price_surface_model", "买入价格面模型", "ml", True, ("smartmoney.raw_daily", "sklearn.RandomForestClassifier"), "今日/回踩/突破/回避路线与买入价格面", "single-stock overlay"),
    StrategyCatalogItem("pullback_rebound_classifier", "回踩反弹分类器", "ml", True, ("smartmoney.raw_daily", "sklearn.RandomForestClassifier"), "回踩后目标先到/破位风险", "single-stock overlay"),
    StrategyCatalogItem("squeeze_breakout_classifier", "收敛突破分类器", "ml", True, ("smartmoney.raw_daily", "sklearn.HistGradientBoostingClassifier"), "波动收敛后的向上突破概率", "single-stock overlay"),
    StrategyCatalogItem("model_stack_blender", "多模型概率融合器", "ml", True, ("strategy_matrix.model_probabilities",), "融合GBM/序列/Kronos/replay概率", "report-time ensemble"),
    StrategyCatalogItem("fundamental_lineup", "基本面阵列", "rule", True, ("research.memory",), "财报/研报证据是否可用", "single-stock + peer"),
    StrategyCatalogItem("fundamental_price_dislocation_model", "财报价格错配模型", "statistical", True, ("research.memory", "smartmoney.raw_daily", "smartmoney.raw_daily_basic"), "财报强弱与价格透支/低估的执行修正", "single-stock + peer"),
    StrategyCatalogItem("smartmoney_sector_ml", "SmartMoney RF/XGB 板块模型", "ml", True, ("smartmoney.ml.persistence", "smartmoney.ml.features"), "SW L2 板块 ML 顺风", "global prior"),
    StrategyCatalogItem("ningbo_active_ml", "宁波 active ML", "ml", True, ("ningbo.candidates_daily", "ningbo.ml.dual_scorer"), "目标股候选池 ML 排名", "global prior + single-stock"),
    StrategyCatalogItem("kronos_pattern", "Kronos K线表征", "dl", True, ("ningbo.kronos cache",), "K线形态 embedding 证据", "global prior + analog"),
    StrategyCatalogItem("analog_kronos_nearest_neighbors", "Kronos相似形态近邻", "dl", True, ("ningbo.kronos cache", "smartmoney.raw_daily"), "相似历史路径分布", "on-demand + global"),
    StrategyCatalogItem("kronos_path_cluster_transition", "Kronos路径簇转移", "dl", True, ("ningbo.kronos cache", "smartmoney.raw_daily"), "当前形态簇未来20-40日转移概率", "global + single-stock overlay"),
    StrategyCatalogItem("temporal_fusion_sequence_ranker", "多周期序列排序模型", "dl", True, ("smartmoney.raw_daily", "sklearn.MLPClassifier"), "多周期走势排序/右尾分数", "single-stock overlay"),
    StrategyCatalogItem("peer_research_auto_trigger", "同行Research自动触发", "statistical", True, ("research.report_runs", "research.memory"), "缺少同行财报深度报告时自动生成并回填对比", "sector orchestration"),
    StrategyCatalogItem("strategy_validation_decay", "策略验证衰减", "statistical", True, ("ta.setup_metrics_daily",), "把滚动胜率/衰减转成模型元信号", "global TA + single-stock context"),
    StrategyCatalogItem("regime_adaptive_weight_model", "体制自适应权重模型", "ml", True, ("ta.regime", "smartmoney.sector_state_daily"), "根据市场体制和 SW L2 相位动态调权", "global prior + sector"),
    StrategyCatalogItem("llm_regime_cache", "LLM 市场体制解释", "llm", True, ("smartmoney.llm_regime_states",), "体制解释/权重约束", "global prior"),
    StrategyCatalogItem("llm_counterfactual_cache", "LLM 反事实韧性", "llm", True, ("smartmoney.llm_counterfactuals",), "反事实风险/失效条件解释", "single-stock"),
    StrategyCatalogItem("event_catalyst_llm", "事件催化LLM抽取", "llm", True, ("research.company_event_memory", "ta.catalyst_event_memory"), "催化/风险事件解释", "single-stock"),
    StrategyCatalogItem("fundamental_contradiction_llm", "基本面矛盾审计LLM", "llm", True, ("research.memory", "smartmoney.llm_counterfactuals"), "财报结论与市场行为是否矛盾", "single-stock"),
    StrategyCatalogItem("scenario_tree_llm", "交易情景树LLM", "llm", True, ("structured_signals", "ifa.core.llm.LLMClient"), "把结构化信号压缩成可证伪执行路径", "report-layer"),
    StrategyCatalogItem("intraday_profile", "日内/T+0画像", "execution", True, ("duckdb.intraday_5min",), "T+0/VWAP/成交密集区", "single-stock on-demand"),
    StrategyCatalogItem("volume_profile_support", "成交密集支撑", "execution", True, ("duckdb.intraday_5min",), "分钟级成本区承接/压力", "single-stock on-demand"),
    StrategyCatalogItem("vwap_reclaim_execution", "VWAP收复执行", "execution", True, ("duckdb.intraday_5min",), "盘中收复VWAP后的执行质量", "single-stock on-demand"),
    StrategyCatalogItem("t0_uplift", "底仓T+0增益", "execution", True, ("duckdb.intraday_5min", "smartmoney.raw_daily"), "高抛低吸可捕捉振幅和扣费后增益", "single-stock on-demand"),
    StrategyCatalogItem("ta_family_T", "TA T趋势", "ta", True, ("ta.candidates_daily", "ta.setup_metrics_daily"), "趋势/突破 setup", "global TA"),
    StrategyCatalogItem("ta_family_P", "TA P回踩", "ta", True, ("ta.candidates_daily", "ta.setup_metrics_daily"), "回踩 setup", "global TA"),
    StrategyCatalogItem("ta_family_R", "TA R反转", "ta", True, ("ta.candidates_daily", "ta.setup_metrics_daily"), "反转 setup", "global TA"),
    StrategyCatalogItem("ta_family_F", "TA F形态", "ta", True, ("ta.candidates_daily", "ta.setup_metrics_daily"), "形态 setup", "global TA"),
    StrategyCatalogItem("ta_family_V", "TA V量价", "ta", True, ("ta.candidates_daily", "ta.setup_metrics_daily"), "量价 setup", "global TA"),
    StrategyCatalogItem("ta_family_S", "TA S板块", "ta", True, ("ta.candidates_daily", "ta.setup_metrics_daily"), "板块共振 setup", "global TA"),
    StrategyCatalogItem("ta_family_C", "TA C筹码", "ta", True, ("ta.candidates_daily", "ta.setup_metrics_daily"), "筹码 setup", "global TA"),
    StrategyCatalogItem("ta_family_O", "TA O订单流", "ta", True, ("ta.candidates_daily", "ta.setup_metrics_daily"), "订单流 setup", "global TA"),
    StrategyCatalogItem("ta_family_Z", "TA Z统计", "ta", True, ("ta.candidates_daily", "ta.setup_metrics_daily"), "统计反转 setup", "global TA"),
    StrategyCatalogItem("ta_family_E", "TA E事件", "ta", True, ("ta.candidates_daily", "ta.setup_metrics_daily"), "事件 setup", "global TA"),
    StrategyCatalogItem("ta_family_D", "TA D顶部预警", "ta", True, ("ta.warnings_daily", "ta.setup_metrics_daily"), "顶部/退出预警", "global TA"),
)


FUTURE_STRATEGY_IDEAS: tuple[StrategyCatalogItem, ...] = ()


def implemented_count() -> int:
    return len(IMPLEMENTED_STRATEGIES)


def future_count() -> int:
    return len(FUTURE_STRATEGY_IDEAS)


def by_category() -> dict[str, list[StrategyCatalogItem]]:
    out: dict[str, list[StrategyCatalogItem]] = {}
    for item in IMPLEMENTED_STRATEGIES:
        out.setdefault(item.category, []).append(item)
    return out
