# Outstanding Work — Master Task List

**Two parallel workstreams**:
1. **Stock Edge tuning** — 调参主工作（前期遗留，10/12 done）
2. **UI bug fixes** — 4/30 baseline review 发现的 51 个 UI bugs

**Source documents**:
- Stock Edge: `docs/stock_edge_tuning_work_list.md` (living plan)
- UI bugs: GitHub issue [#16](https://github.com/yunpli/ifa-claude/issues/16)

**Strategy**: Phase order = **S → E → L → T → C → V** (Stock Edge precursor, then UI: Engineering, LLM prompts, Templates/Builder, CSS, Verification). Each phase ends with a baseline regen / equivalent acceptance + diff against prior version.

Stock Edge first because:
- It's the longest-running deferred work (Tier 3 still has T3.2/T3.3 + a known engineering precondition)
- Production-impacting (controls 5d/10d/20d decision scores)
- Independent from UI work — can be parallelized once decision is made
- The "调代理不调生产" precondition (S0 below) blocks all downstream tuning until verified

Or **alternative scheduling**:
- If UI fixes are user-facing-urgent, do UI first (Phase E-V), Stock Edge second
- If Stock Edge tuning has an analyst pipeline waiting, do it in parallel

User to choose.

**Acceptance gate per phase**:
1. All tasks in phase done + commit pushed
2. Re-generate full 9-report 4/30 baseline (UI phases) OR pass tune gates (Stock Edge phase)
3. Spot-check vs prior version (compare HTML diff / lift metrics)
4. User sign-off before next phase

---

## Phase S — Stock Edge tuning (3 tasks · est. 3-4 days)

Source: `docs/stock_edge_tuning_work_list.md`. 10/12 done (Tier 1+2 complete, Tier 3 partial). Remaining:

| # | ID | Task | Acceptance |
|---|---|---|---|
| **S0** | (precondition) | **Verify "optimizer 调代理不调生产" bug** is fixed (memory: `project_stock_edge_optimizer_surrogate_bug.md`). Before this is verified, ANY tuning result is suspect because optimizer's `_evaluate_overlay` may still be optimizing a 9-term tanh surrogate instead of `compute_strategy_matrix → build_decision_layer`. | After: cite specific commit / file / function showing optimizer wires to production decision layer. If still broken, FIX FIRST before S1/S2. |
| S1 | T3.2 | ML 模型跨日期复用（2 days complex） — 同股票邻近 PIT 日期共享 sklearn fit；缓存 key = (ts_code, fit_window_start..fit_window_end)；命中率监控 | After: T3.2 acceptance criteria in tuning work list. |
| S2 | T3.3 | 扩 panel 到 Top 100 × 24 dates（数小时 wall time）— 跑 2400 rows、K=6 folds、产 production-grade variant YAML | After: 20d val rank IC K-fold median ≥ +0.05 + 全部 folds 正向 + bootstrap CI 下界 > 0 + ≥75% regime 桶改善。Pass auto_promote_if_passing. |

**Phase S exit gate**: T3.2 + T3.3 acceptance per `docs/stock_edge_tuning_work_list.md` §0.1. Production variant YAML promoted via `auto_promote_if_passing` + git tag + ledger entry.

**Notes**:
- T3.2 + T3.3 are sequential (T3.3 depends on T3.2 to be feasible at scale)
- T3.4 (decision ledger) already done (`b394fdb`)
- T3.1 (DB I/O batching) already done (`167573e`)
- Latest results (2026-05-05 K=4 × 50 stocks × 12 dates panel): 5d positive 2/4 folds, 10d positive 2/4, **20d positive 4/4 with median +0.034** (close to +0.05 target)

---

## Phase E — Engineering bugs (5 tasks · est. 4-6h)

These touch `builder/data` code; regen reports after each task.

| # | ID | File | Bug | Acceptance |
|---|---|---|---|---|
| E1 | M5 | `ifa/families/market/noon.py:_build_n11_review_hooks` | Add `insert_judgment(...)` for each parsed review hook (mirror morning S12). | After: evening §08 中报判断 Review shows the noon hyps verifications, not "未找到". |
| E2 | MC4 | `ifa/families/macro/morning.py:_build_s7_macro_to_a` (or wherever sector tags are joined) | Replace `"".join(sectors)` with `" · ".join(sectors)` for sector tag rendering. | After: macro §07 受益方向 cell shows "资源品 · 工业金属 · 化工 · 机械设备" instead of "资源品工业金属化工机械设备". |
| E3 | A2 | `ifa/families/asset/morning.py` + `evening.py:_build_s6_chain` (or template) | Fix duplicate "A 股端A股端" — either prompt forbids label repeat OR builder strips leading repeat. | After: asset §06 chain blocks show single "A 股端" prefix. |
| E4 | T2 | `ifa/families/tech/morning.py` + `evening.py` section list | Remove "潜在蓄势待发标的池" section entirely from both slots. | After: tech morning §08 and evening §06 gone. Sections renumber accordingly. |
| E5 | T4 | `ifa/families/tech/data.py:fetch_tech_news` + caller end_bjt | Investigate why morning shows "未捕获" while evening has news from same 24h prior. Check `end_bjt` value, lookback window, importance filter. | After: morning report's news window correctly captures previous-trade-day evening news. |

**Phase E exit gate**: regen 9 reports, diff section count + content density vs prior. Push commit.

---

## Phase L — LLM prompt fixes (9 tasks · est. 6-8h)

Prompt edits change report content but not structure. Regen reports after each major prompt change OR batch them per family.

### Cross-family prompts

| # | ID | Prompt | Fix |
|---|---|---|---|
| L1 | S1 | All `*_S1_TONE_*` / `*_E1_HEADLINE_*` (4 families × 2-3 slots = ~10 prompts) | Force schema: `{headline: <one sentence ≤ 50 chars>, top3: [Action(action, threshold, when)] len==3, summary?: optional}`. Reject prose for headline. |
| L2 | S2 | `MARKET_NOON_REVIEW_HOOKS_*`, `MARKET_EVENING_REVIEW_*` | Force JSON list: `[{question: str, why: str ≤ 30 chars, threshold: str}]`. Template iterates as separate cards (not one big paragraph). |
| L3 | S10 | New top-banner prompt OR repurpose §01 | Add 1-sentence "顾问寄语": today's key takeaway + position recommendation hint + what to watch. |

### Family-specific

| # | ID | Prompt | Fix |
|---|---|---|---|
| L4 | M3 | market sentiment prompt | Change "做多情绪" copy threshold; add context "vs 近期均值 / 历史分布"; or rephrase to "局部赚钱效应". |
| L5 | MC5 | macro evening hyps verifier | Pass sector-level pct in context blob OR change prompt to verify only what data layer supplies. Eliminate "暂无法判断" pollution. |
| L6 | MC6 | macro morning §01 prompt | Force pick ONE most important macro thread, not three parallel. |
| L7 | T1 | tech §02 layer-cake intro prompt | Prepend 2-line plain-language intro explaining 5-layer logic. Add top-line "今日 X 层领涨". |
| L8 | T6 | tech §07 stock leader columns | Rename: 所在层 → "AI 产业链层"; 龙头类型 → "属性 / 标签"; 失效 → "退场信号". |
| L9 | A6 | asset §05 商品→板块传导 prompt | Plainer language; add 1-line "为什么 X 商品涨会带动 Y 板块". |

**Phase L exit gate**: regen + visual scan. Specifically check headline brevity, no "暂无法判断" pollution, news content actionable.

---

## Phase T — Templates / Builder pattern fixes (8 tasks · est. 6-8h)

Structural template changes + builder logic for self-fuse + helper audit.

| # | ID | Where | Fix |
|---|---|---|---|
| T1 | S3 | All `build_*_section` builders across 4 families | Roll out `return None` self-fuse pattern (Bug #14) to remaining ~20 builders. Each returns None when its content is empty; runner skips. |
| T2 | S6 | `ifa/core/render/format.py` (or per-family `_fmt_*` helpers) | Audit all `_fmt_pct`, `_fmt_amount`, `_fmt_num`. Ensure no raw float reaches templates. Add `_fmt_pct_safe(v, decimals=2)` wrapper that returns "—" on None and rounds otherwise. |
| T3 | S7 | New `ifa/core/render/glossary.py` + template support | Glossary tooltip system: define common terms (成本传导, 龙头类型, ASIC, 算力链, etc.) with hover/tap definition. |
| T4 | S14 | Section ordering convention doc + builder structure | Define cross-family section template: §01 = headline, §02 = primary dashboard, §03 = secondary, §last = hyps emit. Refactor as needed. |
| T5 | M2 | market noon §10 scenarios template | Convert from prose to card layout (one card per scenario; bullish/bearish/neutral color-coded). |
| T6 | MC1 | macro §03/§04 builders | Apply self-fuse (covered in T1 but explicit because macro has the most empty placeholders). |
| T7 | A4 | asset §07 builder | Apply self-fuse for news/events. |
| T8 | A7 | asset morning §06 chain template | Add CSS chevron-flow for cost-pass-through: 上游 → 中游 → 下游 visual. |

**Phase T exit gate**: regen. Verify no empty sections rendered as placeholders. Verify all numbers properly formatted.

---

## Phase C — CSS / Layout (12 tasks · est. 4-6h)

Pure presentation; no content changes. Final phase before verification.

### Cross-family CSS

| # | ID | File | Fix |
|---|---|---|---|
| C1 | S4 | `styles.css` | Mobile responsive: tables with > 4 cols collapse to card view at < 768px. Apply media queries. |
| C2 | S5 | `styles.css` + template helper | Lists > 10 items: default show 5, JS-free `<details>` expand for rest. |
| C3 | S8 | `report.html` banner | Compress metadata to footer/hover; keep title + run-mode + staleness; move template_version + report-run-id to footer. |
| C4 | S9 | `report.html` + `styles.css` | Add sticky TOC sidebar (desktop) + top "目录" pills (mobile). Each section gets `id="sNN"`. Floating "回顶" button after scroll > 1 viewport. |
| C5 | S11 | `styles.css` | Typography hierarchy: §01/§last (顾问寄语 + review hooks) larger + bordered; §02-§middle standard; §appendix smaller gray. |
| C6 | S12 | `styles.css` --colors variables | Confirm A股 红涨 / 绿跌 convention. Apply consistently across family templates. |
| C7 | S13 | `styles.css` `@media print` | `page-break-inside: avoid` on cards/tables. `page-break-before: always` for major sections. Repeating headers on tables that span pages. |

### Family-specific CSS

| # | ID | File | Fix |
|---|---|---|---|
| C8 | M4 | `_index_panel.html` | Hide rows where all data fields are None (北证50 if not populated). |
| C9 | MC2 | `_macro_policy_grid.html` | If all rows have "无新增信号", collapse to one-liner "今日无新增政策信号". |
| C10 | MC3 | `_macro_to_a_share.html` | Mobile: 6-col table → vertical card per macro variable. |
| C11 | A3 | `_asset_dashboard.html` | Mobile: 8-col table → 4 essential cols + tap-to-expand. Strip ops-internal caption ("CZCE 不可用"). |
| C12 | A5 | `_asset_anomalies.html` row | 触发条件 / 可能影响 / 观察指标 as label/value grid (3-row × 2-col). |

**Phase C exit gate**: regen. Cross-device check (desktop + iPhone simulator). PDF rendering verified.

---

## Phase V — Verification + final pass (4 tasks · est. 2-3h)

Catch regressions, double-check accuracy.

| # | ID | What | How |
|---|---|---|---|
| V1 | A3 + G6 | Sparkline data accuracy cross-family | Generate today's run + historical replay for same date. Spot-check 5 instruments. Verify slot cutoff (11:30 / 15:00). |
| V2 | All P0 | Visual diff vs initial 4/30 baseline | Compare new HTML vs baseline (saved before Phase E). Confirm ALL P0 issues are gone. |
| V3 | Mobile | iPhone 13 viewport | Open all 9 reports at 390×844px. Note any horizontal scroll, broken layouts, illegible text. |
| V4 | Print | PDF export quality | Generate PDF for each report. Check page breaks, no orphaned headers, no clipped tables. |

---

## Summary

| Phase | Tasks | Est. time | Acceptance |
|---|---|---|---|
| **S (Stock Edge tuning)** | 3 | 3-4 days | T3.2 + T3.3 done; production variant YAML auto-promoted; 20d K-fold median ≥ +0.05 |
| E (UI Engineering) | 5 | 4-6h | report structure stable |
| L (UI LLM prompts) | 9 | 6-8h | content concise, no hallucination filler |
| T (UI Templates / Builder) | 8 | 6-8h | self-fuse working, numbers formatted |
| C (UI CSS / Layout) | 12 | 4-6h | mobile + print + TOC + typography |
| V (UI Verification) | 4 | 2-3h | nothing regressed, all P0 gone |
| **Total** | **41 tasks** | **5-7 days** | – |

Note:
- Stock Edge phase S is **wall-clock days**, not active hours, due to ML training time
- UI phases (E-V) sum 22-31 active hours of work
- S0 (precondition verification) is critical: if optimizer surrogate bug is still present, tuning results are meaningless — must investigate first
- 51 UI issue items collapse to 38 tasks because many issues share fixes (e.g., S3 self-fuse pattern in Phase T covers MC1 + A4 + others)

---

## Execution checkpoints

- Per phase: commit + push + regen + brief PR-style summary back to user
- Per task: edit + immediate test if possible
- Per session: maintain todo list aligned with this doc
- Per breaking change to report structure: regen FULL 9-report set, not just one

## Out-of-scope (deferred)

- B14 dark mode (P3)
- B13 sparkline volume overlay (P2 nice-to-have)
- E9 macro empty-cell "—" placeholder (P2 minor)
- G10 §03 strength scale heatmap (P2)
