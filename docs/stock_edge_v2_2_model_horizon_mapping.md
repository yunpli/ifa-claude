# Stock Edge v2.2 模型到 5/10/20 交易日映射

> 基于 `ifa/families/stock/strategies/catalog.py` 与 `matrix.py` 当前实现。  
> 当前 catalog 共有 85 个 implemented strategy item。  
> “概率”一律按当前实现理解为未校准模型估计，除非后续 calibration artifact 明确标记 calibrated。

## 1. 完整模型映射表

| 模型/策略 | 类别 | 当前是否已实现 | 适合 5d | 适合 10d | 适合 20d | 用途 | 核心/辅助/风险/解释 | 是否需要调参 | 是否需要补数据 | 是否应进入报告解释 | 备注 |
|---|---|---|---|---|---|---|---|---|---|---|---|
| `trend_following` 趋势延续 | rule | 是 | 辅助 | 核心 | 核心 | 趋势方向与追涨约束 | 核心/辅助 | 是 | 否 | 是 | 日线已足够，需三周期权重拆分 |
| `support_pullback` 支撑回踩 | rule | 是 | 核心 | 核心 | 辅助 | buy zone 与失效价 | 核心 | 是 | 否 | 是 | 5d 最重要执行信号之一 |
| `breakout_pressure` 压力突破 | rule | 是 | 辅助 | 核心 | 辅助 | 突破/回踩买点 | 核心/辅助 | 是 | 否 | 是 | 需要 chase warning 联动 |
| `momentum_5d` 5日动量 | statistical | 是 | 核心 | 辅助 | 否 | 短线惯性/过热 | 核心/风险 | 是 | 否 | 是 | 5d 中正向与过热双向解释 |
| `volume_confirmation` 量能确认 | statistical | 是 | 辅助 | 核心 | 辅助 | 量价确认 | 辅助 | 是 | 否 | 是 | 10d 持续性重要 |
| `volatility_structure` 波动结构 | statistical | 是 | 风险 | 风险 | 风险 | ATR/止损宽度/仓位风险 | 风险 | 是 | 否 | 是 | 三周期都用于风险等级 |
| `liquidity_slippage` 流动性滑点风险 | statistical | 是 | 核心 | 风险 | 风险 | 容量、滑点、成交可行性 | 风险 veto | 是 | 否 | 是 | 5d 必须进风险约束 |
| `range_position` 区间位置 | statistical | 是 | 辅助 | 辅助 | 核心 | 60日位置和追高约束 | 辅助 | 是 | 否 | 是 | 20d 判断赔率位置 |
| `volatility_contraction` 波动收敛 | statistical | 是 | 辅助 | 核心 | 辅助 | 蓄势/突破前结构 | 辅助 | 是 | 否 | 是 | 10d squeeze 相关 |
| `drawdown_recovery` 回撤修复 | statistical | 是 | 辅助 | 辅助 | 核心 | 修复质量和反弹持续性 | 辅助 | 是 | 否 | 是 | 20d 趋势恢复背景 |
| `gap_risk` 跳空风险 | statistical | 是 | 核心 | 风险 | 否 | 追高/开盘风险 | 风险 veto | 是 | 否 | 是 | 5d 必须高权重 |
| `gap_risk_open_model` 开盘跳空风险模型 | ml | 是 | 核心 | 风险 | 否 | 次日不利跳空概率 | 风险 veto | 是 | 否 | 是 | 当前为单股即时 RF，需 5d 校准 |
| `auction_imbalance_proxy` 集合竞价失衡代理 | execution | 是 | 核心 | 辅助 | 否 | 开盘/收盘承接代理 | 执行辅助 | 是 | 否 | 是 | 无真实竞价数据时只能代理 |
| `trend_quality_r2` 趋势质量R2 | statistical | 是 | 辅助 | 核心 | 核心 | 趋势斜率与纯度 | 核心/辅助 | 是 | 否 | 是 | 20d 核心，10d 辅助 |
| `candle_reversal_structure` K线反转结构 | rule | 是 | 核心 | 辅助 | 否 | 下影反转/上影派发 | 辅助/风险 | 是 | 否 | 是 | 5d 买点和风险解释 |
| `volume_price_divergence` 量价背离 | statistical | 是 | 风险 | 核心 | 辅助 | 上涨缩量/下跌放量 | 风险 | 是 | 否 | 是 | 10d 判断持续性 |
| `moneyflow_7d` 7日主力净流 | smartmoney | 是 | 核心 | 核心 | 辅助 | 资金确认 | 核心 | 是 | 否 | 是 | 5/10d 核心 |
| `orderflow_mix` 大单结构 | smartmoney | 是 | 核心 | 核心 | 辅助 | 超大单/大单结构 | 核心 | 是 | 否 | 是 | 需与 moneyflow persistence 拆权重 |
| `northbound_regime` 北向资金体制 | smartmoney | 是 | 风险 | 辅助 | 辅助 | 全局风险偏好 | 风险/解释 | 是 | 否 | 是 | 不是个股主因 |
| `market_margin_impulse` 两融杠杆脉冲 | smartmoney | 是 | 风险 | 辅助 | 辅助 | 杠杆风险偏好 | 风险/解释 | 是 | 否 | 是 | 市场 regime gate |
| `block_trade_pressure` 大宗交易压力 | smartmoney | 是 | 风险 | 风险 | 风险 | 折溢价承接/派发 | 风险 | 是 | 否 | 是 | 样本事件稀疏，不能过高权重 |
| `lhb_institution_hotmoney_divergence` 龙虎榜机构游资分歧 | smartmoney | 是 | 核心 | 辅助 | 否 | 机构/游资共振或分歧 | 核心/风险 | 是 | 否 | 是 | 事件存在时强解释 |
| `flow_persistence_decay` 资金持续性衰减 | smartmoney | 是 | 辅助 | 核心 | 辅助 | 资金连续/转弱 | 核心/风险 | 是 | 否 | 是 | 10d 核心 |
| `limit_up_microstructure` 涨停微结构 | rule | 是 | 核心 | 辅助 | 否 | 封单/炸板/连板结构 | 核心/风险 | 是 | 否 | 是 | 5d 事件路径核心 |
| `limit_up_event_path_model` 涨停事件路径模型 | statistical | 是 | 核心 | 核心 | 否 | 涨停/炸板后的延续衰减 | 核心/风险 | 是 | 否 | 是 | 需要事件条件化调参 |
| `smartmoney_sw_l2` SW L2 板块资金 | smartmoney | 是 | 辅助 | 核心 | 核心 | 板块资金顺逆风 | 核心 | 是 | 否 | 是 | SW L2 已覆盖 |
| `sector_diffusion_breadth` SW L2 扩散宽度 | smartmoney | 是 | 辅助 | 核心 | 核心 | 板块扩散/拥挤 | 核心/风险 | 是 | 否 | 是 | 10d 很重要 |
| `same_sector_leadership` 同板块龙头位置 | smartmoney | 是 | 辅助 | 核心 | 核心 | 同行业位置 | 辅助 | 是 | 否 | 是 | 当前同行财务仍依赖 Research cache |
| `peer_relative_momentum` 同行相对强弱 | statistical | 是 | 辅助 | 核心 | 核心 | 5/10/15日同行强弱 | 核心/辅助 | 是 | 否 | 是 | 目标股必须在图表中明确标识 |
| `peer_leader_fundamental_spread` 同行财报质量价差 | statistical | 是 | 否 | 辅助 | 辅助 | 同行财务质量/估值 | 辅助/解释 | 是 | Research 不全 | 是 | 不阻塞三周期 |
| `peer_financial_alpha_model` 同行财务Alpha模型 | statistical | 是 | 否 | 辅助 | 辅助 | 财务质量、估值、价格滞后 | 辅助/解释 | 是 | Research 不全 | 是 | 20d 低权重 |
| `hierarchical_sector_shrinkage` 行业层级收缩 | statistical | 是 | 辅助 | 核心 | 核心 | 样本不足时行业先验 | 辅助 | 是 | 否 | Debug/摘要 | 需防止过度平滑个股特征 |
| `daily_basic_style` 交易质量/估值风格 | statistical | 是 | 辅助 | 辅助 | 核心 | 换手/量比/估值拥挤 | 风险/辅助 | 是 | 否 | 是 | 20d 风格风险 |
| `historical_replay_edge` 单股历史相似形态Replay | statistical | 是 | 辅助 | 核心 | 辅助 | 历史相似片段收益 | 核心/辅助 | 是 | 否 | 是 | 需按 5/10/20 重新做 target |
| `target_stop_replay` 目标/止损路径Replay | statistical | 是 | 辅助 | 核心 | 核心 | 目标/止损谁先到 | 核心 | 是 | 否 | 是 | 必须三周期化 |
| `entry_fill_replay` 入场成交Replay | statistical | 是 | 核心 | 辅助 | 否 | 未来 5 日买入区间成交 | 核心执行 | 是 | 否 | 是 | 5d 核心 |
| `entry_fill_classifier` 入场成交概率分类器 | ml | 是 | 核心 | 辅助 | 否 | 成交概率/成交质量 | 核心执行 | 是 | 否 | 是 | 当前未正式校准 |
| `quantile_return_forecaster` 收益分位预测 | ml | 是 | 辅助 | 辅助 | 核心 | p10/p50/p90 收益带 | 核心/解释 | 是 | 否 | 是 | 当前名称偏 20/40d，需三周期分位 |
| `conformal_return_band` 保序置信收益带 | statistical | 是 | 辅助 | 辅助 | 核心 | 不确定性区间 | 解释/风险 | 是 | 否 | 是 | 需校准后再强展示 |
| `stop_first_classifier` 先止损概率 | ml | 是 | 核心 | 核心 | 核心 | 风险优先级 | 风险 veto | 是 | 否 | 是 | 三周期都必须 |
| `isotonic_score_calibrator` 单调概率校准 | statistical | 是 | 核心 | 核心 | 核心 | score 到概率校准 | 校准/审计 | 是 | 需要 labels | 报告只显示校准状态 | 必须先支持 5/10/20 |
| `right_tail_meta_gbm` 右尾收益GBM元模型 | ml | 是 | 否 | 辅助 | 核心 | 右尾收益概率 | 辅助/核心 | 是 | 否 | 是 | 40d 原生，应降级为 20d 右尾辅助 |
| `target_stop_survival_model` 目标止损生存模型 | ml | 是 | 辅助 | 核心 | 核心 | target-first / stop-first | 核心 | 是 | 否 | 是 | 三周期必须重配 horizon |
| `stop_loss_hazard_model` 止损危险率模型 | ml | 是 | 核心 | 核心 | 核心 | 止损危险率 | 风险 veto | 是 | 否 | 是 | 5d/10d/20d 都需要 |
| `multi_horizon_target_classifier` 多目标周期分类器 | ml | 是 | 辅助 | 核心 | 核心 | 多周期目标概率 | 核心 | 是 | 否 | 是 | 当前是 15/25/40，需改 5/10/20 |
| `target_ladder_probability_model` 目标阶梯概率模型 | ml | 是 | 辅助 | 核心 | 核心 | 目标阶梯/用时/止损 | 核心 | 是 | 否 | 是 | 三周期主力模型 |
| `path_shape_mixture_model` 路径形态混合模型 | ml | 是 | 辅助 | 核心 | 辅助 | 路径簇分布 | 辅助 | 是 | 否 | 是 | 10d 适配强 |
| `mfe_mae_surface_model` MFE/MAE收益风险面 | ml | 是 | 辅助 | 核心 | 核心 | 最大有利/不利波动 | 核心风险 | 是 | 否 | 是 | 必须输出到 10/20d |
| `position_sizing_model` 连续仓位模型 | ml | 是 | 辅助 | 核心 | 核心 | 概率、止损、流动性推仓位 | 核心/风控 | 是 | 否 | 是 | 需要分 horizon 仓位 |
| `forward_entry_timing_model` 未来5日择时模型 | ml | 是 | 核心 | 辅助 | 否 | 今天买/等待回踩 | 核心执行 | 是 | 否 | 是 | 5d 核心 |
| `entry_price_surface_model` 买入价格面模型 | ml | 是 | 核心 | 辅助 | 否 | 买入价格路线 | 核心执行 | 是 | 否 | 是 | 直接输出 buy_zone |
| `pullback_rebound_classifier` 回踩反弹分类器 | ml | 是 | 辅助 | 核心 | 辅助 | 回踩后反弹/破位风险 | 核心/辅助 | 是 | 否 | 是 | 10d 强 |
| `squeeze_breakout_classifier` 收敛突破分类器 | ml | 是 | 辅助 | 核心 | 辅助 | 收敛后突破概率 | 核心/辅助 | 是 | 否 | 是 | 10d 强 |
| `model_stack_blender` 多模型概率融合器 | ml | 是 | 辅助 | 核心 | 核心 | 多模型融合 | 核心 | 是 | 否 | Debug/摘要 | 必须 horizon-aware |
| `fundamental_lineup` 基本面阵列 | rule | 是 | 否 | 否 | 辅助 | 财报/研报证据可用性 | 解释 | 否 | Research 不全 | 是 | 不阻塞 |
| `fundamental_price_dislocation_model` 财报价格错配模型 | statistical | 是 | 否 | 辅助 | 辅助 | 财报强弱 vs 价格透支 | 风险/解释 | 是 | Research 不全 | 是 | 20d 辅助 |
| `smartmoney_sector_ml` SmartMoney RF/XGB 板块模型 | ml | 是 | 辅助 | 核心 | 核心 | 板块 ML 顺风 | 核心/辅助 | 是 | 否 | 是 | 需要确认 artifact 新鲜度 |
| `ningbo_active_ml` 宁波 active ML | ml | 是 | 辅助 | 辅助 | 辅助 | 候选池 ML 排名 | 辅助 | 是 | 依赖 Ningbo cache | 是 | 不应跑 Ningbo 脚本，只复用 |
| `kronos_pattern` Kronos K线表征 | dl | 是 | 解释 | 辅助 | 辅助 | K线 embedding 证据 | 解释 | 是 | 依赖 Kronos cache | 是 | 当前 matrix 中不单独加方向分 |
| `analog_kronos_nearest_neighbors` Kronos相似形态近邻 | dl | 是 | 辅助 | 核心 | 核心 | 相似历史路径分布 | 核心/辅助 | 是 | 依赖 Kronos cache | 是 | 适合 10/20d |
| `kronos_path_cluster_transition` Kronos路径簇转移 | dl | 是 | 辅助 | 核心 | 核心 | 路径簇转移 | 核心/辅助 | 是 | 依赖 Kronos cache | 是 | 当前写 20-40d，需三周期化 |
| `temporal_fusion_sequence_ranker` 多周期序列排序模型 | dl | 是 | 辅助 | 辅助 | 核心 | 多周期序列排序 | 核心/辅助 | 是 | 否 | 是 | 当前 sklearn MLP，并非真正 TFT |
| `peer_research_auto_trigger` 同行Research自动触发 | statistical | 是 | 否 | 否 | 辅助 | Research 编排 | 解释/数据 | 否 | Research 不全 | Debug/说明 | 当前 deferred 深化 |
| `strategy_validation_decay` 策略验证衰减 | statistical | 是 | 辅助 | 核心 | 核心 | setup 胜率/衰减 | 风险/元信号 | 是 | 否 | 是 | 需按 horizon 重估 |
| `regime_adaptive_weight_model` 体制自适应权重模型 | ml | 是 | 辅助 | 核心 | 核心 | 市场/板块体制动态调权 | 核心/辅助 | 是 | 否 | 是 | 必须只调权，不直接预测 |
| `llm_regime_cache` LLM 市场体制解释 | llm | 是 | 解释 | 解释 | 解释 | 体制解释/权重约束 | 解释/风险 | 否 | 否 | 是 | 必须用系统 LLM 工具产物，不用当前模型 |
| `llm_counterfactual_cache` LLM 反事实韧性 | llm | 是 | 解释 | 解释 | 解释 | 反事实风险/失效条件 | 解释/风险 | 否 | 否 | 是 | 不能直接当 alpha |
| `event_catalyst_llm` 事件催化LLM抽取 | llm | 是 | 辅助 | 辅助 | 辅助 | 催化/风险事件解释 | 解释/风险 | 是 | 当前事件 cache 稀疏 | 是 | 事件存在才展示 |
| `fundamental_contradiction_llm` 基本面矛盾审计LLM | llm | 是 | 否 | 否 | 辅助 | 财报与市场行为矛盾 | 解释/风险 | 否 | Research 不全 | 是 | 20d 风险提示 |
| `scenario_tree_llm` 交易情景树LLM | llm | 是 | 解释 | 解释 | 解释 | 结构化信号压缩成情景树 | 报告解释 | 是 | 否 | 是 | 必须只吃结构化信号 |
| `intraday_profile` 日内/T+0画像 | execution | 是 | 核心 | 辅助 | 否 | VWAP/成交密集区/T+0 | 核心执行 | 是 | 分钟线需补 | 是 | 只对有底仓 T+0 生效 |
| `volume_profile_support` 成交密集支撑 | execution | 是 | 核心 | 核心 | 辅助 | 分钟成本区承接/压力 | 核心执行 | 是 | 分钟线需补 | 是 | 10d 可作买点质量 |
| `vwap_reclaim_execution` VWAP收复执行 | execution | 是 | 核心 | 辅助 | 否 | VWAP 收复质量 | 核心执行 | 是 | 分钟线需补 | 是 | 5d 重点 |
| `t0_uplift` 底仓T+0增益 | execution | 是 | 核心 | 否 | 否 | 底仓高抛低吸增益 | 执行辅助 | 是 | 分钟线需补 | 是 | 必须带 `has_base_position` 约束 |
| `ta_family_T` TA T趋势 | ta | 是 | 辅助 | 核心 | 辅助 | 趋势/突破 setup | 核心/辅助 | 是 | 否 | 是 | 取 TA 已实现策略族 |
| `ta_family_P` TA P回踩 | ta | 是 | 核心 | 核心 | 辅助 | 回踩 setup | 核心/辅助 | 是 | 否 | 是 | 5/10d 重要 |
| `ta_family_R` TA R反转 | ta | 是 | 辅助 | 核心 | 辅助 | 反转 setup | 辅助 | 是 | 否 | 是 | 需防止逆势过拟合 |
| `ta_family_F` TA F形态 | ta | 是 | 辅助 | 核心 | 辅助 | 形态 setup | 辅助 | 是 | 否 | 是 | 依赖 setup validation |
| `ta_family_V` TA V量价 | ta | 是 | 核心 | 核心 | 辅助 | 量价 setup | 核心/辅助 | 是 | 否 | 是 | 5/10d 重要 |
| `ta_family_S` TA S板块 | ta | 是 | 辅助 | 核心 | 核心 | 板块共振 setup | 核心 | 是 | 否 | 是 | 与 SW L2 联动 |
| `ta_family_C` TA C筹码 | ta | 是 | 辅助 | 核心 | 辅助 | 筹码 setup | 辅助/风险 | 是 | 否 | 是 | 10d 控制松动风险 |
| `ta_family_O` TA O订单流 | ta | 是 | 核心 | 核心 | 辅助 | 订单流 setup | 核心 | 是 | 否 | 是 | 与 moneyflow 联动 |
| `ta_family_Z` TA Z统计 | ta | 是 | 辅助 | 核心 | 辅助 | 统计反转 setup | 辅助 | 是 | 否 | 是 | regime-sensitive |
| `ta_family_E` TA E事件 | ta | 是 | 核心 | 辅助 | 否 | 事件 setup | 辅助/解释 | 是 | 否 | 是 | 事件出现时展示 |
| `ta_family_D` TA D顶部预警 | ta | 是 | 核心风险 | 核心风险 | 核心风险 | 顶部/退出预警 | 风险 veto | 是 | 否 | 是 | reduce/sell 重要 |

## 2. 三周期核心模型集

### 5d 核心

`support_pullback`, `momentum_5d`, `liquidity_slippage`, `gap_risk`, `gap_risk_open_model`, `auction_imbalance_proxy`, `candle_reversal_structure`, `moneyflow_7d`, `orderflow_mix`, `lhb_institution_hotmoney_divergence`, `limit_up_microstructure`, `limit_up_event_path_model`, `entry_fill_replay`, `entry_fill_classifier`, `forward_entry_timing_model`, `entry_price_surface_model`, `intraday_profile`, `volume_profile_support`, `vwap_reclaim_execution`, `t0_uplift`, `ta_family_P/V/O/E/D`。

### 10d 核心

`trend_following`, `support_pullback`, `breakout_pressure`, `volume_confirmation`, `trend_quality_r2`, `volume_price_divergence`, `moneyflow_7d`, `orderflow_mix`, `flow_persistence_decay`, `smartmoney_sw_l2`, `sector_diffusion_breadth`, `same_sector_leadership`, `peer_relative_momentum`, `historical_replay_edge`, `target_stop_replay`, `target_stop_survival_model`, `path_shape_mixture_model`, `mfe_mae_surface_model`, `pullback_rebound_classifier`, `squeeze_breakout_classifier`, `smartmoney_sector_ml`, `kronos_path_cluster_transition`, `strategy_validation_decay`, `regime_adaptive_weight_model`, `ta_family_T/P/F/V/S/C/O/Z/D`。

### 20d 核心

`trend_following`, `trend_quality_r2`, `range_position`, `drawdown_recovery`, `smartmoney_sw_l2`, `sector_diffusion_breadth`, `same_sector_leadership`, `peer_relative_momentum`, `target_stop_replay`, `quantile_return_forecaster`, `conformal_return_band`, `stop_first_classifier`, `right_tail_meta_gbm`, `target_stop_survival_model`, `stop_loss_hazard_model`, `multi_horizon_target_classifier`, `target_ladder_probability_model`, `mfe_mae_surface_model`, `position_sizing_model`, `model_stack_blender`, `smartmoney_sector_ml`, `analog_kronos_nearest_neighbors`, `kronos_path_cluster_transition`, `temporal_fusion_sequence_ranker`, `strategy_validation_decay`, `regime_adaptive_weight_model`。

## 3. 降级或暂时不用

| 模型族 | 当前处理 |
|---|---|
| 40d/长期右尾目标 | 不作为主决策；`right_tail_meta_gbm`、`target_ladder`、`multi_horizon` 可降级为 20d 辅助，必须改 horizon/target 参数。 |
| Research/Fundamental | 只做 20d 辅助背景/风险提示；不阻塞 5d/10d/20d。 |
| LLM 类 | 只做解释、情景树、反事实、冲突说明；不直接输出交易概率。 |
| Kronos cache 缺失 | 不阻塞；有 cache 时作为 10d/20d 模型证据。 |
| Intraday 缺失 | 不阻塞 10d/20d；会降低 5d execution confidence。 |

## 4. 必须先校准才适合强影响决策

- `isotonic_score_calibrator`
- `entry_fill_classifier`
- `gap_risk_open_model`
- `target_stop_survival_model`
- `stop_loss_hazard_model`
- `multi_horizon_target_classifier`
- `target_ladder_probability_model`
- `right_tail_meta_gbm`
- `mfe_mae_surface_model`
- `model_stack_blender`

在校准前，报告只能展示为“未校准模型估计/模型分”，不能展示为确定性上涨概率。
