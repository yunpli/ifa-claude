# Run modes

Every CLI invocation runs in exactly one of three modes:

| Mode         | Trigger                                  | Database        | Output dir                                | `report_runs.run_mode` |
|--------------|------------------------------------------|-----------------|-------------------------------------------|------------------------|
| `test`       | Developer, CI, smoke tests               | `ifavr_test`    | `~/claude/ifaenv/out/test/<date>/<run-id>/`     | `test`        |
| `manual`     | Operator (post-deploy human re-run)      | `ifavr` (prod)  | `~/claude/ifaenv/out/manual/<date>/<run-id>/`   | `manual`      |
| `production` | Cron (scheduled)                         | `ifavr` (prod)  | `~/claude/ifaenv/out/production/<date>/<run-id>/` | `production`|

## How it's selected

1. Explicit `--mode` CLI flag (highest precedence).
2. `IFA_RUN_MODE` env var.
3. Default = `manual` (safest while we're still building).

## Why both DB-level and column-level isolation

- **Separate `ifavr_test` DB** → test runs cannot ever pollute production data,
  no matter how badly something is misconfigured. This is the hard boundary.
- **`run_mode` column on `report_runs`** → inside the production DB we still
  need to distinguish "this was the scheduled 8:50 cron run" from "an operator
  manually re-ran the morning report at 11am after fixing a data issue".
  Review queries, KPI dashboards, and the next-day continuity logic will
  filter on this.

## Operator-visible side effects

| Artifact                     | `test`                                    | `manual` / `production`              |
|------------------------------|-------------------------------------------|---------------------------------------|
| Database writes              | only in `ifavr_test`                      | in `ifavr`                            |
| HTML / JSON output           | under `out/test/`                         | under `out/manual/` or `out/production/` |
| LLM calls                    | real (cost is real)                       | real                                  |
| TuShare calls                | real                                      | real                                  |
| Disclaimer footer            | annotated `⚠ TEST MODE — not for clients` | normal Lindenwood disclaimer          |

The "real LLM/TuShare calls in test mode" choice is intentional: the whole
point of the test mode is to verify the *real* pipeline, not a mocked one. If
later we want a `--dry-run` (skip LLM, return canned responses), that's a
separate flag layered on top of `--mode test`.
