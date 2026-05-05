# Stock Edge v2.2 Tuning Runtime Handoff

## 已实现

1. global preset 兼容时进入报告运行路径。
2. single overlay 基于 global-overlaid params 生成/复用。
3. single overlay 覆盖 global overlay。
4. `_runtime_tuning` 同时记录 global 与 single artifact。
5. artifact metrics 写入 `objective_version=stock_edge_5_10_20_v1`。
6. 进度输出包含 candidate idx、score、best_score、elapsed、ETA。
7. global promotion 支持 emit patch / apply / backup / variant。

## Smoke 结果

小规模 global dry-run：

```bash
PYTHONUNBUFFERED=1 uv run python scripts/stock_edge_global_preset.py \
  --as-of 2026-04-30 \
  --limit 10 \
  --max-candidates 3 \
  --progress-every 1 \
  --dry-run \
  --no-backfill-short-history
```

结果：

- 10 stocks
- 3 candidates
- progress 输出正常
- best score `0.2731`
- dry-run 未写 artifact

Patch emit smoke：

```bash
PYTHONUNBUFFERED=1 uv run python scripts/stock_edge_promote_global_preset.py \
  --artifact /Users/neoclaw/claude/ifaenv/models/stock/tuning/global_preset/__GLOBAL__/20260430.json \
  --base-yaml ifa/families/stock/params/stock_edge_v2.2.yaml \
  --emit-patch \
  --output-dir /Users/neoclaw/claude/ifaenv/manifests/stock_edge_global_promotion/smoke_20260505
```

输出：

- `/Users/neoclaw/claude/ifaenv/manifests/stock_edge_global_promotion/smoke_20260505/yaml_patch_candidate.yaml`
- `/Users/neoclaw/claude/ifaenv/manifests/stock_edge_global_promotion/smoke_20260505/yaml_patch_candidate.md`

## 测试结果

```bash
uv run pytest tests/stock -q
```

结果：

```text
68 passed
```

## 后续正式调参建议

1. 先跑 Top100 × 32 smoke，确认 objective 分布。
2. 再跑 Top500 × 256 coarse search。
3. 分 horizon 分别跑 5d/10d/20d weighting study。
4. 按 market regime / SW L2 / liquidity bucket 做 multiplier。
5. 做 calibration：isotonic / Platt / reliability curve。
6. 通过 OOS 后 emit YAML patch。
7. 人工 review 后 promotion 到 baseline 或 YAML variant。
8. 再跑 report smoke 和 `tests/stock`。

