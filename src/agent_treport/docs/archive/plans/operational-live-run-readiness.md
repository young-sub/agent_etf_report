# Operational Live Run Readiness

## Status

Completed.

## Purpose

Add an explicit operator readiness check between operational holdings sync and
`run-report` for the temporary local ETF Tracker upstream. The check is
focus-ETF-specific and answers whether today's operational run can produce a
user-ready report for the requested ETF.

## Public Interface

- `agent_treport.signal_report.adapters.operational_readiness.check_operational_run_readiness(...)`
- `agent-treport check-operational-readiness`

Required CLI arguments:

- `--holdings-path`
- `--focus-etf-id`

Optional CLI arguments:

- `--observed-partitions`, default `30`
- `--sync-metadata-path`
- `--max-observed-age-days`, default `3`
- `--operator-timezone`, default `Asia/Seoul`

## Completed Behavior

- Readiness statuses are `ready`, `ready_with_warnings`, `hold`, and `failed`.
- `ready` and `ready_with_warnings` allow the operator to run `run-report`.
- `hold` and `failed` are operator-review-only and not user-ready.
- Missing requested holdings manifests return JSON `failed`.
- Invalid JSON inputs and invalid CLI options return exit `2`.
- Missing auto-discovered sync metadata returns JSON `hold`.
- Explicit missing sync metadata returns exit `2`.
- Manifest/metadata mismatch returns JSON `failed`.
- Same-day sync recency and latest observed-date freshness are evaluated as
  separate checks in the operator timezone.
- Sync-quality warning and risk evidence is projected into readiness without
  changing sync, `run-report`, or `ReportQualityGate` blocking semantics.
- Mapping coverage uses the existing sync-quality thresholds:
  `<0.50` is `hold`, `0.50..0.80` is `ready_with_warnings`, and `>=0.80` is OK.
- Readiness scans only the copied partitions needed to select current and
  previous focus-ETF snapshots.
- Public reason and warning codes are stable and do not parse exception
  strings.
- Output excludes source manifest absolute paths, partition source paths, source
  URLs, raw rows, and full sample sets.

## Verification

- `../.venv/Scripts/python.exe -m pytest tests/test_agent_treport_operational_readiness.py`
- `../.venv/Scripts/python.exe -m pytest tests/test_agent_treport_cli.py`
- `../.venv/Scripts/python.exe -m pytest tests/test_agent_treport_operational_readiness.py tests/test_agent_treport_operational_holdings_adapter.py`
- `../.venv/Scripts/python.exe -m pytest tests/test_agent_treport_operational_readiness.py tests/test_agent_treport_cli.py`
- Focused `ruff check` on touched readiness and CLI files.

Full repository verification and manual live smoke are recorded in the final
implementation report for this goal.
