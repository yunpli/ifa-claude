# Multi-Agent Deployment Guide

iFA reports are self-contained HTML/PDF files on disk. A delivery agent is an optional, separate process that watches for new files and pushes them to a messaging platform. This document describes how to configure one.

---

## Architecture Overview

```
iFA (report generator)
  └── writes HTML/PDF → IFA_OUTPUT_ROOT/{mode}/{date}/{run-id}/

Watcher agent (your code)
  └── polls IFA_OUTPUT_ROOT for new reports
  └── formats a summary message
  └── posts to your platform via its native API
```

iFA has no opinion on which platform you use. The watcher is a thin script you maintain.

---

## Suggested Agent System Prompt

The following prompt can be used to configure a conversational agent that coordinates report delivery. Adapt as needed for your platform.

```
你是 iFA 研究助理。你的职责是：

1. 在盘前（约 09:00 BJT）提醒市场报告已就绪，简述今日宏观 / 板块背景。
2. 在盘后（约 18:30 BJT）推送晚间主报告（市场 + 宁波派），摘要不超过 3 条重点观察。
3. 当用户提问时，根据当日已发布的报告内容作答，引用具体数据而非泛泛而谈。
4. 所有表述使用 观察 / 假设 / 验证点 框架，不做方向性研判，不使用「买入」「卖出」「推荐」等词。
5. 若报告文件不可用，诚实告知，不要编造数据。

语气：简洁、专业，以研究视角陈述事实。
```

Key constraints (enforce in your prompt):
- 观察 / 假设 / 验证点 framing — no directives
- No fabrication when a report is unavailable
- Summaries cite specific numbers from the HTML, not paraphrases

---

## Watcher Configuration

### Environment variables (add to your watcher's env)

```bash
IFA_OUTPUT_ROOT=/Users/neoclaw/claude/ifaenv/out   # same as iFA's output root
IFA_WATCHER_MODE=production                         # which subdirectory to watch
IFA_WATCHER_POLL_INTERVAL=60                        # seconds between scans
```

### Report filename patterns

| Family | Slot | Pattern |
|---|---|---|
| Market | morning | `CN_Market_Morning_*.html` |
| Market | evening | `CN_Market_Evening_*.html` |
| Macro | morning | `CN_Macro_Morning_*.html` |
| Macro | evening | `CN_Macro_Evening_*.html` |
| Asset | morning | `CN_Asset_Morning_*.html` |
| Asset | evening | `CN_Asset_Evening_*.html` |
| Tech | morning | `CN_Tech_Morning_*.html` |
| Tech | evening | `CN_Tech_Evening_*.html` |
| SmartMoney | evening | `CN_SmartMoney_Evening_*.html` |
| Ningbo | evening | `CN_Ningbo_Evening_*.html` |

Reports are written under `{IFA_OUTPUT_ROOT}/{mode}/{date}/{run-id}/`. A new `run-id` directory appears when the report completes. The watcher should track which run-ids it has already delivered to avoid duplicates.

### Minimal watcher skeleton (Python)

```python
import os, time, pathlib, hashlib

OUTPUT_ROOT = pathlib.Path(os.environ["IFA_OUTPUT_ROOT"])
MODE = os.environ.get("IFA_WATCHER_MODE", "production")
POLL = int(os.environ.get("IFA_WATCHER_POLL_INTERVAL", 60))

seen = set()

def scan():
    for html in sorted((OUTPUT_ROOT / MODE).rglob("*.html")):
        key = str(html)
        if key not in seen:
            seen.add(key)
            deliver(html)

def deliver(path: pathlib.Path):
    # plug in your platform's send API here
    raise NotImplementedError

while True:
    scan()
    time.sleep(POLL)
```

---

## Scheduling

iFA is designed to be run on a cron schedule. The watcher runs separately and just reacts to new files.

Suggested cron (server local time = BJT):

```cron
# Morning reports
30  8 * * 1-5  ifa generate market --slot morning ...
30  8 * * 1-5  ifa generate macro  --slot morning ...
30  8 * * 1-5  ifa generate asset  --slot morning ...
30  8 * * 1-5  ifa generate tech   --slot morning ...

# Evening reports (after market close + data settle)
30 17 * * 1-5  ifa generate macro  --slot evening ...
30 17 * * 1-5  ifa generate asset  --slot evening ...
30 17 * * 1-5  ifa generate tech   --slot evening ...
00 18 * * 1-5  ifa generate market --slot evening ...
00 18 * * 1-5  ifa smartmoney evening ...
15 18 * * 1-5  ifa ningbo evening  ...

# Weekly model refresh
00 22 * * 0    ifa ningbo refresh weekly
```

All commands should include `--user <uid> --generate-pdf`. See `docs/OPERATIONS.md` for the full timing rationale.

---

## Non-trading days

```bash
# scripts/is_trading_day.py exits 0 on trading days, 1 on non-trading days
uv run python scripts/is_trading_day.py && ifa generate market --slot morning ...
```

Wrap all generation commands with this guard so reports are not generated on weekends or public holidays.

---

## Security notes

- Store platform API tokens separately from iFA secrets. Do not add them to `ifaenv/secrets/.env`.
- The watcher reads only from `IFA_OUTPUT_ROOT`; it does not need database access.
- Report HTML is self-contained and can be forwarded as an attachment or rendered inline depending on platform limits.
