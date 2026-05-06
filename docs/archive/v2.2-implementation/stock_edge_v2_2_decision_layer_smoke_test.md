# Stock Edge v2.2 三周期决策层 Smoke Test

> 日期：2026-05-05  
> 本轮不训练、不调参、不正式补数据。

## 执行命令

```bash
uv run python -m py_compile \
  ifa/families/stock/decision_layer.py \
  ifa/families/stock/analysis.py \
  ifa/families/stock/backtest/labels.py \
  ifa/families/stock/report/builder.py \
  ifa/families/stock/report/runner.py \
  ifa/families/stock/report/markdown.py \
  ifa/families/stock/report/html.py

uv run pytest tests/stock/test_forward_labels.py tests/stock/test_analysis.py tests/stock/test_report.py -q

uv run pytest tests/stock -q
```

## 结果

| 检查项 | 结果 |
|---|---|
| py_compile | 通过 |
| 核心三周期测试 | 5 passed |
| 全量 `tests/stock` | 62 passed |
| `decision_5d` 存在 | 通过 |
| `decision_10d` 存在 | 通过 |
| `decision_20d` 存在 | 通过 |
| 三对象 JSON serialize | 通过 |
| required fields | 通过 |
| calibrated=false warning | 通过 |
| 无 intraday 时 5d 降级 | 通过，`decision_5d.data_quality.status = partial` |
| 无 Research/Fundamental 不阻塞 20d | 通过 |
| 旧 40d 不进入用户主决策 section | 通过 |
| token 泄露 | 未发现；本轮没有打印 env/token |

## 示例 JSON 摘要

```json
{
  "decision_5d": {
    "horizon": "5d",
    "horizon_label": "一周内短线",
    "decision": "wait",
    "user_facing_label": "等待回踩",
    "score": 0.5177,
    "score_type": "execution_score",
    "risk_level": "low",
    "confidence_level": "medium",
    "buy_zone": {"low": 14.87, "high": 15.09},
    "chase_warning_price": 16.18,
    "stop_loss": {"price": 14.63},
    "first_take_profit": {"low": 16.1, "high": 16.24},
    "probability_display_warning": "当前概率估计未经过 5/10/20 三周期正式校准，不能当作确定性上涨概率；主决策以 score、风险和价格执行为准。"
  },
  "decision_10d": {
    "horizon": "10d",
    "score_type": "swing_score"
  },
  "decision_20d": {
    "horizon": "20d",
    "score_type": "position_score"
  }
}
```

## 输出位置说明

本轮 smoke test 使用 pytest 临时目录验证 HTML/MD 渲染，不生成正式生产报告文件。正式 report runner 仍按 Stock Edge 既有输出策略写入 `settings.output_root` 下的：

```text
<IFA_OUTPUT_ROOT>/<run_mode>/<YYYYMMDD>/stock_edge/
```

研发/测试截图和 pytest 临时产物不进入生产输出。

