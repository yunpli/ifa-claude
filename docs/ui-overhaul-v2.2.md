# V2.2 UI Overhaul — Design System Reference

> **Audience**: anyone touching `ifa/core/render/templates/` or writing new sections.
> **Companion**: [`docs/v2.2-release-notes.md`](v2.2-release-notes.md) for the why; this doc is the how.

---

## Design principles

1. **One judgment per card**, not paragraphs of recap.
2. **§01 promise = §01 content** — if the card says "三件事", show three actionable items, not 1 sentence + 1 paragraph.
3. **Pills > concatenated text** for lists (sectors, tags, related markets).
4. **Cards > tables** when rows have ≥ 4 columns of dense Chinese text.
5. **Self-fuse, not placeholder** — empty data → drop the section, never render "暂无数据".
6. **Mobile is a first-class viewport** — < 768px must work without horizontal scroll.
7. **A股 红涨/绿跌** — `--up: #991b1b` (red) and `--down: #166534` (green). Never override per-template.

---

## CSS tokens (do not redefine per-template)

```css
--ink:        #0d172a;   /* primary text */
--ink-2:      #2d3a4f;   /* body text */
--ink-muted:  #5a6776;   /* labels, captions */
--ink-soft:   #8c98a8;   /* timestamps */
--rule:       #d8dde6;   /* borders */
--rule-soft:  #eceff4;   /* dividers, badges background */
--paper:      #ffffff;
--paper-tint: #f6f7f9;   /* card surface */
--accent:     #8a1c20;   /* deep institutional crimson */
--accent-2:   #c4892a;   /* heritage gold (neutral signal) */
--accent-soft:#fbf3f4;   /* lightest crimson tint */
--up:         #991b1b;   /* 红=涨 */
--down:       #166534;   /* 绿=跌 */
--up-soft:    #fef2f2;
--down-soft:  #ecfdf5;
```

Type tokens:
```css
--font-serif: "Noto Serif SC", serif;   /* headlines, large numbers */
--font-mono:  "JetBrains Mono", monospace;  /* tickers, datetimes */
/* Body uses default sans-serif stack. */
```

---

## Component catalogue

### `.ifa-headline-card` — premium evening §01

Used by: `_commentary.html` when `c.headline + c.top3` present.

```html
<div class="ifa-headline-card">
  <span class="ifa-headline-card__label">晚盘综述</span>
  <h3 class="ifa-headline-card__headline">{{ headline }}</h3>
  <ol class="ifa-headline-card__top3">
    <li>{{ top3[0] }}</li>
    <li>{{ top3[1] }}</li>
    <li>{{ top3[2] }}</li>
  </ol>
  <p class="ifa-headline-card__summary">{{ summary }}</p>
</div>
```

Visual: gradient background, accent left border, top-right radial accent glow, `01/02/03` decimal-leading-zero counters.

### `.ifa-tone__top3` — morning §01 (tone_card)

Used by: `_tone_card.html` (4 families' morning §01) + `_tech_tone.html` (market morning/noon, tech morning §01).

Same `top3` list visual as headline card but inside a tone card. Validation points get demoted to `<details>` collapsible "支撑材料".

### `.ifa-review-hook` — noon §11 review hooks

Used by: `_review_hooks.html` (market noon §11).

```html
<article class="ifa-review-hook">
  <header class="ifa-review-hook__head">
    <span class="ifa-review-hook__num">01</span>
    <h3 class="ifa-review-hook__q">{{ question }}</h3>
  </header>
  <p class="ifa-review-hook__why">
    <strong>为什么重要</strong>{{ why_it_matters }}
  </p>
  <p class="ifa-review-hook__threshold">
    <strong>验证阈值</strong>{{ threshold }}
  </p>
  <div class="ifa-review-hook__tags">
    {% for tag in related.split('、') %}
      <span class="ifa-pill">{{ tag }}</span>
    {% endfor %}
  </div>
</article>
```

Layout: 1-col mobile, 2-col desktop (≥720px). Each card has 22px crimson serif numeral on the left.

### `.ifa-scenario-card` — noon §10 scenarios

3-col color-coded grid:
- `--bullish` → green top bar (`var(--up)` semantic-inverted to keep visual clarity? Actually green for 看多 to NOT clash with 红涨 conv. Confirm: green border = 看多 in our UI. Audience expects this.)
- `--bearish` → red top bar
- `--neutral` → gold top bar (`--accent-2`)

Each card: badge (▲ 看多 / ▼ 看空 / ◆ 震荡) + priority chip + scenario name + 触发条件 + 观察重点.

### `.ifa-chain-flow` — asset morning §06 chevron pipeline

```
┌─────┐  ┌─────┐  ┌─────┐
│上游 │▶│中游 │▶│下游/│
│信号 │  │影响 │  │A 股│
└─────┘  └─────┘  └─────┘
```

Mobile (<720px): rotates to vertical with arrow rotated 90°.

Color borders: 上游 gold (`--accent-2`), 中游 ink-muted, 下游 crimson (`--accent`).

### `.ifa-chain-card__row` — asset evening §06 chain review

Two labelled rows per card:
- 商品端: `<commodity recap stripped of self-prefix>`
- A 股端: `<A股 recap stripped>`
- Plus a takeaway italic conclusion.

### `.ifa-risk-card` — early reports §11 today's risk list

Severity-colored top bar:
- `--high` red top bar + ⚠ 高 badge
- `--medium` gold top bar + ▲ 中 badge
- `--low` green top bar + ● 低 badge

Each card: 触发条件 / 可能影响 / 观察指标 as labelled fields + 数据时点 muted footer.

### `.ifa-hypo-card` — early reports §12 today's hypotheses

Numbered cards mirroring `_review_hooks`:
- 22px crimson serif numeral (01/02/03)
- Hypothesis as serif title
- 验证方式 + 通过判定 chip-labelled paragraph rows
- 关联板块 + 观察窗口 as `ifa-pill` chips at bottom
- Confidence pill (HIGH/MEDIUM/LOW with severity colors) on top right

First 5 visible, rest in `<details>` expand.

### `.ifa-pill` — universal tag chip

```html
<span class="ifa-pill" data-tone="up">顺周期板块</span>
<span class="ifa-pill" data-tone="down">纯防御板块</span>
<span class="ifa-pill" data-tone="accent">主线候选</span>
<span class="ifa-pill" data-tone="neutral">通用</span>
```

Critical: pills now have `white-space: normal` + `word-break: break-word` + `max-width: 100%` so long Chinese sector names don't force the parent table column to overflow.

### `.ifa-toc-pills` — global table of contents

Sticky desktop / top mobile. Auto-generated from `report.sections`. Anchors to `#s{order}`.

### `.ifa-back-top` — floating return-to-top button

CSS-only; appears after scroll > 1 viewport via `:target` pseudo.

### `.ifa-banner__staleness` — staleness alert

Red-bordered alert at top of report when `compute_staleness_warning` returns non-None. Tells the reader explicitly: "部分数据未更新至 2026-05-06，最新可用日期为 2026-04-30。"

---

## Section type → template mapping

| `s.type` | Template | Used by |
|---|---|---|
| `tone_card` | `_tone_card.html` | morning §01 (macro/asset) |
| `tech_tone` | `_tech_tone.html` | market morning/noon §01, tech morning §01 |
| `commentary` | `_commentary.html` | evening §01 (all 4 families) |
| `review_hooks` (NEW) | `_review_hooks.html` | market noon §11 |
| `scenario_plans` | `_scenario_plans.html` | market noon §10 |
| `chain_review` | `_chain_review.html` | asset evening §06 |
| `chain_transmission` | `_chain_transmission.html` | asset morning §06 |
| `mapping_table` | `_mapping_table.html` | macro morning §07, asset morning §05 |
| `risk_list` | `_risk_list.html` | morning §11 (all early reports) |
| `hypotheses_list` | `_hypotheses_list.html` | morning §12 (all early reports) |
| `leader_table` | `_leader_table.html` | tech morning §07 |
| `layer_map` | `_layer_map.html` | tech morning/evening §02 |
| ... | ... | ... |

Adding a new section type? Add a clause to `report.html`'s `{% if s.type == 'X' %}` chain.

---

## Schema retry layer (LLM compliance)

`ifa.families.macro.morning._safe_chat_json(..., required_fields=[...])`:

```python
parsed, resp, status = _safe_chat_json(
    ctx.llm, system=SYSTEM_PERSONA, user=user, max_tokens=1800,
    required_fields=["headline", "top3"],
)
```

If the LLM returns `top3=[]` or omits the field, the layer:
1. Sleeps 2s
2. Re-prompts: "你刚才漏了 top3，必须 3 条 ≤22 字..."
3. Sleeps 4s, retries
4. Sleeps 8s, retries
5. Falls through to whatever the LLM produced (even partial); template falls back to legacy headline+summary if absent.

Status codes (visible in `model_outputs.status`):
- `parsed` — LLM got it right first time
- `schema_retry_ok_attempt1` / `attempt2` / `attempt3` — retry succeeded
- `schema_retry_partial_after3` — exhausted all retries; some fields still missing
- `parse_failed` — JSON didn't parse even after one stricter retry

Special case for `top3`: list must have `len ≥ 3` to count as populated.

---

## Self-fuse pattern

Builders return `dict | None`:

```python
def _build_e7_news(ctx) -> dict | None:
    if ctx.news_df is None or ctx.news_df.empty:
        return None  # ← signals "drop this section"
    # ... normal build ...
    return {"key": "...", "title": "...", "type": "news_list", "content_json": {...}}
```

Runner skips None:

```python
sec = builder()
if sec is None:
    on_log(f"  {label} skipped (data not available at this slot)")
    continue
sections.append(sec)
```

`_retag` (used by macro/asset/tech evening to relabel sections from morning) is also `None`-safe:

```python
def _retag(sec: dict | None, ...) -> dict | None:
    if sec is None:
        return None
    sec = dict(sec)
    sec["key"] = new_key
    ...
```

---

## Anti-patterns to avoid

❌ **Inline `<style>` blocks scoped to one template** — e.g., `_chain_transmission.html` originally had its CSS inline; when `_chain_review.html` (separate template) needed the same classes, evening reports got bare divs. **Always hoist shared CSS to `styles.css`.**

❌ **`white-space: nowrap` on tags** without `max-width` — long Chinese pill text overflowing → table column collapses → vertical-text rendering. `.ifa-pill` now has `normal` + `break-word`.

❌ **Tables without `table-layout: fixed`** — browsers redistribute column widths to fit content; long pills steal width from neighbours. `.ifa-matrix` is now `fixed`.

❌ **LLM headline duplication** — when LLM emits "中游影响中游影响铁矿石" or "A股端A股端...", template `strip_prefix` macro must run multi-pass with namespace assignment. See `_chain_review.html`.

❌ **TOC duplicated in client_brief AND global** — single source of truth: `report.html`'s `.ifa-toc-pills`. Don't render mini-TOCs inside other components.

❌ **Empty placeholder filler** — never render "本节暂无数据" or "未捕获重大事件，本节使用既有政策记忆"; return `None` and let runner skip.

❌ **Raw float to template** — always use `{{ value | fmt_pct }}` or core helpers. `-0.9999021622150495%` should NEVER reach the renderer.

---

## Adding a new section type

1. Define LLM schema in `ifa/families/<fam>/prompts.py` with explicit ≤N 字 limits + structured field list.
2. Add `_build_X` to `ifa/families/<fam>/{morning,evening}.py` returning `dict | None`.
3. Pass `required_fields=["..."]` to `_safe_chat_json` for any field that breaks the layout if absent.
4. Add `{% elif s.type == 'X' %}{% include "_X.html" %}{% endif %}` in `report.html`.
5. Write `_X.html` using existing CSS components where possible:
   - Use `.ifa-pill` for tags
   - Use `.ifa-section--elevated` for §01-class importance
   - Use `.ifa-section--appendix` for footnote-class disclaimer
   - Include `_section_head.html` partial for the title + `id="s{order}"` anchor
6. If you need new CSS classes, define them in `styles.css` (NOT inline in the template).
7. Add a smoke-test render with mock data:
   ```python
   from ifa.core.render import HtmlRenderer
   r = HtmlRenderer()
   out = r.env.get_template('_X.html').render(s={...})
   assert '<expected>' in out
   ```

---

## Verification checklist before merging UI changes

- [ ] All 9 morning/noon/evening modules `import ifa.families.X.morning` clean
- [ ] `HtmlRenderer().env.get_template('_X.html').render(...)` doesn't raise on mock data
- [ ] Mock data with `top3 = []` doesn't blow up (check legacy fallback path)
- [ ] Mock data with `top3 = [single item]` doesn't blow up (schema partial)
- [ ] Mobile viewport simulation (DevTools 390×844) doesn't horizontal-scroll
- [ ] Print preview doesn't orphan headers
- [ ] CJK long sector names (≥16 chars) don't force vertical text in adjacent columns
- [ ] LLM hallucination of duplicate label prefix ("X端X端...") gets stripped by `strip_prefix` macro

---

## Reference: filtering helpers

```jinja
{{ value | fmt_pct }}            → "+1.23%" or "—"
{{ value | fmt_pct_signed }}     → "+1.23%" / "−1.23%" (Unicode minus)
{{ value | fmt_amt_yi }}         → "1.23 万亿" / "1.23 亿" auto-scale
{{ value | fmt_num(2) }}         → "1,234.56"
{{ value | fmt_int }}            → "1,234"
{{ value | fmt_price }}          → "1234.56"
{{ value | fmt_dir }}            → "up" / "down" / "flat" (CSS data-tone hint)
{{ "成本传导" | ifa_term }}       → wraps in tooltip span if term in glossary
```

All return `"—"` on `None` / non-numeric input. Never `0.0` or empty string.

---

Last updated: V2.2.0 release (2026-05-06).
