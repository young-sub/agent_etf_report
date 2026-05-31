# Signal Intelligence Report Structure

## Status

Working product structure with canonical payload v1 implemented. The current
implementation is deterministic and fixture-first under
`agent_treport.signal_report` and `agent_treport.workflows.signal_report`.

## Product Definition

`SignalIntelligenceReport` is the target Agent TReport research product for active ETF behavior. It is not a raw holdings change table and not a news digest. It interprets ETF brand behavior from holdings changes, multi-ETF confirmation, external evidence, and data quality.

The product question is:

```text
Which securities, sectors, themes, or cash positions are gaining or losing active ETF brand attention, how reliable is that signal, and what should be checked next?
```

## Reference Insights

`DepthProductQualityReference` (`../references/Agent_TReport-main`) contributes report density and enrichment expectations:

- ETF-level `new_positions`, `exited_positions`, and `weight_changes`.
- Target ticker selection from buy, sell, and weight-change categories.
- Ticker enrichment through financial metrics, recent price returns, analyst ratings, news, web search, sector or industry data, and charts.
- Korean Telegram/report style that emphasizes concise conclusions and readable sections.
- Risk: some legacy prompts overreach into BUY/HOLD/SELL ratings, price targets, and investment recommendations.

`BreadthOperationsReference` (`../references/ETF_tracker-main`) contributes broad universe operations:

- Multi-brand and multi-ETF identifiers: `brand_id`, `source_provider_id`, and `etf_id`.
  Reference or provider rows that call the ETF identifier `fund_id` must map it to
  `etf_id` at the source parser boundary. Copied operational exports and tests use
  `etf_id`.
- Cumulative holdings, security normalization, country/sector/theme classification, cash separation, and coverage metrics.
- Estimated security flow, net flow, gross flow, ETF participation count, ETF brand participation, cash movement, and data quality metrics.
- Structured `analytics_payload`, `report_tracks`, `telegram_layout_v2`, objective metrics, and eval casebooks.
- Track-style analysis for active ETF trends and top buy/sell picks.

## Core Principle

The runtime should build one canonical `ReportPayload` first. Telegram, HTML, PDF, and later quality gates should render or evaluate that payload instead of independently regenerating numbers or judgments.

```text
Raw ETF Holdings
-> Holdings Change Engine
-> Signal Scoring Engine
-> Evidence Enrichment
-> ReportPayload
-> Telegram Signal Alert
-> HTML Research Report
-> PDF Snapshot
```

LLMs may help produce explanatory text after the structured payload exists, but the source of truth for numbers, labels, scores, evidence grades, and data quality is the payload.

Model commentary is optional and policy-gated. Renderers may include safe
explanatory commentary, but commentary that introduces BUY/HOLD/SELL-style
ratings, price targets, trading-action language, allocation advice, or claims
that canonical payload scores, review labels, evidence grades, or data-quality
findings should change must be omitted while the report still succeeds.

The `ReportQualityGate` evaluates the canonical `ReportPayload` plus the final
rendered Markdown preview after commentary policy has already been applied. It
release-blocks error-severity product-quality violations before the Markdown
report is stored as user-ready output. The default contract currently blocks
missing required Markdown preview sections, including Market Map, ETF Follow
Sheets, and Evidence Ledger; missing required payload top-level sections;
prohibited investment language; forbidden rendered fragments; raw `claim_scope`
or raw `used_in` exposure; missing Signal Board value reflection for ticker,
English review label, Korean display review label, and English evidence grade;
and missing representative target-section values from the Markdown preview.

Every rendered report attempt that reaches quality evaluation stores
`quality.json` as internal quality evidence. Warning-only quality results keep
the workflow succeeded and expose `quality_report` alongside the canonical
payload and Markdown report in the local user-ready output. Error-severity
quality failures return `report_quality_failed`, keep the quality artifact and
state summary, and do not store `report.md`.

Successful `SignalReportWorkflow` runs also persist a read-only harness
evaluator review as `harness_evaluator_review.json`. The evaluator checks the
already-produced payload, Markdown, HTML, Telegram alert, and quality artifacts
against the explicit report-quality rubric, records `pass`, `fail`, or
`uncertain` verdict semantics, and publishes the result through
`agent_pack_review_summaries` for run inspection, `TraceExportRecord`, and
optional MLflow export. It does not regenerate artifacts, mutate prompts,
skills, schemas, routing, memory, or repository files.

`HTMLResearchReport` is now implemented as a local `report.html` artifact
rendered from the same canonical `ReportPayload` as Markdown. Successful local
runs expose it through `output.user_ready.artifacts.html_report` while keeping
`markdown_report`; both rendered reports share the single `quality.json`
artifact and the same `ReportCommentaryPolicy` omission behavior. HTML quality
checks extend the shared `ReportQualityGate` with HTML-scoped required-section,
representative-value, Signal Board value, prohibited-language, forbidden
fragment, raw `claim_scope`, and raw `used_in` checks before either report is
stored as user-ready output.

`TelegramSignalAlert` is now implemented as a durable local `telegram_alert.txt`
artifact rendered from the same canonical `SignalReportPayload`. It contains
only Telegram `sendMessage.text` for HTML parse mode; parse mode, the linked
full-report artifact id, and quality status stay in artifact metadata and
`quality.json`. Successful local runs expose it through
`output.user_ready.artifacts.telegram_alert`.

The first HTML slice is dependency-free and local-only: one self-contained
HTML file with internal CSS and vanilla JavaScript for Signal Board
sorting/filtering. All content remains readable without JavaScript. Drilldowns
use anchors and `<details>` for Ticker Dossiers, Evidence Ledger, ETF Follow
Sheets, Market Map, Data Quality, and Methodology. The slice intentionally
excludes React, Vite, external assets, charts, PDF, publishing, auth, live data,
dashboard iframe integration, and generic dashboard rendering changes.

HTML Signal Board JavaScript is verified through manual/tool browser smoke on a
deterministic generated artifact, not through committed Playwright, Selenium, or
frontend dependency tests yet.

Market Map, ETF Follow Sheets, and Evidence Ledger are required Markdown report
sections. Missing target headings fail with `missing_markdown_section`, and
missing representative target-section payload values fail with
`markdown_target_section_value_missing`. The Markdown preview is extended in
place; there is no separate `MarkdownV2` surface for this requirement.

Deterministic payload text should not become stiff boilerplate. It should preserve uncertainty, conflicting evidence, and multiple plausible readings when the data does not support one clear interpretation. Over-interpretation is worse than under-interpretation.

## Output Stack

| Output | Role | Design Constraint |
| --- | --- | --- |
| `ReportPayload` | Single JSON-compatible source of truth | Contains all calculations, labels, evidence links, and data quality fields. |
| `TelegramSignalAlert` | Korean-first scoreboard alert | Durable local Telegram HTML message text in `telegram_alert.txt`; top 5 ranked signals max, no delivery credentials, no representative source links in this slice. |
| `HTMLResearchReport` | Main exploration surface | Local self-contained artifact with filterable/sortable Signal Board, anchors, `<details>` drilldowns, evidence links, ETF follow sheets, and dossier cards. |
| `PDFSnapshot` | Share/archive fixed copy | Shorter than HTML, includes executive summary, signal board, top dossiers, methodology, and evidence appendix. |

## Canonical Payload Shape

The target shape is:

```json
{
  "meta": {},
  "coverage": {},
  "executive_summary": {},
  "signal_board": [],
  "market_map": {},
  "etf_follow_sheets": [],
  "ticker_dossiers": [],
  "evidence_ledger": [],
  "methodology": {},
  "data_quality": {}
}
```

All generic fields must remain strict JSON-compatible values before being stored in artifacts or runtime payloads.

Payload fields should not disappear when optional enrichment is absent. Unknown scalar values should be represented as `null`, empty collections as `[]`, and missing enrichment should be recorded in `data_quality.limitations` or `data_quality.issues`. This keeps renderer behavior deterministic while making coverage limits visible.

## Data Quality

`ReportPayload.data_quality` is the user-facing source of truth for report data
quality. Operational holdings provenance remains a separate operator/debug
artifact, but the workflow passes the path-safe provenance subset from the input
provider into payload building so selected operational diagnostics are visible in
the rendered report.

The payload projection is intentionally narrow:

- If operational sync metadata is unavailable, `data_quality.issues` includes
  `operational_sync_metadata_unavailable` at medium severity and records a
  Korean limitation explaining that source-data diagnostics were not included.
- `sync_quality.warnings` become medium-severity issues with codes prefixed by
  `operational_`.
- `sync_quality.risk_failures` become high-severity issues with codes prefixed
  by `operational_`.
- `sync_quality.status="warning"` or `"risk_failed"` adds the corresponding
  Korean operational limitation. `status="ok"` does not add an operational
  limitation.
- Scalar sync-quality metrics are appended as coverage notes using
  `operational_<metric>=<value>`, with null ratios rendered as
  `not_applicable`.
- `unmapped_security_samples` is sync metadata and CLI stdout recovery input for
  **SecurityMapping** work. It is not projected into `ReportPayload`, run-report
  provenance, issues, limitations, or coverage notes.

The projection must not dump raw sync metadata, source paths, URLs, timestamps,
sample rows, or distribution objects into the payload. Row-level issues such as
`missing_ticker` remain separate from export-level operational issues, and high
operational data-quality issues do not make `ReportQualityGate` fail by
themselves.

Operational live runs use a separate operator readiness step before `run-report`.
See `operational-live-runbook.md` for the explicit
`sync-operational-holdings -> check-operational-readiness -> run-report` flow,
readiness statuses, freshness checks, SecurityMapping recovery guidance, and
final user-ready requirements.

Operational `run-report` now requires an explicit readiness handoff for final
`user_ready` delivery. `ready` adds `user_ready.readiness` with empty
disclosures. `ready_with_warnings` can add `user_ready.readiness` only when the
warnings project to user-facing disclosures, and the same warnings are
projected into `ReportPayload.data_quality` as medium-severity
`operational_readiness` issues with `readiness_` code prefixes and
`readiness_<metric>=<value>` coverage notes. `hold` can produce only
`operator_review_only` output when the operator supplies the explicit override;
its reasons project to high-severity `operational_readiness` payload issues.
Missing readiness with that explicit override also produces
`operator_review_only` output, synthetic path-safe readiness evidence with
status `not_provided`, and a high-severity `operational_readiness` issue using
the existing `readiness_readiness_not_provided` code. The override is invalid
with deliverable `ready` or disclosure-valid `ready_with_warnings` readiness
and stops before model calls, SQLite setup, or artifact creation.
These readiness issues disclose delivery risk but do not make
`ReportQualityGate` fail by themselves, because the quality gate remains a
product/rendering contract.

## Security Resolution Recovery Flow

`SecurityMaster` is the operator-facing ledger for observed securities.
`SecurityResolutionExport` is the compiled sync-facing contract consumed by
`sync-operational-holdings`. The legacy `SecurityMapping` remains supported as a
minimal `security_id -> ticker` export, but new universe-wide recovery should
flow through `SecurityMaster`.

Ticker coverage semantics are now classification-aware:

- `ticker_candidate` rows form the denominator for
  `ticker_mapping_coverage_ratio`.
- `cash_like` and `non_equity` rows are excluded from that denominator and
  counted as non-ticker exclusions.
- `unknown` rows remain review work until classified.

The local SecurityMaster loop is:

1. Run `agent-treport sync-operational-holdings` and inspect
   `sync_metadata.json` or the CLI stdout summary for
   `unmapped_security_samples`, `security_classification`, and
   `ticker_mapping_coverage_ratio`.
2. Run `agent-treport import-security-master-seed --stock-mapping-csv
   <stock_mapping.csv> --workspace data/agent_treport/security-master
   --output-path data/agent_treport/security-master/security_master.json`.
   Seed rows create `auto_verified` entries unless an existing verified or
   auto-verified entry conflicts; conflicts are preserved and written to the
   review queue.
3. Run `agent-treport resolve-security-master --holdings-path <copied_manifest>
   --security-master-path <security_master.json> --output-path
   <security_master.resolved.json> --review-queue-path <review_queue.json>`.
   Structural rules mark clear cash-like and non-equity rows as `excluded` and
   auto-verify clear ticker identifiers such as KRX codes, Korean ISIN display
   tickers, and Bloomberg-style equity codes; unresolved ticker candidates go
   to the review queue. OpenFIGI lookup is enabled by default and may
   auto-verify unambiguous equity matches.
   If OpenFIGI stops at the configured request cap, rerun the command with the
   previous resolved master as `--security-master-path`; unresolved entries are
   retried while accepted and review-blocked entries are preserved.
4. Review `review_queue.json` manually. No LLM or external lookup proposal is
   approved automatically.
5. Run `agent-treport export-security-resolution --security-master-path
   <security_master.resolved.json> --output-path <security_resolution.json>`.
   The export excludes `proposed`, `review_required`, `unresolved`, and
   `conflict` entries.
6. Re-run sync with `--security-resolution-path <security_resolution.json>` and
   compare `sync_quality.metrics.ticker_mapping_coverage_ratio`.

The older SecurityMapping proposal loop remains available for minimal mapping
patches and can read either legacy sync metadata or native collection-summary
evidence:

1. Run `agent-treport sync-operational-holdings` and inspect
   `sync_metadata.json` or the CLI stdout summary for
   `unmapped_security_samples` and `ticker_mapping_coverage_ratio`. For native
   history, inspect `collection_summary.json.security_coverage` for
   `recovery_samples`, unresolved ticker-candidate count, unknown count, and
   `ticker_mapping_coverage_ratio`.
2. Run `agent-treport propose-security-mapping-recovery --sync-metadata-path
   <sync_metadata.json> --model codex --output-path <proposal.json>`, or
   `agent-treport propose-security-mapping-recovery --collection-summary-path
   <collection_summary.json> --model codex --output-path <proposal.json>`.
   The proposal command reads only the selected evidence file and sends only
   `security_id`, `name`, `observed_row_count`, `observed_etf_count`,
   `observed_date_count`, `name_alias_count`, and native
   `security_classification` when needed to distinguish `unknown` from each
   sample to the model.
   It does not read source manifests, partition rows, source URLs, ETF ids,
   dates, or an existing mapping. On success, stdout emits a compact
   schema-versioned operator result with the source evidence type or echoed
   `sync_metadata_path`, `output_path`, proposal counts, and `model_called`.
3. Review the proposal artifact manually. A
   `SecurityMappingRecoveryProposal` is untrusted model output and is not
   eligible to update a mapping directly.
4. Create a reviewed `SecurityMappingPatch` with only `schema_version` and
   `mappings`, where each mapping contains only `security_id` and `ticker`.
5. Run `agent-treport apply-security-mapping-patch --security-mapping-path
   <security_mapping.json> --patch-path <patch.json> --output-path
   <merged_security_mapping.json>`. Use `--allow-replacements` only when a
   reviewer explicitly accepts changing an existing ticker, and use
   `--overwrite` only to replace the selected output file. On success, stdout
   emits a compact schema-versioned operator result with the echoed mapping,
   patch, and output paths plus merge counts.
6. For legacy sync, re-run sync with
   `--security-mapping-path <merged_security_mapping.json>` and compare
   `sync_quality.metrics.ticker_mapping_coverage_ratio`. For native history,
   review decisions belong in `SecurityMaster`, then regenerate
   `SecurityResolutionExport` and re-run
   `export-holdings-comparison --security-resolution-path
   <security_resolution.json>`; no holdings history refresh is required.

The saved proposal schema is
`agent_treport.security_mapping.recovery_proposal.v1`. Legacy sync proposal
artifacts contain `schema_version`, `source_sync_metadata_path`, and
`proposals`; native collection-summary proposal artifacts identify
`source_evidence_type="native_collection_summary"` and the collection summary
file name path-safely. The saved merged mapping remains the existing
`agent_treport.security_mapping.v1` schema only: `schema_version` and sorted
`mappings`. ADR 0004 defines the full saved artifact schemas and the separate
CLI success stdout result schemas.

## Meta

`meta` identifies the report and the analysis envelope.

Required target fields:

- `report_id`.
- `as_of_date`.
- `period.current`.
- `period.previous`.
- `period.lookback_days`.
- `universe`.
- `report_type`.
- `language`.
- `generated_at`.
- `report_version`.
- `scoring_version`.

## Coverage

`coverage` is the trust floor of the report. A convincing report with weak coverage is dangerous.

Target fields include:

- `etf_count`.
- `holding_rows`.
- `securities_count`.
- `source_provider_count` or `brand_count`.
- `mapped_security_ratio`.
- `price_coverage_ratio`.
- `financial_coverage_ratio` from `external_evidence_summary.json`, where
  `null` means the external financial evidence stage was not summarized.
- `disclosure_coverage_ratio` from `external_evidence_summary.json`, where
  `null` means the external disclosure evidence stage was not summarized.
- `news_coverage_ratio`, where `null` means news enrichment was not run or not
  summarized and `0.0` means it ran but found no target evidence. When an
  external evidence summary is present, its news category ratio is the source
  of truth; otherwise the legacy evidence-ticker heuristic is preserved.
- `analyst_coverage_ratio`, where `null` means analyst enrichment was not
  provided for payload v1.
- `classification_coverage_ratio`.
- `ticker_mapping_coverage_ratio`.

External evidence coverage is category-aware for financial, disclosure, and
news evidence. Not-run, skipped, failed, no-data, and covered states appear in
coverage notes or data-quality limitations instead of being hidden behind a
single zero ratio. `analyst_coverage_ratio` remains `null`/not-provided in this
goal and is not reused as a generic external-evidence ratio.

## Executive Summary

The summary should place the conclusion first, then risks.

Target fields:

- `headline`.
- `market_read`.
- `top_takeaways`.
- `primary_risks`.

The summary should not make direct investment recommendations. It can say a signal is worth tracking, watching, treating cautiously, or withholding because data quality is insufficient.

## Signal Board

`SignalBoard` is the central table of the product. It ranks securities or cash/theme signals by structured scores.

Required target columns:

- `rank`.
- `ticker`.
- `name`.
- `market`.
- `sector`.
- `theme`.
- `signal_direction`.
- `signal_type`.
- `participating_etfs`.
- `net_flow_estimate_krw`.
- `weight_delta_pp`.
- `holding_delta_shares`.
- `new_or_exit`.
- `signal_score`.
- `confidence`.
- `evidence_grade`.
- `review_label`.
- `primary_reason`.

Review labels are signal-review states, not investment advice or trading actions:

| Label | Meaning |
| --- | --- |
| `focus` / `중점 모니터링` | Stronger signal worth priority review because it is confirmed across ETFs or supported by relatively stronger evidence. |
| `monitor` / `모니터링` | Meaningful change, but evidence is limited, early, or needs more follow-up. |
| `caution` / `유의` | Holdings signal conflicts with outside evidence or has meaningful interpretation risk. |
| `defer` / `판단 유보` | Data quality or coverage is insufficient for a useful interpretation. |

Canonical enum values should remain English snake_case for stable tests, filters, and quality gates. Korean labels should be stored in lightweight `display` objects for renderer reuse. Korean terminology should follow finance, ETF, research, and asset-management usage; avoid unnatural literal translations that practitioners would not use. Avoid `action_label` naming because it can read as trading instruction; use `review_label` instead.

## Signal Types

Signal types should explain ETF brand behavior more precisely than simple increase/decrease labels.

Initial target vocabulary:

- `new_position`.
- `full_exit`.
- `weight_increase`.
- `weight_decrease`.
- `multi_etf_accumulation`.
- `multi_etf_distribution`.
- `rotation_in`.
- `rotation_out`.
- `cash_raise`.
- `theme_concentration`.
- `conviction_add`.

## Market Map

`market_map` summarizes the universe-level flow.

Target slices:

- `by_theme`.
- `by_sector`.
- `by_country`.
- `cash_position`.
- `concentration`.
- `crowding`.

This is where multi-brand/multi-ETF aggregation belongs. It should not be hidden inside ticker prose.

## ETF Follow Sheets

`etf_follow_sheets` provide focus-ETF and per-ETF drilldowns.

Each ETF sheet should eventually include:

- ETF identity and ETF brand.
- AUM, NAV, and recent return when available.
- Top holdings.
- New positions.
- Exited positions.
- Increased positions.
- Decreased positions.
- Cash, short-term bond, derivative, or TRS changes.
- Theme exposure changes.
- Brand behavior read.
- Data quality.

The report should support a focus ETF lens without losing the universe view. A focus ETF signal is more useful when compared to whether the broader universe confirms, diverges from, or ignores the same security/theme.

## Ticker Dossiers

`TickerDossier` explains why a top signal matters.

Each dossier should contain four things:

- Holding-change facts.
- Why-now hypothesis.
- Supporting evidence.
- Counter evidence or invalidation conditions.

Target fields:

- `ticker`.
- `name`.
- `summary`.
- `holding_facts`.
- `why_now_hypothesis`.
- `supporting_evidence`.
- `counter_evidence`.
- `invalidation_conditions`.
- `final_label`.

## Evidence Ledger

`EvidenceLedger` is the source table for report evidence. Evidence should not be buried only inside prose.

Target evidence types:

- `holding_change`.
- `company_disclosure`.
- `earnings`.
- `analyst_report`.
- `regulatory`.
- `market_reaction`.
- `news`.
- `price_volume`.
- `valuation`.
- `macro`.
- `sector_data`.
- `other`.

Target fields:

- `evidence_id`.
- `ticker` or `scope`.
- `type`.
- `source`.
- `title`.
- `published_at`.
- `url`.
- `stance`.
- `strength`.
- `claim_scope`.
- `evidence_role`.
- `relevance`.
- `novelty`.
- `interpretation_basis`.
- `observed_direction` for `holding_change` evidence.
- `used_in`.

External evidence is collected outside report generation through
`ExternalEvidenceEnrichment`. Provider adapters first normalize live or fixture
responses into category candidates. `run-report` receives only compiled
`EvidenceItemInput` JSON and, optionally, an adjacent or explicit
`external_evidence_summary.json`; it never receives raw provider payloads,
headers, endpoint URLs, credentials, or provider exceptions.

External evidence is claim-scoped. It may affect `external_evidence_support` or
`contradiction_penalty` only when its `claim_scope` matches the current
`SignalBoardRow.claim_scope`, it is curated for that claim, and it has a
non-empty `interpretation_basis`. News titles or article text must not be used
to infer price direction.

Default collection emits context evidence only. Optional claim alignment is a
bounded classifier step over SignalBoard claim summaries and normalized
candidates. Low-confidence, weakly grounded, neutral, or ambiguous alignment
stays `evidence_role="context"` and does not change score. Only high-confidence
support or challenge decisions with an interpretation basis compile to scoring
claim-scoped evidence.

Each `SignalBoardRow` also contributes one `holding_change` evidence ledger
entry as a claim-scoped `primary_observation`. That entry supports the holdings
change claim itself, not a price direction or investment action, and it must not
increase `external_evidence_support`.

Raw `claim_scope` values may appear in payload JSON and `used_in` references,
but user-facing Markdown must use display text derived from signal metadata,
such as "NVDA 다중 ETF 비중 확대 신호", rather than the raw scope string.

Report-level evidence displays `보고서 전체 근거`, unmatched raw signal
references display `관련 신호 미확인`, unknown `used_in` references display
`관련 위치 미확인`, and empty `used_in` displays `미사용`.

External evidence source URLs are report-visible only when an adapter marks a
public canonical URL safe. API endpoint URLs, signed URLs, tracking URLs,
credential-bearing URLs, raw query URLs, headers, and provider raw URLs must not
be rendered.

## Evidence Grade

Evidence grades should separate holdings facts from inferred reasons.

| Grade | Meaning |
| --- | --- |
| `Confirmed` | Holding change plus direct filing, earnings, explicit event, or strong corroborating evidence. |
| `Plausible` | Holding change plus news, financial, sector, or industry support. |
| `Weak` | Holding change exists but outside evidence is thin. |
| `Conflicted` | ETF behavior conflicts with news, earnings, price/volume, or other evidence. |
| `Unusable` | Mapping, date, price, quantity, or coverage quality prevents interpretation. |

## Scoring Direction

Signal scores should be computed, not free-form LLM judgment.

Initial scoring components:

```text
signal_score =
  position_change_strength
  + cross_etf_confirmation
  + portfolio_materiality
  + external_evidence_support
  + recency_alignment
  - data_quality_penalty
  - contradiction_penalty
```

Recommended starting weights:

| Component | Meaning | Weight |
| --- | --- | ---: |
| `position_change_strength` | Size of the weight, share, or market-value change itself. | 30 |
| `cross_etf_confirmation` | Degree to which the same directional signal appears across ETFs or brands. | 20 |
| `portfolio_materiality` | Importance of the security inside ETF portfolios, such as current weight, new position, full exit, or top-holding relevance. | 15 |
| `external_evidence_support` | Degree to which claim-scoped curated non-holdings evidence supports the holdings signal. Strong=12, moderate=8, weak=3. | 12 |
| `recency_alignment` | Alignment between the analysis period and the timing of supporting evidence. | 10 |
| `data_quality_penalty` | Penalty for unmapped securities, missing identifiers, missing classifications, or coverage gaps. | up to -20 |
| `contradiction_penalty` | Penalty when claim-scoped curated outside evidence conflicts with the holdings signal. Weak=-5, moderate=-10, strong=-15. | up to -15 |

The exact formula should be introduced only when a behavior slice tests it.

## Renderer Guidance

`TelegramSignalAlert` should answer whether the full report is worth opening.

Telegram constraints:

- Render Telegram HTML parse-mode message text only; workflow metadata records
  `telegram_parse_mode="HTML"` and the full HTML report artifact reference.
- Use `<b>` and `<code>` only as renderer-created tags, and HTML-escape all
  payload-derived text.
- Select from canonical `payload.signal_board` sorted by `rank`, top 5 max.
  Do not recalculate scores, thresholds, labels, evidence grades, or
  data-quality findings, and do not filter out `defer` or `Unusable` rows.
- Include a compact data-quality line and the fixed full-report reference
  `HTML artifact: <code>artifact_treport_html_report</code>`.
- Do not add representative source links, PDF links, Telegram delivery,
  credentials, scheduling, or retry behavior in this slice.

`HTMLResearchReport` is the main analysis surface because it can support
sorting, filtering, collapsible cards, links, and drilldowns. The current
implementation is a local artifact, not a server app or dashboard replacement.

`PDFSnapshot` is an archive/share artifact. It should summarize the HTML report rather than replace it.

During the canonical payload slice, Markdown remains the first local report artifact, but it should render from `ReportPayload` rather than from separate change calculations. Markdown is a preview/report surface, not the source of truth. It must not recalculate scores, labels, evidence grades, or data-quality findings.

## Data Boundaries

The fixture-first payload, temporary operational sync bridge, native collection
path, external evidence enrichment, and identity-safe evidence attachment are
implemented. The active product roadmap is now
`data-collection-independence-roadmap.md`, which moves Agent TReport from ETF
Tracker copied exports to Agent TReport-owned ETF universe, ETF brand metadata,
holdings history, reviewed security identity, evidence enrichment, and native
operational handoff. Live integrations must still enter through
`IntegrationAdapter`s behind ports.

Financial, disclosure, and news evidence collection is implemented through
fixture-backed tests and bounded opt-in live adapters. External evidence still
enters through `IntegrationAdapter`s behind ports, and the report payload
continues to consume path-safe domain evidence instead of provider payloads.

Current evidence categories:

- Financial evidence: price/volume, valuation, earnings, recommendation trend,
  and basic financial metrics when available.
- Disclosure evidence: company disclosures, earnings filings, ownership or
  insider events, material events, and regulatory events.
- News evidence: article headline, source, published date, relevance or
  sentiment when provided, and a bounded catalyst summary.

Potential data sources for these categories:

- Issuer holdings snapshots or PCF data.
- Daily trade disclosure where available.
- Regulatory filings.
- ETF brand commentary and ETF materials.
- Price and volume data.
- Financial metrics.
- Earnings and company disclosures.
- Analyst reports.
- News and web search.

## Historical First Payload Slice Boundaries

The following bullets were out of scope for the completed first payload slice.
They are not current roadmap exclusions when a later goal explicitly promotes
them. Later slices have promoted Telegram rendering, local HTML rendering,
bounded live holdings adapters, and bounded financial, disclosure, and news
evidence adapters.

- Telegram rendering was out of scope for the first payload slice; it is now
  addressed by the later TelegramSignalAlert local renderer slice.
- Telegram delivery.
- Live or framework-backed HTML rendering.
- PDF rendering.
- Live holdings adapters.
- Live news/search/price adapters were out of scope for the first payload slice;
  bounded financial, disclosure, and news adapters are now implemented behind
  explicit opt-in provider adapters.
- LLM judge or subjective quality scoring.
- BUY/HOLD/SELL recommendations or price targets.

## Completed Follow-Up Slice

The completed payload slice is archived at
`archive/plans/canonical-signal-report-payload-v1.md`.

The completed `UserReadyLocalAgent` slice is archived at
`archive/plans/user-ready-local-agent.md`. `SignalReportWorkflow` can now be run
locally through `agent-treport run-report`, inspected through persisted runtime
evidence, and reviewed through generated report artifacts and trace export.

The completed first-slice `ReportQualityGate` plan is archived at
`archive/plans/report-quality-gate.md`. `SignalReportWorkflow` now stores
quality evidence for each rendered report attempt, blocks error-severity
Markdown/payload quality failures before `report.md` becomes user-ready, and
exposes `quality_report` in successful local output. The Markdown preview now
renders Market Map, ETF Follow Sheets, and Evidence Ledger in place, and the
quality gate treats missing target headings or representative target values as
release-blocking renderer contract drift.

The completed `HTMLResearchReport` local artifact foundation renders and stores
`artifact_treport_html_report` as `report.html` with media type `text/html`.
Successful local output exposes `html_report` alongside `markdown_report`,
while quality failures still store only the shared `quality.json` artifact.

The completed `TelegramSignalAlert` local renderer slice renders and stores
`artifact_treport_telegram_alert` as `telegram_alert.txt` with media type
`text/plain`. Successful local output exposes `telegram_alert` alongside the
canonical payload, Markdown report, HTML report, and shared quality report.
`ReportQualityGate` now includes a `telegram_alert` scope; Telegram alert
quality errors block all user-ready rendered artifacts while still preserving
`quality.json`.

The completed evaluator-only harness review slice adds
`artifact_treport_harness_evaluator_review` as a read-only review artifact and
stores `review.signal_report_evaluator` in `agent_pack_review_summaries`.
Trace export and MLflow consume that review through the existing review-summary
surface; no generic Planner/Generator/Evaluator runtime abstraction was added.
