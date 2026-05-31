# Native ETF Source Acquisition Foundation Plan

## Status

Implemented on 2026-05-15. Later all-provider SourceProvider rollout and
provider-flexible handoff work superseded this as an active plan. This file is
archived implementation evidence.

## Goal

Move Agent TReport from fixture/import-only ETF holdings input toward native
SourceProvider-scoped catalog and holdings acquisition, while preserving the
existing fixture, readiness, history, and report paths.

First live provider: KODEX, selected by
`../source-provider-audit.md`.

## Result

- Provider audit completed for TIGER, KODEX, TIMEFOLIO, ACE, SOL, HYUNDAI, and
  RISE.
- KODEX selected as first live provider.
- Source acquisition contracts, fake provider, staged catalog update,
  source-backed holdings history update, path-safe summaries, and CLI steps are
  implemented.
- Manual live KODEX smoke completed explicitly: catalog succeeded with 234
  entries; one selected ETF holdings target `2ETF01` for requested date
  `2026-05-15` returned observed date `2026-05-15`, 201 rows, and one written
  snapshot.

## Slice Order

1. Document provider audit and first live provider selection.
2. Add source acquisition contracts and fake provider tests.
3. Stage complete source catalogs before mutating universe state.
4. Update holdings history from source fetch results while preserving duplicate
   skip and refresh-required behavior.
5. Emit path-safe source acquisition summaries.
6. Add explicit CLI commands for source catalog collection and source holdings
   history update.
7. Add KODEX live adapter behind explicit live opt-in.
8. Update runbook, roadmap, ADR, and focused docs.
9. Run focused validation, full validation, and a scoped refactor pass.

## Public Behaviors To Prove

- A complete source catalog can update `universe_state.json`.
- Incomplete, invalid, or path-unsafe source catalogs do not mutate existing
  universe state.
- Target identity is `source_provider_id + provider_etf_id`; URL/endpoint
  locators are internal provider details.
- Fake provider holdings results can produce `fetched`, `skipped_existing`,
  `failed`, `rate_limited`, and `unsupported` target outcomes.
- Partial source holdings acquisition writes successful snapshots and records
  failed targets.
- Changed duplicate snapshots still require explicit refresh.
- Source acquisition summaries include only allowed ids, dates, counts,
  outcomes, failure classes, retry counts, and aggregate counts.
- CLI fake/default commands do not call the network.
- Live commands require explicit `--live` and selected live SourceProvider.
- Missing or invalid live provider selection fails before network I/O.

## Out Of Scope

- Implementing every provider.
- Bulk live backfill.
- One-command orchestration.
- Scheduler and provider-load management beyond one explicit smoke.
- Raw provider payload persistence or raw URL debug artifacts.
- Readiness threshold changes.
- `agent_pack` changes.

## Validation

Focused checks:

```powershell
../.venv/Scripts/python.exe -m pytest tests/test_agent_treport_universe_collection.py tests/test_agent_treport_operational_holdings_adapter.py tests/test_agent_treport_operational_readiness.py tests/test_agent_treport_cli.py
../.venv/Scripts/python.exe -m ruff check src/agent_treport tests/test_agent_treport_universe_collection.py tests/test_agent_treport_operational_holdings_adapter.py tests/test_agent_treport_operational_readiness.py tests/test_agent_treport_cli.py
../.venv/Scripts/python.exe -m pyright
```

Full check before completion:

```powershell
../.venv/Scripts/python.exe -m pytest
```

Manual live smoke is explicit only and must use one SourceProvider, one ETF, and
one requested business date. It is not part of automated regression.
