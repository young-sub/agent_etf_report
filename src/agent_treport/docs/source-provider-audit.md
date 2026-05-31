# Source Provider Acquisition Audit

## Status

Created for the Native ETF Source Acquisition Foundation goal on 2026-05-15.
Updated for provider-flexible live handoff on 2026-05-16.
Updated for provider-scoped operational cache layout contract on 2026-05-31.

Current handoff state:

- The report handoff denominator is the EligibleHandoffCohort after path-safe
  exclusions, not every provider target attempted during acquisition.
- EquityAnalysisETF scope excludes bond, CD-rate, money-market, government
  bond, corporate bond, financial bond, mixed treasury, and delisted products
  even when provider names include active-management wording.
- The latest live history has 21,967 rows across 445 snapshots, updated at
  2026-05-16T03:54:19.505533+00:00. The target week 2026-05-11 through
  2026-05-15 has 73 eligible EquityAnalysisETFs and 364 of 365 expected
  latest-week snapshots.
- The remaining latest-week gap is HYUNDAI canonical ETF
  `etf_hyundai_2912753` for 2026-05-11 with repeated
  `invalid_provider_payload` and no observed date. This is target-level
  exclusion evidence plus retry/backfill work, not a global handoff failure.
- Source acquisition summaries omit provider-local ETF target keys from
  default evidence; the internal source catalog still retains provider target
  keys for acquisition.

## Provider-Scoped Operational Cache Layout

Issue #17 promotes provider-scoped operational cache layout as an offline
validation substrate before history reconciliation, daily refresh, security
resolution rollout, or report/readiness cutover. The registered live
SourceProvider cohort is `kodex`, `ace`, `hyundai`, `timefolio`, `tiger`,
`rise`, and `sol`; each provider maps to
`data/agent_treport/live-source/source-provider-operational/<provider>/` or a
generated equivalent from `inspect-operational-source-cache`.

Expected child artifacts are `catalog/source_catalog.json`,
`catalog/universe_state.json`, `catalog/source_acquisition_summary.json`,
`focus_etf_set.json`, `holdings-history/`, and `security-master/`. ACE remains
the reference fixture when present, but missing provider artifacts are reported
as actionable diagnostics rather than silently passing readiness. Provider ids,
FocusETFSet ids, and referenced artifact paths must stay path-safe and within
the provider cache root.

Issue #18 adds offline holdings-history reconciliation for this layout. The
canonical mixed history remains the source of truth; provider histories are
validated against it or created only when missing. Reconciliation summaries must
preserve canonical totals, report provider row/ETF/snapshot/date coverage, flag
missing/extra/changed provider snapshots, keep the HYUNDAI 2026-05-11
`etf_hyundai_2912753` gap explicit when supplied as expected evidence, and leave
KODEX partial coverage visible through missing canonical dates.

## Scope

This audit reviews the read-only reference provider behavior before adding the
first Agent TReport-owned live source adapter. The reference code is behavioral
evidence only; provider parsing in Agent TReport must be reimplemented behind
new domain-facing source acquisition contracts.

Audited reference sources:

- `../references/ETF_tracker-main/etf_change_report_agent/ingestion/entry_catalog/collector.py`
- `../references/ETF_tracker-main/etf_change_report_agent/pipeline/fetch_holdings.py`
- `../references/ETF_tracker-main/etf_change_report_agent/pipeline/holdings_router.py`
- `../references/ETF_tracker-main/GUIDE.md`
- `../references/Agent_TReport-main/etf_team/gathering_etf_data.py`

## Common Contract

The provider set supports a common Agent TReport source acquisition contract:

- Source catalog acquisition is scoped by `source_provider_id`.
- A source catalog entry identifies a provider ETF by
  `source_provider_id + provider_etf_id`.
- A source catalog entry can map to canonical Agent TReport universe fields:
  `etf_id`, `etf_name`, `brand_id`, `brand_name`, and `source_provider_id`.
- Source catalog entries preserve optional strategy/activity labels and explicit
  ActiveStrategyETF classification fields:
  `is_active_strategy_etf`, `active_strategy_source`, and
  `active_strategy_confidence`.
- The full provider catalog remains in `source_catalog.json`. The staged
  `universe_state.json` built from SourceProvider acquisition includes
  ActiveStrategyETF entries only by default; passive and unknown strategy
  entries remain reviewable catalog evidence.
- Holdings fetch targets use `source_provider_id + provider_etf_id` plus one
  requested business date.
- Holdings fetch results return a target outcome, requested date, observed date,
  normalized holdings rows, row count, failure code class, and retry count.
- Operator evidence can summarize ids, dates, outcomes, ActiveStrategyETF
  classification counts, classification source/confidence, stale-latest
  warnings, and failure classes without exposing URLs, endpoints, raw rows, raw
  payloads, headers, or local paths.

## ActiveStrategyETF Classification

Agent TReport now classifies SourceProvider catalog entries before mutating
`universe_state.json`.

Classification precedence:

- Explicit passive evidence from catalog metadata or product-name keywords is
  applied before active evidence.
- Explicit high-confidence SourceProvider metadata can classify an entry as
  ActiveStrategyETF.
- Agent TReport-owned seed evidence may classify only exact
  `source_provider_id + provider_etf_id` matches. Seed files are local
  Agent TReport fixtures derived from read-only reference evidence, not runtime
  dependencies on the reference project.
- TIMEFOLIO entries default to ActiveStrategyETF with medium confidence.
- The product-name token `액티브` can classify an entry with low confidence
  when stronger passive or contradictory evidence is absent.
- Entries still unclear after those rules remain unknown and are excluded from
  default universe and holdings targets.

`source_acquisition_summary.json` records active-strategy, passive-strategy,
and unknown-strategy counts plus capped unknown review samples. It also records
classification source and confidence for catalog entries while staying
path-safe.

## Bounded Latest Holdings Smoke

Default SourceProvider holdings targets are EquityAnalysisETF universe entries.
They are active strategy ETFs whose holdings are appropriate for equity-style
analysis after the fixed bond-like and delisted exclusions are applied.
Passive, unknown-strategy, and non-equity-analysis entries remain in source
catalog evidence but are excluded from default collection targets and handoff
denominators.

For bounded live smoke, explicit provider ETF ids are exclusive for the holdings
command. A failed or stale selected target records path-safe failure evidence
and does not trigger an unselected alternate EquityAnalysisETF request. When
multiple provider ETF ids are supplied, every selected id is processed; a single
selected id remains the bounded one-target smoke path. Default unselected target
ordering remains deterministic: high-confidence SourceProvider metadata, then
high-confidence seed evidence, then TIMEFOLIO provider default, then
low-confidence name-token evidence, with catalog order as the tie-breaker.

Latest holdings smoke is considered support evidence only when the candidate
outcome is fetched or live-confirmed skipped as an existing matching snapshot,
rows are non-empty, a single observed date is present, and that observed date is
accepted as the provider-returned latest evidence. If `observed_date >=
requested_date`, the target may be accepted as fresh latest evidence. If
`observed_date < requested_date`, only same-day, prior-day, or prior-business-
day freshness relative to `provider_query_date` is accepted. Older fetched
snapshots are still stored in `holdings_history`, but the summary records
`stale_latest_holdings` warning evidence and the provider rollout status remains
`catalog_only`.

This common contract is enough for fake providers, staged catalog-to-universe
mutation, source-backed history updates, path-safe summaries, and one live
provider smoke per bounded operator run.

## Provider-Specific Behavior

### TIGER

- Catalog uses an AJAX HTML listing endpoint with pagination and HTML row
  parsing around `data-ksd-fund`.
- Holdings use a POST endpoint keyed by `ksdFund`, `fixDate`, and a period/list
  shape.
- Provider-specific concerns: HTML parsing, Korean detail labels, separate
  listing page HTML for return/listing metadata, and KSD fund id extraction.
- Agent TReport implementation status: `supported` as of the 2026-05-15
  operator-bounded ActiveStrategyETF smoke. Catalog smoke succeeded with 226
  entries and 27 ActiveStrategyETF candidates. Holdings for provider ETF id
  `KR7471780007`, requested/provider query/observed date `2026-05-13`,
  fetched 29 rows and wrote one snapshot.

### KODEX

- Catalog uses a JSON endpoint with page number and page size. Rows expose
  provider ids such as `fId` and names such as `fNm`.
- Holdings use JSON product and product-PDF endpoints keyed by the same provider
  ETF id and a dotted `gijunYMD` requested date.
- Provider-specific concerns: KODEX JSON field names, dated PDF fallback to the
  product payload, and provider date fields such as `gijunYMD`.
- This is the cleanest first live adapter because catalog and holdings are JSON,
  no credentials are evident in the reference behavior, and one ETF/date smoke
  can be bounded without crawling every ETF.

### TIMEFOLIO

- Catalog is parsed from HTML anchors containing `idx`.
- Holdings are parsed from an HTML table on a detail page keyed by `idx` and
  `pdfDate`.
- Provider-specific concerns: table scraping, request-date query mutation, and
  Excel URL construction. The older Agent TReport reference also relies on
  Timefolio Excel downloads, which is useful behavioral evidence but too narrow
  and file-oriented for the first Agent TReport native source contract.
- Agent TReport implementation status: `supported` at the SourceProvider
  adapter seam as of the 2026-05-15 parser closure. A bounded in-memory live
  smoke for provider ETF id `5`, requested/provider query/observed date
  `2026-05-14`, returned `outcome="fetched"`, `row_count=56`, and
  `freshness_status="fresh_latest"`.
- Closure finding: the live table can include holdings with no provider
  security code. Agent TReport now keeps those non-cash rows as
  `security_classification="unknown"` with runtime-owned uncoded security ids
  instead of failing the whole target.

### ACE

- Catalog and holdings use JSON APIs under ACE domains.
- Holdings combine detail/product metadata and a PDF holdings API keyed by
  `fundCd` and `std_dt`.
- Provider-specific concerns: multiple API calls per target, provider field
  names, and API response envelope differences.
- Agent TReport implementation status: `supported` as of the 2026-05-15
  operator-bounded ActiveStrategyETF smoke. Catalog smoke succeeded with 108
  entries and 22 ActiveStrategyETF candidates. Holdings for provider ETF id
  `K55101DH7878`, requested/provider query/observed date `2026-05-14`, fetched
  50 rows and wrote one snapshot.

### SOL

- Catalog combines HTML parsing and a paginated API search endpoint.
- Holdings require detail-page context and an AJAX PDF list endpoint keyed by
  `fund_cd` and `work_dt`.
- Provider-specific concerns: headers/referer behavior, tokenized download path
  for Excel, and slower provider-specific spacing in the reference.
- Agent TReport implementation status: `supported` as of the 2026-05-15
  operator-bounded ActiveStrategyETF smoke. Catalog smoke succeeded with 79
  entries and 17 ActiveStrategyETF candidates. Holdings for provider ETF id
  `211099`, requested/provider query/observed date `2026-05-14`, fetched 21
  rows and wrote one snapshot.

### HYUNDAI

- Catalog uses a JSON ETF list API with nested fund objects.
- Holdings require product metadata to derive `fundCode` and `etfCode`, then a
  separate holdings API keyed by date.
- Provider-specific concerns: encoded Korean field names and two identifiers
  needed before holdings fetch.
- Agent TReport implementation status: `supported` as of the 2026-05-15
  operator-bounded ActiveStrategyETF smoke. Catalog smoke succeeded with 5
  entries and 5 ActiveStrategyETF candidates. Holdings for provider ETF id
  `2338258`, requested/provider query/observed date `2026-05-13`, fetched 39
  rows and wrote one snapshot.

### RISE

- Catalog is HTML-first with detail-page enrichment for activity labels.
- Holdings use detail HTML plus a POST endpoint that returns holdings table HTML.
- Provider-specific concerns: HTML table parsing, detail-page extraction, and
  conservative per-host spacing.
- Agent TReport implementation status: `supported` at the SourceProvider
  adapter seam as of the 2026-05-15 parser closure. A bounded in-memory live
  smoke for provider ETF id `44H6`, requested/provider query/observed date
  `2026-05-14`, returned `outcome="fetched"`, `row_count=39`, and
  `freshness_status="fresh_latest"`.
- Closure finding: the live holdings response can be a row fragment rather
  than a full table document. Agent TReport now accepts row fragments at the
  adapter seam while preserving the same normalized holdings row contract.

## Reference Structure Assessment

The reference structure is a useful guide for provider behavior and load risks,
but it is accidental coupling for Agent TReport:

- It mixes URL identity, provider id, manager id, local persistence paths, raw
  rows, retries, rate-limit state, and output shaping in the same flow.
- It treats target URLs as central routing inputs, while Agent TReport has
  decided that URL/endpoint locators are not target identity.
- It stores and exposes raw source URLs and local paths in some operational
  outputs, which Agent TReport must exclude from default evidence.
- It includes broad multi-provider crawling, rotation, cooldown persistence, and
  backfill behavior that are future work for Agent TReport, not first-slice
  foundation requirements.

Agent TReport should reuse the behavioral lessons: provider-specific parsers
behind a provider-neutral contract, conservative live opt-in, safe failure
classification, and no automated live regression.

## Registered Live Providers

KODEX was the first live provider, but live provider expansion is no longer a
future roadmap gap. The registered live SourceProvider cohort is KODEX, ACE,
HYUNDAI, TIMEFOLIO, TIGER, RISE, and SOL, all behind the source acquisition
contract and explicit `--live --source-provider` opt-in.

If a live endpoint requires credentials, browser automation, heavy anti-bot
bypass, or broad crawling for the supported SourceProvider surface, that
provider remains excluded from the current run with path-safe evidence instead
of blocking the completed cohort.

Manual smoke on 2026-05-15 confirmed the bounded KODEX path remained usable for
ActiveStrategyETF holdings: catalog acquisition returned 234 entries with 18
ActiveStrategyETF candidates, and selected provider ETF id `2ETFH5` fetched
observed date `2026-05-14` with 34 normalized rows.

## Rollout Status

As of the operator-bounded ActiveStrategyETF live smoke run on 2026-05-15:

| Provider | Status | Bounded Live Evidence |
| --- | --- | --- |
| KODEX | `supported` | Catalog 234 entries, 18 ActiveStrategyETF candidates; provider ETF id `2ETFH5`; requested/provider query/observed date `2026-05-14`; `row_count=34`; `outcome="fetched"`; one snapshot written. |
| ACE | `supported` | Catalog 108 entries, 22 ActiveStrategyETF candidates; provider ETF id `K55101DH7878`; requested/provider query/observed date `2026-05-14`; `row_count=50`; `outcome="fetched"`; one snapshot written. |
| HYUNDAI | `supported` | Catalog 5 entries, 5 ActiveStrategyETF candidates; provider ETF id `2338258`; requested/provider query/observed date `2026-05-13`; `row_count=39`; `outcome="fetched"`; one snapshot written. |
| TIMEFOLIO | `supported` | Parser closure in-memory live smoke: provider ETF id `5`; requested/provider query/observed date `2026-05-14`; `row_count=56`; `outcome="fetched"`; `freshness_status="fresh_latest"`. |
| TIGER | `supported` | Catalog 226 entries, 27 ActiveStrategyETF candidates; provider ETF id `KR7471780007`; requested/provider query/observed date `2026-05-13`; `row_count=29`; `outcome="fetched"`; one snapshot written. |
| RISE | `supported` | Parser closure in-memory live smoke: provider ETF id `44H6`; requested/provider query/observed date `2026-05-14`; `row_count=39`; `outcome="fetched"`; `freshness_status="fresh_latest"`. |
| SOL | `supported` | Catalog 79 entries, 17 ActiveStrategyETF candidates; provider ETF id `211099`; requested/provider query/observed date `2026-05-14`; `row_count=21`; `outcome="fetched"`; one snapshot written. |

Post-provider offline verification covers the full registered provider set in
rollout order. The shared registry/factory can enumerate and instantiate all
live providers, every provider fixture can feed native holdings history through
the SourceProvider contract, and provider summaries remain path-safe without
raw URLs, endpoints, local paths, raw rows, or raw payload strings.

All seven providers are fresh `supported` at the SourceProvider holdings parser
seam. The conditional OperationalETFDataSource normalized business-equivalence
comparison and downstream live handoff were not run in the TIMEFOLIO/RISE
parser closure because the available CLI handoff path persists live row-level
holdings before comparison, which exceeded that goal's raw-live-persistence
bound.

## Live Replacement Baseline Result

On 2026-05-16, the live replacement baseline ran one representative
ActiveStrategyETF equivalence check per provider against the current
operational copy. The first check showed KODEX and ACE live snapshots on
`2026-05-14` where the operational copy had no same-date representative rows,
so those two representatives were rerun with the nearest aligned operational
date. The final date-aligned representative gate produced four passes and
three failures:

| Provider | Representative provider ETF id | Observed date | Result | Evidence |
| --- | --- | --- | --- | --- |
| KODEX | `2ETFH5` | `2026-05-13` | failed | 34 live rows, 34 operational rows, 34 matched; one weight mismatch for security `KRD010010001` with absolute difference `1.54386`. |
| ACE | `K55101DH7878` | `2026-05-13` | passed | 50 live rows, 50 operational rows, 50 matched. |
| HYUNDAI | `2338258` | `2026-05-13` | passed | 39 live rows, 39 operational rows, 39 matched. |
| TIMEFOLIO | `5` | `2026-05-14` | failed | 56 live rows, 56 operational rows, 55 matched; code-set mismatch between live `UNCODED:timefolio:5:29:95b487c61277` and operational `CASH_UNCODED:timefolio:5`. |
| TIGER | `KR7471780007` | `2026-05-13` | passed | 29 live rows, 29 operational rows, 29 matched. |
| RISE | `44H6` | `2026-05-14` | passed | 39 live rows, 39 operational rows, 39 matched. |
| SOL | `211099` | `2026-05-14` | failed | 21 live rows, 20 operational rows, 20 matched; operational copy is missing live security `CASH00000001`. |

The live cumulative history now has normalized representative snapshots only:
9 snapshots, 352 rows, and partitions for `2026-05-13` and `2026-05-14`.
Because KODEX, TIMEFOLIO, and SOL failed the representative gate, the baseline
stopped before bulk live backfill, live-generated operational export,
readiness, and optional report generation at that point. The all-provider
parser-seam status remained `supported`; operational replacement was still
blocked until the representative mismatches were resolved.

Follow-up offline normalization fixes after this live stop aligned
SourceProvider cash handling with the operational input contract: uncoded
cash now uses `CASH_UNCODED:{source_provider_id}:{provider_etf_id}`, Korean
cash names classify as `cash_like`, missing provider cash weights derive from
market value when the snapshot's non-missing weights fit market-value
proportions, and SOL zero-weight cash total rows are dropped when their market
value duplicates the sum of all other rows. Bounded representative refreshes
for KODEX, TIMEFOLIO, and SOL then brought all seven representatives into
equivalence.

Bulk baseline started after the representative gate passed, then stopped on
KODEX blocked/rate-limit evidence. KODEX attempted 12 of 23 planned requests,
fetched 11 snapshots, and stopped with one blocked target. Aggregate bulk
evidence recorded 125 planned requests, 114 attempted requests, 113 written
snapshots, 5,568 written rows, and one stopped provider. Later live fills and
scope adjudication removed bond-like active-labelled ETFs from the required
equity ActiveStrategyETF cohort and excluded delisted SOL `210920`. Under that
KODEX-excluded active cohort, ACE, HYUNDAI, TIMEFOLIO, TIGER, RISE, and SOL
all have complete current/prior windows with zero missing snapshots and zero
window gaps. KODEX remains outside the completed cohort until a later daily
retry can reach `www.samsungfund.com` and backfill the missing windows.

Operational analysis must therefore isolate provider or ETF failures rather
than stall the whole run. A host-level provider block excludes only that
provider for the current run; an ETF-specific failure excludes only that ETF
when other ETFs from the provider remain fetchable. Readiness and report
analysis continue with the remaining eligible cohort, disclose the exclusion as
path-safe data-quality evidence, retry on the next daily collection cycle, and
naturally re-include the provider or ETF after a successful retry supplies the
required current/prior comparison window.

## Stop Conditions

Stop the live adapter work if any of these occur:

- The selected provider cannot return one catalog and one ETF/date holdings
  result without credentials.
- The selected provider requires browser automation or anti-bot bypass.
- Provider-specific parsing starts leaking into `SignalReportWorkflow`.
- Raw URLs, endpoints, response bodies, headers, raw rows, local paths, or
  credentials appear in default summaries, readiness handoff, or report-visible
  output.
- Staged catalog validation cannot prevent incomplete catalog mutation.

## Future Hardening

Known provider-load and rate-limit work intentionally left out of this goal:

- Durable provider cooldown state.
- Cross-run retry/backoff policy.
- Per-provider concurrency limits and target rotation.
- Holiday-aware bulk backfill.
- Raw provider payload opt-in design.
- Manual operator controls for provider load budgets.
- Live smoke evidence archival policy beyond path-safe summaries.
