# Stock Edge Weekly Tune Runbook

> **范围**: 文档示例，不在 codebase 内实际运行（stay-dev-not-ops）。
>
> **目标**: 周末/周一凌晨自动重调 Stock Edge 参数；gates 通过则覆盖 `stock_edge_v2.2.yaml`，写 backup + ledger，并 git commit + push。

---

## 1. 触发节奏

| 节奏 | 内容 | 备注 |
|---|---|---|
| 每周日 23:00 BJT | 完整 panel rebuild + K-fold + auto-gate + apply-to-baseline | A 股周一开盘前完成 |
| 每月 15 日 04:00 BJT | 同上但 panel 更大（Top 200 × 20 dates） | 更稳健，不阻塞每周节奏 |
| 季度末 | 手动 review 累积 ledger，检查参数漂移趋势 | 不自动化 |

---

## 2. 一行核心命令

```bash
cd /path/to/ifa-claude
git pull --ff-only origin main
ifa stock tune \
    --top 100 \
    --pit-samples 16 \
    --max-candidates 768 \
    --n-iterations 3 \
    --workers 4 \
    --k-fold 4 \
    --val-dates-per-fold 2 \
    --min-train-dates 4 \
    --bootstrap-iterations 1000 \
    --search-algo tpe \
    --successive-halving \
    --auto-promote \
    --apply-to-baseline
```

执行流程（若 gates 通过）:

1. Panel 从本地 PostgreSQL 重建 → cache 到 `/Users/neoclaw/claude/ifaenv/data/stock/replay_panels/`
2. K-fold rolling walk-forward × 4 folds
3. 每 fold 用 Optuna TPE + successive halving 搜索 768 candidates
4. 全 9 gate 评估 (G1..G9)
5. Horizon-selective: 仅通过 per-horizon gates 的 horizon weights 写入 YAML
6. 自动备份 `ifa/families/stock/params/stock_edge_v2.2.yaml.bak_<timestamp>`
7. 用 promoted YAML 覆盖 baseline
8. 写一行到 `stock.tuning_promotion_log`（audit ledger）
9. 输出 git_tag 字符串（例 `stock-edge-tune-20260512-040523-20d`）

---

## 3. 完整 weekly_tune.sh 脚本示例

> 放在 `/Users/neoclaw/claude/ifaenv/scripts/weekly_tune.sh`，不在 repo 内。

```bash
#!/usr/bin/env bash
set -euo pipefail

REPO=/path/to/ifa-claude
LOG_DIR=/Users/neoclaw/claude/ifaenv/logs/stock_edge_tune
STAMP=$(date +%Y%m%d_%H%M%S)
LOG_FILE="$LOG_DIR/weekly_tune_${STAMP}.log"

mkdir -p "$LOG_DIR"
cd "$REPO"

{
  echo "=== Weekly tune started at $(date) ==="

  # 1. Pull latest (don't lose other team work)
  git fetch origin main
  git pull --ff-only origin main

  # 2. Run tune
  PYTHONUNBUFFERED=1 uv run python -m ifa.cli stock tune \
      --top 100 \
      --pit-samples 16 \
      --max-candidates 768 \
      --workers 4 \
      --k-fold 4 \
      --val-dates-per-fold 2 \
      --min-train-dates 4 \
      --bootstrap-iterations 1000 \
      --search-algo tpe \
      --successive-halving \
      --auto-promote \
      --apply-to-baseline \
      2>&1 | tee -a "$LOG_FILE"

  # 3. Check whether the YAML actually changed
  if git diff --quiet ifa/families/stock/params/stock_edge_v2.2.yaml; then
    echo "=== No YAML changes (gates rejected). Exit clean. ==="
    exit 0
  fi

  # 4. Verify tests still pass with new YAML
  uv run pytest tests/stock -q

  # 5. Read the most recent ledger row for tag + horizons
  LEDGER=$(uv run python - <<'PY'
from ifa.core.db import get_engine
from sqlalchemy import text
eng = get_engine()
with eng.connect() as c:
    row = c.execute(text("""
        SELECT git_tag, horizons_applied, horizons_kept_baseline
        FROM stock.tuning_promotion_log
        ORDER BY id DESC LIMIT 1
    """)).fetchone()
    if row:
        print(f"{row[0]}|{row[1]}|{row[2]}")
PY
)
  GIT_TAG=$(echo "$LEDGER" | cut -d'|' -f1)
  HORIZONS=$(echo "$LEDGER" | cut -d'|' -f2)

  # 6. Commit + tag + push
  git add ifa/families/stock/params/stock_edge_v2.2.yaml
  git add ifa/families/stock/params/stock_edge_v2.2.yaml.bak_*
  git commit -m "tune(stock-edge): weekly auto-promote — horizons $HORIZONS

[autotune] gate-passed weekly tuning at $STAMP
ledger tag: $GIT_TAG"
  git tag "$GIT_TAG"
  git push origin main --follow-tags

  echo "=== Weekly tune completed at $(date) — applied: $HORIZONS ==="
} 2>&1 | tee -a "$LOG_FILE"
```

---

## 4. macOS launchd plist（推荐 — 用户在 Mac M1）

> 放在 `~/Library/LaunchAgents/com.user.stock_edge_weekly.plist`。

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.user.stock_edge_weekly</string>
    <key>ProgramArguments</key>
    <array>
        <string>/Users/neoclaw/claude/ifaenv/scripts/weekly_tune.sh</string>
    </array>
    <key>StartCalendarInterval</key>
    <dict>
        <!-- 每周日 23:00 PDT (周一 14:00 BJT) -->
        <key>Weekday</key><integer>0</integer>
        <key>Hour</key><integer>23</integer>
        <key>Minute</key><integer>0</integer>
    </dict>
    <key>StandardOutPath</key>
    <string>/Users/neoclaw/claude/ifaenv/logs/stock_edge_tune/launchd.stdout.log</string>
    <key>StandardErrorPath</key>
    <string>/Users/neoclaw/claude/ifaenv/logs/stock_edge_tune/launchd.stderr.log</string>
</dict>
</plist>
```

加载/卸载:
```bash
launchctl load ~/Library/LaunchAgents/com.user.stock_edge_weekly.plist
launchctl list | grep stock_edge
launchctl unload ~/Library/LaunchAgents/com.user.stock_edge_weekly.plist
```

---

## 5. Linux 替代（systemd timer）

> 仅供参考；当前部署是 macOS。

`/etc/systemd/system/stock-edge-tune.service`:
```ini
[Unit]
Description=Stock Edge weekly tune

[Service]
Type=oneshot
ExecStart=/path/to/weekly_tune.sh
User=neoclaw
WorkingDirectory=/path/to/ifa-claude
```

`/etc/systemd/system/stock-edge-tune.timer`:
```ini
[Unit]
Description=Run stock-edge tune every Sunday 23:00

[Timer]
OnCalendar=Sun 23:00
Persistent=true

[Install]
WantedBy=timers.target
```

启用：
```bash
sudo systemctl enable --now stock-edge-tune.timer
systemctl list-timers | grep stock-edge
```

---

## 6. 失败处理 / 回滚

### 6.1 auto-promote 拒绝（gates 不通过）

正常情况，无需干预。检查 reject 报告：

```bash
ls -t /Users/neoclaw/claude/ifaenv/manifests/stock_edge_global_promotion/rejected/ | head -3
cat /Users/neoclaw/claude/ifaenv/manifests/stock_edge_global_promotion/rejected/rejected_*.json | jq '.gates'
```

### 6.2 promoted YAML 引入回归

观察生产报告质量后发现 YAML 不好，回滚：

```bash
# 找最近一次自动 backup
ls -t ifa/families/stock/params/stock_edge_v2.2.yaml.bak_* | head -3

# 回滚到最近的 backup
cp ifa/families/stock/params/stock_edge_v2.2.yaml.bak_<timestamp> \
   ifa/families/stock/params/stock_edge_v2.2.yaml

# 验证 + commit + push 回滚
uv run pytest tests/stock -q
git add ifa/families/stock/params/stock_edge_v2.2.yaml
git commit -m "revert(stock-edge): rollback to <timestamp> — autotune $tag introduced X regression"
git push origin main
```

### 6.3 cron 跑挂了

```bash
# 找最新 log
ls -t /Users/neoclaw/claude/ifaenv/logs/stock_edge_tune/*.log | head -1
# 看错误
tail -50 /Users/neoclaw/claude/ifaenv/logs/stock_edge_tune/launchd.stderr.log
```

常见失败：
- DB 连接断 → 检查 `pg_isready -p 55432`
- 磁盘满 → `/Users/neoclaw/claude/ifaenv/data/stock/replay_panels/` 老 parquet 清理
- TuShare backfill 限流 → log 里看 `429` 或 `rate limit`

---

## 7. 监控查询

### 7.1 最近 promotion 历史

```sql
SELECT
    created_at,
    accepted,
    horizons_applied,
    horizons_kept_baseline,
    git_tag,
    -- gates_summary 是 JSONB
    jsonb_array_length(gates_summary) AS n_gates,
    (SELECT COUNT(*) FROM jsonb_array_elements(gates_summary) g WHERE (g->>'passed')::bool) AS n_pass
FROM stock.tuning_promotion_log
ORDER BY created_at DESC
LIMIT 20;
```

### 7.2 Per-horizon 通过率（过去 4 周）

```sql
WITH recent AS (
    SELECT * FROM stock.tuning_promotion_log
    WHERE created_at >= NOW() - INTERVAL '28 days'
)
SELECT
    horizon,
    COUNT(*) FILTER (WHERE horizon = ANY(SELECT jsonb_array_elements_text(horizons_applied))) AS times_applied,
    COUNT(*) FILTER (WHERE horizon = ANY(SELECT jsonb_array_elements_text(horizons_kept_baseline))) AS times_kept_baseline
FROM recent, UNNEST(ARRAY['5d','10d','20d']) AS horizon
GROUP BY horizon
ORDER BY horizon;
```

### 7.3 参数漂移趋势

```sql
-- 看每次 promote 后 decision_layer.horizons.20d.weights 的变化
-- (需要 join 到 git history 才能完整 reconstruct，这里只能看 ledger 元信息)
SELECT created_at, git_tag, horizons_applied
FROM stock.tuning_promotion_log
WHERE accepted = true OR jsonb_array_length(horizons_applied) > 0
ORDER BY created_at DESC LIMIT 30;
```

---

## 8. 干跑（dry-run）测试 cron 命令

在真正部署 launchd 之前，手动跑一次完整流程并加 `--dry-run`：

```bash
# Tune 但不写 YAML、不 commit、不 git push
ifa stock tune \
    --top 100 --pit-samples 16 --k-fold 4 \
    --search-algo tpe --successive-halving \
    --auto-promote --dry-run \
    --variant-output /tmp/dryrun_test.yaml
```

确认输出 + ledger 行写入 + variant YAML 内容合理后，再去掉 `--dry-run` 并加 `--apply-to-baseline`。

---

## 9. 与其他 family 的协调

| Family | 调参频率 | 依赖关系 |
|---|---|---|
| **Stock Edge** | 每周（本 runbook） | YAML baseline = 唯一权威，单层 per-stock overlay |
| **TA Family** | 月度（封版后基本不动） | 用 `ta walk-forward` CLI（独立 codebase 路径） |
| **Research** | 季度 | 主要因子提取，参数变化少 |
| **SmartMoney** | 季度（v2.1.3 封版） | RF/XGB 模型重训单独节奏 |

每周 Stock Edge 调参不影响其他 family 的 YAML。

---

## 10. 关键不变量（每次 cron 跑前后必满足）

1. ✅ `stock_edge_v2.2.yaml` 始终 git-tracked（不是 untracked / generated 文件）
2. ✅ 每次 promote 都有 `.bak_<timestamp>` backup 文件
3. ✅ 每次 promote 都有 `stock.tuning_promotion_log` 行
4. ✅ `git tag` 与 ledger 的 `git_tag` 字段对齐
5. ✅ `tests/stock` 在 commit 前必须通过 (cron 脚本 step 4)
6. ✅ 个股 overlay 永远不污染 baseline YAML
7. ✅ TA / Research / SmartMoney 的 YAML 不被 Stock Edge cron 触碰

---

*Living document — 修改 cron 调用方式时更新本文。*
