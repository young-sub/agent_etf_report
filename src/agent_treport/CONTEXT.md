# Agent TReport Context

Agent TReport is the first domain application built on `agent_pack`. It reimplements the observable behavior of the legacy Agent TReport reference from scratch while keeping reusable finance/reporting capabilities separate from thin workflow orchestration.

## Language

**SignalReportWorkflow**:
The Agent TReport workflow that orchestrates holdings preparation, model analysis, canonical ReportPayload generation, and report artifacts while keeping reusable signal intelligence logic in domain capabilities.
_Avoid_: prototype, demo, toy agent, capability module

**UserReadyLocalAgent**:
A locally runnable Agent TReport product workflow that creates a SignalIntelligenceReport and exposes progress, results, logs, and artifacts through durable runtime evidence.
_Avoid_: generic runtime demo, toy agent, full reference parity, runtime Agent actor

**LocalFollowUpContract**:
The stable successful-run handoff that tells a local user or automation how to inspect the persisted Agent TReport run and open its artifacts.
_Avoid_: transient console hint, workflow state, generic runtime command requirement

**ReferenceParityTarget**:
The long-term target where the observable behavior of `../references/Agent_TReport-main` is reimplemented from scratch and extended with new product features.
_Avoid_: port, copy, migration

**BreadthOperationsReference**:
The role of `../references/ETF_tracker-main` as the source for broad ETF coverage, universe operations, cumulative holdings, security normalization, delivery hardening, and evaluation practice.
_Avoid_: primary product-quality reference, direct base implementation

**OperationalETFDataSource**:
The user's live ETF Tracker operational folder whose already-crawled ETF holdings data can be read read-only and copied into Agent TReport local runs.
_Avoid_: reference implementation, fixture, provider API, runtime dependency

**OperationalHoldingsExport**:
The Agent TReport-normalized copied local ETF holdings manifest plus sibling partitioned JSONL records from an OperationalETFDataSource used as deterministic holdings input for a SignalReportWorkflow run.
_Avoid_: live crawler, manual fixture rewrite, external API response

**OperationalExportFingerprint**:
Deterministic integrity evidence that binds an OperationalRunReadiness handoff to the exact OperationalHoldingsExport content it inspected.
_Avoid_: readiness status, quality score, source freshness check, model provenance

**OperationalSyncQualityDiagnostic**:
A deterministic quality status and summary produced while copying an OperationalHoldingsExport to explain source-data normalization risk without deciding report readiness; statuses are `ok`, `warning`, and `risk_failed`.
_Avoid_: ReportQualityGate violation, run-report blocker, model judgment

**SecurityMapping**:
A deterministic source-to-display identifier mapping that keeps `SecurityHolding.security_id` stable while resolving optional ticker display values for report surfaces.
_Avoid_: price enrichment, sector classification, live lookup, security identity replacement

**SecurityResolutionExport**:
A normalized-holdings-export-facing projection from **SecurityMaster** that contains only export-eligible ticker mappings and explicit non-ticker exclusions for deterministic sync or native history export normalization.
_Avoid_: SecurityMaster, review ledger, unresolved queue, model proposal artifact

**SecurityMaster**:
An operator-facing security identifier ledger that links observed `security_id` values to ticker, name, exchange, confidence, status, and review evidence before export to **SecurityMapping**.
_Avoid_: SecurityMapping, price enrichment, live lookup cache, authoritative trading security database

**ReviewedSecurityGroup**:
A reviewed aggregate exposure identity, represented by `security_group_id`, that permits multiple observed securities to be treated as one **SignalIntelligenceReport** exposure.
_Avoid_: ticker alias, display ticker, provider lookup result, auto-verified ticker mapping

**ShareClass**:
A distinct class of an issuer's equity with its own rights or listing identity that remains a separate report exposure from other classes.
_Avoid_: issuer alias, same-company duplicate, ticker collision

**SecurityMasterStatus**:
The review state for a **SecurityMaster** entry: `verified`, `auto_verified`, `proposed`, `review_required`, `unresolved`, `conflict`, or `excluded`.
_Avoid_: readiness status, sync quality status, proposal-only confidence

**SecurityIdentifierType**:
The observed identifier shape for a **SecurityMaster** entry: `isin`, `bloomberg_equity_code`, `krx_code`, `ticker_like`, `cash_like`, `non_equity`, or `unknown`.
_Avoid_: ticker source, resolution confidence, exchange classification

**SecurityClassification**:
The normalized holdings classification that separates `ticker_candidate`, `cash_like`, `non_equity`, and `unknown` rows before ticker coverage and report data-quality rules are applied.
_Avoid_: ticker value, holding change type, sector, asset allocation label

**SecurityMasterConfidence**:
The operator-readable evidence strength for a **SecurityMaster** entry, limited to `high`, `medium`, or `low`.
_Avoid_: numeric scoring contract, export permission by itself, model certainty

**SecurityMappingRecoverySample**:
A path-safe representative unresolved ticker-candidate or unknown holding identity that helps downstream recovery improve reviewed security decisions while keeping sync and native export free of automatic ticker approval.
_Avoid_: raw source row, draft SecurityMapping, enrichment candidate, cash ticker candidate, reviewed exclusion

**TickerCollisionReviewEvidence**:
Path-safe review evidence that a display or lookup ticker appears for multiple observed securities in the same ETF/date without a shared **ReviewedSecurityGroup**.
_Avoid_: automatic merge instruction, missing ticker coverage, provider lookup result

**SecurityMappingRecoveryProposal**:
An untrusted proposed set of ticker resolutions for SecurityMappingRecoverySample entries that must be reviewed before it can affect a SecurityMapping.
_Avoid_: SecurityMappingPatch, verified mapping, authoritative mapping

**SecurityMappingPatch**:
A human-reviewed set of SecurityMapping additions or replacements that may be deterministically merged into a SecurityMapping.
_Avoid_: SecurityMappingRecoveryProposal, raw LLM output, live lookup result

**OperationalDataQualityProjection**:
A path-safe projection of OperationalHoldingsExport provenance into ReportPayload data-quality issues, limitations, and coverage notes.
_Avoid_: raw sync metadata dump, ReportQualityGate rule, renderer-only warning

**OperationalRunReadiness**:
A local operator-facing decision about whether a copied OperationalHoldingsExport is `ready`, `ready_with_warnings`, `hold`, or `failed` for a user-selected **FocusETFSet** before a SignalReportWorkflow live operational run.
_Avoid_: OperationalSyncQualityDiagnostic, ReportQualityGate, external data crawler

**OperatorReviewOnlyReport**:
A generated SignalIntelligenceReport artifact set that is inspectable by an operator but must not be treated as user-ready because operational readiness did not allow final delivery.
_Avoid_: user-ready report, failed report, discarded artifact

**OperationalReadinessDisclosure**:
A user-facing notice that explains readiness warnings which must accompany a ready-with-warnings operational report before it can be treated as user-ready.
_Avoid_: hidden operator note, raw sync metadata dump, quality gate violation

**NativeOperationalHandoff**:
The reproducible operator-facing result that binds Agent TReport-owned holdings history, reviewed security identity when available, optional external evidence, readiness, report quality, report artifacts, and inspection references into one local handoff.
_Avoid_: publishing job, scheduler run, raw provider evidence package, generic runtime handoff

**PrePublishPreview**:
An operator-triggered Agent TReport flow that assembles holdings freshness, readiness, external evidence, SignalIntelligenceReport artifacts, quality evidence, and a NativeOperationalHandoff so the operator can inspect the TelegramSignalAlert before delivery.
_Avoid_: Telegram delivery, scheduler run, autonomous publishing, broad live crawl

**TelegramDelivery**:
The operator-approved one-time Telegram Bot API send of a user-ready PrePublishPreview's TelegramSignalAlert, recorded with path-safe delivery state and receipt evidence.
_Avoid_: PrePublishPreview, TelegramSignalAlert, scheduler run, multi-channel publishing, raw API payload

**TelegramDeliveryApproval**:
The operator approval that authorizes sending one eligible PrePublishPreview TelegramSignalAlert to a disclosed Telegram target alias.
_Avoid_: PrePublishExternalDataApproval, bot credential validation, generic consent flag, scheduler approval

**TelegramDeliveryReceipt**:
The path-safe evidence record for a TelegramDelivery attempt or duplicate-send block.
_Avoid_: raw Telegram API response, credential log, chat transcript, scheduler history

**DailyPublishClosure**:
The operator-facing decision that one daily PrePublishPreview has user-ready pre-publish closure, actual Telegram delivery, duplicate-send prevention evidence, handoff identity consistency, and passed validation evidence sufficient to treat the Telegram publish as operationally complete; operator approval evidence can be cited but is not required for this closure decision.
_Avoid_: TelegramDelivery, PrePublishPreview, scheduler completion, published report

**DailyPublishClosureEvidence**:
The path-safe result-package evidence that records a DailyPublishClosure decision and its supporting pre-publish, delivery, duplicate-prevention, preservation, and validation signals.
_Avoid_: raw Telegram body, delivery summary, validation log dump, audit archive

**OperatorApprovedDailyPublishFlowEvidence**:
The path-safe operator record that approves the manually executed daily publish flow across external evidence collection, model export, actual Telegram delivery, duplicate-send check, and DailyPublishClosure verification within the documented scope.
_Avoid_: scheduler approval, provider expansion approval, forced delivery approval, credential grant

**FullLivePrePublishArtifactClosure**:
The pre-publish success level where Agent TReport executes the approved full-live SourceProvider, external evidence, and real model path, generates the TelegramSignalAlert artifact, and surfaces the Telegram message body in the final handoff for operator quality review while still not sending Telegram delivery.
_Avoid_: Telegram Bot API send, final user-ready closure, scheduler completion

**KnownUnvalidatedProviderException**:
An operator-disclosed provider exception for an implemented live external evidence provider/API that lacks successful live smoke evidence and is excluded from full user-ready closure's required provider denominator for the current goal.
_Avoid_: silent omission, successful provider, provider health score, provider failure

**ValidatedExternalEvidenceProviderSet**:
The live external evidence provider/API set that must finish with success or normal no-data for a full user-ready PrePublishPreview closure.
_Avoid_: every implemented provider, sampled providers, hidden provider subset

**SmokeBoundedEvidenceReuse**:
Reuse of successful or normal no-data external evidence only inside one approved smoke boundary with matching holdings/report target identity, requested provider set, and evidence category.
_Avoid_: cross-run evidence database, stale provider success, raw payload cache

**PrePublishExternalDataApproval**:
An operator approval profile for a PrePublishPreview or daily operational run that separately authorizes disclosed live source catalog, live holdings acquisition, live external evidence, and model export scopes.
_Avoid_: generic consent flag, Telegram delivery approval, provider credential validation

**ApprovalTrace**:
The generic `agent_pack` trace/evidence projection that summarizes Agent TReport operator approval evidence for review, comparison, simulation, and audit without becoming a new approval authority.
Generic runtime governance records may sit behind the same boundary as stored `ApprovalLifecycleRecord` and `PermissionDecisionRecord` evidence, but they remain separate from the Agent TReport approval profile and do not grant runner permission by themselves.
_Avoid_: PrePublishExternalDataApproval, TelegramDeliveryApproval, runner permission grant, MLflow approval UI

**VerifiedOperationalFlowAcceptance**:
The strict acceptance decision that confirms a native operational handoff has bounded source holdings evidence tied to an eligible or exported **ETF** in the same handoff, reviewed security identity, external evidence summary, report artifacts, quality evidence, and inspect/artifact references. Manual closure requires one bounded live smoke where a real **SourceProvider** holdings fetch succeeds and updates or preserves **HoldingsHistoryStore** through the normal duplicate-skip rules, then that same smoke history succeeds through the real Codex model/report path with verified operational flow acceptance passed. Provider-only, model-only, general user-ready, or synthetic pass-fixture success records partial evidence, not closure.
_Avoid_: user-ready status, ReportQualityGate status, readiness status, CLI exit code

**DataCollectionIndependence**:
The future Agent TReport capability to recreate ETF holdings, ETF brand metadata, news, and financial-data collection inside this project without runtime dependence on legacy reference projects.
_Avoid_: copying legacy code, importing reference runtime, current live-run readiness

**CollectedHoldingsOutput**:
The Agent TReport-owned ETF holdings output produced by native collection for readiness and SignalReportWorkflow use without reading an OperationalETFDataSource or legacy source manifest.
_Avoid_: native output, ETF Tracker copied export, sync metadata output

**HoldingsSnapshot**:
The holdings rows for one **ETF** at one observed date, canonically identified by `etf_id` and observed date.
_Avoid_: provider version, raw provider response, repeated same-day collection

**HoldingsObservedDateGap**:
The difference among the operator-requested holdings date, the provider query date actually sent to a SourceProvider, and the provider-observed holdings date returned by that SourceProvider, commonly because the requested date is not a business day or the provider has not yet published same-day holdings.
_Avoid_: fetch failure, duplicate snapshot, provider version

**HoldingsSnapshotRefresh**:
An explicit operator-requested correction that replaces only the selected **HoldingsSnapshot** for one **ETF** and one observed date with newly collected rows.
_Avoid_: automatic overwrite, duplicate collection, silent correction

**HoldingsComparisonWindow**:
The pair of current and previous observed dates used to compare active **ETF** holdings snapshots for readiness and SignalReportWorkflow input.
_Avoid_: per-ETF date window, arbitrary latest two files, unmatched snapshot pair

**HoldingsHistoryStore**:
The Agent TReport-owned local history of **HoldingsSnapshots** used for de-duplication, refresh, audit, and later export views.
_Avoid_: transient report export, provider cache, sync bridge

**RegisteredLiveSourceProviderCohort**:
The complete set of currently registered live **SourceProviders** that an operational handoff must account for before provider or ETF exclusions are applied.
_Avoid_: successful providers only, provider registry implementation, EligibleHandoffCohort

**EligibleHandoffCohort**:
The SourceProvider-collected **ETFs** whose required current and previous **HoldingsSnapshots** are complete enough to be analyzed after **RegisteredLiveSourceProviderCohort** exclusions are recorded for one operational handoff.
_Avoid_: registered provider denominator, all tracked ETFs, all active providers, retry queue, missing coverage list

**LiveRetryCooldown**:
The path-safe local evidence that suppresses repeat SourceProvider or ETF retry attempts for a bounded time after blocked or rate-limited live acquisition evidence.
_Avoid_: scheduler, daemon, durable cooldown database, provider health score

**HandoffExclusionEvidence**:
Path-safe operator and readiness evidence explaining why a SourceProvider or **ETF** was left out of an **EligibleHandoffCohort** for one operational handoff.
_Avoid_: raw source diagnostics, provider payload, report payload dump, provider health score

**CollectionSummary**:
Path-safe native collection evidence that describes a CollectedHoldingsOutput for readiness without exposing raw rows, source paths, URLs, credentials, or provider envelopes.
_Avoid_: sync metadata, raw provider dump, report payload data-quality section

**SourceProvider**:
The external data-source identity, such as a website, API, or local source file family, that supplies ETF catalog or holdings data and is identified by `source_provider_id`.
_Avoid_: ETFBrand, asset manager, provider-only ETF identity

**ProviderETFId**:
The SourceProvider-assigned ETF identifier used to locate or fetch one ETF from that source, such as KODEX `fId`, TIGER `ksdFund`, or TIMEFOLIO `idx`.
_Avoid_: canonical ETF identity, ETFBrand identity, URL

**SourceProviderRolloutStatus**:
The operator-facing live rollout state for a SourceProvider: `supported` means catalog plus bounded holdings smoke succeeded, `catalog_only` means catalog smoke succeeded but active-strategy holdings smoke has not been attempted or only older general-ETF holdings evidence exists, `active_holdings_failed` means catalog acquisition and **ActiveStrategyETF** classification succeeded but bounded latest holdings smoke for selected **ActiveStrategyETF** candidates failed, and `blocked` means catalog acquisition itself hit a documented stop condition.
_Avoid_: provider health score, readiness outcome, report quality status

**DerivedCashWeight**:
A cash holding weight estimated from its market value share within one ETF snapshot when the OperationalETFDataSource omits a source weight.
_Avoid_: zero-filled cash weight, ignored cash row, residual allocation

**UncodedCashHolding**:
A cash holding from an OperationalETFDataSource row that has no source security code but is still identifiable as cash.
_Avoid_: missing-code security, skipped cash row, anonymous holding

**CashLikeHolding**:
A cash, currency, deposit-style, or short-term cash-equivalent ETF holding that should be treated as cash exposure in Agent TReport analysis.
_Avoid_: equity holding, ticker candidate, long-term bond, maturity-unknown bond

**DepthProductQualityReference**:
The role of `../references/Agent_TReport-main` as the source for high-density analysis, product surfaces, personas, holdings commentary, social workflows, charts, and report quality expectations.
_Avoid_: direct base implementation, narrow-only target

**ReusableDomainCapability**:
A domain function or service that can be reused by multiple Agent TReport workflows and later extracted into another project without carrying workflow-specific logic.
_Avoid_: workflow helper, node utility, script function

**ETF**:
The canonical analyzed vehicle in Agent TReport, identified across collection runs by canonical `etf_id` while ETF name remains display metadata.
_Avoid_: mixed fund/ETF naming, generic fund when the product means ETF

**ActiveStrategyETF**:
An **ETF** whose investment strategy is classified as actively managed rather than passive index-tracking.
_Avoid_: active ETF, active universe ETF, currently tracked ETF

**EquityAnalysisETF**:
An **ActiveStrategyETF** whose holdings are equity-style enough for Agent TReport signal analysis. Bond, CD-rate, money-market, government-bond, corporate-bond, financial-bond, mixed treasury, and delisted ETFs are not EquityAnalysisETFs even when provider names include active-management wording.
_Avoid_: all active ETFs, bond active ETF, money-market active ETF, focus candidate by name token only

**FocusETFSet**:
The user-selected group of **ETFs** that the operator wants the live operational handoff and **SignalIntelligenceReport** to emphasize. A FocusETFSet is chosen for analysis intent, not automatically as one ETF per ETFBrand or SourceProvider.
_Avoid_: single focus ETF, provider representative, ETFBrand sample set

**FocusETFSetFile**:
The path-safe local operator input file that stores a **FocusETFSet** as user intent only: schema version, focus ETF ids, and optional label or notes.
_Avoid_: provider health file, retry state, collection summary, readiness evidence

**ETFBrand**:
The ETF brand or asset-management-company identity associated with one or more ETFs, identified across collection runs by canonical `brand_id` while the brand name remains display metadata.
_Avoid_: portfolio manager, individual fund manager, source-provider-only id

**UniverseMetadataChange**:
The added, changed, removed, or unchanged status produced by comparing canonical ETF and ETFBrand metadata in the previous local universe state with the current collection result.
_Avoid_: holdings change, signal, price movement, portfolio rebalance

**SignalIntelligenceReport**:
The target Agent TReport research product that interprets active ETF brand behavior from holdings changes, multi-ETF confirmation, external evidence, scoring, and data quality.
_Avoid_: raw holdings table, news digest, direct investment recommendation

**MultiETFSignalAnalysis**:
The default analysis mode for SignalIntelligenceReport. It reads signals across multiple ETFs and brands first, then optionally applies a focus ETF lens without losing the universe comparison.
_Avoid_: single-ETF default analysis, isolated ETF change report

**ReportPayload**:
The canonical JSON-compatible source of truth for a SignalIntelligenceReport. Telegram, HTML, PDF, and quality gates should render or evaluate this payload instead of regenerating numbers or judgments independently.
_Avoid_: renderer-specific message, LLM-only prose, transient run result

**TelegramSignalAlert**:
The Korean-first, scoreboard-first Telegram HTML message text that tells a user whether the full SignalIntelligenceReport is worth opening.
_Avoid_: Telegram bot delivery, long report, trading signal, investment recommendation

**HTMLResearchReport**:
The local browser-readable SignalIntelligenceReport surface for exploring the canonical ReportPayload with richer drilldowns than Markdown.
_Avoid_: generic runtime dashboard, React app, live publishing surface

**SignalBoard**:
The ranked table inside a ReportPayload that captures each security or cash/theme signal with direction, type, score, confidence, evidence grade, review label, and primary reason.
_Avoid_: unsorted ticker list, raw change table

**TickerDossier**:
A per-security evidence card explaining why a top signal matters through holding facts, why-now hypotheses, supporting evidence, counter evidence, and invalidation conditions.
_Avoid_: long company profile, unsupported thesis paragraph

**EvidenceLedger**:
The structured source table of evidence items used by a ReportPayload, including source, type, stance, strength, URL, and where each item was used.
_Avoid_: hidden citation text, untracked source list

**ExternalEvidenceEnrichment**:
The Agent TReport capability that attaches non-holdings evidence to target securities so a **SignalIntelligenceReport** can explain why a holdings change may matter.
_Avoid_: broad web crawl, raw provider dump, investment recommendation engine

**FinancialEvidence**:
ExternalEvidenceEnrichment evidence about market data, valuation, earnings, recommendation trend, or basic company financial metrics.
_Avoid_: price target, investment rating, portfolio advice

**DisclosureEvidence**:
ExternalEvidenceEnrichment evidence from company filings, material disclosures, ownership or insider events, earnings filings, or regulatory events.
_Avoid_: ETF holdings disclosure, raw filing archive, legal advice

**NewsEvidence**:
ExternalEvidenceEnrichment evidence from bounded news sources about a target security, including headline, source, publication date, and provider-supplied relevance or sentiment when available.
_Avoid_: social sentiment, broad search digest, inferred trading direction

**ClaimScopedEvidence**:
An evidence item whose effect is tied to one explicit identity-safe `SignalBoard` claim through `claim_scope`. External evidence can affect support or contradiction scoring only when it is curated, relevant, novel enough, has an interpretation basis, and matches the exact claim.
_Avoid_: ticker-only evidence scoring, inferring direction from news prose, display-label matching

**EvidenceDisplayReference**:
A user-facing reference derived from signal metadata that explains where evidence was used without exposing raw `claim_scope` identifiers.
_Avoid_: raw claim scope string, hidden citation, renderer-only citation label

**PrimaryObservation**:
The holdings-change evidence generated directly from ETF snapshots for a `SignalBoard` claim. It documents the observed increase, decrease, new position, or exit but does not imply a price direction or investment action.
_Avoid_: price signal, recommendation, external corroboration

**ReportCommentaryPolicy**:
The deterministic policy that decides whether optional model commentary can be rendered. It omits commentary that introduces investment ratings, price targets, trading-action language, allocation advice, or attempts to change canonical payload scores, review labels, evidence grades, or data-quality findings.
_Avoid_: prompt-only safety, renderer-by-renderer ad hoc filtering

**ProhibitedInvestmentLanguagePolicy**:
The shared deterministic policy that identifies investment-rating, price-target, trading-action, or portfolio-allocation language that must not appear in user-facing SignalIntelligenceReport output.
_Avoid_: renderer regex, compliance engine, advisory classifier

**ReportQualityContract**:
The explicit product-quality rules an Agent TReport ReportPayload and its rendered outputs must satisfy, including required structure, data grounding, interpretation coverage, risk or follow-up coverage, and prohibited claims.
_Avoid_: loose tone preference, snapshot assertion, LLM judge prompt

**ReportQualityGate**:
A deterministic ReusableDomainCapability that evaluates a ReportPayload or rendered report output against a ReportQualityContract and blocks user-ready reports only for error-severity violations.
_Avoid_: investment advice validator, model evaluator, renderer helper

**ReportQualityResult**:
The structured outcome of a ReportQualityGate evaluation, including pass/fail status, quality violations, and a concise JSON-compatible summary.
_Avoid_: free-form audit note, test snapshot, model judgment

**ReportQualityViolation**:
One rule breach found by a ReportQualityGate, scoped to payload or rendered output and classified as warning or error severity.
_Avoid_: exception, lint message, investment compliance finding

**IntegrationAdapter**:
A boundary around one external provider, channel, file format, or API that hides provider-specific I/O behind a reusable domain-facing interface.
_Avoid_: embedded API call, inline client

**Port**:
A protocol owned by a reusable domain capability that defines what external data or side effect it needs without naming a concrete provider.
_Avoid_: concrete client, provider class

**CompositionLayer**:
The thin assembly boundary that chooses concrete integration adapters and injects them into reusable domain capabilities for one run or command.
_Avoid_: service locator, global config import

**ThinWorkflow**:
A workflow that coordinates capabilities and records execution but does not own reusable finance, research, formatting, or publishing logic.
_Avoid_: team, graph node package

## Relationships

- **ThinWorkflow** composes one or more **ReusableDomainCapabilities**.
- **SignalReportWorkflow** is the first candidate **UserReadyLocalAgent**.
- A successful **UserReadyLocalAgent** CLI run produces a **LocalFollowUpContract** in the composition layer; the **SignalReportWorkflow** does not own local follow-up commands.
- A **UserReadyLocalAgent** is usable when its run can be started locally, inspected from stored runtime evidence, and reviewed through generated artifacts without relying on transient console output.
- **SignalIntelligenceReport** is represented by a **ReportPayload** before renderer-specific outputs are produced.
- Multiple observed securities may be aggregated into one **SignalIntelligenceReport** exposure only when reviewed identity data assigns them the same **ReviewedSecurityGroup**; otherwise they remain separate even when their display tickers match.
- Different **ShareClasses** of the same issuer remain separate **SignalIntelligenceReport** exposures; issuer similarity is not enough to create a **ReviewedSecurityGroup**.
- A **ReviewedSecurityGroup** may unify multiple source identifiers for the same **ShareClass** and should provide a consistent user-facing display label while preserving member source identifiers as evidence; missing group display labels are report data-quality warnings, not aggregation blockers.
- **TelegramSignalAlert** renders from **ReportPayload** and must not recalculate scores, labels, evidence grades, or data-quality findings.
- **TelegramSignalAlert** selects at most five **SignalBoard** rows by canonical rank order and does not apply alert-specific scoring, thresholds, or label filters.
- **TelegramSignalAlert** is canonical-payload-only and does not include optional model commentary.
- **TelegramSignalAlert** may rewrite the top-ranked **SignalBoard** fields into a natural Korean headline, but it must not change their meaning or introduce new judgments.
- **HTMLResearchReport** renders from **ReportPayload** and must not recalculate scores, labels, evidence grades, or data-quality findings.
- A successful **UserReadyLocalAgent** may expose an **HTMLResearchReport** through the **LocalFollowUpContract** while keeping the Markdown preview available.
- A successful **UserReadyLocalAgent** exposes a **TelegramSignalAlert** as a durable local artifact through the **LocalFollowUpContract**; this does not include Telegram bot delivery.
- In a pre-publish preview flow, a `user_ready` **NativeOperationalHandoff** means the report artifacts and **TelegramSignalAlert** are ready for operator inspection before Telegram delivery; it does not imply Telegram Bot API delivery, delivery receipts, duplicate-send prevention, or scheduler execution.
- A **TelegramDelivery** consumes an existing **PrePublishPreview** handoff; `run-pre-publish-preview` remains preview-only and never performs Telegram Bot API delivery.
- A **TelegramDelivery** requires separate operator approval from **PrePublishExternalDataApproval** because it sends the generated **TelegramSignalAlert** to an external chat target and owns delivery receipt, duplicate-send, and retry semantics.
- A **TelegramDeliveryApproval** discloses the path-safe handoff identity, run id, **TelegramSignalAlert** artifact id, message fingerprint, message length, parse mode, Telegram target alias, required `telegram_delivery` scope, and excluded raw fields before a **TelegramDelivery** can perform network calls.
- A **TelegramDeliveryApproval** may disclose Telegram credential environment variable names and target aliases, but not bot tokens, raw chat ids, `.env` contents, raw Telegram API payloads, or absolute local paths; delivery execution may load credentials through the existing local environment or dotenv convention, but persisted evidence remains value-free.
- A real **TelegramDelivery** requires both approved **TelegramDeliveryApproval** and an explicit live-delivery operator action; fake or dry-run delivery remains the default testable path.
- A **TelegramDelivery** is eligible only from a `user_ready` **PrePublishPreview** handoff with `delivery_blocked=false`, full user-ready closure met, `telegram_delivery=not_sent`, and a present **TelegramSignalAlert** message and artifact; review-only, failed, blocked, or incomplete handoffs stop before network calls.
- A **TelegramDelivery** duplicate-send identity is the handoff run id, **TelegramSignalAlert** artifact id, message fingerprint, and Telegram target alias; an existing `sent` receipt for that identity blocks another network call.
- A **TelegramDelivery** with only failed receipt attempts for the same duplicate-send identity may be retried only through an explicit operator retry action, and retry evidence is appended rather than replacing earlier failed attempts.
- A **TelegramDeliveryReceipt** may record delivery status, duplicate-send identity, handoff run id, **TelegramSignalAlert** artifact id, message fingerprint and length, parse mode, target alias, approval status summary, attempt count, attempted timestamp, adapter identity, sanitized provider message evidence, safe error details, and relative receipt or package paths.
- A **TelegramDeliveryReceipt** must not store bot tokens, raw chat ids, raw Telegram request or response payloads, absolute local paths, stack traces, `.env` contents, or raw API payloads.
- A failed **TelegramDelivery** preserves the original **PrePublishPreview** handoff and records failure through **TelegramDeliveryReceipt** and result-package evidence; any `telegram_delivery=failed` projection belongs to a delivery output copy rather than mutating the original preview evidence.
- A successful **TelegramDelivery** also preserves the original **PrePublishPreview** handoff and records `sent` state through **TelegramDeliveryReceipt**, command output, and delivery summary evidence.
- An **OperatorReviewOnlyReport** cannot be force-sent through **TelegramDelivery** in the current product boundary; exceptional forced delivery is a separate future design.
- **DailyPublishClosureEvidence** is written by `verify-daily-publish-closure` inside an existing result package as `daily_publish_closure.json`; the verifier must not regenerate **PrePublishPreview** evidence, send Telegram, call external providers, create delivery approval/preflight templates, or mutate `telegram_delivery_summary.json`.
- **DailyPublishClosure** is met only when a package has a user-ready **PrePublishPreview** handoff, passed validation command evidence, matching live `sent` **TelegramDeliveryReceipt** evidence, matching duplicate-block evidence, and identity consistency across run id, **TelegramSignalAlert** artifact id, message fingerprint, target alias, and idempotency key when present.
- **DailyPublishClosureEvidence** trusts matching individual **TelegramDeliveryReceipt** records over `telegram_delivery_summary.json`; a latest delivery summary status of `duplicate_blocked` is compatible with closure when matching `sent` and `duplicate_blocked` receipts exist.
- **OperatorApprovedDailyPublishFlowEvidence** can be cited by **DailyPublishClosureEvidence** when approved, but missing or revoked operator-flow evidence is warning-only and does not prevent `closure_met`.
- **OperatorApprovedDailyPublishFlowEvidence** approves only the current manual daily publish flow. Provider-set expansion, model/export boundary changes, added Telegram target aliases, scheduler or autonomous execution, forced `operator_review_only` delivery, correction or reannouncement flows, and raw payload or credential storage policy changes require a new approval.
- **FullLivePrePublishArtifactClosure** and full user-ready closure are separate. FullLivePrePublishArtifactClosure is met when the full live path reaches generated report artifacts and the final handoff exposes the **TelegramSignalAlert** message body for operator quality review. Full user-ready closure is met only when readiness, all required full-live provider/API outcomes, and report quality allow delivery. If blockers remain after artifact generation, the handoff is `operator_review_only`, `delivery_blocked=true`, and still preserves the TelegramSignalAlert artifact and displayed message body.
- For **FullLivePrePublishArtifactClosure**, **OperationalRunReadiness** `hold` is a blocker for full user-ready closure but does not by itself stop review artifact generation when a holdings comparison can be produced safely. **OperationalRunReadiness** `failed`, report generation failure, or **ReportQualityGate** error-severity failure still produces a failed handoff without a TelegramSignalAlert message body.
- Required full-live provider failures are exclusion evidence for the current pre-publish run, not a reason to stop artifact generation by themselves. Failed, blocked, rate-limited, unavailable, invalid-payload, or timed-out ETF SourceProvider and required external evidence provider/API outcomes are excluded from downstream analysis or evidence projection when safe, recorded path-safely, and prevent full user-ready closure. The remaining eligible holdings and evidence continue through report and TelegramSignalAlert generation unless holdings comparison, report generation, or report quality cannot safely complete.
- A **PrePublishPreview** timeout is an operator boundary, not Telegram delivery behavior. The default preview timeout is 600 seconds; `--allow-preview-timeout-overrun` is an explicit operator override. Timeout handoffs preserve safe evidence references when available, block full user-ready closure, and do not expose a Telegram message body.
- Daily full-live **SourceProvider** acquisition may be staged as multiple provider-specific commands against one scratch **HoldingsHistoryStore**. The scratch `source_acquisition_summary.json` aggregates path-safe per-provider results so final pre-publish handoffs do not lose earlier provider evidence.
- Final **PrePublishPreview** handoffs use path-safe artifact references. They may expose relative local artifact paths for operator inspection, but must not expose file URIs, absolute local paths, raw provider URLs/endpoints, credentials, or Telegram Bot API delivery targets.
- A **PrePublishExternalDataApproval** has separate `live_source_catalog`, `live_holdings_acquisition`, `live_external_evidence`, and `model_export` daily scopes; a separate `live_source_baseline` scope is for initial, recovery, or bulk backfill operations rather than normal daily pre-publish closure.
- A **PrePublishExternalDataApproval** records approval evidence for existing staged daily operational commands; it does not merge live source acquisition and pre-publish preview into one command.
- A **PrePublishExternalDataApproval** approves a disclosed daily export boundary shape, not one specific trading day's changing ETF, holdings, or ticker values; narrower runs inside the approved provider and target-count bounds do not require reapproval.
- A canonical **PrePublishExternalDataApproval** is durable project data for repeated daily operation, while scratch approval profiles are acceptable for bounded smoke verification.
- A **PrePublishPreview** requires only the **PrePublishExternalDataApproval** scopes needed for the run path it is taking; cached evidence replay without a real model does not require external export approval.
- A final **PrePublishPreview** handoff may include path-safe **PrePublishExternalDataApproval** summary and artifact references, but not credentials, environment values, raw comments, or machine-specific absolute paths.
- A **PrePublishExternalDataApproval** may disclose **KnownUnvalidatedProviderException** records separately from requested **ValidatedExternalEvidenceProviderSet** calls; promoting an exception into the required set is provider expansion and requires reapproval.
- A **PrePublishPreview** reads the existing **HoldingsHistoryStore** by default. Missing required **HoldingsSnapshots** may trigger live **SourceProvider** acquisition only through explicit operator opt-in, and any acquired snapshots must follow the normal de-duplication, cooldown, exclusion, and refresh rules.
- A **PrePublishPreview** is stricter than a general **NativeOperationalHandoff** about **ExternalEvidenceEnrichment**: it must include an external evidence summary from cached normalized evidence or bounded live evidence. A summary with `status="not_run"` cannot produce `user_ready` for the pre-publish preview flow.
- Cached normalized **ExternalEvidenceEnrichment** can support a `user_ready` **PrePublishPreview** when it is schema-valid, identity-safe for the current **SignalBoard** targets, and summarizes attempted financial, disclosure, and news coverage. Missing target or category coverage is a report limitation, while an explicit external-evidence policy failure makes the preview `operator_review_only`.
- **SmokeBoundedEvidenceReuse** can prevent unnecessary repeated provider calls during one approved smoke, but evidence from another smoke boundary must not be used to satisfy full user-ready closure.
- Final **PrePublishPreview** handoffs distinguish **ValidatedExternalEvidenceProviderSet** outcomes from **KnownUnvalidatedProviderException** records; only failures from the validated required set block full user-ready closure.
- An external-evidence policy failure in a **PrePublishPreview** may still produce review-only report artifacts from partial normalized evidence. The final handoff must be `operator_review_only`, block delivery, and preserve the policy failure reason instead of treating the artifact generation itself as failed.
- The operator-facing **PrePublishPreview** command gathers live external evidence by default, while the lower-level explicit **ExternalEvidenceEnrichment** collection command keeps its separate live opt-in. Cached normalized evidence remains valid for deterministic tests and explicit local replay.
- The default live **PrePublishPreview** external-evidence denominator is the current **ValidatedExternalEvidenceProviderSet**: `finnhub`, `yfinance`, `dart`, `alpha_vantage`, `newsapi`, and `naver`. SEC EDGAR is disclosed as a **KnownUnvalidatedProviderException** and is not requested by this pre-publish goal. New provider onboarding remains a separate goal. A provider `no_data` outcome is a coverage limitation, not a delivery blocker; credential, blocked, rate-limit exhaustion, provider-unavailable, invalid-payload, or timeout-exhausted policy failures make the preview `operator_review_only` only when they occur for a validated required provider.
- The default live **PrePublishPreview** external-evidence target set is every analysis-eligible **SignalBoard** target, subject to a default operational maximum of 25 targets that prevents uncontrolled live API load. Operators may explicitly lower or raise the maximum when the approval profile covers the requested bound. Provider-policy-specific lower caps are allowed inside the global maximum and must be disclosed as coverage limitations instead of hidden. When the maximum caps an otherwise eligible full-live target set, the preview records a `capped_full_live_targets` limitation instead of hiding the cap.
- Full-live **PrePublishPreview** user-ready closure requires every provider/API in the **ValidatedExternalEvidenceProviderSet** to be attempted with vendor-policy-aware pacing and to finish without a policy-failure outcome. Provider `no_data` is a normal response limitation. Credential-required, blocked, rate-limit exhaustion, provider-unavailable, invalid-payload, or timeout-exhausted outcomes can still prove approval/live-boundary closure, but they prevent full user-ready closure for that run when they occur for a required provider/API.
- A **KnownUnvalidatedProviderException** remains visible in preflight, approval, handoff, and documentation evidence, but it is not counted as a required provider/API failure for full user-ready closure until successful live smoke evidence promotes it into the validated required set.
- Alpha Vantage pre-publish news enrichment uses a provider-level grouped `NEWS_SENTIMENT` request for a capped target subset instead of one request per target. The provider cap is disclosed as `provider_limitations` in the external evidence summary and handoff; rate-limit exhaustion after policy-aware handling still blocks full user-ready closure because Alpha Vantage is required.
- Same-smoke **SmokeBoundedEvidenceReuse** may reuse successful or normal `no_data` provider outcomes only when run id, requested validated provider set, approval boundary fingerprint, evidence category, and current report target identity match. This is local to the approved smoke boundary and is not a cross-run evidence database.
- **MultiETFSignalAnalysis** is the default mode for **SignalIntelligenceReport**; focus-ETF analysis is a lens or later specialized mode, not the default workflow shape.
- An **ETF** keeps the same identity across native collection runs when its canonical `etf_id` is unchanged; ETF name changes are display updates, not new ETFs or **UniverseMetadataChange** changes.
- An **ETFBrand** keeps the same identity across native collection runs when its canonical `brand_id` is unchanged; brand name changes are display updates, not new brands or **UniverseMetadataChange** changes.
- A **SourceProvider** may supply catalog or holdings data for one or more **ETFBrands**; it is source provenance, not ETF business identity.
- Source acquisition is scoped by **SourceProvider**, not by **ETFBrand**; when one **SourceProvider** exposes multiple **ETFBrands**, the collection path should gather all ETFBrand information through the same source boundary instead of creating ETFBrand-specific parsing logic.
- A holdings fetch target is identified by `source_provider_id + provider_etf_id`; canonical **ETF** identity remains `etf_id` in Agent TReport universe state, and provider URLs are locators or provenance rather than identity.
- Provider URLs or endpoint locators may be stored inside source target/catalog state for acquisition, but they must not appear in **CollectionSummary**, **OperationalRunReadiness**, report-visible data quality, or default operator evidence; raw provider payloads and raw URL debug artifacts require an explicit later opt-in design.
- An **ETF** can change **ETFBrand** attribution without changing ETF identity, but Phase 2 does not model individual portfolio managers.
- A same-identity **ETF** has a changed **UniverseMetadataChange** when its `brand_id` or `source_provider_id` changes.
- A same-identity **ETFBrand** has a changed **UniverseMetadataChange** when its `source_provider_id` changes; brand name changes are display updates only.
- A removed **UniverseMetadataChange** is valid only after a complete universe collection; incomplete or uncertain collection should not remove prior ETF or ETFBrand state.
- A removed **ETF** remains in local universe state with removed status and is excluded from default holdings collection; reappearance of the same `etf_id` restores active status as a changed **UniverseMetadataChange**.
- A removed **ETFBrand** remains in local universe state with removed status; reappearance through an active **ETF** restores active status as a changed **UniverseMetadataChange**.
- A **UniverseMetadataChange** compares ETF and ETFBrand metadata only; holdings composition changes remain **PrimaryObservation** evidence later in report analysis.
- Previously collected **HoldingsSnapshots** for a removed **ETF** remain in native holdings history for audit, backtest, and explicit manual analysis, but default latest comparison exports for readiness and **SignalReportWorkflow** include only active **ETFs**.
- The default latest **HoldingsComparisonWindow** for live operational handoff may be selected per **ETF**. Each included **ETF** must have its own current and previous **HoldingsSnapshots**; the overall **SignalIntelligenceReport** may disclose mixed comparison windows when different **ETFs** use different current or previous dates.
- If the **FocusETFSet** has fewer than three **ETFs** with both sides of the selected **HoldingsComparisonWindow**, **OperationalRunReadiness** is `hold`. Non-focus active **ETF** gaps are disclosure and recovery evidence, not a separate user-ready blocker, when the FocusETFSet minimum and safety contracts are satisfied.
- **UniverseMetadataChange** operator evidence may include canonical ids, display names, tracked field names, source provider ids, counts, source type, and timestamps, but must exclude raw provider payloads, holdings rows, URLs, credentials, and local paths.
- Invalid, incomplete, or path-unsafe native universe collection must not update local universe state; only a successful complete collection may replace state and produce **UniverseMetadataChange** evidence.
- Live source catalog acquisition must stage a source catalog first and update Agent TReport universe state only after the catalog is complete and valid; incomplete live catalog results must not remove or mutate existing ETF or ETFBrand state.
- A source catalog contains the full ETF catalog exposed by a **SourceProvider**. For SourceProvider acquisition, default universe state includes **ActiveStrategyETF** entries only; universe `status=active` still means currently tracked in that local universe, not active-management strategy by itself.
- Source catalog state may preserve stable provider-supplied provenance such as strategy labels, listing dates, activity labels, or return-period fields when a **SourceProvider** exposes them consistently, but those fields must not become readiness or report-visible data until a later enrichment slice gives them explicit product meaning.
- **ActiveStrategyETF** target selection excludes unknown strategy classifications by default; unknown entries remain in path-safe catalog evidence for operator review instead of becoming holdings targets.
- **EquityAnalysisETF** scope further narrows **ActiveStrategyETF** for operational signal analysis. Bond, CD-rate, money-market, government-bond, corporate-bond, financial-bond, mixed treasury, and delisted ETFs must be excluded from collection targets, live handoff exports, readiness denominators, and **FocusETFSet** candidates.
- Low-confidence product-name evidence such as an active-management marker in the ETF name may include an **ActiveStrategyETF** in target selection when no stronger passive evidence or contradictory source metadata is present.
- TIMEFOLIO ETFs are classified as **ActiveStrategyETF** by default because TIMEFOLIO is treated as an active-ETF-focused manager; stronger passive evidence or explicit passive source metadata still overrides this default.
- Read-only **BreadthOperationsReference** seed files may override ambiguous SourceProvider catalog strategy classification only when `source_provider_id + provider_etf_id` matches exactly; explicit passive live-catalog evidence wins, and any local copied seed evidence is Agent TReport-owned while reference seed files remain read-only.
- Active-strategy classification should not perform extra live detail-page enrichment by default; unclear ETFs remain review candidates unless the SourceProvider catalog already includes the evidence or an offline fixture/test supplies it.
- Bounded live holdings smoke selects **ActiveStrategyETF** candidates by evidence strength: explicit high-confidence SourceProvider metadata, high-confidence seed evidence, TIMEFOLIO provider default, then low-confidence product-name evidence; ties preserve provider catalog order, and explicit passive evidence excludes the ETF regardless of weaker active evidence.
- **SignalBoard**, **TickerDossier**, and **EvidenceLedger** are sections of a **ReportPayload**.
- **ClaimScopedEvidence** and **PrimaryObservation** are recorded in **EvidenceLedger**.
- **ExternalEvidenceEnrichment** produces **FinancialEvidence**, **DisclosureEvidence**, and **NewsEvidence** for target securities and compiles them into **ClaimScopedEvidence** only when the evidence has an interpretation basis for the exact signal claim.
- **ExternalEvidenceEnrichment** selects targets from identity-safe **SignalBoard** claims and uses reviewed listing or lookup metadata for provider calls; ticker values do not identify claims or decide evidence attachment by themselves.
- If **ExternalEvidenceEnrichment** cannot identify an unambiguous provider lookup target, the holdings signal remains report-visible but external evidence collection for that target is excluded and disclosed as coverage or data-quality evidence.
- **ExternalEvidenceEnrichment** normalizes provider-specific financial, disclosure, and news responses into provider-neutral category evidence candidates before report projection. Raw provider responses are not report evidence.
- Report-visible external evidence titles are normalized for the **ReportQualityGate** surface. If a provider-normalized title is blank or contains prohibited investment recommendation language, the report payload uses a neutral evidence title derived from ticker, evidence type, and source rather than rendering the raw title.
- **ExternalEvidenceEnrichment** may use a bounded model classifier to align normalized evidence candidates to **SignalBoard** claims. The classifier returns structured alignment only; low-confidence or weakly grounded alignments remain context evidence and do not affect score.
- **ExternalEvidenceEnrichment** supports report interpretation and data-quality disclosure; it does not change **OperationalRunReadiness**, **OperationalExportFingerprint**, or **HoldingsSnapshot** identity.
- **ExternalEvidenceEnrichment** is the pipeline stage between target security identification and **SignalIntelligenceReport** generation. The first implementation should expose a reusable library boundary and an explicit collection command for scheduler/manual use, but it does not own the scheduler, one-command daily pipeline, Telegram delivery, or publishing.
- **ExternalEvidenceEnrichment** should enrich a bounded cohort of target securities selected from the highest-ranked ticker-bearing **SignalBoard** candidates. Securities without ticker evidence, cash-like holdings, and non-equity holdings remain data-quality limitations instead of enrichment targets.
- A live **ExternalEvidenceEnrichment** provider failure must be explicit. It should not silently fall back to another vendor or disappear from operator evidence, while **SignalIntelligenceReport** generation remains able to run without external evidence when the enrichment stage is skipped or fails before handoff.
- **ExternalEvidenceEnrichment** provider outcomes should be recorded as structured path-safe evidence before any policy exception is raised. Scheduler-facing failure uses machine-readable error codes and summary artifact paths, while `run-report` consumes evidence files rather than provider exceptions.
- A failed **ExternalEvidenceEnrichment** stage may still hand off partial or empty evidence to **SignalIntelligenceReport** generation. The scheduler or operator may treat enrichment failure as an alert while continuing report generation, and the report must disclose missing provider or category coverage.
- Live **ExternalEvidenceEnrichment** uses bounded retry and cooldown behavior like live holdings acquisition: retry only transient read failures, cap each request at three total attempts, stop a provider on blocked or rate-limited evidence, and suppress repeat blocked-provider requests for 24 hours within path-safe local cooldown evidence.
- A **DailyOperationalExternalDataPreflight** discloses the approved export boundary before live SourceProvider acquisition, live **ExternalEvidenceEnrichment**, or real model export. It may include provider and model identities, required approval scopes, evidence categories, target counts, live SourceProvider cohort, focus ETF ids, capped safe identifier samples, data classes, credential variable names, excluded raw fields, and report/model export scope. It must not include raw holdings rows, raw provider payloads, raw URLs/endpoints, credentials or environment values, stack traces, absolute local paths, raw report text, or raw approval comments.
- A **PrePublishExternalDataApproval** is a durable operator profile with `pending`, `approved`, or `revoked` status, optional expiry, approved scopes, provider/model sets, maximum target-count bound, live SourceProvider cohort, data classes, excluded raw fields, report/model export scope, and a boundary fingerprint. Pending, revoked, expired, tampered, or narrower-than-requested profiles are not valid approval.
- Approval scopes are independent: `live_source_catalog`, `live_holdings_acquisition`, `live_external_evidence`, `model_export`, and the separate non-daily `live_source_baseline`. Cached evidence replay with a fake model requires no approval; cached evidence replay with real Codex requires `model_export`; live external evidence with a fake model requires only `live_external_evidence`.
- A valid approval authorizes a disclosed boundary shape rather than one trading day's changing tickers, ETF ids, holdings fingerprint, or generated date. Provider/model expansion, max-target expansion, new data classes, changed excluded raw fields, changed report/model export scope, live SourceProvider cohort expansion, expiry, revocation, or fingerprint mismatch requires reapproval; narrower provider subsets, target counts, and data-class subsets are allowed inside an approved boundary.
- Overlapping **ExternalEvidenceEnrichment** candidates from multiple providers should be deduplicated before report projection. Multiple providers confirming the same source fact must not inflate `external_evidence_support`; provider overlap belongs in path-safe collection summary evidence.
- Missing, skipped, blocked, rate-limited, or failed external evidence remains visible as report coverage or data-quality limitations instead of disappearing silently.
- **EvidenceDisplayReference** is derived from **SignalBoard** display metadata when evidence references a claim; unmatched raw claim scopes are hidden behind an explicit unresolved display phrase instead of being exposed in user-facing reports.
- **ClaimScopedEvidence** matches **SignalBoard** claims by `security_group_id` or `security_id` identity rather than ticker, name, or display label.
- **ReportCommentaryPolicy** gates optional model prose after **ReportPayload** is fixed.
- **ReusableDomainCapability** owns and depends on **Ports**, not concrete **IntegrationAdapters**.
- **IntegrationAdapter** implements a **Port** and may depend on provider libraries or external APIs.
- **CompositionLayer** injects **IntegrationAdapters** into **ReusableDomainCapabilities**.
- **ReusableDomainCapability** must not depend on a **ThinWorkflow**.
- **ReportQualityGate** is a **ReusableDomainCapability** and should not depend on a **ThinWorkflow**.
- **ReportQualityContract** defines report quality expectations; **ReportQualityGate** evaluates whether a **ReportPayload** or rendered report output satisfies them.
- **ReportQualityGate** records disclosed lower-severity quality gaps without failing a run, but error-severity safety violations or renderer contract drift prevent the output from becoming a **LocalFollowUpContract** success.
- **ReportCommentaryPolicy** and **ReportQualityGate** share the **ProhibitedInvestmentLanguagePolicy** instead of maintaining separate renderer or commentary regex rules.
- The first **ReportQualityContract** stays small and explicit, covering required report structure, the named prohibited-language policy, forbidden rendered fragments, required payload sections, and the threshold for blocking errors.
- The default **SignalReportWorkflow** quality contract allows zero error-severity violations; configurable error thresholds are for explicit contract tests or later product variants, not the first workflow default.
- The first **ReportQualityGate** product slice evaluates both the canonical **ReportPayload** and the user-facing Markdown preview; the **HTMLResearchReport** extension reuses the same quality artifact and adds HTML-scoped renderer checks instead of creating a separate quality report.
- The **TelegramSignalAlert** extension reuses the shared **ReportQualityGate** and adds a `telegram_alert` quality scope; a successful **UserReadyLocalAgent** stores Markdown, HTML, and Telegram alert artifacts only after all rendered user-facing scopes pass.
- The in-place Markdown preview is the current local user-facing report artifact. When **Market Map**, **ETF Follow Sheets**, and **EvidenceLedger** are promoted into required Markdown structure, missing headings, missing empty-section fallback text, or missing representative payload values are renderer contract drift and release-blocking **ReportQualityGate** errors, not warning-only gaps or a separate MarkdownV2 surface. The Markdown preview renders target sections as high-signal summaries, while **HTMLResearchReport** remains the later drilldown surface.
- A **ReportQualityGate** block is distinct from a renderer exception: the report rendered, but it violated product-quality rules and must not be treated as user-ready.
- A **ReportQualityGate** produces one **ReportQualityResult** containing zero or more **ReportQualityViolations**.
- A report blocked by **ReportQualityGate** is preserved as quality evidence, not as a user-ready report artifact.
- A **SignalReportWorkflow** run that reaches report rendering records **ReportQualityResult** evidence whether the report passes or fails the quality gate.
- **ReusableDomainCapability** must not import an **IntegrationAdapter**.
- **IntegrationAdapter** must not depend on a **ThinWorkflow**.
- **ReferenceParityTarget** is reached through successive **SignalReportWorkflow** extensions and reusable domain capabilities, not by copying legacy code.
- **BreadthOperationsReference** and **DepthProductQualityReference** are both inputs to the rewrite; neither is copied or treated as the sole target architecture.
- An **OperationalETFDataSource** may supply an **OperationalHoldingsExport**, but Agent TReport must not mutate the live operational folder or import ETF Tracker runtime code.
- The legacy **OperationalETFDataSource** upstream is the ETF Tracker local manifest; the forward upstream for native operational handoff is Agent TReport-owned SourceProvider-collected **HoldingsHistoryStore** output.
- The complete Agent TReport ETF analysis end-to-end target is reached through **DataCollectionIndependence**; the `sync-operational-holdings` path remains a migration and backfill bridge for already-crawled holdings, not the final collection architecture.
- A **CollectedHoldingsOutput** may reuse the **OperationalHoldingsExport** file shape for the first slice, but it is produced by Agent TReport native collection and must not require `sync_metadata.json` or an **OperationalETFDataSource**.
- A **HoldingsSnapshot** should be collected at most once for the same **ETF** and observed date to avoid unnecessary load on asset-manager servers; correcting an erroneous prior collection requires an explicit refresh path.
- Re-running native holdings collection without a **HoldingsSnapshotRefresh** skips already stored **HoldingsSnapshots** when rows match, fails with refresh-required evidence when rows differ, and must not silently overwrite stored holdings.
- Native source holdings acquisition may update successful ETF/date **HoldingsSnapshots** even when other targets in the same acquisition run fail; failed or rate-limited targets are recorded in path-safe acquisition evidence and the run is marked partial or completed with failures.
- Native source acquisition target outcomes are `fetched`, `skipped_existing`, `failed`, `rate_limited`, and `unsupported`; acquisition run outcomes are `succeeded`, `partial`, and `failed`. `stale` remains an observed-date freshness/readiness concern rather than a fetch target outcome.
- A **SourceProviderRolloutStatus** of `catalog_only` or `blocked` is acceptable for an all-provider rollout only when the stop condition is path-safely documented with offline fixture coverage and failure classification; it is not a report readiness outcome.
- Live source acquisition models rate-limit evidence and safe failure classification with provider-specific request spacing, low retry caps, timeouts, and stop-on-blocked behavior for 403, 429, anti-bot, or credential-required responses.
- SourceProvider transport/connectivity failures are classified as `provider_unavailable` evidence rather than provider payload/response evidence; they do not create **LiveRetryCooldown** records because they are not blocked or rate-limited provider-policy evidence.
- A bulk live replacement baseline is complete only when no provider stops, no planned comparison window gap remains, and no attempted target outcome is `failed`, `rate_limited`, or `unsupported`.
- Bulk live backfill pacing uses same-host request spacing rather than ETF-count spacing. The default spacing is `1.2` seconds plus `0.0` to `0.4` seconds jitter per request; SOL and RISE use more conservative reference-level provider overrides unless later evidence supports lowering them.
- The bounded live smoke path keeps validation to one catalog acquisition plus at most two selected ActiveStrategyETF/date holdings candidates per provider. Bulk holdings and backfill use the separate live replacement baseline flow with representative gating, provider failure isolation, and pacing evidence.
- When an explicitly accepted live rollout goal escalates from bounded smoke to bulk live backfill, that backfill is scoped to tracked **ActiveStrategyETF** entries only. Passive and unknown-strategy catalog entries are excluded from the live backfill scope rather than treated as later backfill work.
- The first live replacement baseline requires complete required-window coverage for every tracked **ActiveStrategyETF** in the active provider cohort before that cohort is promoted away from the operational copy. A provider with documented host-level block/rate-limit evidence may be excluded from the active cohort and retried in later daily runs rather than blocking the completed non-blocked cohort. The required baseline window is the latest observed holdings date plus the nearest available prior business-date snapshot for each tracked **ActiveStrategyETF**. Collection planning must inspect existing **HoldingsHistoryStore** coverage first and request only missing ETF/date snapshots from SourceProviders.
- Steady-state operational analysis must not wait on one failed asset manager or one failed **ETF** target. Exclude the failed provider or ETF from that run, record path-safe warning/exclusion evidence, continue analysis with available eligible holdings, and retry the excluded provider or ETF on the next daily collection cycle. When the retry succeeds and the required current/prior window exists, the provider or ETF naturally re-enters analysis without manual intervention.
- A provider or **ETF** with documented temporary blocked, rate-limited, or required-window gap evidence is excluded from the **EligibleHandoffCohort** instead of remaining in its coverage denominator. The exclusion remains operator-visible recovery evidence and next-collection retry work; it does not block handoff for other eligible providers or **ETFs**.
- Operational handoffs use a **FocusETFSet** instead of a single focus **ETF**. The handoff remains user-ready when at least three user-selected focus **ETFs** are in the eligible coverage set with complete current and previous **HoldingsSnapshots**; excluded focus **ETFs** become warning evidence. When fewer than three focus **ETFs** are eligible, readiness is `hold`, not `failed`, because the issue is insufficient focus coverage rather than a broken copied-export contract.
- A **FocusETFSetFile** records only user analysis intent. It must not include provider availability, exclusion evidence, selected comparison dates, retry state, raw provider data, or operational readiness outcomes. Operational CLI paths should accept a FocusETFSetFile as the default focus input; single-focus arguments remain compatibility inputs only.
- **LiveRetryCooldown** suppresses repeat retry attempts for the affected SourceProvider or **ETF** for 24 hours after blocked or rate-limited evidence without introducing a scheduler, daemon, or database. Planning reads the local cooldown evidence, skips targets still in cooldown, and includes cooldown counts in daily health. After cooldown expires, planning includes all missing windows needed to catch up the **HoldingsHistoryStore**.
- **HandoffExclusionEvidence** may include source provider id, canonical **ETF** id, exclusion scope, reason code, missing observed dates, selected current and previous dates, last successful observed date, retry timestamp, cooldown timestamp, cooldown remaining seconds, next backfill date count, capped **ETF** id samples, aggregate counts, and safe human-readable messages. It must not include ProviderETFId, URLs, endpoints, hosts, headers, credentials, response bodies or status text, raw provider payloads or envelopes, raw holdings or security rows, absolute or local paths, environment values, or stack traces.
- A missing observed date before an **ETF** listing or before source-provider holdings publication begins is valid **HandoffExclusionEvidence** rather than a provider failure; it should close the retry item for that date while leaving later eligible dates available for handoff.
- Detailed **HandoffExclusionEvidence** belongs in **CollectionSummary** and daily health. **OperationalRunReadiness** receives only decision-relevant path-safe summaries and disclosures. **ReportPayload** receives concise final-user data-quality warnings only.
- For bounded live validation, an explicit provider ETF id is an exclusive holdings smoke selection set; after one candidate failure, the procedure records that failure path-safely and does not try an alternate **ActiveStrategyETF** target in the same smoke command.
- Durable cross-run cooldowns, broad backoff policy, and scheduler-grade provider-load protection are future live-provider hardening work, but one process must not keep retrying a host after a rate-limited or blocked outcome.
- A **HoldingsSnapshotRefresh** replaces the stored **HoldingsSnapshot** with the latest collected rows and records refreshed snapshot evidence in the path-safe collection summary.
- A **HoldingsSnapshotRefresh** is scoped to the exact selected **ETF** and observed date; broad recrawls must be represented as multiple explicit snapshot refreshes, not as an implicit full overwrite.
- The **HoldingsHistoryStore** is separate from **CollectedHoldingsOutput**: the store keeps cumulative **HoldingsSnapshots**, while export views generate the normalized manifest and partitions consumed by **OperationalRunReadiness** and **SignalReportWorkflow**.
- A SourceProvider-collected **HoldingsHistoryStore** may be kept as path-safe normalized project data to avoid re-fetching already disclosed ETF composition snapshots across clones; raw provider payloads, URLs, headers, credentials, and provider envelopes remain outside that store.
- `data/agent_treport/live-source/holdings-history/` is the canonical location for SourceProvider-collected normalized cumulative holdings history. This cumulative history is commit-eligible preserved project data because historical ETF composition may not be reproducible later and should not be pruned by rolling retention. Repeated run evidence, report artifacts, runtime databases, daily smoke summaries, and similar reproducible per-run outputs use rolling retention, defaulting to the latest 10 runs.
- Final **PrePublishPreview** runs store a path-safe result package under `data/agent_treport/live-source/daily-smoke-summaries/<run_id>/` by default for later debugging and provider-policy hardening; the package may include handoff, smoke summary, approval/preflight summary, external evidence summary, provider exception summary, and validation command results, but not raw payloads, URLs, credentials, environment values, or absolute paths.
- SourceProvider-collected holdings follow the fixed native handoff sequence: **HoldingsHistoryStore** -> latest comparison export -> normalized **OperationalHoldingsExport** plus **CollectionSummary** fingerprint -> **OperationalRunReadiness** -> **SignalIntelligenceReport**. Provider-specific raw structures and provenance stay behind source acquisition or source state boundaries.
- Live SourceProvider holdings can be compared against the same asset manager's operational copy as replacement evidence when the same ETF and observed date have matching constituent security codes, holding amounts, and weights within an explicit tolerance. The default live replacement evidence tolerance is exact security-code match, absolute weight difference at or below `0.01` percentage points, absolute market-value difference at or below `1` KRW, and absolute share difference at or below `0.000001` when shares are present on both sides. Missing shares on one side are diagnostic warnings, not blockers. Display names, ticker display values, and classification labels are parsing diagnostics unless they change the constituent code, amount, or weight comparison.
- The **HoldingsHistoryStore** preserves originally observed holdings identity fields; reviewed **SecurityResolutionExport** decisions are applied by export views instead of mutating stored history.
- When a reviewed **SecurityResolutionExport** conflicts with stored native holdings ticker or classification fields, the export view uses the reviewed decision for normalized output while leaving stored history unchanged.
- Only exported **SecurityResolutionExport** mappings and exclusions can override native holdings classification heuristics; proposed, review-required, unresolved, and conflicting **SecurityMaster** entries do not affect normalized output.
- Native holdings history update evidence may include counts, affected **ETF** ids, observed dates, update action, row counts, active **ETF** coverage ratio, missing active **ETF** ids, selected **HoldingsComparisonWindow** dates, and export fingerprint; it must exclude raw holdings rows, security-level rows, absolute local paths, provider URLs or payloads, and credentials.
- Source acquisition summary evidence may include `source_provider_id`, `brand_id`, canonical `etf_id`, scope, requested dates, observed dates, selected current or previous dates, target outcome, row counts, reason code, retry attempt count, cooldown or retry timestamp, missing observed dates, next backfill date count, run outcome, and aggregate counts. It must exclude provider-local ETF keys, raw network locators, response metadata/content, machine paths, auth material, raw holdings rows, and raw provider wrappers; report-visible output receives only readiness or data-quality summaries derived from this evidence.
- Live holdings acquisition separates `requested_date`, `provider_query_date`, and `observed_date`: the operator request, the date sent to the SourceProvider after business-day normalization if needed, and the holdings snapshot date actually returned.
- A **HoldingsObservedDateGap** is expected when the requested holdings date is not a business day or when the requested business date has not yet been published by the SourceProvider. If normalized holdings rows and exactly one valid observed date are returned, the target outcome remains `fetched`; missing observed date, empty rows, mixed observed dates in one target response, or invalid provider payload remains `failed`.
- Latest uploaded holdings smoke succeeds when the target outcome is `fetched`, rows are non-empty, exactly one `observed_date` is present, and the provider-returned latest upload is current for that SourceProvider. If `observed_date >= requested_date`, the target may be accepted as latest evidence; if `observed_date < requested_date`, only same-day, prior-day, or prior-business-day freshness relative to `provider_query_date` may be accepted, otherwise the snapshot is stale evidence.
- A SourceProvider-returned latest holdings snapshot older than the prior business day may be stored as a **HoldingsSnapshot** with stale-latest warning evidence, but it should not upgrade **SourceProviderRolloutStatus** to `supported`.
- `requested_date`, `provider_query_date`, `observed_date`, and path-safe date-alignment evidence belong in source acquisition operator evidence. **HoldingsSnapshot** identity and **HoldingsHistoryStore** storage use `observed_date`.
- The forward native operator flow is `collect universe -> update holdings history -> export latest comparison -> check operational readiness -> run report`; the older direct fixture holdings export remains a compatibility path, not the forward history-owning path.
- A first-slice **CollectedHoldingsOutput** must be able to pass **OperationalRunReadiness** and feed **SignalReportWorkflow** to final `user_ready` output using path-safe collection evidence, even when the collector is fixture-backed rather than live-provider-backed.
- A **CollectionSummary** is the native collection readiness evidence counterpart to legacy sync metadata; **OperationalRunReadiness** should use it for native collected outputs while legacy sync and backfill exports keep using `sync_metadata.json`.
- Native collection uses existing **OperationalRunReadiness** outcome meanings: broken collected holdings contracts are `failed`, missing or insufficient collection evidence is `hold`, disclosed non-blocking collection limitations are `ready_with_warnings`, and clean evidence is `ready`.
- A **CollectionSummary** is operator evidence; **SignalIntelligenceReport** should receive only concise user-relevant readiness disclosures and data-quality issues derived from it, not system operation details or the full summary.
- A native history **CollectionSummary** carries path-safe security coverage evidence for readiness, including ticker-candidate coverage, unresolved and unknown security counts, non-ticker exclusion counts, reviewed resolution availability, and aggregate recovery samples without raw rows or local paths.
- Native export security coverage should include **TickerCollisionReviewEvidence** when the same ETF/date has one display or lookup ticker across multiple observed securities without a shared **ReviewedSecurityGroup**; share-class mismatches remain separation or regression evidence rather than automatic groups.
- Native security coverage reuses sync ticker coverage thresholds for **OperationalRunReadiness**; unresolved `unknown` holdings are recovery work and produce at least `ready_with_warnings` until classified.
- Native security coverage keeps `ticker_mapping_coverage_ratio` aligned with existing sync semantics as ticker-present ticker candidates divided by all ticker candidates, while separately disclosing whether a reviewed **SecurityResolutionExport** was available and how many reviewed mappings or exclusions affected output.
- A native history comparison export without a reviewed **SecurityResolutionExport** is at best `ready_with_warnings` for **OperationalRunReadiness**, even when source-supplied ticker coverage is complete; fixture/direct compatibility paths do not inherit this warning.
- Native collection is the forward Agent TReport-owned collection path; the legacy sync and backfill bridge remains supported for already-crawled ETF Tracker data but is not the default place to add new collection capabilities.
- Existing normalized **OperationalHoldingsExport** data may be imported into the **HoldingsHistoryStore** as a minimal backfill path, applying the same **HoldingsSnapshot** de-duplication, refresh-required conflict, and **HoldingsSnapshotRefresh** replacement rules without expanding ETF Tracker sync, security resolution, or live provider behavior.
- An **OperationalHoldingsExport** can feed **SignalReportWorkflow** through an **IntegrationAdapter** while preserving deterministic local execution.
- An **OperationalHoldingsExport** owns the local copied partition lookup boundary; Agent TReport ignores source-manifest absolute partition paths and allows only copied-manifest-relative partition paths that stay inside the copied export directory.
- An **OperationalExportFingerprint** covers the canonical copied manifest content plus every copied partition file referenced by that manifest, not only the current and previous focus-ETF partitions selected by a single run.
- An **OperationalSyncQualityDiagnostic** is produced by `sync-operational-holdings` and may mark source-data risk as `warning` or `risk_failed` while keeping the sync command exit successful unless the input contract itself is invalid.
- `risk_failed` in an **OperationalSyncQualityDiagnostic** means a denominator-backed source-data quality ratio crossed a configured risk threshold; isolated non-zero counts are warning candidates, not risk failures by themselves.
- An **OperationalSyncQualityDiagnostic** summary must not expose live source absolute paths even when full sync metadata preserves path provenance for local operator diagnostics.
- An **OperationalSyncQualityDiagnostic** is distinct from **ReportQualityGate**; it can inform **OperationalRunReadiness** and payload data-quality disclosure without becoming a quality-gate failure by itself.
- A **SecurityMapping** may resolve a copied holding's ticker, but it must not replace the holding's stable `security_id`.
- If a **SecurityMapping** does not resolve a non-cash holding, the copied holding's ticker remains null instead of falling back to `security_id`.
- A **SecurityMappingRecoverySample** represents an unmapped non-cash holding observed during sync so a local operator or later assistance loop can improve **SecurityMapping** deterministically after sync.
- A native **SecurityMappingRecoverySample** includes unresolved `ticker_candidate` and `unknown` holdings only; reviewed `cash_like` and `non_equity` holdings are represented as excluded counts rather than recovery samples.
- A **SecurityMappingRecoverySample** exposes aggregate identity evidence only: security id, representative name, observed row/date/ETF counts, alias count, and native classification when needed to distinguish `unknown`; it excludes ETF ids, observed dates, row values, local paths, provider payloads, and URLs.
- A **SecurityMappingRecoveryProposal** may inform a **SecurityMappingPatch**, but it is not itself an accepted **SecurityMapping** change.
- A **SecurityMappingRecoveryProposal** can be requested from aggregate recovery samples in legacy sync metadata or native **CollectionSummary** evidence; the proposal remains untrusted and cannot change **SecurityMapping** or **SecurityResolutionExport** without review.
- A **SecurityMappingPatch** changes a **SecurityMapping** only after human review and deterministic validation.
- The native forward recovery path records reviewed recovery decisions in **SecurityMaster**, then regenerates **SecurityResolutionExport** for the next native history comparison export; **SecurityMappingPatch** remains the legacy minimal mapping compatibility path.
- Reviewed native security recovery does not require a **HoldingsSnapshotRefresh**; after **SecurityMaster** review and **SecurityResolutionExport** regeneration, re-exporting the **HoldingsComparisonWindow** refreshes normalized output, recovery samples, coverage evidence, and fingerprint.
- A **CashLikeHolding** is not a ticker candidate; coded and uncoded cash holdings keep their identity through `security_id` and `is_cash=true`.
- An **OperationalDataQualityProjection** can make source-data risk visible in a **ReportPayload** without changing the **ReportQualityGate** blocking contract by itself.
- **OperationalRunReadiness** may use **OperationalSyncQualityDiagnostic** evidence to advise ready, hold, or failed operator outcomes, but it does not change **OperationalSyncQualityDiagnostic** or **ReportQualityGate** semantics.
- `ready` and `ready_with_warnings` **OperationalRunReadiness** outcomes allow `run-report`; `hold` requires operator review, recovery, or source refresh first; `failed` means the pre-report input contract is broken.
- **OperationalRunReadiness** is checked through a local operator command before `run-report`; it does not decide whether report generation is technically possible, but it decides whether an operational run may expose final `user_ready` output.
- If `run-report` succeeds while **OperationalRunReadiness** is `hold`, the generated artifacts are an **OperatorReviewOnlyReport**, not a **LocalFollowUpContract** success for final users.
- An explicit `failed` **OperationalRunReadiness** handoff means the copied input contract is broken enough that operational `run-report` should stop before model calls or artifact creation in the same CLI flow.
- An explicit `hold` **OperationalRunReadiness** handoff requires an operator-review override before `run-report` spends model cost or creates review artifacts; without the override, the CLI should stop before model calls or artifact creation.
- A successfully generated **OperatorReviewOnlyReport** is an operationally successful CLI run, but its output must mark the report as operator-review-only and must not include final `user_ready` output.
- **OperatorReviewOnlyReport** output may include generated report, quality, and readiness evidence artifact references under `operator_review_only.artifacts`, but those references must not be exposed under `user_ready`.
- **OperatorReviewOnlyReport** output may include an inspect command for local review under `operator_review_only.commands`; that command is not a **LocalFollowUpContract** for final delivery.
- The operator-review override is meaningful only for `hold` or missing-readiness operational runs; combining it with `ready` or disclosure-valid `ready_with_warnings` readiness is ambiguous and should stop before model calls, SQLite setup, or artifact creation.
- When operational `run-report` stops before model calls because readiness does not allow a user-ready run and no operator-review override was given, it is a CLI input/contract error rather than a workflow failure.
- `ready_with_warnings` **OperationalRunReadiness** may still produce final `user_ready` output only when an **OperationalReadinessDisclosure** is present; otherwise operational `run-report` fails closed as a CLI input contract error before artifacts are created.
- An **OperationalReadinessDisclosure** must be visible in the **LocalFollowUpContract** and reflected in the **SignalIntelligenceReport** data-quality section so automation can judge delivery eligibility and final users can see the data limitation in the report itself.
- An **OperationalReadinessDisclosure** summarizes readiness warning code, human-readable message, available metric/value/threshold, and recommended action; it must not expose live paths, raw metadata, sample rows, or full sync payloads.
- The canonical source for report-visible **OperationalReadinessDisclosure** is `ReportPayload.data_quality`; Markdown, HTML, and Telegram surfaces should inherit the disclosure from the payload rather than injecting renderer-specific warning text.
- Final operational `user_ready` output should always include a readiness summary proving **OperationalRunReadiness** was checked; when readiness is `ready`, disclosure entries are empty and no readiness limitation is added to `ReportPayload.data_quality`.
- Report-visible readiness warnings are medium-severity data-quality issues scoped to `operational_readiness` with `readiness_`-prefixed codes; exact user-facing warning copy is product text and should not be treated as a stable domain contract unless a later acceptance test requires it.
- Report-visible readiness hold reasons are high-severity data-quality issues scoped to `operational_readiness` on **OperatorReviewOnlyReport** artifacts; `failed` readiness does not project into a report because report generation should not start.
- **OperationalRunReadiness** data-quality issues do not make **ReportQualityGate** fail by themselves; CLI delivery gating owns user-ready withholding for readiness holds.
- When operational `run-report` consumes **OperationalRunReadiness**, it should preserve a path-safe readiness projection as durable operational evidence inspectable after the run; `user_ready.readiness` remains the delivery summary, not the full evidence record.
- A hold-overridden **OperatorReviewOnlyReport** should preserve the consumed readiness projection as evidence; an override with no readiness handoff creates a synthetic path-safe readiness evidence artifact that records `readiness_not_provided` as the review-only reason.
- Missing-readiness **OperatorReviewOnlyReport** artifacts should also project `readiness_not_provided` into `ReportPayload.data_quality` as a high-severity `operational_readiness` issue using the existing `readiness_readiness_not_provided` code style.
- The durable readiness projection may include readiness status, user-ready allowance, **FocusETFSet**, eligible focus **ETF** count, requested observed partitions, selected dates, observed-age fields, the **OperationalExportFingerprint**, warnings, reasons, next actions, and non-sensitive aggregate summary counts or ratios; it must exclude holdings paths, sync metadata paths, source paths, and sample rows.
- `user_ready.readiness` should stay a compact delivery summary containing readiness status, **FocusETFSet** summary, selected dates, disclosures, and readiness artifact id; detailed reasons, actions, and aggregate diagnostics belong in the readiness evidence artifact.
- Each compact readiness disclosure in `user_ready.readiness` should include its recommended action, while detailed next-action lists and command hints remain in the readiness evidence artifact.
- The CLI **CompositionLayer** owns readiness handoff loading, matching, path-safe projection, and delivery gating; **SignalReportWorkflow** should receive readiness-derived disclosure through provider provenance rather than a new generic runtime API.
- The dedicated readiness evidence artifact is `artifact_treport_operational_readiness` named `operational_readiness.json`; final operational `user_ready.readiness` references this artifact id.
- Fixture `run-report` is outside **OperationalRunReadiness** and should keep the existing `user_ready` shape without operational readiness summary or disclosure fields.
- An operational `run-report` must receive an explicit **OperationalRunReadiness** handoff to qualify for final `user_ready`; the readiness handoff must match the same **OperationalHoldingsExport**, **FocusETFSet**, and requested observed-partition window.
- Missing **OperationalRunReadiness** requires an operator-review override to create **OperatorReviewOnlyReport** artifacts; mismatched **OperationalRunReadiness** stops before model calls even with an override. Fixture runs do not require operational readiness.
- **OperationalRunReadiness** includes a top-level **OperationalExportFingerprint** computed by `check-operational-readiness` at handoff time; operational `run-report` recomputes the same fingerprint at consumption time and treats a missing or different fingerprint as a mismatched readiness handoff.
- `check-operational-readiness` should not emit an **OperationalRunReadiness** handoff when an **OperationalExportFingerprint** cannot be computed for the copied manifest and all referenced copied partitions.
- Synthetic `not_provided` readiness evidence for missing-readiness **OperatorReviewOnlyReport** output does not include an **OperationalExportFingerprint** because no readiness handoff inspected the copied export.
- Operational `run-report` without an explicit readiness handoff should stop before model calls by default; creating review-only artifacts without readiness requires an explicit operator-review override.
- **OperatorReviewOnlyReport** should state why it is review-only: `readiness_not_provided` when no handoff was supplied under an override, or `readiness_hold` when a hold handoff was explicitly overridden.
- `not_provided` is not an **OperationalRunReadiness** outcome from `check-operational-readiness`; it is the synthetic evidence status used only when an operator explicitly permits an **OperatorReviewOnlyReport** without a readiness handoff.
- Mismatched **OperationalRunReadiness** must stop operational `run-report` before model calls even when an operator-review override is present, because attaching the wrong readiness evidence is unsafe for review artifacts too.
- If `ready_with_warnings` disclosures cannot be proven in the readiness handoff before model calls, operational `run-report` must fail closed as a CLI input contract error instead of creating **OperatorReviewOnlyReport** artifacts.
- **OperationalRunReadiness** separates same-day sync recency from copied observed-date freshness; an old sync and an old latest holdings date are different operator problems.
- **OperationalRunReadiness** is **FocusETFSet**-specific and answers whether today's operational run can produce a report for enough requested focus **ETFs**, not whether the whole copied universe is healthy.
- **OperationalRunReadiness** uses `failed` for broken copied-export contracts and `hold` for insufficient focus coverage or reviewable trust risks where a report might still be technically runnable.
- **OperationalRunReadiness** keeps one top-level outcome while exposing reason-specific operator next actions, especially the **SecurityMapping** recovery loop for low ticker mapping coverage.
- A `cash_derivation_failure_ratio` risk failure is a high-priority **OperationalRunReadiness** hold reason because cash exposure and flow interpretation are not trustworthy enough for final user-ready delivery, even when `run-report` can technically generate artifacts.
- A `cash_derivation_failure_ratio` hold requires `review_cash_derivation_risk`: review cash rows, market-value evidence, source weight evidence, and rerun operational sync when needed; it must not be presented as a **SecurityMapping** recovery problem.
- `recover_ticker_mapping` is the required action for low ticker mapping coverage, not for cash derivation risk.
- A warning-level `cash_derivation_failure_ratio` may still allow final `user_ready` only with an **OperationalReadinessDisclosure** explaining that some cash weights were market-value-derived and cash/flow interpretation is limited; its recommended action is `review_cash_derivation_warning`, not a required hold action.
- **DataCollectionIndependence** is reached through later **IntegrationAdapter** and **ReusableDomainCapability** slices; **OperationalRunReadiness** can gate both legacy synced **OperationalHoldingsExport** input with `sync_metadata.json` and native **CollectedHoldingsOutput** input with **CollectionSummary** evidence.
- A **SecurityMaster** is richer operator-review state; a **SecurityResolutionExport** is the compiled reviewed contract consumed by normalized holdings exports, including `sync-operational-holdings` and native history comparison export, and a **SecurityMapping** is the backward-compatible minimal `security_id -> ticker` export.
- A **SecurityMaster** may contain unresolved or review-required entries, but a **SecurityResolutionExport** contains only entries eligible for deterministic ticker display or deterministic non-ticker exclusion during normalized holdings export.
- Resolver structural rules may auto-verify ticker candidates only when the identifier itself is strong evidence: six-digit KRX codes map to themselves, Korean ISINs map to their six-digit display ticker, and Bloomberg-style equity codes map to the leading ticker plus market code.
- A **SecurityResolutionExport** must not include proposed, unresolved, review-required, or conflicting ticker candidates.
- **SecurityMasterStatus** values `verified` and `auto_verified` are eligible for **SecurityMapping** export, `proposed` and `review_required` require more evidence or operator review, `unresolved` means no usable candidate exists yet, `conflict` preserves contradictory evidence without overwriting an accepted value, and `excluded` marks non-ticker candidates such as cash-like or non-equity holdings.
- **SecurityIdentifierType** describes the shape of the observed identifier; resolver rule ids and evidence describe how a ticker candidate was derived or verified.
- **SecurityClassification** owns ticker-candidate denominator semantics: `cash_like` rows use `is_cash=true`, `non_equity` rows use `is_cash=false` and `ticker=null` without being treated as missing-ticker ticker candidates, and `unknown` rows remain reviewable until classified.
- **SecurityMasterConfidence** is not sufficient for **SecurityMapping** export without an export-eligible **SecurityMasterStatus** and accepted source or rule evidence.
- An **IntegrationAdapter** maps provider-specific holdings fields such as `fund_id`, `fund_name`, `code`, `weight_pct`, `quantity`, `eval_amount_krw`, and raw `as_of_date` into the active Agent TReport terms `etf_id`, `etf_name`, `security_id`, `ticker`, `weight_percent`, `shares`, `market_value_krw`, and ISO snapshot dates during sync; copied operational exports and committed test fixtures use the active Agent TReport terms, while provider fields without current domain targets stay in provenance or later enrichment slices.
- An **OperationalHoldingsExport** carries source-data quality evidence in sync metadata; **SignalReportWorkflow** provenance may include the quality subset without duplicating live source paths or full sync envelope fields.
- **DerivedCashWeight** preserves cash holdings with omitted source weights only when the ETF snapshot has enough market-value evidence to estimate the cash weight; otherwise the affected cash row is excluded and counted as source-data quality metadata.
- **DerivedCashWeight** uses the ETF snapshot's valid market-value total as denominator and should be accepted only when source-weight rows in the same snapshot fit that denominator closely enough to trust the derivation.
- **DerivedCashWeight** may be negative when the source cash market value is negative and the ETF snapshot's net market-value denominator remains positive.
- An **UncodedCashHolding** is preserved in an **OperationalHoldingsExport** with a generated Agent TReport `security_id`, no ticker, and enough identity to distinguish it from coded cash holdings in the same ETF snapshot.
- **SignalReportPayload** cash summary calculations may aggregate coded cash holdings and **UncodedCashHolding** rows through `is_cash`, while the underlying holdings remain separately identifiable.
- **CashLikeHolding** includes cash, currency, deposit-style rows, and MMF, REPO, T-BILL, short-term bond, commercial paper, or CP rows only when maturity evidence is clearly less than three months; maturity-unknown bond-like rows are classified as non-equity rather than cash-like.

## Example Dialogue

> **Dev:** "Should Google Finance lookup live inside the ETF change report workflow?"
> **Domain expert:** "No. Treat it as an **IntegrationAdapter** used by a **ReusableDomainCapability**, then let the **ThinWorkflow** compose it."

> **Dev:** "Can the market research capability import yfinance directly?"
> **Domain expert:** "No. Define a **Port** for market data, implement it in an **IntegrationAdapter**, and inject it through the **CompositionLayer**."

> **Dev:** "When we say the next agent should be usable, do we mean a generic runtime Agent actor?"
> **Domain expert:** "No. We mean the **UserReadyLocalAgent** target: the **SignalReportWorkflow** must be locally runnable and inspectable through persisted runtime evidence and artifacts."

> **Dev:** "Should the **SignalReportWorkflow** return inspect or trace-export commands?"
> **Domain expert:** "No. That is the **LocalFollowUpContract** owned by the CLI **CompositionLayer** after a successful local run."

## Flagged Ambiguities

- "Reference implementation" means observable legacy behavior and structure to learn from, not code to port or import.
- `ETF_tracker-main` means **BreadthOperationsReference**; `Agent_TReport-main` means **DepthProductQualityReference** unless explicitly discussing file-level behavior.
- `manager` is ambiguous in ETF source discussions; use **ETFBrand** for the ETF brand or asset-management-company identity, and use individual portfolio manager only when a source explicitly provides person-level manager evidence.
- `ETF_tracker` without `-main` may mean the live **OperationalETFDataSource** rather than the read-only **BreadthOperationsReference**; resolve this distinction before implementation work.
- The legacy **OperationalETFDataSource** uses ETF Tracker local data as a migration/backfill upstream, not a permanent runtime dependency or a copied reference implementation.
- "Freshness" in operational run discussion can mean sync recency or holdings observed-date recency; resolve it as two separate **OperationalRunReadiness** checks.
- "Agent" in Agent TReport means a **UserReadyLocalAgent** or another runnable product workflow unless explicitly discussing `agent_pack` runtime primitives.
- "Simple but complete agent" means local operational completeness for the current **SignalReportWorkflow** surface: run, model call, persisted events, context views, snapshots, artifacts, inspection, and artifact review. It is not the same as the complete ETF analysis end-to-end target, which now includes Agent TReport-owned live collection, enrichment, readiness, and report handoff beyond the legacy sync bridge.
- Historical milestone names remain archive-only. Active code, tests, fixtures, and documentation should use **SignalReportWorkflow** and current signal report terminology.
- Use **ETF** as the active domain term instead of mixing ETF and fund names. Provider-specific raw field names may still be documented at adapter boundaries later.
- "active ETF" is ambiguous: use **ActiveStrategyETF** for 액티브 운용 ETF, and use universe `status=active` or currently tracked **ETF** for local tracking status.
- Use **ReportQualityGate**, **ReportQualityContract**, **ReportQualityResult**, **ReportQualityViolation**, and **ProhibitedInvestmentLanguagePolicy** for report quality language. Avoid "quality checker", "validator", and "compliance" because those terms imply generic validation or regulatory compliance rather than the Agent TReport product-quality gate.
- "Quality diagnostics" during operational sync means **OperationalSyncQualityDiagnostic**, not **ReportQualityGate** or report readiness, unless explicitly discussing payload or renderer quality.
- `weight_pct=null` on source cash rows means missing source weight, not zero exposure; resolve it as **DerivedCashWeight** when market-value evidence is available.
- An empty source `code` does not always mean an unusable row; when the row is identifiable as cash, treat it as an **UncodedCashHolding** instead of applying the generic missing-security skip rule.
