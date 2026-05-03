# 一主三辅 Deep Dive — Main + 3 Auxiliary Reports

> **状态**：V2.1.3 — 4 个 family 完整可用
> **核心模式**：3 辅先跑（macro / asset / tech）→ 主报告（market）汇总验证

---

## 1. 系统理念

A 股短线决策需要多视角：
- **宏观**（macro）— 央行 / 财政 / 政策对市场流动性的影响
- **跨资产**（asset）— 美股 / 黄金 / 油价 / 商品对 A 股的传导
- **技术**（tech）— 板块强弱 / 龙头候选 / 资金分层
- **主线**（market）— 综合上述 + 当日实盘 → 投资动作

iFA 的「一主三辅」让 4 个独立 agent 各跑各的视角，最后由 main 报告整合。

---

## 2. 4 个 family 概览

| Family | 主导 | 时段 | 输出特色 |
|---|---|---|---|
| **macro** | 政策 / 流动性 | morning + evening | DR007 / 央行公开市场 / 财政转移支付 / 央行行长讲话 |
| **asset** | 跨资产传导 | morning + evening | 美股 / 黄金 / 油价 / 铜 / 大豆 / 螺纹钢 → A 股传导链 |
| **tech** | 板块技术面 | morning + evening | SW L1 强弱 + 五层蛋糕（启动/加速/高潮/衰退/冷却）+ 龙头候选 |
| **market** | 主线汇总 | **morning + noon + evening** | 综合三辅 + 实盘 + 假设/验证/复盘三段叙事 |

---

## 3. Schedule（北京时间）

### 3.1 工作日早盘
```
08:00  macro  morning   ── 隔夜全球宏观
08:15  asset  morning   ── 大类资产隔夜/盘前
08:30  tech   morning   ── 板块技术晨报
08:45  market morning   ── A 股早报（综合三辅 + 假设设定）
```

### 3.2 工作日中盘（仅 main）
```
12:30  market noon      ── 上半场复盘 + 下半场展望
```

### 3.3 工作日晚盘
```
17:00  macro  evening   ── 央行 / 财政 / 政策晚报
17:15  asset  evening   ── 大类资产收盘 + 传导评估
17:30  tech   evening   ── 板块复盘 + 次日展望
17:45  market evening   ── A 股晚报（综合三辅 + 实盘 + 假设回看 + 次日观察）
```

**为什么主报告最后跑？** market 报告的 §6 (`three_aux_summary`) 会引用三辅当时的 verdict，所以三辅必须先完成入库。

---

## 4. 数据流

```
TuShare API
   │
   ▼
smartmoney.raw_*（共享）
   │       │       │       │
   ▼       ▼       ▼       ▼
 macro    asset   tech   market
   │       │       │       │
   └───→ public.report_runs / report_sections / report_judgments ←─┘
                                  │
                                  ▼
                            HTML / PDF 输出
```

每次 family.evening 运行：
1. 创建 `ReportRun` 行
2. 调 LLM 生成各 section 的 `content_json`（结构化数据 + 中文叙事）
3. 写入 `report_sections`（每个 section 一行）
4. 「假设」类 section 写入 `report_judgments`（次日 evening 会回看）
5. Render Jinja 模板 → HTML
6. 可选 `--generate-pdf` → headless Chrome 打印 PDF

---

## 5. 共享基础设施

所有 4 个 family 共用：

### 5.1 `ifa.core.report.*`
- `ReportRun` — 数据库行模型
- `insert_report_run` / `insert_section` / `finalize_report_run` — 写入辅助
- `parse_bjt_cutoff` — cutoff 时间转换（BJT → UTC datetime）
- `output_dir_for_run` — 计算输出路径
- `disclaimer.py` — 共享中英对照 disclaimer 段落

### 5.2 `ifa.core.render.*`
- `HtmlRenderer` — Jinja 渲染
- `templates/report.html` — 主模板（按 `s.type` 路由到 `_<section_type>.html`）
- 各 family 的 `_<family>_*.html` — section-specific 模板

### 5.3 `ifa.core.llm.LLMClient`
- 主 LLM + fallback LLM 自动切换
- 失败时模板降级（保证报告能出，只是少叙事）
- `prompt_version` 写入 `report_runs` 表，便于 A/B 追溯

### 5.4 `ifa.core.db.get_engine`
- SQLAlchemy 2.0 engine
- 按 `IFA_RUN_MODE`（test / manual / production）选择不同 DB

---

## 6. CLI 命令参考

```bash
# 一主三辅日常
ifa generate macro  --slot morning|evening   --mode production --generate-pdf
ifa generate asset  --slot morning|evening   --mode production --generate-pdf
ifa generate tech   --slot morning|evening   --mode production --generate-pdf
ifa generate market --slot morning|noon|evening --mode production --generate-pdf

# 加 cutoff 时间（默认是各 slot 的标准时间，可显式 override）
ifa generate market --slot morning --cutoff-time 09:00 --mode production
```

---

## 7. 跨 family 引用

### 7.1 market evening §6 `three_aux_summary`
读取当天三辅 evening 的 `report_judgments` 表，提取每个的「verdict」字段（一行结论），合成：

```
SmartMoney: 主力净流入 12 亿，电子 / 有色 拥挤
Tech:       五层蛋糕 加速 / 高潮 占 60%（看多）
Asset:      美元偏强 + 铜回调 → A 股汇率压力
```

如果某个 aux 当天还未生成，会显示 "—"。所以 schedule 必须保证三辅在 main 之前完成。

### 7.2 market morning 的「假设」会被 evening 回看
- morning 写 `report_judgments` 类型 = `morning_hypothesis`
- evening §7 `review_table` 自动 query 这些假设并对比当日实盘表现
- 形成「假设 → 验证 → 复盘」闭环

---

## 8. Run modes 对 4 个 family 的影响

| Mode | 数据库 | 输出根 | 用途 |
|---|---|---|---|
| `test` | `ifavr_test` | `~/ifaenv/out/test/` | 主三辅测试 |
| `manual` | `ifavr` | `~/ifaenv/out/manual/<date>/` | 手动 / 周末测试 |
| `production` | `ifavr` | `~/ifaenv/out/production/<date>/` | 自动调度 |

**注意**：smartmoney 和 ningbo 不用 `ifavr_test`（test DB 数据稀疏，无法支撑），它们建议用 `manual` 或 `production`。

详见 [`run-modes.md`](./run-modes.md)。

---

## 9. 故障排查

| 症状 | 解决 |
|---|---|
| market evening §6 全是 "—" | 三辅当天还没跑或失败，先跑三辅再 重跑 main |
| market evening §7 review_table 空 | morning 报告没生成或生成失败，缺乏假设原料 |
| LLM 超时 | 设置 `LLM_USE_FALLBACK=true` 强制走备用 LLM |
| 报告生成成功但 PDF 失败 | 检查 headless Chrome 可用性（`brew install --cask chromium`），见 [`pdf-tool.md`](./pdf-tool.md) |
| 三辅同时报错 "trade_cal 缺数据" | `uv run python scripts/is_trading_day.py --refresh` 刷新 trade_cal |

---

## 10. 文件结构

```
ifa/families/
├── _shared/                # macro/asset/tech 共享代码
├── macro/
│   ├── morning.py
│   ├── evening.py
│   └── data.py
├── asset/
│   ├── morning.py
│   ├── evening.py
│   └── data.py
├── tech/
│   ├── morning.py
│   ├── evening.py
│   └── data.py
├── market/
│   ├── morning.py
│   ├── noon.py
│   ├── evening.py
│   └── data.py
└── ...

ifa/core/
├── db.py                   # SQLAlchemy engine
├── llm.py                  # LLMClient
├── report/
│   ├── run.py              # ReportRun, insert_*
│   ├── disclaimer.py       # 共享中英 disclaimer
│   ├── timezones.py        # parse_bjt_cutoff
│   └── output.py           # output_dir_for_run
├── render/
│   ├── html.py             # HtmlRenderer
│   ├── pdf.py              # html_to_pdf (Chrome headless)
│   └── templates/          # Jinja templates
└── tushare.py              # TuShare API client
```

---

完整运维流程见 [`OPERATIONS.md`](./OPERATIONS.md)。每个 family 的 section 详细列表见 [`family-reference.md`](./family-reference.md)。
