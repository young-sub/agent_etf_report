# Data Collection Independence Roadmap

## Status

Completed for the Agent-Pack v1 release-evidence milestone. Phase 1, Phase 2,
Phase 3, Phase 4, native SourceProvider acquisition, source-acquired report
handoff, all-provider live source baseline, provider-flexible live holdings
handoff, external evidence enrichment, identity-safe evidence attachment,
verified operational flow, daily publish closure, and release evidence are
implemented or recorded. The durable v1 closure record is
`../../../docs/agent-pack-v1-release-evidence.md`; later work after this
roadmap is listed below.

## Purpose

Agent TReport must become able to collect ETF universe data, ETF brand metadata, holdings history, security identity evidence, news, financial metrics, prices, and external evidence inside this project. The current `sync-operational-holdings` flow is a migration and backfill bridge for already-crawled ETF Tracker data, not the final end-to-end agent architecture.

The target end-to-end flow is:

```text
collect universe and ETF brand metadata
-> collect holdings history
-> normalize and resolve securities
-> enrich external evidence
-> check operational readiness
-> run SignalReportWorkflow
-> inspect persisted evidence and report artifacts
```

## Current Baseline

- `agent_pack` provides the reusable runtime foundation for local workflows, model calls, durable SQLite runs, artifacts, inspection, trace export, and Workbench review where applicable.
- `SignalReportWorkflow` produces canonical payload, Markdown, HTML, Telegram alert, quality evidence, and readiness evidence artifacts.
- `sync-operational-holdings` can copy already-crawled ETF Tracker holdings into an Agent TReport normalized export and has readiness fingerprint protection.
- `collect-universe-fixture` owns the fixture-backed tracked ETF universe and ETF brand metadata state. It writes `universe_state.json` and path-safe `universe_summary.json` with added, changed, removed, and unchanged metadata evidence.
- `collect-holdings-fixture` still writes a direct normalized fixture export for Phase 1/2 compatibility.
- `update-holdings-history-fixture` is the forward native fixture holdings path. It consumes active ETFs from `universe_state.json`, updates `holdings_history.json`, skips matching ETF/date snapshots, requires explicit ETF/date refresh for changed duplicates, and retains removed ETF history.
- `export-holdings-comparison` creates the latest normalized comparison export from native history for active ETFs only. It can apply reviewed `SecurityResolutionExport` decisions with `--security-resolution-path`, writes `url_holdings_cumulative.json`, partitions, and `collection_summary.json` with active ETF and security coverage evidence.
- `import-holdings-history` backfills the native history store from an existing normalized `OperationalHoldingsExport` using the same duplicate/conflict/refresh rules.
- `collect-source-catalog` and `update-holdings-history-source` are the first
  SourceProvider-scoped acquisition steps. The default/fake path is
  deterministic and offline. Live adapters are explicit opt-in, stage complete
  source catalogs before universe mutation, and support bounded selected
  ETF/date holdings smoke.
- SourceProvider catalogs now preserve the full provider catalog while
  classifying entries for ActiveStrategyETF eligibility. SourceProvider-created
  `universe_state.json` and default source holdings targets include
  ActiveStrategyETF entries only; passive and unknown strategy entries remain
  path-safe catalog review evidence.
- Bounded SourceProvider holdings smoke treats an explicit provider ETF id as
  an exclusive selection set. A failed or stale selected target must not trigger
  an alternate ActiveStrategyETF request. Successful provider support requires
  fresh latest holdings evidence; observed dates on or after the requested date
  may count as fresh evidence, while older snapshots must be same-day,
  prior-day, or prior-business-day relative to the provider query date. Older
  fetched snapshots are stored in native history with stale-latest warning
  evidence and do not upgrade rollout status.
- Operator-bounded live ActiveStrategyETF smoke has run for KODEX, ACE,
  HYUNDAI, TIMEFOLIO, TIGER, RISE, and SOL. Parser fixes and bounded
  representative refreshes brought all seven providers through the
  representative equivalence gate.
- Source-acquired native holdings history can reach
  `export-holdings-comparison`, `check-operational-readiness`, and
  operational `run-report` through fake-provider automated tests. The handoff
  uses `collection_summary.json` and the normalized export fingerprint;
  `source_acquisition_summary.json` remains operator evidence only.
- The live source replacement baseline operator path can verify one
  representative ActiveStrategyETF per provider, plan required latest/prior
  snapshots gap-first from existing history, gate bulk live backfill on all
  representatives passing, apply same-host request spacing with provider
  overrides, stop affected providers on blocked/rate-limit signals, summarize
  daily collection health, and retain reproducible per-run evidence without
  pruning cumulative history.
- The live source replacement baseline started and then stopped KODEX on
  blocked/rate-limit evidence. KODEX remains a documented provider exclusion
  until a later daily retry succeeds. The KODEX-excluded active provider cohort
  is complete enough for provider-flexible operational handoff.
- Latest live holdings facts: 21,967 rows, 445 snapshots, target week
  `2026-05-11` through `2026-05-15`, 73 eligible EquityAnalysisETFs, and
  364/365 expected latest-week snapshots. The remaining gap is HYUNDAI
  `etf_hyundai_2912753` on `2026-05-11`, understood as pre-listing/no-data
  exclusion evidence rather than a global handoff failure.
- `check-operational-readiness` uses `sync_metadata.json` for legacy sync/backfill exports and `collection_summary.json` for native fixture/native-history outputs. Both paths keep the same readiness outcome meanings and fingerprint handoff.
- Provider-flexible live holdings handoff is complete enough to feed
  `run-report` from the latest holdings history/export/readiness seam.
- `run-report --evidence-path` already accepts manual `EvidenceItemInput` JSON
  and projects matching claim-scoped evidence into `EvidenceLedger`,
  `TickerDossier`, score components, and data-quality/coverage placeholders.
- Signal aggregation and evidence attachment now use identity-safe security
  keys. Reviewed `security_group_id` values may aggregate observed securities;
  otherwise `security_id` keeps holdings signals, claim scopes, dossiers, and
  score-impacting evidence separate even when display tickers collide.
- Agent TReport owns standardized financial, disclosure, and news evidence
  collection for target securities through fixture-backed tests and bounded
  opt-in live adapters.
- Agent TReport does not yet own scheduler-grade provider-load protection,
  autonomous daily evidence collection, cross-run evidence databases, broad web
  search, Reddit/social sentiment, publishing, or raw provider-payload storage.

## Sequencing Rules

- Do not start a later phase until the earlier phase has completed behavior tests, verification, documentation, and archive or handoff notes.
- Do not add a live provider before the relevant port, fixture-backed adapter, path-safe evidence contract, and deterministic tests exist.
- Keep provider clients in `IntegrationAdapter`s injected by the composition layer. Reusable domain capabilities own ports and must not import concrete providers.
- Keep `agent_pack` generic. Agent TReport collection concepts must stay in `agent_treport`.
- Keep `sync-operational-holdings` supported as a migration and backfill bridge, but do not treat ETF Tracker copied exports as the final source of truth.
- Each phase should preserve the existing operational `run-report`, readiness, quality, artifact, and inspect contracts unless a later roadmap decision explicitly replaces them.

## Phase 0: Roadmap And Documentation Hygiene

Goal: make this roadmap the active product direction before new implementation starts.

Must complete:

- Keep the Agent TReport context glossary aligned with **DataCollectionIndependence** and the temporary role of **OperationalHoldingsExport**.
- Link this roadmap from the Agent TReport documentation index and root implementation plan.
- Resolve any uncommitted documentation side effects from the operational fingerprint decision before starting Phase 1.
- Archive or update implemented active plans that are no longer active work.

Exit criteria:

- A future agent can identify Phase 1 as the next implementation target without rediscovering product direction.

## Phase 1: DataCollectionIndependence Foundation

Status: implemented on 2026-05-15.

Goal: define the Agent TReport-owned collection boundary and prove it with one fixture-backed vertical tracer bullet that no longer depends on an ETF Tracker copied source manifest.

Archived implementation plan:
`archive/plans/data-collection-independence-foundation.md`.

Must implement:

- A small public collection boundary for ETF universe, ETF brand metadata, and holdings snapshot collection.
- A fixture-backed collection adapter that produces Agent TReport-owned normalized holdings output for `SignalReportWorkflow`.
- A path-safe collection summary that records source, observed dates, row counts, brand and ETF counts, and data-quality signals without raw rows or secrets.
- A composition entrypoint, library function, or CLI command that runs the fixture-backed collection and writes the normalized local output.
- Documentation that reclassifies `sync-operational-holdings` as migration/backfill, while Phase 1 collection is the first native path.

Implemented:

- Public library function: `collect_holdings_fixture`.
- Fixture-only CLI entrypoint: `agent-treport collect-holdings-fixture`.
- Native output reuses the normalized `OperationalHoldingsExport` manifest and partitioned JSONL shape.
- Native readiness evidence is `collection_summary.json`; legacy sync/backfill readiness evidence remains `sync_metadata.json`.
- Native fixture output can pass readiness and produce final `user_ready` through `run-report` with a fake model.

Acceptance:

- The fixture-backed native collection output can feed `check-operational-readiness` and `run-report` without reading an ETF Tracker source manifest.
- The output preserves current normalized ETF, ETF brand, security, weight, shares, market value, cash, and classification semantics.
- Existing sync bridge tests still pass.

Out of scope:

- Live APIs, credentials, scheduling, publishing, cross-run databases, and external enrichment.

Decisions closed in Phase 1:

- Native collection writes the existing normalized `OperationalHoldingsExport` shape for the tracer bullet.
- `CollectionSummary` is the native collection evidence file and stays path-safe.
- The first entrypoints are both library and fixture-only CLI.

## Phase 2: Native ETF Universe And ETF Brand Metadata

Status: implemented on 2026-05-15.

Goal: make Agent TReport own the tracked ETF and ETF brand universe instead of inheriting it from ETF Tracker manifests.

Must implement:

- Canonical ETF and ETF brand identity records.
- Deterministic fixture-backed universe and ETF brand collection.
- Incremental update semantics for local universe state.
- Path-safe evidence for added, changed, removed, and unchanged ETF or ETF brand records.

Acceptance:

- A collection run can produce a stable ETF universe and ETF brand metadata set used by the holdings collector.
- Provider field names such as `fund_id` are mapped at adapter boundaries into active Agent TReport ETF terms.
- Holdings fixture collection consumes active tracked ETFs from local universe state when supplied and rejects untracked or removed ETFs before writing a normalized holdings manifest.

Implemented:

- Public library function: `collect_universe_fixture`.
- Fixture-only CLI entrypoint: `agent-treport collect-universe-fixture`.
- Canonical local state file: `universe_state.json` with active/removed ETF and ETF brand records.
- Path-safe summary evidence: `universe_summary.json` with active/removed counts and added, changed, removed, and unchanged metadata evidence.
- `collect-holdings-fixture --universe-state-path <universe_state.json>` uses active tracked ETFs as holdings targets.
- Same `etf_id` reactivation and same `brand_id` reactivation are reported as changed evidence rather than added evidence.

Out of scope:

- Holdings crawling and external enrichment beyond metadata needed to select holdings targets.

## Phase 3: Native Holdings History And Incremental Store

Status: implemented on 2026-05-15.

Goal: collect and store ETF holdings snapshots directly under Agent TReport ownership while avoiding duplicate historical fetches.

Must implement:

- Fixture-backed native holdings history update from active ETFs in `universe_state.json`.
- Local historical holdings store with observed-date partitioning and deterministic de-duplication by `etf_id + observed_date`.
- Incremental update rules that skip matching snapshots and fail changed duplicates unless that exact ETF/date is explicitly refreshed.
- Latest comparison export that selects each ETF's own latest valid current and prior snapshots, excludes removed ETFs from the default export, and emits active ETF coverage evidence including mixed-window diagnostics when needed.
- Migration/backfill path from existing `OperationalHoldingsExport` into the native store.

Acceptance:

- A native holdings collection run can update local history, produce the latest comparison window, and feed `SignalReportWorkflow`.
- Re-running collection does not duplicate already-collected historical partitions.
- Removed ETF history remains in the store for audit/manual analysis while default latest comparison export includes only active ETFs.
- Readiness treats compatibility single-focus gaps as failed, FocusETFSet coverage below three eligible ETFs as `hold`, and non-focus active ETF gaps as diagnostic `ready_with_warnings` rather than user-ready blockers.

Implemented:

- Public library functions: `update_holdings_history_fixture`, `export_latest_holdings_comparison`, and `import_operational_holdings_export_to_history`.
- CLI entrypoints: `agent-treport update-holdings-history-fixture`, `agent-treport export-holdings-comparison`, and `agent-treport import-holdings-history`.
- Native history store: `holdings_history.json` plus `holdings_history.json.parts/<YYYY-MM-DD>.jsonl`.
- Native history latest export remains compatible with `check-operational-readiness` and `run-report` by writing the existing normalized `OperationalHoldingsExport` shape.
- Path-safe update/export summaries report counts, ETF ids, observed dates, selected window dates, coverage ratio, missing active ETF ids, refreshed snapshots, and normalized export fingerprint without raw rows, absolute paths, provider URLs, provider payloads, or credentials.

Out of scope:

- News, financial metrics, analyst evidence, and publishing.

## Phase 4: Native Security Resolution Recovery

Status: implemented on 2026-05-15.

Goal: move security normalization and ticker/non-ticker recovery into the native collection path.

Must implement:

- SecurityMaster review decisions that can be applied deterministically to native collected holdings.
- SecurityResolutionExport reuse for native collection.
- Recovery tests proving ticker-candidate coverage improves after reviewed decisions.

Acceptance:

- Native collection can classify ticker candidates, cash-like rows, non-equity rows, and unknown rows without relying on legacy sync-only paths.
- Reviewed operator decisions can be applied without untrusted model output changing sync or collection state directly.

Implemented:

- `export-holdings-comparison --security-resolution-path <security_resolution.json>` applies reviewed mappings/exclusions to normalized native history comparison exports without mutating `holdings_history.json`.
- Native `collection_summary.json` includes path-safe `security_coverage` evidence: reviewed resolution availability, mapped/unresolved ticker-candidate counts, unknown counts, non-ticker exclusion counts, reviewed application counts, ticker coverage ratio, and capped aggregate recovery samples.
- Native readiness consumes `security_coverage`, reuses legacy ticker coverage thresholds, warns on unknown holdings, and warns when a native history export lacks reviewed security resolution.
- `propose-security-mapping-recovery` accepts either legacy `--sync-metadata-path` or native `--collection-summary-path`; native model requests receive only aggregate recovery sample fields.
- Re-exporting after reviewed recovery changes normalized rows, recovery samples, coverage evidence, and fingerprint without refreshing holdings history.

Out of scope:

- Fully automated security resolution approval and broad live lookup expansion.

## Phase 4A: Native SourceProvider Acquisition Foundation

Status: implemented on 2026-05-15.

Goal: establish Agent TReport-owned source catalog and source holdings
acquisition, creating the foundation later used by the registered live
SourceProvider cohort and enrichment work.

Implemented:

- Provider audit for TIGER, KODEX, TIMEFOLIO, ACE, SOL, HYUNDAI, and RISE.
- Provider-neutral contracts for source catalog results, catalog entries,
  holdings fetch targets, holdings fetch results, target outcomes, run outcome,
  and path-safe source acquisition evidence.
- Fake provider deterministic tests for complete catalogs, fetched holdings,
  skipped existing snapshots, failed targets, rate-limited targets, unsupported
  targets, changed duplicate refresh-required behavior, and partial summaries.
- Staged catalog acquisition: a complete and valid catalog updates
  `universe_state.json`; incomplete or invalid catalogs do not mutate existing
  universe state.
- Source-backed holdings history updates that preserve the native history
  duplicate skip and explicit refresh semantics while allowing partial writes
  for successful ETF/date snapshots.
- CLI source acquisition steps with offline fake defaults and explicit live
  opt-in.
- KODEX as the initial live smoke provider, selected because its catalog and one
  ETF/date holdings surfaces are JSON and do not require credentials for the
  bounded smoke.
- Manual live smoke on 2026-05-15: KODEX catalog succeeded with 234 catalog
  entries; one selected ETF holdings target `2ETF01` for requested date
  `2026-05-15` succeeded with observed date `2026-05-15`, 201 rows, and one
  written snapshot.

Later phases closed the all-provider live rollout and bulk live baseline
questions. One-command orchestration, scheduler-grade provider-load protection,
raw provider payload persistence, and raw URL debug artifacts remain outside
this historical foundation slice.

## Phase 4B: Source-Acquired Native Report Handoff

Status: implemented on 2026-05-15.

Goal: prove the SourceProvider-owned holdings path can hand off to the existing
operational report seam without relying on ETF Tracker sync manifest input.
This is not the full Native Operational E2E milestone. Later phases closed
enrichment and broad live SourceProvider rollout; scheduler and publishing
remain future work.

Implemented:

- Deterministic fake SourceProvider CLI E2E coverage for
  `collect-source-catalog -> update-holdings-history-source` for two observed
  dates -> `export-holdings-comparison` ->
  `check-operational-readiness` -> `run-report`.
- Clean source-acquired native history with complete current/previous snapshots
  for all active ETFs and a reviewed `SecurityResolutionExport` produces
  readiness `ready`, a readiness artifact, and final `output.user_ready` with
  no `output.operator_review_only`.
- Source-acquired native history without reviewed `SecurityResolutionExport`
  produces `ready_with_warnings`; the missing-resolution warning is disclosed
  in `user_ready.readiness` and projected into `ReportPayload.data_quality`
  without exposing SourceProvider diagnostics.
- Coverage gap cases are proved through the source-acquired report seam:
  compatibility single-focus missing one side of the selected comparison
  window is `failed` and blocks `run-report` before model or artifact
  resources; FocusETFSet coverage below three eligible ETFs is `hold`; and
  non-focus coverage gaps are disclosure-valid diagnostics that do not block
  user-ready output when focus coverage and safety contracts pass.
- Evidence boundary tests prove `source_acquisition_summary.json` is path-safe
  operator evidence only. Readiness ignores source acquisition summaries and
  consumes `collection_summary.json` plus the normalized export fingerprint.
- Duplicate snapshot skip, changed duplicate refresh-required behavior, active
  universe coverage, security coverage, fingerprint matching, and readiness
  handoff semantics remain preserved.

Later phases closed multi-provider acquisition, bulk live baseline, and
enrichment. Durable scheduler-grade provider-load controls, scheduling,
publishing, PDF, one-command native orchestration, and `agent_pack` changes were
not part of this historical handoff slice.

## Phase 5: Financial, Disclosure, And News Evidence Foundation

Status: implemented on 2026-05-16 as the external evidence foundation.

Goal: make `SignalIntelligenceReport` explain why ETF holdings changes may
matter by attaching external evidence to target securities. Evidence is grouped
into financial, disclosure, and news categories, collected through Agent
TReport-owned boundaries, and compiled into the existing report evidence seam.

Implemented:

- Reusable library boundary:
  `agent_treport.signal_report.external_evidence.collect_external_evidence`.
- Manual/operator CLI:
  `agent-treport collect-external-evidence`.
- Provider-neutral normalized evidence candidates with category details for
  financial, disclosure, and news evidence before conversion to
  `EvidenceItemInput`.
- Fixture-backed deterministic collectors/adapters for financial, disclosure,
  and news evidence.
- Bounded live provider adapters behind explicit `--providers` and `--live`:
  Finnhub, yfinance, DART, SEC EDGAR, Alpha Vantage, NewsAPI, and Naver.
- Conversion of collected evidence into `EvidenceItemInput` consumed by
  `run-report --evidence-path` and by library callers.
- Target selection from either a holdings export or an existing target-candidate
  `signal_board` file, capped by `--max-targets` default `2`.
- Optional conservative claim alignment with `--align-claims --model codex`,
  plus fake classifier coverage for automated tests.
- Deduplication before evidence projection so repeated provider confirmations
  do not inflate `external_evidence_support`.
- `external_evidence_summary.json` with target selection, provider outcomes,
  category coverage, dedupe counts, policy failure, evidence path, and cooldown
  path.
- Provider-level cooldown evidence for blocked/rate-limited outcomes, with
  24-hour suppression of live calls unless an operator explicitly supplies the
  one-session cooldown bypass for a bounded smoke run. SEC EDGAR is narrower:
  it enforces at least 0.11 seconds between SEC requests and writes a 15-minute
  cooldown for blocked or rate-limited outcomes.
- Category-aware `ReportPayload.coverage.financial_coverage_ratio`,
  `disclosure_coverage_ratio`, and `news_coverage_ratio` projection from an
  adjacent or explicit external evidence summary.
- Data-quality limitations and coverage notes for missing, skipped, failed, and
  no-data provider/category coverage.

External API policy:

- Live external API calls are never automatic. They require explicit live/provider
  opt-in and a bounded target ticker count.
- Provider clients must live in `IntegrationAdapter`s injected by the
  composition layer. Reusable domain capabilities depend on ports, not concrete
  API clients.
- Each live request uses at most three total attempts. Retry is limited to
  timeouts, network disconnects, HTTP 408, HTTP 429, HTTP 5xx, and SEC EDGAR's
  official 403 rate-threshold page. Missing or invalid credentials, HTTP
  400/401/403 auth/config failures, invalid payloads, and ticker-specific
  no-data are not retried.
- Missing credentials for an explicitly selected live provider are a policy
  failure and still write partial evidence plus summary before the CLI exits
  nonzero. Missing credentials for an unselected provider are irrelevant.
- Raw API payloads, response envelopes, headers, credentials, environment
  values, local paths, stack traces, and provider-specific raw URLs remain out
  of default report output and evidence files. Only adapter-marked public
  canonical URLs may be projected.

Acceptance:

- Fixture-backed enrichment changes `ReportPayload` evidence and coverage from
  not-run/null placeholders to populated, cited, deterministic evidence.
- Live provider execution is opt-in, bounded, path-safe, and does not run during
  automated regression tests.
- Missing, skipped, failed, rate-limited, or blocked evidence remains visible as
  data-quality/coverage limitations instead of disappearing silently.
- Existing readiness, fingerprint, `run-report --evidence-path`, quality gate,
  renderer, and `agent_pack` contracts are preserved.
- `run-report` receives evidence files and optional summary files only. It does
  not receive provider exceptions or provider-specific payload concepts.

Out of scope:

- Publishing or Telegram delivery.
- Reddit sentiment.
- Tavily/Serper broad web search unless a later grill decision accepts a very
  narrow fallback.
- Scheduler, daemon, autonomous daily collection, cross-run evidence database,
  proxy rotation, anti-bot bypass, browser automation, raw payload persistence,
  broad renderer rewrites, and `agent_pack` changes.

## Phase 5A: Identity-Safe Evidence And Signal Aggregation Closure

Status: implemented on 2026-05-17.

Goal: close ADR 0010 by making report aggregation, evidence attachment,
coverage, signal board rows, evidence ledger references, ticker dossiers, and
external evidence target selection use reviewed security identity rather than
bare tickers.

Implemented:

- Reviewed security master and security-resolution exports support optional
  `security_group_id`, `listing_key`, and group display metadata while old v1
  exports still parse with non-grouping defaults.
- Normalized holdings exports propagate identity metadata needed by report
  construction and emit ticker-collision review coverage when one ETF/date
  maps a display or lookup ticker to multiple securities without a shared
  reviewed group.
- Signal report aggregation keys, claim scopes, dossier references, evidence
  ledger `used_in` links, and score-impacting evidence matching now use
  `security_group_id` when reviewed and otherwise `security_id`.
- External evidence target selection uses reviewed listing/lookup metadata for
  provider calls, excludes ambiguous bare ticker targets from enrichment, and
  records coverage/data-quality notes explaining the exclusion.
- Regression tests cover SAN false aliases, GOOG/GOOGL share-class separation,
  same-share-class reviewed aliases, missing group display labels, old v1
  parse compatibility, ambiguous bare ticker enrichment exclusion, and exact
  identity-safe claim-scoped evidence scoring.

Remaining future work:

- Build the operator review workflow for candidate alias groups and ticker
  collision adjudication.
- Add exchange-qualified display labels for ambiguous tickers where reviewed
  listing metadata is available.
- Expand security master data through reviewed decisions only; do not infer
  aliases automatically from ticker, name, issuer, or provider lookup
  similarity.
- No `agent_pack` changes were required for this closure.

## Phase 6: First Live Provider Adapters

Status: superseded by the all-provider SourceProvider rollout foundation on
2026-05-15.

Goal: extend live SourceProvider adapters for KODEX alignment, ACE, HYUNDAI,
TIMEFOLIO, TIGER, RISE, and SOL behind the source acquisition port without
changing readiness or report handoff contracts.

Must implement:

- Provider units in rollout order: KODEX alignment, ACE, HYUNDAI, TIMEFOLIO,
  TIGER, RISE, SOL.
- Deterministic offline catalog parsing, one ETF/date holdings normalization,
  date-alignment, path-safety, and failure-classification tests for each
  provider where feasible.
- Explicit live opt-in with `--live`, explicit `--source-provider`, and one
  selected `--provider-etf-id` for live holdings.
- Provider-load guardrails: provider-specific request spacing, timeout, low
  retry cap, 403/429/blocked classification, and in-process stop-after-blocked
  behavior.
- Bounded manual smoke per provider: catalog once, then one selected ETF/date
  holdings request once. Do not run bulk holdings, backfill, an all-provider
  automated live loop, or a second ETF candidate after a holdings failure.
- SourceProvider-collected rows must preserve the normalized native history
  shape and the fixed downstream handoff:
  `holdings_history -> export-holdings-comparison -> normalized export +
  collection_summary fingerprint -> readiness -> report`.

Acceptance:

- Provider rollout status is documented as `supported`, `catalog_only`, or
  `blocked`.
- Live adapter failures degrade into explicit operator evidence according to
  the source acquisition contract.
- Domain workflow behavior stays deterministic under fake providers.
- Readiness and report-visible output continue to consume
  `collection_summary.json` plus normalized export fingerprints and do not
  expose SourceProvider diagnostics.

Out of scope:

- Bulk live backfill, scheduler/daily autonomous operations, durable cross-run
  provider cooldowns, broad provider-load management, news/search enrichment,
  raw provider payload persistence, publishing, Telegram delivery, PDF, changes
  to `agent_pack`, and provider-specific logic inside workflow or payload
  builders.

Current rollout evidence:

- Operator-bounded ActiveStrategyETF smoke on 2026-05-15 ran one catalog per
  provider and at most two holdings candidates per provider.
- KODEX: `supported`; provider ETF id `2ETFH5`, requested/provider
  query/observed date `2026-05-14`, `row_count=34`, one snapshot written.
- ACE: `supported`; provider ETF id `K55101DH7878`, requested/provider
  query/observed date `2026-05-14`, `row_count=50`, one snapshot written.
- HYUNDAI: `supported`; provider ETF id `2338258`, requested/provider
  query/observed date `2026-05-13`, `row_count=39`, one snapshot written.
- TIMEFOLIO: initially `active_holdings_failed`; parser closure later confirmed
  fresh SourceProvider seam support for provider ETF id `5`,
  requested/provider query/observed date `2026-05-14`, `row_count=56`, no live
  rows persisted.
- TIGER: `supported`; provider ETF id `KR7471780007`, requested/provider
  query/observed date `2026-05-13`, `row_count=29`, one snapshot written.
- RISE: initially `active_holdings_failed`; parser closure later confirmed
  fresh SourceProvider seam support for provider ETF id `44H6`,
  requested/provider query/observed date `2026-05-14`, `row_count=39`, no live
  rows persisted.
- SOL: `supported`; provider ETF id `211099`, requested/provider
  query/observed date `2026-05-14`, `row_count=21`, one snapshot written.
- Cross-provider foundation: registered provider ids are enumerated in rollout
  order, CLI live choices use the shared registry, every provider fixture feeds
  native history through the normalized row shape, and summary path-safety is
  tested across KODEX, ACE, HYUNDAI, TIMEFOLIO, TIGER, RISE, and SOL.

## Phase 6A: ActiveStrategyETF Latest Holdings Collection

Status: implemented on 2026-05-15.

Goal: make all-provider SourceProvider catalog acquisition target
ActiveStrategyETF holdings by default without broad live backfill.

Implemented:

- Explicit ActiveStrategyETF classification fields on SourceProvider catalog
  entries and persisted `source_catalog.json`.
- Path-safe source summary counts for active-strategy, passive-strategy, and
  unknown-strategy entries, with capped unknown review samples and
  source/confidence evidence.
- SourceProvider-created `universe_state.json` filters to ActiveStrategyETF
  entries by default while preserving the full source catalog.
- Default SourceProvider holdings targets exclude passive and unknown strategy
  entries.
- Candidate ordering for bounded smoke uses high-confidence provider metadata,
  high-confidence seed evidence, TIMEFOLIO default, then name-token evidence,
  with provider catalog order as the tie-breaker.
- A selected provider ETF id is exclusive for bounded holdings smoke. Failure
  or stale-latest evidence for that target records rollout status
  `active_holdings_failed` without trying an alternate target.
- Fetched stale snapshots are stored in native `holdings_history` with
  `stale_latest_holdings` warning evidence and do not mark the provider
  `supported`.
- Source-acquired history still hands off through the unchanged
  `holdings_history -> export-holdings-comparison -> collection_summary
  fingerprint -> readiness -> run-report` seam.
- Operator-bounded live smoke on 2026-05-15 used one catalog per provider and
  at most two ActiveStrategyETF holdings candidates per provider. KODEX, ACE,
  HYUNDAI, TIGER, and SOL produced fresh supported holdings snapshots.
  TIMEFOLIO and RISE initially failed both bounded holdings candidates with
  `invalid_provider_payload`.
- TIMEFOLIO/RISE parser closure on 2026-05-15 confirmed both providers can
  parse fresh live holdings through the SourceProvider seam without workflow,
  readiness, report payload, or `agent_pack` changes.

Remaining future work:

- Expand Agent TReport-owned seed fixtures only through reviewed exact-match
  evidence.
- Add live detail-page enrichment only if a later goal explicitly accepts that
  provider-load and crawling scope.
- Add a KRX holiday calendar for requested-date defaults and freshness
  decisions.

## Phase 6B: All-Provider ActiveStrategyETF Live Source Replacement Baseline

Status: implemented on 2026-05-16. The KODEX-included all-provider baseline
remains blocked by KODEX host-level connection/block evidence, but the
KODEX-excluded active provider cohort is complete after scope adjudication.

Goal: promote asset-manager ActiveStrategyETF holdings collection from
parser-seam support toward the operational handoff without changing readiness,
report payload builders, workflow logic, or `agent_pack`.

Implemented:

- Public representative live-vs-operational equivalence with exact security
  code matching, weight tolerance `<= 0.01` percentage points, market value
  tolerance `<= 1` KRW, and shares tolerance `<= 0.000001` only when shares
  are present on both sides.
- Path-safe representative summaries with provider id, ETF ids, observed date,
  row counts, matched count, mismatch counts, tolerance values, fetch outcome,
  warnings, and capped mismatch samples.
- Gap-first baseline planning that reads existing SourceProvider history before
  requesting missing latest and nearest-prior business-date snapshots.
- Discovery baseline planning for tracked ActiveStrategyETF entries that are
  absent from the old operational copy: existing live history is used first,
  and otherwise `latest_discovery`/`prior_discovery` requests are planned from
  the provider's latest known operational anchor date.
- Gated live bulk backfill: all provider representatives must pass before any
  bulk target fetch starts.
- Same-host request spacing with default `1.2s` plus up to `0.4s` jitter and
  more conservative SOL/RISE overrides.
- Stop-on-blocked and stop-on-rate-limit behavior for affected providers.
- Operational failure isolation: after path-safe failure evidence is recorded,
  a blocked provider or failed ETF is excluded from that run, the remaining
  eligible providers/ETFs continue to analysis, and the excluded provider/ETF
  is retried on the next daily collection cycle. Successful later retries
  naturally re-include the provider/ETF once the current/prior window exists.
- Daily collection health summaries and rolling retention for reproducible
  per-run output, while preserving cumulative `holdings-history/`.
- Live generated handoff remains the existing
  `holdings_history -> export-holdings-comparison -> collection_summary
  fingerprint -> readiness -> run-report` seam after the representative gate
  and bulk baseline succeed.

Live result:

- Initial representative gate: ACE, HYUNDAI, TIGER, and RISE passed; KODEX,
  TIMEFOLIO, and SOL failed. Follow-up normalization fixes and bounded
  representative refreshes brought all seven providers into representative
  equivalence.
- Bulk baseline started, then stopped KODEX on blocked/rate-limit evidence
  before the required KODEX window completed. KODEX is excluded from the
  completed cohort and should be retried by later daily collection instead of
  holding other providers.
- Cumulative history after the stopped run has 122 normalized snapshots and
  5,919 rows across `2026-05-12`, `2026-05-13`, and `2026-05-14`.
- Scope adjudication now excludes bond-like active products and delisted SOL
  `210920` from ActiveStrategyETF live analysis scope. ACE, HYUNDAI,
  TIMEFOLIO, TIGER, RISE, and SOL have complete required current/prior windows
  with `missing_snapshot_count=0` and `window_gap_count=0`.
- Stop action after the KODEX block: no further KODEX live requests in the run.
  KODEX remains a documented exclusion until a later daily retry succeeds and
  fills the remaining KODEX window.
- Operational-source dependency removal for asset-manager ETF holdings is
  complete for the non-KODEX active provider cohort. KODEX promotion remains
  pending until its latest/prior window succeeds.

## Phase 7: Native Operational E2E Flow

Status: closure verified. Deterministic coverage was implemented on
2026-05-17, the bounded manual live final review passed on 2026-05-17, and the
Issue #36 deterministic closure sweep re-verified the Phase 7 handoff,
readiness, report, quality, pre-publish, Telegram delivery, and daily publish
closure surfaces on 2026-05-23 with 199 passing tests.

Goal: replace the temporary sync-led runbook with an Agent TReport-owned operational flow.

The staged runbook remains the user-facing default. The verified operational flow adds a thin
verified composer for operators who already have Agent TReport-owned live
holdings history, reviewed security resolution when required, and optional
external evidence inputs.

Target flow:

```text
holdings_history
-> export-holdings-comparison
-> check-operational-readiness
-> run-report
-> native handoff with inspect/artifacts
```

Implemented:

- CLI command: `run-native-operational-handoff`.
- Default report/readiness operating input:
  `data/agent_treport/live-source/source-provider-operational/<provider>/`.
- Compatibility/archive holdings input:
  `data/agent_treport/live-source/holdings-history/`.
- Fresh normalized export by default, with readiness and report execution bound
  to the fresh export fingerprint.
- Resume/debug `--resume-export-path` support that rejects adjacent
  `collection_summary.json` fingerprint mismatches.
- Optional `--use-default-security-resolution` and explicit
  `--security-resolution-path`.
- External evidence summary projection in every final handoff, including a
  generated `status="not_run"` summary when evidence was not run.
- Registered live SourceProvider cohort accounting across `kodex`, `ace`,
  `hyundai`, `timefolio`, `tiger`, `rise`, and `sol`, with path-safe excluded
  providers/ETFs and eligible analysis cohort.
- Final handoff statuses: `user_ready`, `operator_review_only`, and `failed`.
- `verified_operational_flow_acceptance.status` with `passed` or `not_met`; strict verification
  mode through `--require-verified-operational-flow-acceptance`. Passing
  acceptance requires a successful bounded source holdings summary beside the
  handoff history whose selected ETF is present in the same handoff's exported
  or eligible analysis evidence, reviewed security identity, external evidence
  summary, canonical report artifacts, readiness and quality evidence,
  registered cohort accounting, and inspect/artifact references.
- `ready_with_warnings` can still produce `user_ready` when disclosures are
  present. Missing bounded source holdings evidence, reviewed security
  identity, or external evidence `not_run` can leave general handoff user-ready
  while verified operational flow acceptance is `not_met`.
- Readiness `hold` can produce `operator_review_only` only with
  `--allow-operator-review-output`, and the final handoff marks
  `delivery_blocked=true`, `reason="readiness_hold"`, and not user-ready.
- Report quality failure writes a failed handoff, exits nonzero, does not
  produce user-ready or operator-review-only output, and retains quality plus
  inspect references when available.

Acceptance:

- Deterministic tests cover user-ready verified operational flow passed, missing reviewed
  security identity acceptance `not_met`, external evidence `not_run`,
  external evidence partial failure disclosure, registered cohort exclusions,
  `hold` override operator-review-only output, and report-quality failure
  handoff.
- Successful handoffs reference canonical payload, Markdown report, HTML
  report, Telegram alert preview, quality report, readiness artifact,
  source acquisition summary, collection summary, external evidence summary,
  provider/ETF exclusion summary, and inspect command.
- Deterministic full-suite verification passed on 2026-05-17. The bounded
  manual live final review used `.scratch/verified-operational-flow-live-smoke/`
  as the isolated smoke evidence root. The accepted live boundary is HYUNDAI
  requested date `2026-05-13` / provider ETF `2338258`, which returned
  `run_outcome="succeeded"` with target outcome `skipped_existing`, observed
  date `2026-05-13`, row count `39`, and written snapshots `0`; canonical
  history remained unchanged. Native HYUNDAI export/readiness preflight reached
  `ready_with_warnings` with `user_ready_allowed=true`, eligible focus ETF count
  `5`, reviewed security resolution available, and the source target in the
  eligible HYUNDAI cohort. After explicit user approval for the described
  Codex/OpenAI export, the real Codex handoff under
  `.scratch/verified-operational-flow-live-smoke/escalated-hyundai/handoff-codex-html-escape-fix/`
  succeeded with `status="user_ready"`, `delivery_blocked=false`, exit code
  `0`, and `verified_operational_flow_acceptance.status="passed"`. It retained
  canonical payload, Markdown, HTML, Telegram alert, readiness, source
  acquisition, collection, external evidence, provider/ETF exclusion, inspect,
  generated artifact references, and zero-violation quality evidence.

Out of scope:

- New provider onboarding, new external evidence providers, scheduler or
  autonomous daily execution, publishing, Telegram delivery, PDF, cross-run
  evidence databases, raw provider payload persistence, raw URL/endpoint
  exposure, security review UI, automatic alias inference, broad live crawling,
  and `agent_pack` changes.

## Phase 8: Agent-Pack V1 Closure And ETF Agent Release Evidence

Goal: close `agent-pack` v1 against the real domain application it supports and record release evidence.

Must implement:

- Runtime public API and documentation audit based on the native Agent TReport e2e.
- Packaging, examples, verification matrix, and source-of-truth documentation cleanup.
- Archive completed Agent TReport roadmap plans and record remaining post-v1 work.

Acceptance:

- `agent_pack` remains detachable and domain-free.
- Agent TReport native e2e proves the runtime can support a real headless-first domain agent.
- Full tests, type checks, lint checks, and documented smoke procedures are current.

Closure evidence:

- Runtime package, public API, command entrypoint, optional Workbench extra, and
  domain-free boundary evidence are recorded in
  `../../../docs/agent-pack-v1-release-evidence.md`.
- Agent TReport daily publish closure evidence is complete for
  `data/agent_treport/live-source/daily-smoke-summaries/run_20260519_validated_provider_closure_live_evidence_001`,
  where `verify-daily-publish-closure` exits 0 with
  `closure_status="closure_met"`.
- The completed Phase 8 close record is archived at
  `archive/plans/agent-pack-v1-closure-etf-release-evidence.md`.

Out of scope:

- New product features not required to prove the runtime and ETF agent release candidate.

## Later Work After This Roadmap

- PDF snapshot artifact.
- Telegram or Threads publishing.
- Scheduler and autonomous daily operations.
- Cross-run provenance database.
- Domain-specific Workbench or report-review cards.
- Full ReferenceParityTarget beyond the first native end-to-end agent.
