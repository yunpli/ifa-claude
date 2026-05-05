# Stock Edge v2.2 Global Preset Promotion

## 设计原则

global tuning 结果不能永远只作为 runtime JSON overlay。验证通过后，必须能生成可 review、可回滚、可审计的 YAML baseline patch。

同时，脚本不得静默自动改 YAML。

## 新命令

只生成 patch，不改 YAML：

```bash
uv run python scripts/stock_edge_promote_global_preset.py \
  --artifact /path/to/global_preset.json \
  --base-yaml ifa/families/stock/params/stock_edge_v2.2.yaml \
  --emit-patch
```

人工确认后应用到原 YAML，并生成 backup：

```bash
uv run python scripts/stock_edge_promote_global_preset.py \
  --artifact /path/to/global_preset.json \
  --base-yaml ifa/families/stock/params/stock_edge_v2.2.yaml \
  --apply \
  --backup
```

生成 YAML variant，不覆盖原 baseline：

```bash
uv run python scripts/stock_edge_promote_global_preset.py \
  --artifact /path/to/global_preset.json \
  --base-yaml ifa/families/stock/params/stock_edge_v2.2.yaml \
  --apply \
  --variant-output ifa/families/stock/params/stock_edge_v2.2.202605_global.yaml
```

## Patch 内容

Patch 文件包含：

- parameter path
- old value
- new value
- delta
- source artifact
- objective version
- objective score
- candidate count
- recommended_for_promotion

默认输出到：

```text
/Users/neoclaw/claude/ifaenv/manifests/stock_edge_global_promotion/<timestamp>/
```

## Promotion allowlist

只允许 global baseline 参数晋升：

- `aggregate.*`
- `smooth_scoring.*`
- `cluster_weights.*`
- `signal_weights.*`
- `risk.*`

不允许 single-stock overlay 晋升；`pre_report_overlay` artifact 会被拒绝。

## 回滚

`--apply --backup` 会生成 `.bak_<timestamp>`。回滚时恢复 backup 到 base YAML 后重新跑：

```bash
uv run pytest tests/stock -q
```

