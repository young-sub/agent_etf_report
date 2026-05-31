# DataCollectionIndependence Foundation Plan

## Status

Implemented on 2026-05-15. Later roadmap phases through native source
acquisition, provider-flexible handoff, and live holdings baseline promotion have
also advanced. This file is archived implementation evidence, not the active
roadmap state.

## Goal

Define the Agent TReport-owned collection boundary and prove it with one fixture-backed vertical tracer bullet that can feed the existing readiness and `SignalReportWorkflow` path without reading an ETF Tracker source manifest.

The goal is not to add live providers yet. The goal is to make native collection a first-class Agent TReport path so later ETF universe, ETF brand metadata, holdings, and enrichment providers can plug in behind ports instead of extending the temporary sync bridge.

## Problem

The current operational path starts from `sync-operational-holdings`, which copies already-crawled ETF Tracker data into an `OperationalHoldingsExport`. That path is useful for migration, backfill, and avoiding duplicate historical fetches, but it is not the final Agent TReport end-to-end architecture.

Agent TReport needs a native collection boundary that owns:

- which ETFs and brands are tracked;
- how holdings snapshots are collected and normalized;
- how collection evidence is persisted path-safely;
- how collected holdings feed readiness and report generation;
- where later news, financial, price, analyst, and web evidence ports attach.

## First Slice Shape

Build the smallest native collection tracer bullet:

```text
fixture-backed native collector
-> Agent TReport normalized collected holdings output
-> collection summary evidence
-> check-operational-readiness
-> run-report
```

The fixture-backed collector should not read an ETF Tracker source manifest. It may reuse existing deterministic fixture row shapes, but the adapter must present itself as Agent TReport-owned collection, not as `sync-operational-holdings` over a copied source export.

## Closed Design Decisions

- Native output reuses the normalized `OperationalHoldingsExport` manifest and partitioned JSONL shape for the first tracer bullet.
- Canonical Phase 1 terms are **CollectedHoldingsOutput** and **CollectionSummary**, backed by fixture ETF universe, ETF brand metadata, and holdings snapshot contracts.
- The first entrypoints are a public library function, `collect_holdings_fixture`, and a fixture-only CLI command, `collect-holdings-fixture`.
- Existing `OperationalRunReadiness` remains the readiness gate for native collected holdings in Phase 1.
- The existing SHA-256 `OperationalExportFingerprint` scope covers the normalized native output unchanged.
- `CollectionSummary` remains operator evidence. Report-visible native limitations are projected through readiness warnings/disclosures and `ReportPayload.data_quality`, not by exposing the full collection summary.

## Proposed First-Slice Direction

Implemented Phase 1 direction:

- Reused the current normalized `OperationalHoldingsExport` file shape as the first native collected output to avoid breaking readiness, fingerprinting, and `run-report`.
- Treated the producer as native Agent TReport collection, not ETF Tracker sync. Native collection writes `collection_source_type="fixture"` and `collected_at` in the manifest.
- Added path-safe `collection_summary.json` next to the collected holdings output.
- Kept `sync_metadata.json` for the legacy sync bridge and did not write fake sync metadata from native collection.
- Added `agent-treport collect-holdings-fixture` as an explicitly fixture-only CLI surface around the library function.
- Kept live APIs, credentials, scheduling, publishing, and enrichment out of this slice.

## Public Behavior To Prove

- A fixture-backed native collection run writes normalized holdings output without reading an ETF Tracker source manifest.
- The output can be checked by `check-operational-readiness` and consumed by `run-report` with a matching readiness handoff.
- The output preserves current normalized row semantics: `etf_id`, `etf_name`, `brand_id`, `source_provider_id`, `as_of_date`, `security_id`, `ticker`, `name`, `weight_percent`, `shares`, `market_value_krw`, `is_cash`, and `security_classification`.
- Collection summary evidence is path-safe and excludes source paths, raw rows, URLs, credentials, and provider envelopes.
- Legacy `sync-operational-holdings` behavior and tests continue to pass.

## Candidate Public Interfaces

These are candidates, not final contracts. Confirm during grill before coding.

Library boundary:

```python
collect_agent_treport_holdings(
    *,
    collector: HoldingsCollector,
    output_dir: str | Path,
    observed_partitions: int,
    now: Callable[[], datetime] | None = None,
) -> dict[str, JsonValue]
```

Port-style protocols:

```python
class ETFUniverseCollector(Protocol):
    def collect_universe(self) -> ETFUniverseCollection: ...

class HoldingsCollector(Protocol):
    def collect_holdings(self, universe: ETFUniverseCollection) -> HoldingsCollection: ...
```

Possible CLI:

```powershell
..\.venv\Scripts\python.exe -m agent_treport.cli collect-holdings-fixture `
  --dest .scratch\native-collection\holdings `
  --observed-partitions 30
```

Avoid broad abstract interfaces unless the first failing public test requires them. Keep the first implementation small.

## Data Contract Expectations

Native collection output should record at least:

- schema version;
- collection source type, such as `fixture_native_collection` for the first slice;
- collection timestamp;
- requested observed partition count;
- copied or collected observed dates;
- ETF count;
- brand count;
- partition count;
- row count;
- path-safe quality warnings or limitations;
- pointer or fingerprint for the normalized holdings output.

Do not record:

- raw source rows;
- absolute provider paths;
- source URLs unless explicitly approved for a later provider slice;
- credentials or environment variable values;
- full provider response envelopes.

## Implemented Units

Unit 1: Native collection contract and fixture adapter.

- Added minimal native ETF brand, ETF universe, and holdings snapshot contracts.
- Added fixture-backed collector behavior.
- Wrote normalized output in the selected shape.
- Added path-safe collection summary.

Unit 2: Readiness/report integration.

- Proved collected output can pass `check-operational-readiness`.
- Proved `run-report` can consume it with a readiness handoff and produce final `user_ready`.
- Kept fingerprint and readiness behavior intact.
- Ensured native collection limitations project through readiness disclosure and `ReportPayload.data_quality`, without adding the legacy `operational_sync_metadata_unavailable` issue.

Unit 3: CLI, documentation, and bridge positioning.

- Added fixture-only CLI command.
- Updated runbook/docs to distinguish native collection from legacy sync migration/backfill.
- Updated roadmap status and handoff notes for Phase 2.

## TDD Strategy

Start with one focused failing test for the public collection boundary:

- fixture-backed native collection writes a normalized holdings manifest and partition files without a source manifest;
- collection summary is path-safe and has expected counts.

Then add integration tests:

- collected output can produce a readiness handoff;
- collected output plus readiness handoff can run `run-report` with a fake model and produce `user_ready` artifacts;
- legacy sync bridge tests still pass.

Prefer existing Agent TReport fixtures and fakes. Do not call live providers.

## Validation Commands

Expected focused checks:

```powershell
../.venv/Scripts/python.exe -m pytest tests/test_agent_treport_operational_holdings_adapter.py tests/test_agent_treport_operational_readiness.py tests/test_agent_treport_cli.py tests/test_agent_treport_signal_report_workflow.py
```

Expected static checks:

```powershell
../.venv/Scripts/python.exe -m ruff check src/agent_treport tests/test_agent_treport_operational_holdings_adapter.py tests/test_agent_treport_operational_readiness.py tests/test_agent_treport_cli.py tests/test_agent_treport_signal_report_workflow.py
../.venv/Scripts/python.exe -m pyright
```

Run full pytest before completion if the implementation touches shared adapters, workflow composition, or CLI behavior:

```powershell
../.venv/Scripts/python.exe -m pytest
```

## Out Of Scope

- Live ETF provider adapters.
- News, web search, financial metrics, price, analyst, or chart enrichment.
- Scheduler or autonomous daily operation.
- Publishing to Telegram, Threads, or any external channel.
- PDF rendering.
- Cross-run provenance database.
- SecurityMaster reviewed patch automation unless it becomes necessary for the fixture tracer bullet.
- Changes to `agent_pack` unless a failing public runtime behavior proves a generic runtime gap.

## Stop Conditions

Stop and ask before implementation continues if:

- the native output cannot feed current readiness and `run-report` without broad rewrites;
- a new store shape would obsolete `OperationalHoldingsExport` instead of adapting through it;
- a live provider, credential, or external API becomes necessary for the first slice;
- provider-specific vocabulary starts leaking into `SignalReportWorkflow` or reusable domain capabilities;
- path-safe collection evidence conflicts with useful operator diagnostics.

## Completion Report Format

When this phase is implemented, report:

- implemented collection boundary and entrypoint;
- output files and summary schema;
- proof that no ETF Tracker source manifest is read by the native fixture collector;
- readiness and `run-report` integration evidence;
- tests and checks run;
- docs updated;
- remaining risks and next roadmap phase.
