# Evidence Ingestion Priority Record

Status: accepted intake guidance  
Last reviewed: 2026-05-21  
Scope: Agent TReport RSS, Telegram report ingestion, novelty, commentary, feedback, and outcome-learning Work Packet intake.

## Purpose

This record captures the current adopt, defer, and reject decisions for the next Agent TReport evidence-ingestion improvements. Use it as a source-of-truth input when initializing Work Packets related to RSS collection, external report ingestion, novelty scoring, Telegram report structure, or evidence-bound LLM commentary.

This is not an implementation plan. Each future Work Packet should still define its own slices, tests, approval boundaries, and close evidence.

## Existing Boundaries

- `SignalReportPayload` remains the canonical report source of truth.
- `ExternalEvidenceEnrichment` owns external evidence collection and compiles provider output into report-safe evidence inputs.
- Provider adapters must normalize external material before it reaches report generation.
- Raw provider payloads, full RSS item text, full Telegram channel posts, credentials, local paths, and provider exceptions must not become model-visible context or user-ready report artifacts.
- `agent_pack` remains the generic runtime. Agent TReport evidence semantics, ETF scoring, report novelty, and Telegram report interpretation stay in `agent_treport` unless at least one other domain proves the same generic runtime need.
- Live providers, model export, and Telegram delivery follow the approval boundaries in `adr/0012-daily-operational-external-data-approval.md`, `adr/0013-full-live-pre-publish-default.md`, and `operational-live-runbook.md`.

## Adopt Now

### RSS External Evidence Provider

Add RSS collection as an `ExternalEvidenceProvider`, not as a separate research subsystem.

Initial behavior:

- accept a configured feed allowlist;
- fetch fixture feeds in tests and live feeds only behind existing external-data approval;
- normalize items into `ExternalEvidenceCandidate` records, primarily in the `news` category;
- preserve `title`, `source_label`, `published_at`, `safe_url`, short summary, ticker/theme hints, and provider outcome evidence;
- dedupe against existing news providers by ticker, source, date, title, and safe URL where available;
- keep raw feed bodies out of model context and report artifacts.

### Permissioned Telegram Report Ingestion

Add ingestion for selected Telegram channel reports only when the operator has legitimate access to the source content. Do not design this as broad public-channel scraping.

Initial behavior:

- support an exported or adapter-supplied normalized input before adding live retrieval;
- record channel alias, message id or stable source id, timestamp, safe link when available, detected tickers/themes, short summary, and content hash;
- classify the evidence as external commentary or report context first;
- use it for novelty, repetition, theme diffusion, and crowding signals before allowing any score-impacting catalyst role;
- avoid storing full raw posts in report-visible artifacts or model-visible context.

### Deterministic Novelty And Repetition Signals

Add a deterministic novelty/repetition layer before adding more LLM interpretation.

Initial inputs:

- previous Agent TReport report artifacts;
- RSS evidence mention history;
- permissioned Telegram report mention history;
- existing `EvidenceLedgerItem.novelty` and report data-quality notes.

Initial behavior:

- distinguish newly emerging names from repeated names;
- flag already-crowded or already-obvious themes;
- penalize unhelpful repetition in ranking or review labels only when the evidence is explicit;
- surface the reason in the payload or data-quality evidence, not as free-form model commentary.

### Telegram Report Structure

Refine the Telegram signal alert around manager-flow interpretation, themes, risk, and data quality while keeping the current Telegram quality gate constraints.

Preferred sections:

- today's ETF alpha read;
- top emerging names;
- theme map;
- watchlist changes;
- data quality and evidence confidence.

## Defer

### Structured LLM Commentary

Structured commentary is useful after RSS and Telegram evidence are normalized. Do not let model output change canonical scores, review labels, evidence grades, or data-quality outcomes.

Future commentary should be schema-bound and evidence-bound. Candidate fields include `theme`, `why_now`, `manager_read`, `supporting_evidence`, `counter_thesis`, `confidence`, and `missing_evidence`.

### `FeedbackEvent` And `EvalResult` In `agent_pack`

Do not promote these to `agent_pack` yet. Start with Agent TReport-local review or feedback artifacts only if a concrete Work Packet needs them. Promote to the runtime only after another domain needs the same generic capability without Agent TReport vocabulary.

### Outcome Learning

Outcome learning is important but later. It needs a stable mention ledger, cross-run provenance, benchmark price data, and clear alpha/evaluation definitions before it can influence scoring.

### AUM-Adjusted Flow And Price-Move Separation

Keep these as later scoring improvements until the required ETF AUM, NAV, price, share, and market-value data quality is proven. Do not add precise-looking flow math without reliable inputs.

## Reject For Near-Term Work

- A broad four-layer architecture rewrite that duplicates the current payload, evidence, render, and quality-gate pipeline.
- A new `CandidateEvidenceBundle` as a second source-of-truth schema. If needed, create a small view artifact composed from existing payload, target, evidence, and summary models.
- LLM-based manager-intention claims that infer confidence, rebalancing intent, or liquidity motive without source evidence.
- Broad web or Telegram scraping, raw provider payload retention, or full external text injection into model context.
- Agent TReport-specific evidence, ETF, RSS, Telegram, novelty, or scoring concepts inside `agent_pack`.
- LLM judge gates as a replacement for deterministic `ReportQualityGate` checks.

## Work Packet Init Guidance

Use this document as a recommended source when a Work Packet goal mentions any of the following:

- RSS evidence collection;
- Telegram channel or external report ingestion;
- novelty, repetition, crowding, or already-obvious-name penalties;
- Telegram signal alert restructuring;
- evidence-bound LLM commentary;
- feedback or outcome-learning for Agent TReport.

Recommended first Work Packets:

1. RSS external evidence provider with fixture tests and provider outcomes.
2. Permissioned Telegram report ingestion as normalized context evidence.
3. Deterministic novelty and repetition scoring from previous reports plus external mentions.
4. Telegram signal alert structure refinement using existing payload fields and quality gates.
5. Structured evidence-bound commentary after normalized evidence inputs are stable.

Default verification:

- add or update focused tests under `tests/test_agent_treport_external_evidence.py`, `tests/test_agent_treport_pre_publish_preview.py`, `tests/test_agent_treport_signal_report.py`, or `tests/test_agent_treport_signal_report_quality.py` as appropriate;
- run focused tests before broader Agent TReport checks;
- do not perform live provider calls, model export, or Telegram delivery without explicit approval evidence;
- update this record when a deferred item becomes accepted or a rejected item is reconsidered.
