# Stock Edge v2.2 策略/模型调参覆盖表

> 自动从 `IMPLEMENTED_STRATEGIES` 与 `continuous_overlay_bounds()` 生成。

| 模型/策略 | 类别 | 当前是否进入 score | 当前是否进入 search bounds | 可调参数 | 当前是否应调 | 调参方式 | 是否可晋升 YAML | 备注 |
|---|---|---|---|---|---|---|---|---|
| trend_following | rule | 是 | 间接/否 | cluster_weights.* / smooth_scoring.* | 是 | 调所属 cluster 与平滑曲线参数 | global 可晋升 | 入场方向/追涨约束 |
| support_pullback | rule | 是 | 间接/否 | cluster_weights.* / smooth_scoring.* | 是 | 调所属 cluster 与平滑曲线参数 | global 可晋升 | 今日/未来5日回踩买点 |
| breakout_pressure | rule | 是 | 间接/否 | cluster_weights.* / smooth_scoring.* | 是 | 调所属 cluster 与平滑曲线参数 | global 可晋升 | 突破回踩买点 |
| momentum_5d | statistical | 是 | 间接/否 | 间接覆盖 | 待评估 | 目前间接调 cluster；建议补显式 weight 或单独校准 | 视验证 | 短线惯性/过热判断 |
| volume_confirmation | statistical | 是 | 间接/否 | 间接覆盖 | 待评估 | 目前间接调 cluster；建议补显式 weight 或单独校准 | 视验证 | 入场确认 |
| volatility_structure | statistical | 是 | 间接/否 | 间接覆盖 | 待评估 | 目前间接调 cluster；建议补显式 weight 或单独校准 | 视验证 | 止损宽度/仓位风险 |
| liquidity_slippage | statistical | 是 | 间接/否 | 间接覆盖 | 待评估 | 目前间接调 cluster；建议补显式 weight 或单独校准 | 视验证 | 容量/滑点/可执行性风险 |
| range_position | statistical | 是 | 间接/否 | 间接覆盖 | 待评估 | 目前间接调 cluster；建议补显式 weight 或单独校准 | 视验证 | 强势但未极端的买点质量 |
| volatility_contraction | statistical | 是 | 间接/否 | 间接覆盖 | 待评估 | 目前间接调 cluster；建议补显式 weight 或单独校准 | 视验证 | 蓄势/突破前结构 |
| drawdown_recovery | statistical | 是 | 间接/否 | 间接覆盖 | 待评估 | 目前间接调 cluster；建议补显式 weight 或单独校准 | 视验证 | 反转修复质量 |
| gap_risk | statistical | 是 | 间接/否 | 间接覆盖 | 待评估 | 目前间接调 cluster；建议补显式 weight 或单独校准 | 视验证 | 执行质量/不追高约束 |
| gap_risk_open_model | ml | 是 | 是 | signal_weights.gap_risk_open_model | 是 | 调 signal weight；内部模型不在本轮训练 | global 可晋升 | 次日开盘不利跳空概率与执行 veto |
| auction_imbalance_proxy | execution | 是 | 是 | signal_weights.auction_imbalance_proxy | 是 | 调 signal weight；内部模型不在本轮训练 | global 可晋升 | 开盘跳空和承接质量对当日执行的修正 |
| trend_quality_r2 | statistical | 是 | 是 | signal_weights.trend_quality_r2 | 是 | 调 signal weight；内部模型不在本轮训练 | 通常不晋升 | 趋势斜率与趋势纯度 |
| candle_reversal_structure | rule | 是 | 是 | signal_weights.candle_reversal_structure | 是 | 调 signal weight；内部模型不在本轮训练 | 通常不晋升 | 下影反转/上影派发对买点的修正 |
| volume_price_divergence | statistical | 是 | 是 | signal_weights.volume_price_divergence | 是 | 调 signal weight；内部模型不在本轮训练 | 通常不晋升 | 上涨缩量/下跌放量的执行风险 |
| moneyflow_7d | smartmoney | 是 | 间接/否 | cluster_weights.* / smooth_scoring.* | 是 | 调所属 cluster 与平滑曲线参数 | global 可晋升 | 资金确认 |
| orderflow_mix | smartmoney | 是 | 间接/否 | cluster_weights.* / smooth_scoring.* | 是 | 调所属 cluster 与平滑曲线参数 | global 可晋升 | 机构参与代理 |
| northbound_regime | smartmoney | 是 | 是 | signal_weights.northbound_regime | 是 | 调 signal weight；内部模型不在本轮训练 | global 可晋升 | 外资顺逆风与市场风险偏好 |
| market_margin_impulse | smartmoney | 是 | 是 | signal_weights.market_margin_impulse | 是 | 调 signal weight；内部模型不在本轮训练 | global 可晋升 | 融资余额扩张/收缩对风险预算的约束 |
| block_trade_pressure | smartmoney | 是 | 是 | signal_weights.block_trade_pressure | 是 | 调 signal weight；内部模型不在本轮训练 | global 可晋升 | 折溢价大宗交易对承接/派发的事件修正 |
| lhb_institution_hotmoney_divergence | smartmoney | 是 | 间接/否 | 间接覆盖 | 待评估 | 目前间接调 cluster；建议补显式 weight 或单独校准 | 视验证 | 机构/游资事件资金是否共振 |
| flow_persistence_decay | smartmoney | 是 | 间接/否 | 间接覆盖 | 待评估 | 目前间接调 cluster；建议补显式 weight 或单独校准 | 视验证 | 主力净流是否持续、转弱或反转 |
| limit_up_microstructure | rule | 是 | 间接/否 | 间接覆盖 | 待评估 | 目前间接调 cluster；建议补显式 weight 或单独校准 | 视验证 | 封单/炸板/连板结构对买点的约束 |
| limit_up_event_path_model | statistical | 是 | 是 | signal_weights.limit_up_event_path_model | 是 | 调 signal weight；内部模型不在本轮训练 | global 可晋升 | 涨停/炸板/连板后的延续或衰减路径 |
| smartmoney_sw_l2 | smartmoney | 是 | 间接/否 | cluster_weights.* / smooth_scoring.* | 是 | 调所属 cluster 与平滑曲线参数 | global 可晋升 | 板块顺逆风 |
| sector_diffusion_breadth | smartmoney | 是 | 是 | signal_weights.sector_diffusion_breadth | 是 | 调 signal weight；内部模型不在本轮训练 | global 可晋升 | 板块资金是否扩散、是否拥挤 |
| same_sector_leadership | smartmoney | 是 | 间接/否 | cluster_weights.* / smooth_scoring.* | 是 | 调所属 cluster 与平滑曲线参数 | global 可晋升 | 同行位置/基本面触发 |
| peer_relative_momentum | statistical | 是 | 间接/否 | cluster_weights.* / smooth_scoring.* | 是 | 调所属 cluster 与平滑曲线参数 | global 可晋升 | 同行5/10/15日相对强弱 |
| peer_leader_fundamental_spread | statistical | 是 | 间接/否 | 间接覆盖 | 待评估 | 目前间接调 cluster；建议补显式 weight 或单独校准 | 视验证 | 同行财报质量/估值为主，市值动量为辅 |
| peer_financial_alpha_model | statistical | 是 | 是 | signal_weights.peer_financial_alpha_model | 是 | 调 signal weight；内部模型不在本轮训练 | global 可晋升 | 同行财务质量、估值与价格滞后的 alpha |
| hierarchical_sector_shrinkage | statistical | 是 | 是 | signal_weights.hierarchical_sector_shrinkage | 是 | 调 signal weight；内部模型不在本轮训练 | global 可晋升 | 个股样本不足时向SW L2/同行先验收缩 |
| daily_basic_style | statistical | 是 | 间接/否 | 间接覆盖 | 待评估 | 目前间接调 cluster；建议补显式 weight 或单独校准 | 视验证 | 流动性/估值拥挤度 |
| historical_replay_edge | statistical | 是 | 间接/否 | 间接覆盖 | 待评估 | 目前间接调 cluster；建议补显式 weight 或单独校准 | 视验证 | 相似历史片段后的目标命中/止损统计 |
| target_stop_replay | statistical | 是 | 间接/否 | 间接覆盖 | 待评估 | 目前间接调 cluster；建议补显式 weight 或单独校准 | 视验证 | 目标价或止损价谁先触发与用时 |
| entry_fill_replay | statistical | 是 | 间接/否 | 间接覆盖 | 待评估 | 目前间接调 cluster；建议补显式 weight 或单独校准 | 视验证 | 未来5日买入区间成交与先破位概率 |
| entry_fill_classifier | ml | 是 | 是 | signal_weights.entry_fill_classifier | 是 | 调 signal weight；内部模型不在本轮训练 | global 可晋升 | 未来5日能否成交和成交质量 |
| quantile_return_forecaster | ml | 是 | 是 | signal_weights.quantile_return_forecaster | 是 | 调 signal weight；内部模型不在本轮训练 | global 可晋升 | p10/p50/p90收益分布 |
| conformal_return_band | statistical | 是 | 是 | signal_weights.conformal_return_band | 是 | 调 signal weight；内部模型不在本轮训练 | global 可晋升 | 收益预测区间与不确定性 |
| stop_first_classifier | ml | 是 | 是 | signal_weights.stop_first_classifier | 是 | 调 signal weight；内部模型不在本轮训练 | global 可晋升 | 风险优先级 |
| isotonic_score_calibrator | statistical | 是 | 是 | signal_weights.isotonic_score_calibrator | 是 | 调 signal weight；内部模型不在本轮训练 | global 可晋升 | 矩阵分数到真实概率的校准 |
| right_tail_meta_gbm | ml | 是 | 是 | signal_weights.right_tail_meta_gbm | 是 | 调 signal weight；内部模型不在本轮训练 | global 可晋升 | 20-40日右尾概率 |
| target_stop_survival_model | ml | 是 | 是 | signal_weights.target_stop_survival_model | 是 | 调 signal weight；内部模型不在本轮训练 | global 可晋升 | 目标先到/止损先到概率 |
| stop_loss_hazard_model | ml | 是 | 是 | signal_weights.stop_loss_hazard_model | 是 | 调 signal weight；内部模型不在本轮训练 | global 可晋升 | 止损先到概率与风险 veto |
| multi_horizon_target_classifier | ml | 是 | 是 | signal_weights.multi_horizon_target_classifier | 是 | 调 signal weight；内部模型不在本轮训练 | global 可晋升 | 15日20%/25日30%/40日50%目标概率 |
| target_ladder_probability_model | ml | 是 | 是 | signal_weights.target_ladder_probability_model | 是 | 调 signal weight；内部模型不在本轮训练 | global 可晋升 | 15/25/40日目标概率、止损先到和命中时间 |
| path_shape_mixture_model | ml | 是 | 是 | signal_weights.path_shape_mixture_model | 是 | 调 signal weight；内部模型不在本轮训练 | global 可晋升 | 当前路径簇的未来收益/目标/止损分布 |
| mfe_mae_surface_model | ml | 是 | 是 | signal_weights.mfe_mae_surface_model | 是 | 调 signal weight；内部模型不在本轮训练 | global 可晋升 | 预测最大上行/最大不利回撤与收益风险比 |
| position_sizing_model | ml | 是 | 是 | signal_weights.position_sizing_model | 是 | 调 signal weight；内部模型不在本轮训练 | global 可晋升 | 由概率、风险、流动性推导建议仓位 |
| forward_entry_timing_model | ml | 是 | 是 | signal_weights.forward_entry_timing_model | 是 | 调 signal weight；内部模型不在本轮训练 | global 可晋升 | 今天买与等待回踩的择时概率 |
| entry_price_surface_model | ml | 是 | 是 | signal_weights.entry_price_surface_model | 是 | 调 signal weight；内部模型不在本轮训练 | global 可晋升 | 今日/回踩/突破/回避路线与买入价格面 |
| pullback_rebound_classifier | ml | 是 | 是 | signal_weights.pullback_rebound_classifier | 是 | 调 signal weight；内部模型不在本轮训练 | global 可晋升 | 回踩后目标先到/破位风险 |
| squeeze_breakout_classifier | ml | 是 | 是 | signal_weights.squeeze_breakout_classifier | 是 | 调 signal weight；内部模型不在本轮训练 | global 可晋升 | 波动收敛后的向上突破概率 |
| model_stack_blender | ml | 是 | 是 | signal_weights.model_stack_blender | 是 | 调 signal weight；内部模型不在本轮训练 | global 可晋升 | 融合GBM/序列/Kronos/replay概率 |
| fundamental_lineup | rule | 是 | 间接/否 | 间接覆盖 | 待评估 | 目前间接调 cluster；建议补显式 weight 或单独校准 | 视验证 | 财报/研报证据是否可用 |
| fundamental_price_dislocation_model | statistical | 是 | 间接/否 | 间接覆盖 | 待评估 | 目前间接调 cluster；建议补显式 weight 或单独校准 | 视验证 | 财报强弱与价格透支/低估的执行修正 |
| smartmoney_sector_ml | ml | 是 | 间接/否 | 缺少直接 search bound | 待评估 | 目前间接调 cluster；建议补显式 weight 或单独校准 | 视验证 | SW L2 板块 ML 顺风 |
| ningbo_active_ml | ml | 是 | 间接/否 | 缺少直接 search bound | 待评估 | 目前间接调 cluster；建议补显式 weight 或单独校准 | 视验证 | 目标股候选池 ML 排名 |
| kronos_pattern | dl | 是 | 间接/否 | 缺少直接 search bound | 待评估 | 目前间接调 cluster；建议补显式 weight 或单独校准 | 视验证 | K线形态 embedding 证据 |
| analog_kronos_nearest_neighbors | dl | 是 | 是 | signal_weights.analog_kronos_nearest_neighbors | 是 | 调 signal weight；内部模型不在本轮训练 | global 可晋升 | 相似历史路径分布 |
| kronos_path_cluster_transition | dl | 是 | 是 | signal_weights.kronos_path_cluster_transition | 是 | 调 signal weight；内部模型不在本轮训练 | global 可晋升 | 当前形态簇未来20-40日转移概率 |
| temporal_fusion_sequence_ranker | dl | 是 | 是 | signal_weights.temporal_fusion_sequence_ranker | 是 | 调 signal weight；内部模型不在本轮训练 | global 可晋升 | 多周期走势排序/右尾分数 |
| peer_research_auto_trigger | statistical | 是 | 是 | signal_weights.peer_research_auto_trigger | 是 | 调 signal weight；内部模型不在本轮训练 | global 可晋升 | 缺少同行财报深度报告时自动生成并回填对比 |
| strategy_validation_decay | statistical | 是 | 是 | signal_weights.strategy_validation_decay | 是 | 调 signal weight；内部模型不在本轮训练 | global 可晋升 | 把滚动胜率/衰减转成模型元信号 |
| regime_adaptive_weight_model | ml | 是 | 是 | signal_weights.regime_adaptive_weight_model | 是 | 调 signal weight；内部模型不在本轮训练 | global 可晋升 | 根据市场体制和 SW L2 相位动态调权 |
| llm_regime_cache | llm | 是 | 间接/否 | 缺少直接 search bound | 待评估 | LLM 只调门控/权重，不调输出数字 | 视验证 | 体制解释/权重约束 |
| llm_counterfactual_cache | llm | 是 | 间接/否 | 缺少直接 search bound | 待评估 | LLM 只调门控/权重，不调输出数字 | 视验证 | 反事实风险/失效条件解释 |
| event_catalyst_llm | llm | 是 | 是 | signal_weights.event_catalyst_llm | 谨慎 | 调 signal weight；内部模型不在本轮训练 | 通常不晋升 | 催化/风险事件解释 |
| fundamental_contradiction_llm | llm | 是 | 是 | signal_weights.fundamental_contradiction_llm | 谨慎 | 调 signal weight；内部模型不在本轮训练 | 通常不晋升 | 财报结论与市场行为是否矛盾 |
| scenario_tree_llm | llm | 是 | 间接/否 | 无 | 否 | 报告解释层，不调 alpha | 否 | 把结构化信号压缩成可证伪执行路径 |
| intraday_profile | execution | 是 | 间接/否 | 间接覆盖 | 待评估 | 目前间接调 cluster；建议补显式 weight 或单独校准 | 视验证 | T+0/VWAP/成交密集区 |
| volume_profile_support | execution | 是 | 是 | signal_weights.volume_profile_support | 是 | 调 signal weight；内部模型不在本轮训练 | 通常不晋升 | 分钟级成本区承接/压力 |
| vwap_reclaim_execution | execution | 是 | 是 | signal_weights.vwap_reclaim_execution | 是 | 调 signal weight；内部模型不在本轮训练 | 通常不晋升 | 盘中收复VWAP后的执行质量 |
| t0_uplift | execution | 是 | 间接/否 | 间接覆盖 | 待评估 | 目前间接调 cluster；建议补显式 weight 或单独校准 | 视验证 | 高抛低吸可捕捉振幅和扣费后增益 |
| ta_family_T | ta | 是 | 是 | ta_family_weights.* / signal_weights.strategy_validation_decay | 是 | 调 TA family 权重与验证衰减 | global 可晋升 | 趋势/突破 setup |
| ta_family_P | ta | 是 | 是 | ta_family_weights.* / signal_weights.strategy_validation_decay | 是 | 调 TA family 权重与验证衰减 | global 可晋升 | 回踩 setup |
| ta_family_R | ta | 是 | 是 | ta_family_weights.* / signal_weights.strategy_validation_decay | 是 | 调 TA family 权重与验证衰减 | global 可晋升 | 反转 setup |
| ta_family_F | ta | 是 | 是 | ta_family_weights.* / signal_weights.strategy_validation_decay | 是 | 调 TA family 权重与验证衰减 | global 可晋升 | 形态 setup |
| ta_family_V | ta | 是 | 是 | ta_family_weights.* / signal_weights.strategy_validation_decay | 是 | 调 TA family 权重与验证衰减 | global 可晋升 | 量价 setup |
| ta_family_S | ta | 是 | 是 | ta_family_weights.* / signal_weights.strategy_validation_decay | 是 | 调 TA family 权重与验证衰减 | global 可晋升 | 板块共振 setup |
| ta_family_C | ta | 是 | 是 | ta_family_weights.* / signal_weights.strategy_validation_decay | 是 | 调 TA family 权重与验证衰减 | global 可晋升 | 筹码 setup |
| ta_family_O | ta | 是 | 是 | ta_family_weights.* / signal_weights.strategy_validation_decay | 是 | 调 TA family 权重与验证衰减 | global 可晋升 | 订单流 setup |
| ta_family_Z | ta | 是 | 是 | ta_family_weights.* / signal_weights.strategy_validation_decay | 是 | 调 TA family 权重与验证衰减 | global 可晋升 | 统计反转 setup |
| ta_family_E | ta | 是 | 是 | ta_family_weights.* / signal_weights.strategy_validation_decay | 是 | 调 TA family 权重与验证衰减 | global 可晋升 | 事件 setup |
| ta_family_D | ta | 是 | 是 | ta_family_weights.* / signal_weights.strategy_validation_decay | 是 | 调 TA family 权重与验证衰减 | global 可晋升 | 顶部/退出预警 |

## 覆盖结论

- 策略/模型总数：85。
- 直接 signal weight 覆盖数：40。
- TA 11 个 family 当前主要通过 TA family 上下文和 `strategy_validation_decay` 进入调参；下一轮应把 `ta_family_weights.*` 纳入 promotion allowlist。
- LLM 类信号只可调门控/权重，不可让 LLM 改数值、概率或价格。
- ML/DL 类当前多数是 report-time / single-stock 轻量模型或复用模型，当前 tuning 是参数 overlay 搜索，不是完整模型训练。
- `right_tail_meta_gbm` 等 40d/right-tail 遗留模型应降级为 20d 辅助或 legacy audit，不进入主 objective。
