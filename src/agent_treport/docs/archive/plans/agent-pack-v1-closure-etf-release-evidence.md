# Agent-Pack V1 Closure And ETF Agent Release Evidence

## Status

Completed on 2026-05-20.

## Source

- Work Packet:
  `.scratch/work-packets/wp-20260520-agent-pack-v1-closure-etf-release-evidence.md`
- Runtime release evidence:
  `docs/agent-pack-v1-release-evidence.md`
- Roadmap source:
  `src/agent_treport/docs/data-collection-independence-roadmap.md`, Phase 8

## Outcome

The v1 release-candidate evidence milestone is closed. `agent-pack` remains a
domain-free runtime package, and Agent TReport remains the ETF domain
application proving the runtime through a real headless-first workflow.

## Evidence Summary

- The package metadata, console scripts, optional dashboard extra, and public
  runtime exports are recorded in `docs/agent-pack-v1-release-evidence.md`.
- A runtime boundary regression test now checks that `src/agent_pack` Python
  modules do not encode Agent TReport or ETF-domain terms.
- Agent TReport native operational and daily publish closure evidence remains
  linked from `docs/implementation-plan.md` and
  `src/agent_treport/docs/operational-live-runbook.md`.
- Post-v1 runtime and Agent TReport work is listed separately from release
  closure.

## Verification

- `../.venv/Scripts/python.exe -m pytest --basetemp .scratch/pytest-wp-v1-release tests/test_package_skeleton.py tests/test_public_api.py tests/test_cli_transport.py tests/test_run_inspection.py`:
  13 passed.
- `../.venv/Scripts/python.exe -m pytest --basetemp .scratch/pytest-wp-v1-daily tests/test_agent_treport_daily_publish_closure.py`:
  18 passed.
- `../.venv/Scripts/python.exe -m agent_treport.cli verify-daily-publish-closure --package-path data/agent_treport/live-source/daily-smoke-summaries/run_20260519_validated_provider_closure_live_evidence_001`:
  exited 0 with `closure_status="closure_met"`.
- `../.venv/Scripts/python.exe -m pytest --basetemp .scratch/pytest-wp-v1-full`:
  596 passed.
- `../.venv/Scripts/python.exe -m ruff check .`: all checks passed.
- `../.venv/Scripts/python.exe -m pyright`: 0 errors, 0 warnings, 0
  informations.

## Remaining Work

Remaining work is post-v1 scope:

- Runtime provider adapter contract hardening.
- Runtime tool permission policy and approval lifecycle.
- Durable execution, locking, idempotency, replay, and worker-safe resume.
- Agent TReport PDF, scheduler/autonomous operation, cross-run provenance, and
  broader reference parity.
