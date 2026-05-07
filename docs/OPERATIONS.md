# iFA 系统运维手册

> **版本**：v1.0
> **更新**：2026-05-02
> **目标读者**：iFA 系统运维者（无需 ML 背景，会跑命令、看报告即可）

---

## 1. 系统总览

### 1.1 iFA 是什么

iFA（**i**ntelligent **F**inancial **A**dvisor）是一个 A 股研究/推荐报告自动生成系统。每天/每周/每月按既定时间表生成多种研究报告，用户/客户阅读 HTML 或 PDF 输出。

### 1.2 报告家族（family）总览

| Family | 报告时间 | 内容 |
|---|---|---|
| `market` | 工作日早 9:00 / 中 12:30 / 晚 17:00 | A 股盘前/盘中/盘后 + 主要指数 |
| `smartmoney` | 工作日晚 17:30 | 主力资金流（SW 板块）+ 龙头股 + ML 信号 |
| `macro` | 工作日晚 17:30 | 宏观经济（央行/财政/政策）+ 大类资产 |
| `briefing` | 工作日晚 19:00 | 主三辅简报合并 |
| `asset` | 工作日早 8:30 | 大类资产隔夜/盘前 |
| `tech` | 工作日早 8:30 | 板块技术面 + 龙头候选 |
| `weekend` | 周日晚 20:00 | 一周回顾 + 下周展望 |
| **`ningbo`** | 工作日晚 17:30 | **宁波派短线策略**（含 ★1-★5 共识矩阵 + 启发式 + 双轨 ML 推荐）|
| **`sme`** | 交易日晚 22:40 ETL / 23:10 简报 | **Smart Money Enhanced**（SW L2资金结构、主力/散户代理流、扩散状态、forward labels、客户简报）|

### 1.3 系统架构

```
┌─ TuShare API ──────────┐
│ raw_daily, daily_basic │
│ moneyflow, sw_member   │
└──────┬─────────────────┘
       │ ETL（每日）
       ▼
┌─ PostgreSQL ───────────┐
│ smartmoney.* (raw 数据)│
│ ningbo.candidates_daily│
│ ningbo.recommendations │
│ ningbo.model_registry  │
└──────┬─────────────────┘
       │ 计算 + 推理
       ▼
┌─ 报告生成器 ───────────┐
│ HTML / PDF 输出        │
└────────────────────────┘
```

### 1.4 这本手册怎么读

- **每天/每周日常**：看第 2 章
- **某个具体命令**：看第 3 章速查表
- **看到告警**：看第 4 章监控分级
- **出问题了**：看第 5 章应急手册
- **第一次部署**：看第 6 章

---

## 2. 日常运营节奏

### 2.1 工作日：每日时间表

| 时间 | 命令 | 预期耗时 |
|---|---|---|
| 早 8:00 | `ifa etl run --family all` | 5–15 min |
| 早 8:30 | `ifa asset overnight` + `ifa tech morning` | 各 ~3 min |
| 早 9:00 | `ifa market morning` | ~5 min |
| 中 12:30 | `ifa market midday` | ~3 min |
| 晚 17:00 | `ifa market evening` | ~5 min |
| 晚 17:30 | `ifa smartmoney evening` + `ifa macro evening` + **`ifa ningbo evening --scoring dual`** | 各 ~5–10 min |
| 晚 19:00 | `ifa briefing daily` | ~3 min |
| 晚 22:40 | `scripts/sme_incremental_2240.sh` | 交易日执行，非交易日结构化 skip |
| 晚 23:10 | `scripts/sme_briefing_2310.sh` | 生成观察当日的 SME 客户简报 |

**关键**：每个命令都会写 HTML 到 `~/ifaenv/out/<family>/<date>/`，看到「Report saved: ...」就成功了。

### 2.2 周末

| 时间 | 命令 | 备注 |
|---|---|---|
| 周日晚 20:00 | `ifa weekend report` | 一周复盘 |
| 周日晚 22:00 | **`ifa ningbo refresh weekly`** | ⭐ 模型重训 + 冠军挑战 |
| 周末 23:00+ | `scripts/sme_nightly_tune_2300.sh` | SME 调参 artifact / 可选 gated YAML promotion |

### 2.3 每月 1 号

| 时间 | 命令 | 目的 |
|---|---|---|
| 早 9:00 | `ifa ningbo refresh monthly` | 模型健康体检（10 分钟）|

### 2.4 每季度（1 月 / 4 月 / 7 月 / 10 月 1 号）

| 命令 | 目的 |
|---|---|
| `ifa ningbo refresh quarterly` | 重新评估 Kronos / TabNet 等先前被拒的模型族（30 分钟）|

---

## 3. 命令速查表

### 3.1 ningbo（重点 — 含模型管理）

```bash
# 日常
ifa ningbo evening --scoring dual --mode manual          # 晚报（双轨 + 共识矩阵）

# ML 治理
ifa ningbo refresh weekly                                # 周日：训练 + 冠军挑战
ifa ningbo refresh monthly                               # 月初：健康体检
ifa ningbo refresh quarterly                             # 季初：重审模型族

ifa ningbo registry status                               # 看当前 active 模型 + 历史
ifa ningbo registry promote aggressive <version>         # 手动晋升（紧急）
ifa ningbo registry rollback aggressive                  # 紧急回退

# 历史回填（一次性）
ifa ningbo backfill-candidates --start 2024-01-02 --end 2026-04-30
ifa ningbo candidate-outcomes --start 2024-01-02 --end 2026-04-30
ifa ningbo stats                                         # 看历史性能统计
```

### 3.2 market

```bash
ifa market morning           # 9:00 早盘
ifa market midday            # 12:30 中盘
ifa market evening           # 17:00 晚盘
```

### 3.3 smartmoney

```bash
ifa smartmoney evening       # 主报告
ifa smartmoney aux1          # 辅助 1
ifa smartmoney aux2          # 辅助 2
ifa smartmoney aux3          # 辅助 3
```

### 3.4 macro / asset / tech / briefing / weekend

```bash
ifa macro evening
ifa asset overnight
ifa tech morning
ifa briefing daily
ifa weekend report
```

### 3.5 sme

```bash
# 日常生产
scripts/sme_incremental_2240.sh
scripts/sme_briefing_2310.sh

# 状态与诊断
uv run python -m ifa.cli sme status --json
uv run python -m ifa.cli sme doctor --json

# 周末调参 artifact
SME_TUNE_START=2021-01-01 SME_TUNE_MIN_SAMPLE_DAYS=120 scripts/sme_nightly_tune_2300.sh
```

SME 脚本第一步检查交易日历。非交易日输出 `status=non_trade_day` / `action=skip` 并 exit 0，投递系统可以发简短跳过通知。

### 3.6 通用工具

```bash
# 数据库 ETL
ifa etl run --family all                                 # 拉所有 raw 数据
ifa etl run --family smartmoney --since 2026-04-01      # 增量

# 数据完整性检查
uv run python scripts/check_raw_coverage.py
```

---

## 4. 监控与告警

### 4.1 怎么知道报告生成成功

1. 看终端输出最后一行：`Report saved: /path/to/file.html`
2. 浏览器打开该文件，确认有内容
3. 或者：`ls ~/ifaenv/out/<family>/$(date +%Y%m%d)/` 看是否有当日文件

### 4.2 怎么知道 ningbo 模型还健康

```bash
# 看当前 active 模型 + 最近表现
ifa ningbo registry status

# 看本周 weekly refresh 报告
cat ~/ifaenv/out/ningbo/refresh_logs/$(date +%Y-%m-%d)_weekly.md

# 看本月 monthly health check
cat ~/ifaenv/out/ningbo/refresh_logs/$(date +%Y-%m-%d)_monthly.md
```

### 4.3 关键指标的正常范围

| 指标 | 正常范围 | 警戒值 | 危险值 |
|---|---|---|---|
| `aggressive` T5_Mean (近 30 天) | +1.5% ~ +3.5% | < 0% | < -1% 连续 60 天 |
| `conservative` Sharpe (近 30 天) | 0.20 ~ 0.40 | < 0.10 | < 0 连续 30 天 |
| `aggressive` 胜率 | 45% ~ 60% | < 35% | < 30% 连续 30 天 |
| `conservative` 最大回撤 | < -55% | < -65% | < -75% |

### 4.4 告警分级

- 🟢 **绿**：所有指标正常 → 无操作
- 🟡 **黄**：1 个指标进入警戒 → 看本月 health report，下周关注是否改善
- 🟠 **橙**：2+ 指标警戒 / 1 个进入危险 → 跑 `monthly` + `quarterly` 重审，考虑手动晋升其他模型
- 🔴 **红**：active 模型连续 60 天负收益 → **立即回退到 heuristic**，给开发者发邮件

---

## 5. 应急手册

### 5.1 报告生成失败

```bash
# 查最近一次失败日志
tail -200 ~/ifaenv/logs/$(ls -t ~/ifaenv/logs/ | head -1)

# 常见原因 + 修复
# 1. 数据库连不上 → 重启 postgres: bash scripts/postgres-start.sh
# 2. TuShare 限流  → 等 30 分钟重试
# 3. LLM 超时     → 切换 fallback：环境变量 LLM_USE_FALLBACK=true
```

### 5.2 ningbo 模型表现持续下滑

```bash
# 1. 跑健康体检看具体哪个 slot 不行
ifa ningbo refresh monthly

# 2. 看历史版本，找一个表现更好的回退
ifa ningbo registry status

# 3. 紧急回退到上一个 active 版本
ifa ningbo registry rollback aggressive
ifa ningbo registry rollback conservative

# 4. 如果回退后仍不行 → 强制 active 设为 heuristic（保底）
# (heuristic 永远在线，不会失效)

# 5. 联系开发者
```

### 5.3 数据缺失（TuShare API 故障）

```bash
# 检查最近一次 ETL 是否成功
ifa etl status

# 重跑（如果失败原因已修复）
ifa etl run --family all --since $(date -v-3d +%Y-%m-%d)

# 如果 TuShare 长时间故障 → 暂停所有报告（避免基于残缺数据出报告）
# 手工 disable cron jobs
```

### 5.4 LLM API 超时 / 熔断

```bash
# 临时切到 fallback LLM
export LLM_USE_FALLBACK=true
ifa <family> <slot>   # 重跑

# 如果 fallback 也挂 → 报告会用模板降级（无 LLM 叙述），仍能出
```

### 5.5 紧急回退（模型）

```bash
ifa ningbo registry rollback aggressive
ifa ningbo registry rollback conservative

# 验证
ifa ningbo registry status
```

### 5.6 联系开发者 checklist

发邮件/IM 时附：
1. 出错的命令（完整 cmdline）
2. 终端最后 50 行输出
3. `~/ifaenv/logs/` 最近 1 小时的日志
4. `ifa ningbo registry status` 输出（如果是 ningbo 问题）
5. 最近一次 weekly/monthly refresh 报告

---

## 6. 部署与升级

### 6.1 初次部署清单

```bash
# 1. 装环境
brew install uv postgresql@16
git clone <repo> && cd ifa-claude
uv sync

# 2. 启动 PostgreSQL
bash scripts/postgres-bootstrap.sh    # 第一次
bash scripts/postgres-start.sh         # 之后

# 3. 配置环境变量
cp .env.example .env
# 编辑 .env：填入 TUSHARE_TOKEN, LLM_PRIMARY_API_KEY, PG_PASSWORD

# 4. 跑 alembic 迁移
uv run alembic upgrade head

# 5. 历史数据回填（一次性，几小时）
uv run python scripts/fast_backfill.py
uv run python -m ifa.cli ningbo backfill-candidates --start 2024-01-02 --end <today>
uv run python -m ifa.cli ningbo candidate-outcomes --start 2024-01-02 --end <today>

# 6. 第一次 weekly refresh（bootstrap 模型 registry）
uv run python -m ifa.cli ningbo refresh weekly

# 7. 验证
uv run python -m ifa.cli ningbo registry status
uv run python -m ifa.cli ningbo evening --scoring dual --mode test
```

### 6.2 调度推荐（cron / launchd）

#### Linux / macOS unix cron

```cron
# /etc/cron.d/ifa  (crontab -e)

# 每日 ETL（早 8:00）
0 8 * * 1-5  cd /path/to/ifa && /usr/local/bin/uv run ifa etl run --family all >> ~/ifaenv/logs/cron.log 2>&1

# 早盘 8:30
30 8 * * 1-5  cd /path/to/ifa && /usr/local/bin/uv run ifa asset overnight && /usr/local/bin/uv run ifa tech morning

# 9:00
0 9 * * 1-5  cd /path/to/ifa && /usr/local/bin/uv run ifa market morning

# 12:30
30 12 * * 1-5 cd /path/to/ifa && /usr/local/bin/uv run ifa market midday

# 17:00
0 17 * * 1-5  cd /path/to/ifa && /usr/local/bin/uv run ifa market evening

# 17:30 (并行)
30 17 * * 1-5 cd /path/to/ifa && /usr/local/bin/uv run ifa smartmoney evening &
30 17 * * 1-5 cd /path/to/ifa && /usr/local/bin/uv run ifa macro evening &
30 17 * * 1-5 cd /path/to/ifa && /usr/local/bin/uv run ifa ningbo evening --scoring dual

# 19:00
0 19 * * 1-5  cd /path/to/ifa && /usr/local/bin/uv run ifa briefing daily

# 周日晚 22:00 — ningbo 模型重训
0 22 * * 0  cd /path/to/ifa && /usr/local/bin/uv run ifa ningbo refresh weekly

# 每月 1 号早 9:00 — 健康体检
0 9 1 * *  cd /path/to/ifa && /usr/local/bin/uv run ifa ningbo refresh monthly

# 每季度（1/4/7/10 月 1 号）早 9:30 — 架构评审
30 9 1 1,4,7,10 *  cd /path/to/ifa && /usr/local/bin/uv run ifa ningbo refresh quarterly
```

#### macOS launchd（如果不想用 cron）

可以参考 Apple 的 [launchd plist 文档](https://www.launchd.info/) 自行配置。
本手册暂不提供完整 plist，建议用 unix cron（更简单透明）。

### 6.3 版本升级流程

```bash
# 1. 备份当前模型 + DB
cp -r ~/ifaenv/models /tmp/models_backup_$(date +%Y%m%d)
pg_dump -h 127.0.0.1 -p 55432 -U ifa ifavr | gzip > /tmp/db_backup_$(date +%Y%m%d).sql.gz

# 2. 拉新代码
git pull
uv sync

# 3. 跑 alembic 迁移（会列出待应用的 migration）
uv run alembic upgrade head

# 4. 跑一次手动 evening 验证
ifa ningbo evening --scoring dual --mode test --report-date $(date -v-1d +%Y-%m-%d)

# 5. 如果 OK，让 cron 自动接管。
#    如果有问题：git revert + alembic downgrade，恢复 DB 备份
```

### 6.4 跨机器迁移

```bash
# 老机器
pg_dump -h 127.0.0.1 -p 55432 -U ifa ifavr | gzip > ifa_db.sql.gz
tar czf ifa_models.tar.gz ~/ifaenv/models ~/ifaenv/embeddings
tar czf ifa_repo.tar.gz /path/to/ifa-claude

# 新机器
# 1. 部署 PostgreSQL（参考 6.1）
gunzip -c ifa_db.sql.gz | psql -h 127.0.0.1 -p 55432 -U ifa ifavr
mkdir -p ~/ifaenv && tar xzf ifa_models.tar.gz -C ~/  # 注意路径
tar xzf ifa_repo.tar.gz
cd ifa-claude && uv sync
# 2. 验证
ifa ningbo registry status
```

---

## 7. 故障排查 FAQ

**Q：晚报时间到了但报告没生成**
A：`tail -200 ~/ifaenv/logs/<latest>.log` 看最后报错。常见：DB 连接超时（重启 postgres）、TuShare 限流（等 30 分钟）、LLM 超时（切 fallback）。

**Q：weekly refresh 跑出 NO CHANGE，是不是模型坏了？**
A：不是。NO CHANGE 表示「新训练的模型没有显著好于当前 active」，**保持现状是正确决定**（不动比乱动好）。只有连续 4 周都 NO CHANGE 且 active 表现下滑才需关注。

**Q：T5_Mean 和 Sharpe 哪个更重要？**
A：取决于客户偏好。我们设计了双轨：`aggressive` 优化 T5_Mean，`conservative` 优化 Sharpe。客户可自由选用。

**Q：★★★★★ 一定能赚吗？**
A：不能。★★★★★ 是 *3 个独立模型一致看好*，是高 conviction 信号但**不是确定信号**。短线市场有不可预测因素（个股利空、指数熊熊回吐）。建议作为权重提升，不是 all-in。

**Q：模型训练用了多久数据？**
A：当前 2024-01-02 起至今，约 2.5 年。每周 refresh 用最近 6 个月作 OOS 评估。后续会随时间自然累积。

**Q：能不能加新策略（除了神枪手/聚宝盆/半年翻倍）？**
A：技术上可以，但需要开发者修改代码 + 重新跑 backfill。运维者不能直接操作。

---

## 附录 A：环境变量清单

| 变量 | 必填 | 说明 |
|---|---|---|
| `TUSHARE_TOKEN` | ✅ | TuShare API token |
| `LLM_PRIMARY_API_KEY` | ✅ | 主 LLM API key |
| `LLM_PRIMARY_BASE_URL` | ✅ | 主 LLM 端点 |
| `LLM_FALLBACK_API_KEY` | ✅ | 备用 LLM key |
| `PG_PASSWORD` | ✅ | PostgreSQL 密码 |
| `IFA_RUN_MODE` | 否 | `test` / `manual` / `production`，默认 `manual` |
| `IFA_OUTPUT_ROOT` | 否 | 报告输出根目录，默认 `~/ifaenv/out` |
| `IFA_LOG_ROOT` | 否 | 日志根目录，默认 `~/ifaenv/logs` |
| `LLM_USE_FALLBACK` | 否 | 强制走备用 LLM，默认 `false` |

## 附录 B：数据库 schema 速查

```
smartmoney 库（raw 数据 + 计算层）
├── raw_daily, raw_daily_basic, raw_moneyflow ...
├── sw_member_monthly                    # SW 板块成员月度快照
├── sector_moneyflow_sw_daily            # SW L2 板块资金流
├── trade_cal                            # 交易日历
└── factor_daily, sector_state_daily ... # 计算层

ningbo 库（短线策略 + ML）
├── candidates_daily                     # 全部策略命中（每天 ~310 条）
├── candidate_outcomes                   # 15 天前向标签
├── recommendations_daily                # 每日 top-10 推荐（含 3 个 scoring_mode）
├── recommendation_tracking              # 每日追踪每条推荐的实时表现
├── recommendation_outcomes              # 推荐的最终 outcome
├── strategy_params                      # 策略参数版本
├── model_registry                       # ML 模型注册表（active per slot）
└── promotion_log                        # 模型晋升历史
```

## 附录 C：日志位置

```
~/ifaenv/
├── logs/                       # 运行日志（按日期）
├── out/                        # 报告 HTML 输出
│   ├── market/<date>/
│   ├── smartmoney/<date>/
│   ├── ningbo/<date>/
│   └── ningbo/refresh_logs/   # weekly/monthly/quarterly 报告
├── models/
│   └── ningbo/<version>/      # ML 模型 artifact
└── embeddings/
    └── ningbo/kronos_small_v1/  # Kronos 缓存（如启用）
```

---

**文档结束** | 有问题查 GitHub issues 或联系开发者
