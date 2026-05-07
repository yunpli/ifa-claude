# IFA SmartMoney — Codex/Agents 上下文

> **更新**: 2026-05-07 (Codex 完成 V2.2.2 SME MVP1 release)
> **接手必读**: 📌 [`docs/ta-handover-2026-05-04.md`](docs/ta-handover-2026-05-04.md) — Claude 写的完整 TA family handover,包括做了什么、回测结果、调参演进、还有什么没做。
> **🎯 调参经验沉淀**: 📌 [`docs/ta-tuning-playbook.md`](docs/ta-tuning-playbook.md) — 10 条启发式规则 + iteration log,任何调参前先读。
> **TA deep-dive**: [`docs/ta-strategy-deep-dive.md`](docs/ta-strategy-deep-dive.md)
> **当前状态**:
> - V2.2.2 已完成 SME MVP1 release：独立 `sme` family、`sme.*` schema、22:40/23:10 BJT production scripts、forward-label/tuning-ready 链路、客户简报与 release 文档。
> - V2.2.2 原规划但未落地的 Research 横向对比、TA 盘中、Stock Edge intraday/personalized、Stock Edge final tuning 均顺延到 V2.2.3；主动文档以 `docs/v2.2.2-release-notes.md` / `README.md` / `docs/tuning-playbook.md` 为准。
> - TA M10 P0+P1+P2 (4 次调参 iteration) 已完成 + push,head commit `dc56965`
> - 30 setups / 11 families / 180d 真实持仓回测,Tier A 跑赢 universe +0.67pp
> - 用户会 terminal 跑 `scripts/ta_backfill_360d.py` (~55-60 min) 扩到 360d
> - 跑完后:`uv run python -m ifa.cli ta tier-perf --start 2024-12-01 --end 2026-04-14` 验证 alpha 在 360d 仍 ≥ +0.5pp
> - 如果 robust → production-ready;否则诊断哪些参数过拟合

---

## 平行工作分工说明

- **本 session 当前任务（research 财报分析 v2.2）**：负责 `ifa/families/research/**` 中财报 quick/deep、研报解析、报告渲染、持久化接口与 `tests/research/**`。触碰这些文件时只做本任务必要范围，避免覆盖另一 session 的 UI/文案并行改动。
- **另一 session（Claude / 可能并行）**：若继续做 Research UI 或 SmartMoney，应以各自段落为界；共同文件先读后改，保留对方上下文。
- **共享但谨慎**：`ifa/core/**`（DB / units / 渲染基础）、`alembic/versions/`（不同迁移文件互不冲突，但 head 推进时注意 down_revision）、根目录 `CLAUDE.md` 与 `AGENTS.md`（按各自段落各自维护；AGENTS 从 CLAUDE 同步）。

## Codex 工作准则（长期有效）

- **项目记忆更新位置**：Codex 后续在本项目形成的状态更新、踩坑记录、分工边界、阶段性结论，优先写入 `AGENTS.md`。`CLAUDE.md` 由 Claude session 维护；需要同步时只同步明确共享的事实，避免覆盖另一人的上下文。
- **协作边界**：默认尊重并保留另一 session 对 `ifa/families/research/**`、`tests/research/**`、`docs/research-deep-dive.md` 的改动；如必须触碰，应先确认上下文、只做最小必要编辑，并在本文件记录原因。
- **身份标准**：Codex 在本项目中的工程目标按“顶级华尔街量化工程师 + 30 年全栈工程师 + 熟悉 A 股微观结构的基金经理/交易员”执行。所有实现必须面向 production-grade、可审计、可回测、可解释、可运维的 billion-dollar business 标准，不写 toy demo。
- **代码质量底线**：数据口径优先于页面效果；PIT 正确性、单位一致性、缺失值显式传播、可复现输出、失败降级、测试覆盖、日志与监控优先。LLM 只能增强叙述，不得编造财务数字或替代规则层判断。
- **数据/逻辑改动留痕规则**：任何涉及数据层口径、单位换算、PIT 规则、特征计算逻辑、状态机/标签定义、训练/回测目标、ETL 增量语义的实现改动，都必须同步留下足够的代码注释或 docs 说明。说明至少覆盖：旧口径/新口径差异、为什么改、会影响哪些表/字段/回测结果、历史数据是否需要重算、后续 dev/ops 应该如何验证或回滚。不要只改代码让未来开发者从 SQL/实现里猜业务含义。
- **产品判断底线**：任何报告或信号必须服务真实投资流程：能帮助 PM/分析师/交易员更快判断“是否值得继续研究、风险在哪里、下一步验证什么”。不做漂亮但不可交易、不可验证、不可解释的输出。
- **Research 财报分析形态**：财报分析不是单一 generic report，当前固定为“报表类型 × 深度档位”的 4 类单股报告：`quarterly quick`（只读最新季报）、`annual quick`（只读最新年报）、`quarterly deep`（最多三年/12 个季度，逐季看 YoY + QoQ）、`annual deep`（最多三年/3 份年报，看 YoY + 较上年变化）。不要做 stock comparison。手动验证曾用 `智微智能` / `致尚科技` / `鹏鼎控股`，当前新增验证样本按用户要求改为 `朗科科技` 跑完整 matrix。
- **Research 持久化原则**：所有从历史季报/年报/研报 PDF 解析或派生出的财报因子，必须落到本地数据库后再组合生成报告。Canonical 长期记忆使用 PostgreSQL `research.period_factor_decomposition`（分期五维拆解）与 `research.pdf_extract_cache`（研报 PDF 摘要缓存）；DuckDB 只用于本地 scratch / ad hoc OLAP，不作为 Research 基本面权威存储。Stock Intel / TA 侧需要基本面 lineup 时调用 `ifa.families.research.memory.load_fundamental_lineup(...)`，不要解析 HTML。
- **Research 报告资产复用**：同一股票、同一 `analysis_type`、同一 `tier`、同一最新财报期已经有成功生成的报告时，默认从 `research.report_runs` 取 `output_html_path` / `scope_json.md_path` 直接列给用户，不重新生成；manual / production 只是输出目录不同，不作为强制重算边界。需要强制重跑时 CLI 用 `--fresh`。Stock Intel 若需要财报底稿，应先查 `find_reusable_report(...)`；没有则同步触发对应 quick/deep 生成，再通过 `load_fundamental_lineup(...)` 取结构化基本面。

### Smart Money Enhanced / SME 当前实现记录（Codex 2026-05-07）

- **产品与工作清单文档**：新增 `docs/sme-product-design.md` 与 `docs/sme-mvp1-work-list.md`。SME 是新 family，代码上对既有 `SmartMoney` 0 依赖；对 `smartmoney.*` 本地老表只读，所有新增计算与持久化写入独立 `sme.*` 表。
- **数据/逻辑口径文档**：新增 `docs/sme-data-logic-contracts.md`，以后 SME 任何单位、PIT、特征、状态机、标签、ETL 语义改动必须同步更新该文档或代码注释。
- **MVP1 schema 已落库**：新增 Alembic `alembic/versions/l0m1n2o3p4q5_sme_mvp1_schema.py` 并已本地 `upgrade head`。核心表包括 `sme_sw_member_daily`、`sme_stock_orderflow_daily`、`sme_sector_orderflow_daily`、`sme_sector_diffusion_daily`、`sme_sector_state_daily`、`sme_labels_daily`、审计/合约/单位注册表。
- **CLI 入口**：新增统一入口 `uv run python -m ifa.cli sme ...`，覆盖 `doctor`、`status`、`etl audit/backfill/incremental`、`compute membership/stock-flow/sector-flow/diffusion/state`、`labels`。每日增量入口支持 `--source-mode prefer_smartmoney --labels --json` 等生产参数。
- **MVP1 数据流口径**：当前 source mode 只启用 `prefer_smartmoney`，读取 `smartmoney.raw_moneyflow`、`raw_daily`、`raw_daily_basic`、`raw_sw_member`、`sw_member_monthly`、`raw_sw_daily`；不改写任何 `smartmoney.*` 表。金额统一落为 `_yuan` 列，来源单位由 SME unit registry/contract 做审计。
- **diffusion/state 优化**：`diffusion` 与 `state` 已从逐日循环改为区间批量 SQL。diffusion 用当前 PIT 成分股 universe 计算 1/3/5/10 日滚动主力净额扩散率，3/5 日收益改为按成分股滚动复利口径；state 修正 `rebound` 不可达问题，并补充 `retail_chase`、`leader_crowded` 等风险标记。
- **交易日语义硬规则**：SME/SmartMoney/Stock Edge 里“上一日、下一日、最近 N 日、未来 h 日”都必须指交易日，不是日历日。实现必须使用 canonical trading calendar / trading-date row_number / `previous_trade_date` / `next_trade_date`，禁止用 `date +/- N` 表达交易窗口，禁止用单一数据源表日期隐式替代交易日历。2021-05-10 曾因 `raw_daily` 缺失但 `raw_moneyflow` 存在而被漏算，根因就是把 `raw_daily` 当交易日历。
- **stock orderflow v1.3 修正**：`net_mf_amount_yuan` 是 Tushare 官方净流入额；四类订单 buy/sell 金额是成交 bucket，不再拿 bucket 重算值和官方净流入做 reconciliation。`net_recomputed_yuan`/`reconciliation_error_yuan` 现在表示 bucket balance audit。bucket balance 不再作为 `quality_flag` 阻断条件，因为 BJ/920 系列存在 bucket net 等于官方净流入的源口径差异；真正 degraded 只用于行情/成交额缺失或非法值。历史窗口已按 `stock_orderflow_v1_3` 重跑。
- **NULL/0 规则**：缺失行情、成交额、收益率必须保留 NULL 并显式 degraded，不能 `COALESCE(..., 0)` 把缺失伪装成真实零。只有业务语义上“缺失即 0”的字段才允许这样处理，并必须写注释。
- **sector/label v1.1 修正**：本地 `raw_sw_daily` 多数是 SW L1，不是完整 L2；`sector_orderflow_v1_1` 对 L2 return 使用 `COALESCE(raw_sw_daily.pct_change, amount_weight_return, equal_weight_return)` fallback。`labels_forward_v1_1` 使用未来 h 个交易日复利收益，重算前先删除目标窗口/horizon，确保旧 NULL labels 不残留。
- **全量重算状态**：2021-01-01 → 2026-05-06 已按最终逻辑版本重跑完成；覆盖 1290/1290 个 source trade dates，无缺失 source dates，`null_label_rows=0`，sector/diffusion/state 对齐。最终逻辑版本：`stock_orderflow_v1_3`、`sector_orderflow_v1_1`、`diffusion_v1_2`、`state_machine_v1_1`、`labels_forward_v1_1`。SME schema 约 8.48GB（VACUUM ANALYZE 后统计已更新）。仅剩 3 行 `2022-11-09` stock degraded，原因是三只股票有 moneyflow 但 Tushare daily 仍无行情；保留 degraded 是正确行为。sector degraded 主要是低覆盖板块，属于覆盖质量信号。
- **年度回填脚本**：新增 `scripts/sme_backfill_year.sh` 及 `scripts/sme_backfill_2021.sh` 到 `scripts/sme_backfill_2025.sh`。脚本会输出并记录实际耗时、SME schema 前后总存储、增量 bytes/GB、各 SME 表前后 rows/table/index bytes；日志写入 `/Users/neoclaw/claude/ifaenv/logs/sme_backfill/`。
- **每日增量脚本**：新增 `scripts/sme_incremental_0300.sh`，生产默认跑 `sme etl incremental --as-of auto --run-mode production --source-mode prefer_smartmoney --labels --json` 并随后执行 doctor。incremental 默认在目标交易日核心 SME 表已完整时 no-op；需要强制重算用 CLI `--force`。
- **SME 每日生产 gate 与推荐 schedule**：新增 `scripts/sme_daily_gate.py`，并接入 `scripts/sme_incremental_0300.sh` / `scripts/sme_incremental_2240.sh`、`scripts/sme_briefing_2310.sh`、`scripts/sme_briefing_0400.sh`。推荐生产节奏改为北京时间晚间：22:40 跑 incremental，23:10 左右跑 `sme_briefing_2310.sh`，观察日就是当天交易日，避免凌晨“上一交易日”歧义。脚本第一步查 `smartmoney.trade_cal`：交易日才执行；非交易日输出结构化 JSON（`status=non_trade_day`, `action=skip`）并 exit 0，方便投递 agent 发“今日非交易日，无 SME 简报/ETL”的消息。`sme_briefing_0400.sh` 只保留给 legacy 凌晨 previous-trading-day 模式，不作为推荐调度。
- **客户报告口径**：用户明确要求最终报告“不求复杂、不要过程、只要通俗易懂的结论”。SME 资金结构报告生产端可用 `uv run python -m ifa.cli sme market-structure --date auto --client` 输出终端结论，或用 `uv run python -m ifa.cli sme brief --date auto --format html --output ...` 生成独立 HTML 简报。完整 JSON 只用于审计/调参/第三方机器集成。不要把公式、过程、阈值表、证据数组直接塞给最终客户。
- **SME 简报日期与输出目录**：`ifa sme brief` 的标题必须写明观察交易日，例如“2026年5月6日资金结构简报”，页头同时展示观察日期与北京时间报告生成时间。无显式 `--output` 时默认写入 IFA 标准目录：`/Users/neoclaw/claude/ifaenv/out/<run_mode>/<YYYYMMDD>/sme/CN_sme_brief_<YYYYMMDD>_<HHMM>.<ext>`；production schedule 应使用 `--run-mode production`。
- **SME 简报数据支撑**：客户版不展示公式，但“主要流入/主要流出”必须给出可读数据支撑。当前口径为按主力资金排序，主力=超大单+大单净额；小中单是散户代理；机构席位代理和龙虎榜事件资金是披露样本辅助验证，不代表全市场机构全量仓位。
- **SME 简报模板隔离**：新增 `ifa/families/sme/templates/brief.html`，信息层级参考老 SmartMoney 晚报，但模板代码 0 依赖老 SmartMoney / core render templates；不得在 SME 简报模板中 `{% include %}` 老模板文件，避免老模板变更影响 SME 生产输出。
- **效果优先原则**：用户明确不关心内部实现了多少模型/策略/规则，只关心真实预测效果和实际决策能力。SME 内部可以复杂，但所有模型/规则必须服务 walk-forward、OOC/OOS、调参和可验证收益；对客户输出必须简单直观。LLM 可用于 narrative 压缩，但不得改写结构化结论、方向、数值或风险等级。
- **调参中间结果持久化**：不要僵硬坚持“数据层不变”。只要某个中间判断会进入回测/调参/OOS 验证，就应考虑落入 SME 自己的新表并带 logic_version。`sme_market_structure_daily` 已用于持久化每日资金结构策略快照，避免报告时动态重算导致历史不可复现。
- **策略评估链路**：新增 `sme_strategy_eval_daily`，用 `ifa sme compute strategy-eval --start ... --end ...` 将 `sme_market_structure_daily` 的 primary/secondary/defensive/repair/avoid/crowding buckets 与 `sme_labels_daily` 的 1/3/5/10/20 交易日 forward labels 连接。新增 `ifa sme tuning-ready --start ... --end ...` 检查样本是否够调参。调参优化目标应看 OOS/OOC 的 `avg_signal_score`、`success_rate`、drawdown/runup，不看内部实现数量。
- **Nightly 脚本**：新增 `scripts/sme_incremental_2240.sh` 与 `scripts/sme_nightly_tune_2300.sh` 给第三方平台集成。nightly 脚本自动取最新成熟 label date，刷新 market structure、strategy eval、tuning-ready、bucket-review，并输出 artifacts 到 `/Users/neoclaw/claude/ifaenv/out/sme_tuning/nightly/<timestamp>/`。
- **SME 参数化原则**：市场结构参数已 YAML 化在 `ifa/families/sme/params/market_structure_v1.yaml`。连续参数是主调参面（阈值、权重、惩罚项），离散只用于结构选择（如 `primary.mode`）。`ifa sme tune promote-profile` 会自动检查候选 profile 是否已在窗口内评估、样本是否 ready、是否有正向 bucket，再允许 `--apply` 写回 YAML `active_profile`。nightly 可用 `SME_MARKET_STRUCTURE_PROFILE` 评估 profile，用 `SME_TUNE_PROMOTE_PROFILE` + `SME_TUNE_APPLY_PROMOTION=1` 自动晋升。
- **当前验证**：`uv run pytest tests/sme -q` 通过 28 条；`py_compile` 通过；`bash -n scripts/sme_incremental_2240.sh scripts/sme_nightly_tune_2300.sh` 通过；`uv run alembic upgrade head` 已 apply `m1n2o3p4q5r6` 与 `n2o3p4q5r6s7`；`uv run python -m ifa.cli sme compute market-structure --start 2026-01-01 --end 2026-04-30 --json` 已回填 77 个交易日；`uv run python -m ifa.cli sme compute strategy-eval --start 2026-01-01 --end 2026-04-30 --json` 生成 2,026 行评估；`uv run python -m ifa.cli sme tuning-ready --start 2026-01-01 --end 2026-04-30 --json` 返回 ready horizons `[1,3,5,10]`；`uv run python -m ifa.cli sme tune bucket-review --start 2026-01-01 --end 2026-04-30 --json` 输出 promote `secondary/crowding_risk/avoid`、rebuild `primary/repair/defensive`。`uv run python -m ifa.cli sme status --json` 显示 `sme_market_structure_daily` 78 行最新 `2026-05-06`、`sme_strategy_eval_daily` 2,026 行最新 `2026-04-30`、总存储约 8.486GB。早期信号：current `secondary` 与 `crowding_risk` 桶显著优于 `primary`，下一轮调参应优先重做 bucket ranking/threshold。`ifa/cli/stock.py`、`scripts/stock_edge_panel_tune.py` 仍是本任务外既有脏改，SME 工作不要覆盖它们。

### Stock Edge / 个股作战室当前实现记录（Codex 2026-05-05）

- **最新 handover（调参治理重点）**：`docs/stock_edge_v2_2_tuning_governance_handover_2026_05_05.md` 是当前最新接手入口；旧 `docs/stock_edge_v2_2_tuning_handover_2026_05_05.md` 已标记 superseded，只能作历史背景。
- **提交状态**：Stock Edge v2.2 三周期主实现已 commit/push 到 `origin/main`，commit `5a578a6`；调参治理修正已 commit/push，commit `9b4b4a0` (`Refactor Stock Edge tuning governance`)。
- **调参机制口径**：YAML 是 baseline 和搜索边界；global preset 实验阶段仍写 JSON artifact，但经过验证后必须通过 `scripts/stock_edge_promote_global_preset.py` 生成可审计 YAML patch / baseline variant 并人工晋升。single-stock overlay 只作为 runtime JSON 局部适配，永远不写回 YAML。
- **调参 review 历史风险（已修）**：此前 `prepare_report_params()` 没有先读取 `global_preset/__GLOBAL__`，optimizer/objectives 也有 `40d` 主 objective 遗留；这些已在 2026-05-05 调参治理修正中处理。下一轮 review 重点转为：global preset 是否应晋升 YAML baseline、objective 权重是否经过 OOS 验证、是否需要更强搜索器/校准器。
- **2026-05-05 调参治理修正**：已补 `docs/stock_edge_v2_2_tuning_architecture_review.md`、`docs/stock_edge_v2_2_5_10_20_objective_refactor.md`、`docs/stock_edge_v2_2_global_preset_promotion.md`、`docs/stock_edge_v2_2_strategy_tuning_coverage.md`、`docs/stock_edge_v2_2_tuning_runtime_handoff.md`。当前 global preset 兼容时会先叠加，single overlay 再覆盖；objective 主路径改为 `stock_edge_5_10_20_v1`，40d 只做 legacy audit；新增 `scripts/stock_edge_promote_global_preset.py` 用于 emit/apply 可审计 YAML patch，默认不静默修改 YAML。
- **产品命名**：原 `stock intel` 改为 **Stock Edge（个股作战室）**；代码继续复用 `ifa/families/stock/**`，不要新建平行 `stockedge` package。
- **输出目录**：报告、调参 artifact、分钟线 parquet 等运行输出统一落到 `/Users/neoclaw/claude/ifaenv/`，不要污染 repo。手动报告路径形态为 `/Users/neoclaw/claude/ifaenv/out/<run_mode>/<YYYYMMDD>/stock_edge/`。
- **as-of 规则**：交易日北京时间 15:00 前用 T-1，15:00 后用当天；非交易日用最近已完成交易日。
- **默认调参**：默认报告会先走 `prepare_report_params()`；若 10 天 TTL 内已有兼容单股 overlay 则复用，否则做单股 pre-report overlay，再生成报告。周末全市场/top-liquidity preset 与单股 overlay 都是独立 script/CLI，可被外部系统单独调用。
- **当前策略数**：`IMPLEMENTED_STRATEGIES` 已到 85 个；其中 84 个进入策略矩阵打分，1 个为报告层 `scenario_tree_llm`。覆盖规则/统计/TA/SmartMoney/Research/ML/DL(Kronos)/LLM cache/execution。新增重点模块包括 `historical_replay_edge`、`target_stop_replay`、`entry_fill_replay`、`liquidity_slippage`、`t0_uplift`、`flow_persistence_decay`、`analog_kronos_nearest_neighbors`、`kronos_path_cluster_transition`、`right_tail_meta_gbm`、`temporal_fusion_sequence_ranker`、`target_stop_survival_model`、`stop_loss_hazard_model`、`gap_risk_open_model`、`multi_horizon_target_classifier`、`target_ladder_probability_model`、`path_shape_mixture_model`、`mfe_mae_surface_model`、`forward_entry_timing_model`、`entry_price_surface_model`、`regime_adaptive_weight_model`、`peer_financial_alpha_model`、`limit_up_event_path_model`、`position_sizing_model`、`pullback_rebound_classifier`、`squeeze_breakout_classifier`、`fundamental_price_dislocation_model`、`model_stack_blender`、`event_catalyst_llm`、`fundamental_contradiction_llm`、`scenario_tree_llm`。
- **预测核心**：Stock Edge v2.2 主 decision layer 聚焦 5/10/20 个交易日，分别输出是否可买/等待/持有/减仓、买入区间、追高警戒、止损、第一止盈、目标区间、支持/反对模型与冲突解释。旧 40d / 20-40d 只能作为 legacy audit 或 20d 辅助证据，不进入用户主决策。
- **场景树**：报告新增“预测执行场景树”，把今日执行/今日等待/未来5日买点/失效路径拆成可证伪条件，每条路径必须显示触发条件、动作、买入带、目标、失效价和观察信号。数值只来自结构化模型；任何 LLM 表述压缩必须用项目工具 `ifa.core.llm.LLMClient`，不得改写价格、概率、止损。
- **Research deep 前置依赖**：Stock Edge 默认在最终计划生成前调用 `ensure_stock_edge_research_prefetch()`，确保目标股 + 最多 4 个 SW L2 可见龙头都有 `annual deep` 与 `quarterly deep`。已有 `research.report_runs` 成功资产则复用，缺失才通过 `ifa.families.research.report.service.ensure_research_report()` 生成；生成/复用后重新加载 snapshot，让 `research.memory.load_fundamental_lineup()` 消费最新持久化因子。目标股 deep 可用项目 `LLMClient` 且有 timeout；同行 deep 默认 rules-only，避免多个同行 narrative 调用阻塞交易执行卡。
- **同板块对比主轴**：同板块/同行对比主要看财务报表与 Research deep 结构化因子，包括 ROE、营收增速、CFO/NI、资产负债率、估值分位；市值和 5/10/15 日涨跌幅只作为辅助交易定位，不作为主排序。
- **T+0 约束**：A 股 T+0 只能用于有底仓；`t0_uplift` 可评估日内高抛低吸增益，但 executable T+0 plan 仍必须检查 `has_base_position`。
- **参数治理**：新增策略参数必须 YAML 化，当前主配置为 `ifa/families/stock/params/stock_edge_v2.2.yaml`；不要硬编码离散档位作为生产逻辑。
- **当前验证**：`uv run pytest tests/stock -q` 通过 68 条；调参治理相关 `py_compile` 通过。最新 manual deep 朗科科技报告：`/Users/neoclaw/claude/ifaenv/out/manual/20260430/stock_edge/CN_stock_edge_300042_SZ_20260430_225208.html`；桌面/移动 QA 截图在同目录 `qa/` 下，Playwright 检查 `scrollWidth == innerWidth`，场景树/免责声明/目标股标记、同板块财务对照主图、财务分表、财报-价格错配模型、多目标周期模型、未来5日择时模型、回踩反弹模型、收敛突破模型、目标/止损生存模型、模型融合器均存在，用户侧“数据新鲜度”不存在。

---

## 项目概览

**目标**: 将 SmartMoney 晚报的板块源从 DC（东财概念）全面迁移到 SW（申万 L2），并完成报告各节的重构与 LLM/ML 集成。

**技术栈**: Python 3.12 · uv · PostgreSQL 16 (port 55432) · SQLAlchemy 2.0 · Jinja2

**DB 连接**: `get_engine()` from `ifa.core.db` → `smartmoney` schema

---

## 为什么要换源（关键背景）

| 源 | 问题 |
|----|------|
| DC (东财概念) `raw_dc_member` | 只有 ~18 天历史，无法做时间正确的 PIT 查询 |
| THS (同花顺) `ths_member` | 只有当前快照，无 in_date/out_date，会引入前视偏差 |
| **SW (申万) `index_member_all`** | ✅ 完整历史 in_date/out_date，回溯至 1993 年 |

---

## 三阶段路线图

```
A 阶段（数据原料）✅      B 阶段（改配方）⬅ 当前    C 阶段（用新配方加工）
─────────────────────   ─────────────────────        ─────────────────────
A1. SW 成员 ETL ✅      B1. sector_flow_sw_l2 ⬅ 起点  C1. 跑板块资金流聚合
A2. 拉 SW 成员数据 ✅   B2. factors/flow.py            C2. 跑 compute（因子/状态/信号）
A3. raw backfill  ✅    B3. factors/leader.py           C3. 训练回测 2021-2025
A4. raw全覆盖    ✅     B4. data.py                    C4. 训练 RF + XGB 模型
                         B5. transition_matrix          C5. OOS 验证 2025-2026
                         B6. evening.py 重构            C6. 生成最终晚报
                         B7. LLM aug 集成
                         B8. ML §10 双模型
                         B9. run-mode badge
```

---

## A 阶段完成状态（全部 ✅）

### ✅ A1: SW 成员 ETL + 迁移
- `ifa/families/smartmoney/etl/sw_member_fetcher.py` — 完整 ETL
- `alembic/versions/c2e8f1a40b56_smartmoney_sw_member_tables.py` — 建表
- `alembic/versions/2d0c597983b9_merge_*.py` — 合并 heads
- `alembic/versions/a9f3c2e17d84_widen_kpl_list_numerics.py` — 放宽 NUMERIC 精度（已 apply）

### ✅ A2: 初次拉取 SW 成员数据
- `smartmoney.raw_sw_member`: 5,847 行（含完整 in_date/out_date）
- `smartmoney.sw_member_monthly`: 327,547 行，65 个月快照（2021-01 → 2026-05）
- 月度快照逻辑: `in_date <= snapshot_month AND (out_date IS NULL OR out_date > snapshot_month)`

### ✅ A3+A4: Raw backfill（2021-01 → 2025-10-31）
- 使用 `scripts/fast_backfill.py` 完成（877天，1585万行，195分钟）
- 跳过 `raw_dc_member`（已被 SW 替代）
- `raw_sw_daily` / `raw_index_daily` 按 code 批量拉（31+8次 API 而非 39×N_days）

### Alembic 当前 head
```
c1d2e3f4g5h6 (head)   # ta.event_signal_daily — M10 E 族数据表
```

链路: `a9f3c2e17d84` → `f2a3b4c5d6e7` (ta_schema_v0) → `a8b9c0d1e2f3` (ta_setup_metrics_regime_winrates) → `b1c2d3e4f5g6` (ta_sector_phase_metrics) → **`c1d2e3f4g5h6`** (ta_event_signal_daily)。

---

## 数据库覆盖率矩阵（2026-05-01 实测）

### 原始数据层 — 核心（2021全覆盖）✅

| 表 | 2021 | 2022 | 2023 | 2024 | 2025 | 2026 |
|---|---|---|---|---|---|---|
| raw_daily | 242 | 242 | 242 | 242 | 243 | 77 |
| raw_daily_basic | 243 | 242 | 242 | 242 | 243 | 77 |
| raw_moneyflow | 243 | 242 | 242 | 242 | 243 | 77 |
| raw_margin | 243 | 242 | 242 | 242 | 243 | 77 |
| raw_top_inst | 243 | 242 | 242 | 242 | 243 | 77 |
| raw_sw_daily | 243 | 242 | 242 | 242 | 243 | 77 |
| raw_index_daily | 243 | 242 | 242 | 242 | 243 | 77 |
| raw_kpl_list | 241 | 242 | 242 | 242 | 243 | 77 |
| raw_top_list | 242 | 242 | 241 | 242 | 243 | 77 |
| raw_limit_list_d | 241 | 242 | 241 | 242 | 243 | 77 |
| raw_block_trade | 240 | 242 | 242 | 242 | 243 | 77 |
| raw_moneyflow_hsgt | 233 | 236 | 231 | 233 | 237 | 75 |

### 原始数据层 — TuShare 无历史（只有近期，非缺失）

| 表 | 2021 | 2022 | 2023 | 2024 | 2025 | 2026 |
|---|---|---|---|---|---|---|
| raw_moneyflow_ind_dc | 0 | 0 | 73 | 242 | 243 | 77 |
| raw_ths_hot | 0 | 0 | 62 | 241 | 243 | 77 |
| raw_moneyflow_ind_ths | 0 | 0 | 0 | 73 | 242 | 77 |
| raw_kpl_concept | 0 | 0 | 0 | 53 | 242 | 77 |
| raw_kpl_concept_cons | 0 | 0 | 0 | 57 | 242 | 77 |
| raw_dc_hot | 0 | 0 | 0 | 192 | 239 | 77 |
| raw_dc_index | 0 | 0 | 0 | 8 | 243 | 77 |

### 已弃用 / 无数据

| 表 | 说明 |
|---|---|
| raw_dc_member | 已弃用 → SW 替代（仅 18 天近期数据，勿用） |
| raw_cyq_chips | 筹码分布，未启用 |

### 计算层（B+C 阶段产出，当前仅有近期）

| 表 | 现状 | 目标 |
|---|---|---|
| factor_daily | 2023部分+2025近期 | C2 跑全 2021-2026 |
| sector_state_daily | 2025近期 | C2 跑全 |
| market_state_daily | 2025近期 | C2 跑全 |
| stock_signals_daily | 空 | C4 训练后 C5 产出 |
| predictions_daily | 空 | C4 训练后产出 |
| **sector_moneyflow_sw_daily** | **不存在** | **B1 建表+聚合** |

---

## B 阶段详细规格（B1–B9）

### B1: `sector_flow_sw_l2.py`（新建）⬅ 从这里开始
**路径**: `ifa/families/smartmoney/etl/sector_flow_sw_l2.py`

**Step 1 — 新 Alembic migration 建表** `sector_moneyflow_sw_daily`:
```sql
CREATE TABLE smartmoney.sector_moneyflow_sw_daily (
    trade_date     DATE        NOT NULL,
    l2_code        VARCHAR(12) NOT NULL,
    l2_name        VARCHAR(64),
    l1_code        VARCHAR(12),
    l1_name        VARCHAR(64),
    net_amount     NUMERIC,          -- SUM(net_mf_amount) 单位: 万元
    buy_elg_amount NUMERIC,          -- SUM(buy_elg_amount) 超大单买入
    sell_elg_amount NUMERIC,         -- SUM(sell_elg_amount)
    buy_lg_amount  NUMERIC,          -- SUM(buy_lg_amount) 大单买入
    sell_lg_amount NUMERIC,
    stock_count    INTEGER,          -- COUNT(DISTINCT ts_code)
    PRIMARY KEY (trade_date, l2_code)
);
CREATE INDEX ON smartmoney.sector_moneyflow_sw_daily (trade_date);
```

**Step 2 — 聚合函数** (idempotent，支持按日或批量):
```sql
INSERT INTO smartmoney.sector_moneyflow_sw_daily
    (trade_date, l2_code, l2_name, l1_code, l1_name,
     net_amount, buy_elg_amount, sell_elg_amount,
     buy_lg_amount, sell_lg_amount, stock_count)
SELECT
    m.trade_date,
    s.l2_code, s.l2_name, s.l1_code, s.l1_name,
    SUM(m.net_mf_amount)     AS net_amount,
    SUM(m.buy_elg_amount)    AS buy_elg_amount,
    SUM(m.sell_elg_amount)   AS sell_elg_amount,
    SUM(m.buy_lg_amount)     AS buy_lg_amount,
    SUM(m.sell_lg_amount)    AS sell_lg_amount,
    COUNT(DISTINCT m.ts_code) AS stock_count
FROM smartmoney.raw_moneyflow m
JOIN smartmoney.sw_member_monthly s
  ON m.ts_code = s.ts_code
  AND s.snapshot_month = date_trunc('month', m.trade_date)::date
WHERE m.trade_date = ANY(:dates)   -- 或 BETWEEN :start AND :end
GROUP BY m.trade_date, s.l2_code, s.l2_name, s.l1_code, s.l1_name
ON CONFLICT (trade_date, l2_code) DO UPDATE SET
    net_amount      = EXCLUDED.net_amount,
    buy_elg_amount  = EXCLUDED.buy_elg_amount,
    sell_elg_amount = EXCLUDED.sell_elg_amount,
    buy_lg_amount   = EXCLUDED.buy_lg_amount,
    sell_lg_amount  = EXCLUDED.sell_lg_amount,
    stock_count     = EXCLUDED.stock_count,
    l2_name         = EXCLUDED.l2_name,
    l1_code         = EXCLUDED.l1_code,
    l1_name         = EXCLUDED.l1_name
```

**Step 3 — CLI 入口**（加入 runner.py 的每日 ETL 链）:
- `aggregate_sector_flow_sw(engine, dates: list[date]) -> int` — 批量
- `aggregate_sector_flow_sw_for_date(engine, trade_date: date) -> int` — 单日

**C1 回填命令**（B1 完成后执行）:
```bash
uv run python -c "
from ifa.families.smartmoney.etl.sector_flow_sw_l2 import aggregate_sector_flow_sw
from ifa.core.db import get_engine
import datetime as dt
# 拉所有有 raw_moneyflow 的交易日
from sqlalchemy import text
eng = get_engine()
with eng.connect() as c:
    dates = [r[0] for r in c.execute(text(
        'SELECT DISTINCT trade_date FROM smartmoney.raw_moneyflow ORDER BY trade_date'
    ))]
n = aggregate_sector_flow_sw(eng, dates)
print(f'Done: {n} rows')
"
```

---

### B2: `factors/flow.py` 修改
- 现有: 从 `raw_moneyflow_ind_dc` 取板块资金流（DC 源）
- 新增: 从 `sector_moneyflow_sw_daily` 取 SW L2 路径
- 参数: `sector_source: str = 'sw_l2'`（默认换 SW）
- 保留 DC 路径作为 fallback（`sector_source='dc'`）

---

### B3: `factors/leader.py` + `factors/candidate.py` 修改
- 现有: 从 `raw_dc_member` 查板块成员
- 新增: 从 `sw_member_monthly` 查（PIT 正确）
  ```python
  snapshot_month = date_trunc('month', trade_date)
  WHERE snapshot_month = :sm AND l2_code = :sector_code
  ```

---

### B4: `data.py` 修改
- 所有 `load_sector_*` 函数默认 `sector_source='sw_l2'`
- `load_sector_structures()`: 已有 kpl fallback，改为优先用 SW L2 成员

---

### B5: `transition_matrix.py`（新建）
**路径**: `ifa/families/smartmoney/transition_matrix.py`

**逻辑**:
1. 从 `sector_state_daily` 读历史 phase 序列（7 种 phase）
2. 构建经验转移矩阵（7×7）
3. Bayesian 每板块调整（per-sector 历史 vs 全局先验）
4. LLM ±10% 微调钩子
5. 输出: `predict_next_phase(sector_code, current_phase, trade_date) -> dict[phase, prob]`

---

### B6: `evening.py` 各节重构

已完成的改动（勿重复）:
- ✅ 金额单位: 万→亿（`_fmt_amt` 默认 scale=1e8）
- ✅ intro 去重（`_section_head.html` 已渲染，模板不再输出）
- ✅ §05 高质净流入加 LLM 解读列
- ✅ §07 周期网格加 leader_name 注释
- ✅ run-mode badge（TEST/MANUAL/PRODUCTION）

**待做**（严格按规格）:

§02 — 10日资金面水位迷你折线图:
- 从 `factor_daily` 取最近 10 个交易日 `north_flow` + `net_amount` (SW L2 汇总)
- 渲染 SVG 迷你折线（嵌入 HTML inline）

§03/§04 — 每个板块 top-5 个股钻取:
- 排除非 A 股板块（富时罗素/MSCI/沪深300 成分是个股标签，不是行业）
- SW L2 板块 → 查 `sw_member_monthly` 成员 → 从 `factor_daily` 取个股数据 → top-5 by net_amount

§05 — 高质净流入门槛提高:
- 门槛: 净流入 ≥ 10亿 AND 超大单占比 ≥ 2%
- 去重: 同一板块只保留最高分那条

§06 — 拥挤度风险卡片改表格:
- 现有: 分散的 card UI
- 改为: 紧凑表格（板块 | 拥挤度分 | 资金分布 | 风险描述）

§07 — 7×N 相位轨迹矩阵 + 转移概率预测:
- 7 种 phase: 启动/加速/高潮/衰退/冷却/蛰伏/反弹
- 矩阵展示当前活跃板块最近 N 天的 phase 轨迹
- 每格加转移概率（来自 B5 transition_matrix）

§08 — 明日→下个交易日; 加板块内股票; 标注算法来源:
- 标题: "下个交易日操作建议"
- 每个推荐板块展开显示候选股
- 注明: "(RF模型)" 或 "(XGB模型)"

§09/§10 — 加术语定义解释框

§10 — 拆分双模型:
- 短线池 (1-3天): RandomForest
- 中长线池 (1-2月, 目标 +30~50%): XGBoost
- 分开展示，各标注算法和预期持仓周期

§11 — 加章节定义 + 术语词汇表

---

### B7: LLM aug 模块集成
6 个已写好但未集成的模块（路径待确认）:
- `concept_cluster` — 概念聚类
- `regime_classifier` — 市场体制识别
- `hypothesis_grader` — 假设评分
- `backtest_forensics` — 回测归因
- `policy_polarity` — 政策极性
- `counterfactual` — 反事实分析

集成到 `evening.py` 对应节，通过 `ctx.llm_aug` 传入。

---

### B8: 双 ML 模型 §10
- RandomForest: 短线因子（1-3日动量、资金流方向、连板热度）
- XGBoost: 中长线因子（周期位置、资金趋势、基本面代理）
- 模型参数文件: `models/params_v2026_05_{rf,xgb}.json`（C4 训练后冻结）

---

### B9: run-mode badge 解耦
- 新增环境变量 `IFA_REPORT_RUN_BADGE`（值: `test`/`manual`/`production`）
- 优先级: env var > DB profile 推断
- 默认: 无 env var 时从 DB URL 推断（localhost=test，其余=production）

---

## C 阶段规格

### C1: 跑 sector_moneyflow_sw_daily 回填（B1 完成后）
```bash
# 见 B1 的 C1 回填命令
# 预期: ~1169天 × 约100个SW L2板块 = ~116,900行，几分钟内完成（纯SQL聚合）
```

### C2: 跑 compute 全量回填（B2-B4 完成后）
```bash
# 从 2021-01-04 到今天，补跑 factor_daily / sector_state_daily / market_state_daily
uv run python -m ifa.cli backfill --family smartmoney --start 2021-01-04 --end 2026-04-30
```
⚠️ 注意: compute 历史数据有 `'content_type'` / `'trade_date'` KeyError 问题，B6 修 evening.py 时一并处理。

### C3: 训练回测 2021-2025
- OOS 窗口: 2021-01 → 2025-10（in-sample training）
- 滚动验证窗口设置待定

### C4: 训练 RF + XGB 模型，冻结 v2026_05
- 特征工程见 B8 规格
- 输出: `models/params_v2026_05_rf.json` + `models/params_v2026_05_xgb.json`

### C5: OOS 验证 2025-11 → 2026-04
- 用冻结模型跑 stock_signals_daily / predictions_daily

### C6: 生成最终晚报 2026-04-30
```bash
IFA_REPORT_RUN_BADGE=production uv run python -m ifa.cli report --family smartmoney --date 2026-04-30
```

---

## 数据库 Schema 快速参考

```
smartmoney 库:
  raw_daily               — 个股日行情
  raw_daily_basic         — 个股基本面日数据
  raw_moneyflow           — 个股资金流（主力/超大单/大单）核心！
  raw_sw_member           — SW成员全历史 PK(l1_code,ts_code,in_date)
  sw_member_monthly       — SW月度快照 PK(snapshot_month,l2_code,ts_code) 65个月
  sector_moneyflow_sw_daily — SW L2 板块日资金流汇总 ← B1 建
  factor_daily            — 每日因子（north_flow,net_amount,vol_ratio等）
  sector_state_daily      — 板块状态（phase,role,cycle_phase等）
  market_state_daily      — 市场整体状态
  stock_signals_daily     — 个股信号（ML输出）
  predictions_daily       — ML预测结果
  raw_kpl_list            — 涨停池（leader fallback）
  raw_sw_daily            — SW板块价格/成交（非资金流）
  raw_index_daily         — 指数日行情（上证/深证/创业板/科创板等8个）
  backtest_runs / backtest_metrics — 回测结果
  report_runs / report_judgments   — 报告记录
  etl_watermarks          — ETL 水位线
```

---

## 文件结构快速参考

```
ifa/families/smartmoney/
  evening.py              — 晚报主逻辑（B6 主战场）
  data.py                 — 数据加载层（B4 修改）
  transition_matrix.py    — 相位转移矩阵（B5 新建）
  etl/
    runner.py             — 每日 ETL runner
    raw_fetchers.py       — 所有原始数据拉取函数
    sw_member_fetcher.py  — SW成员 ETL（A1 完成）
    sector_flow_sw_l2.py  — SW L2 板块流聚合（B1 新建）
  factors/
    flow.py               — 资金流因子（B2 修改）
    leader.py             — 龙头识别（B3 修改）
    candidate.py          — 候选股（B3 修改）

ifa/core/render/templates/
  report.html             — 含 run-mode badge（已更新）
  styles.css              — badge 样式（已更新）
  _sm_quality_flow.html   — §05 含解读列（已更新）
  _sm_cycle_grid.html     — §07 含 leader_name（已更新）
  _sm_sector_structure.html — §08 板块结构（已更新）

scripts/
  fast_backfill.py        — 优化 raw 回填脚本（A4 用，已完成）
  check_raw_coverage.py   — raw backfill 进度查询

alembic/versions/
  c2e8f1a40b56_*.py       — raw_sw_member + sw_member_monthly
  2d0c597983b9_*.py       — merge heads
  a9f3c2e17d84_*.py       — 放宽 raw_kpl_list NUMERIC 精度（已 apply）
```

---

## 常用命令

```bash
# 查 raw 数据覆盖率
uv run python scripts/check_raw_coverage.py

# 查所有表年度覆盖（快速）
uv run python -c "
from ifa.core.db import get_engine
from sqlalchemy import text
eng = get_engine()
with eng.connect() as c:
    for tbl in ['raw_moneyflow','raw_daily','factor_daily','sector_state_daily','sector_moneyflow_sw_daily']:
        try:
            rows = c.execute(text(f'''
                SELECT EXTRACT(YEAR FROM trade_date)::int, COUNT(DISTINCT trade_date)
                FROM smartmoney.{tbl} GROUP BY 1 ORDER BY 1
            ''')).fetchall()
            print(f'{tbl}: {dict(rows)}')
        except Exception as e:
            print(f'{tbl}: {e}')
"

# 重新拉 SW 成员（季度更新）
uv run python -c "
from ifa.families.smartmoney.etl.sw_member_fetcher import run_sw_member_full_refresh
from ifa.core.db import get_engine
print(run_sw_member_full_refresh(get_engine()))
"

# 生成晚报（手动模式）
uv run python -m ifa.cli report --family smartmoney --date 2026-04-30

# Alembic 迁移
uv run alembic upgrade head
uv run alembic current
```

---

## 已知问题 / 注意事项

1. **compute `'content_type'` / `'trade_date'` KeyError**: compute 阶段对历史数据报错，原因是某个 LLM 返回字段名不一致。B6 修 `evening.py` 时一并处理。

2. **DC sector codes vs SW sector codes**: DC 用 `BK*.DC`，SW 用 `801xxx.SI`，两套代码系统不互通。B3 之后统一走 SW，`raw_dc_member` 不再使用。

3. **kpl fallback**: `load_sector_structures()` 在 `stock_signals_daily` 为空时自动 fallback 到 `raw_kpl_list` 关键词匹配（已实现），是临时方案，C 阶段有真实 ML 信号后自然失效。

4. **`raw_dc_member`**: 只有 18 天近期数据，已弃用，勿用于任何历史分析。

5. **TuShare 无历史的表**: `raw_moneyflow_ind_dc`（2023起）、`raw_moneyflow_ind_ths`（2024起）、`raw_kpl_concept`（2024起）等，早年确实无数据，非 bug。B 阶段主路径不依赖这些表。

6. **`raw_moneyflow_hsgt` 缺口**: 北向资金数据略有缺口（每年约 5-10 天），是交易所不开放日（MSCI 审议等），正常现象。

7. **SME MVP1 market-structure 策略快照**: 新增 `ifa sme market-structure --date auto --json`。该解释器不只看指数涨跌，而是读取 SME 本地 SW L2 主力流、扩散、状态、全市场涨跌家数和成交额，输出流入/流出性质、拥挤风险、压制修复、一级/二级/脱敏/修复方向、资金状态和 1-3 个交易日情景推演。真实分时和外部变量暂不作为核心数据层依赖；外部变量用 `--external-summary` 注入，未来若面向客户展示需单独持久化来源、时间、模型和 prompt hash。

---

## TA Family — 当前状态附录（2026-05-04）

### Setup 库（M10 后）— 28 个 / 11 族

| 族 | Setups | 性质 |
|---|---|---|
| T 趋势 | T1 突破 · T2 回踩续涨 · T3 加速 | 做多 |
| P 回踩 | P1 MA20 · P2 缺口回补 · P3 紧密整理 | 做多 |
| R 反转 | R1 双底 · R2 头肩底 · R3 锤子线 | 做多 |
| F 形态 | F1 旗形 · F2 三角形 · F3 矩形 | 做多 |
| V 量价 | V1 量价齐升 · V2 缩量蓄势 | 做多 |
| S 板块 | S1 共振 · S2 跟风 · S3 补涨 | 做多 |
| C 筹码 | C1 集中 · C2 松动（警示） | 做多/警示 |
| **O 主力资金** (M10) | O1 机构连续抢筹 · O2 龙虎榜机构净买入 · O3 涨停封单结构 | 做多 |
| **D 顶部反转** (M10) | D1 双顶 · D2 头肩顶 · D3 流星线 | 警示（不进 Tier A/B） |
| **Z 统计** (M10) | Z1 极端 z-score · Z2 超卖反弹 | 做多 |
| **E 事件** (M10) | E1 业绩预告/快报/披露窗口催化 | 做多/警示（按 polarity） |

### M10 新增数据源 / ETL

- `ta.event_signal_daily`（PK: trade_date, ts_code, event_type）— 由 `ifa.families.ta.etl.event_etl.fetch_event_signals(client, engine, trade_date=...)` 拉 Tushare `forecast` / `express` / `disclosure_date`。已回填 2026-04-15 → 2026-04-30（157 行）。**尚未接入每日 ETL runner**（待 P1）。
- 复用既有：`smartmoney.raw_top_inst`（exalter='机构专用' AND net_buy>0 → O 族）、`raw_top_list`（net_amount/float_values 都在元单位）、`raw_kpl_list` + `raw_limit_list_d`（涨停封单 + 'Z' 炸板状态）、`raw_moneyflow`（5d super-large+large 净流入）。

### 单位陷阱（已踩过）

1. `raw_top_list.net_amount`、`float_values` **都已在元单位**，不需要 ×10000；
2. `raw_kpl_list.bid_amount` / `lu_bid_vol` 在最近数据中**全为 NULL**，应使用 `limit_order`（封单金额，元）+ `free_float`（自由流通市值，元）；
3. `raw_top_inst.side` 是 '0'/'1'（top-buy-list 还是 top-sell-list 的归属），**不是买/卖方向**；判断机构净买用 `exalter='机构专用' AND net_buy > 0`。

### 待办优先级（用户已确认 2026-05-04）

**P0 报告与产品向**
1. Q1 双轨 universe（long pool + risk pool）+ `warnings_daily`
2. Q7 ATR 三段位推荐价 entry/stop/target（持久化 + 显示）
3. Q3 Tier A=10/B=20 折叠重排,Tier C 不渲染 HTML
4. Q4 §13 风险扫描 → §11 表现归因之前;28 setup 聚光灯只展示今日有候选的
5. Q8.5 D 族在风险扫描里独立成节

**P1 调参前的工程基础**
6. Forward-return 自动 ETL（T+5 / T+10 / **T+15** — 主目标）
7. Walk-forward 回测引擎（独立于报告生成）
8. Q8.2 基本面二筛（市值 ≥30 亿 + 4 季 ROE 不全负）
9. Q8.3 集中度约束（TierA 同 L2 ≤ 4，TierB 同 L2 ≤ 6）
10. 黑天鹅 ETL（停牌 ✓ + 立案 + 重大重组 + ST 加帽 — 拉 Tushare `anns_d`）
11. 覆盖率监控 + 参数版本管理（v2.2 / v2.3 并存）
12. event_etl 接入每日 runner

**P2 调参（用户已要求 P2/P3 互换）**
13. 90d-IS / 252d-OOS walk-forward → 冻结 v2.3
14. Q8.1 setup 相关性去重（用历史命中算 setup-pair 相关矩阵）

**P3 装饰性历史回放**
15. 4 月 SmartMoney compute backfill + 报告生成 + TA 报告生成
16. 持仓状态机（hit entry / hit stop / hit target → `position_events_daily`）

### 关键文件索引

```
ifa/families/ta/
  setups/              ← 28 个 setup + base.py + scanner.py + ranker.py + context_loader.py
  regime/              ← classifier + transitions
  etl/event_etl.py     ← M10 新增（forecast / express / disclosure_date → ta.event_signal_daily）
  params/ta_v2.2.yaml  ← 当前生产参数
  report/              ← evening builder + templates + labels.py + llm_aug.py
  sector_phase_metrics.py ← 数据驱动 phase 评分（替代手调 map）

alembic/versions/c1d2e3f4g5h6_ta_event_signal_daily.py  ← 当前 head
```
