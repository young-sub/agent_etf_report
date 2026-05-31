# Operational Live Runbook

## Purpose

This runbook covers the local operational flows for producing an Agent TReport
Signal Intelligence Report from normalized holdings output.

The forward native tracer path is fixture-backed:

```text
collect-universe-fixture
-> update-holdings-history-fixture --universe-state-path
-> export-holdings-comparison --security-resolution-path
-> check-operational-readiness
-> run-report
```

The legacy sync/backfill bridge remains supported:

```text
sync-operational-holdings -> check-operational-readiness -> run-report
```

The legacy sync upstream is the ETF Tracker local manifest supplied by the
operator. The SourceProvider acquisition path below is Agent TReport-owned:
fake provider runs are deterministic and offline, while live provider runs
require explicit `--live` opt-in and a selected SourceProvider.

Extraction-era compatibility keeps the supported CLI as `agent-treport` and
keeps operational defaults under `data/agent_treport/...`. The later
`agent_etf_report` rename must not change these runbook commands until a
post-separation rename packet provides an accepted migration or alias policy.

Readiness answers one FocusETFSet question:

```text
Can today's operational run produce a user-ready report for enough selected focus ETFs?
```

External evidence collection is a separate report-support step. It does not
change **OperationalRunReadiness**, but it can improve `SignalIntelligenceReport`
support, coverage, and data-quality disclosure when its path-safe output is
passed to `run-report --evidence-path`. Scheduler orchestration, publishing,
autonomous delivery, and automatic SecurityMapping changes remain out of scope.

## Daily Flow

For the fixture-native history path, run the steps explicitly:

1. `collect-universe-fixture`
2. `update-holdings-history-fixture --universe-state-path <universe_state.json>`
3. Optional universe-wide security recovery:
   `resolve-security-master -> export-security-resolution`
4. `export-holdings-comparison --security-resolution-path <security_resolution.json>`
5. `check-operational-readiness`
6. Optional evidence enrichment for target securities
7. `run-report --evidence-path <external_evidence.json>` when enrichment ran

For the legacy sync/backfill bridge, run the steps explicitly:

1. `sync-operational-holdings`
2. Optional universe-wide security recovery:
   `import-security-master-seed -> resolve-security-master ->
   export-security-resolution -> sync-operational-holdings --security-resolution-path`
3. `check-operational-readiness`
4. Optional evidence enrichment for target securities
5. `run-report --evidence-path <external_evidence.json>` when enrichment ran

`check-operational-readiness` produces the explicit handoff consumed by
operational `run-report`. Final `user_ready` output is exposed only when that
handoff matches the same normalized holdings export, focus ETF, observed
partition request, current date, previous date, and export fingerprint, and
when the readiness status allows delivery.

Evidence enrichment should run after the target securities are known from the
normalized holdings comparison and before `run-report` consumes the evidence
file. It should not mutate holdings history, collection summaries, readiness
handoffs, or export fingerprints.

## Native Operational Handoff Composer

The staged runbook above remains the default operator workflow because every
intermediate artifact is inspectable. Phase 7 also adds a thin composer for a
verified single-command handoff when the inputs already exist:

```text
export-holdings-comparison -> check-operational-readiness -> run-report -> native handoff
```

The composer defaults to the canonical live holdings history:

```text
data/agent_treport/live-source/holdings-history/
```

It does not collect live holdings, call broad external evidence APIs, publish
alerts, schedule future runs, or mutate canonical live history. It creates a
fresh normalized comparison export by default and binds readiness plus
`run-report` to that fresh export fingerprint. Resume/debug use of an existing
export is available through `--resume-export-path`, but the adjacent
`collection_summary.json` fingerprint must match the export content.

Example verified handoff command:

```powershell
..\.venv\Scripts\python.exe -m agent_treport.cli run-native-operational-handoff `
  --run-id <RUN_ID> `
  --history-dir data\agent_treport\live-source\holdings-history `
  --universe-state-path <NATIVE_UNIVERSE_STATE_JSON> `
  --focus-etf-set-path data\agent_treport\focus-etf-sets\default_focus_etf_set.json `
  --security-resolution-path data\agent_treport\security-master\security_resolution.json `
  --evidence-path <external_evidence.json> `
  --evidence-summary-path <external_evidence_summary.json> `
  --dest .scratch\native-operational-handoff\<RUN_ID> `
  --model codex
```

Use `--use-default-security-resolution` only when the operator explicitly wants
the default reviewed export at
`data\agent_treport\security-master\security_resolution.json`. If no reviewed
security resolution is supplied, the composer can still produce general
`user_ready` under existing `ready_with_warnings` behavior, but
`verified_operational_flow_acceptance.status` is `not_met`.

External evidence is optional for general handoff. If evidence was not run, the
composer writes an `external_evidence_summary.json` with `status="not_run"` and
the report can still be user-ready. Fixture or live evidence partial failure
does not block user-ready; it must remain visible through
`external_evidence.category_coverage` and report data-quality limitations. A
malformed evidence file or claim scope outside identity-safe
`signal:security:` / `signal:security_group:` scopes is a CLI input-contract
failure before report execution.

Add `--require-verified-operational-flow-acceptance` when verification must fail
unless the verified operational flow evidence set is present. The evidence set
includes a successful bounded `source_acquisition_summary.json` beside the
history store whose selected ETF is present in the same handoff's exported or
eligible analysis evidence, reviewed security identity, external evidence
summary, canonical report artifacts, readiness and quality evidence, registered
cohort accounting, and inspect/artifact references. In this mode, a general
`user_ready` report still writes its handoff JSON, but the command exits nonzero if
`verified_operational_flow_acceptance.status="not_met"`.

The final handoff JSON is written to
`<dest>\native_operational_handoff.json` and printed to stdout. Successful
`user_ready` and `operator_review_only` handoffs include path-safe references
to:

- canonical payload, Markdown report, HTML report, Telegram alert preview, and
  quality report;
- readiness artifact, source acquisition summary, collection summary, external
  evidence summary, and provider/ETF exclusion summary;
- inspect command.

The provider/ETF exclusion summary uses the registered live SourceProvider
cohort as the denominator: `kodex`, `ace`, `hyundai`, `timefolio`, `tiger`,
`rise`, and `sol`. It distinguishes providers with no active ETFs, providers
with no eligible comparison-window ETFs, excluded ETFs with reasons, and the
remaining eligible analysis cohort. The handoff always includes this statement:

```text
This report evaluated the registered live provider cohort, disclosed excluded providers/ETFs with reasons, and judged user-ready status using the remaining eligible cohort.
```

Failure handling:

- `ready` and disclosure-valid `ready_with_warnings` can produce final
  `status="user_ready"`.
- `hold` stops before report execution unless
  `--allow-operator-review-output` is supplied. With that explicit override,
  the final handoff is `status="operator_review_only"`,
  `delivery_blocked=true`, `reason="readiness_hold"`, and not user-ready.
- `failed` readiness stops before report execution and writes a failed handoff.
- `ReportQualityGate` failure is not operator-review-only. The command exits
  nonzero, writes `status="failed"`, retains quality evidence, includes
  inspect references when the run store exists, and includes recovery
  instructions.

## Pre-Publish Preview

`run-pre-publish-preview` is the operator-facing live-analysis preview before
Telegram delivery. It composes the existing native handoff path rather than
introducing a separate workflow framework:

```text
holdings latestness/readiness evidence
-> export-holdings-comparison
-> check-operational-readiness
-> external evidence preparation
-> run-report
-> pre-publish handoff
```

The command defaults to Agent TReport-owned live holdings history:

```text
data/agent_treport/live-source/holdings-history/
```

It does not send Telegram messages, start a Workbench server, schedule future
runs, persist raw provider payloads, or mutate canonical live holdings history.
In this flow, `status="user_ready"` means the operator can inspect the
Telegram alert and report artifacts immediately before delivery; it does not
mean Telegram Bot API delivery occurred.

Default live evidence behavior is full-live for the validated provider/API
denominator:

- `run-pre-publish-preview` defaults to `finnhub`, `yfinance`, `dart`,
  `alpha_vantage`, `newsapi`, and `naver`.
- SEC EDGAR remains disclosed as a known unvalidated provider exception in
  preflight, approval templates, handoff JSON, and result packages. It is not
  requested by this goal and does not block full user-ready closure unless a
  later approved goal promotes it into the validated required provider set.
- The default evidence target maximum is `25` analysis-eligible SignalBoard
  targets.
- Operators can override the provider list and target count with preview CLI
  options when the approval profile covers the requested boundary.
- The preview has an overall timeout default of 600 seconds. Use
  `--allow-preview-timeout-overrun` only when the operator explicitly accepts a
  longer pre-publish run; a timeout blocks full user-ready closure and writes a
  failed handoff without a Telegram message body.
- `collect-external-evidence` still requires explicit `--live` for live
  providers.
- Provider/API requests must use vendor-policy-aware pacing. Alpha Vantage
  `NEWS_SENTIMENT` uses one grouped ticker request for a capped target subset
  and discloses the cap as `provider_limitations`, rather than making one
  request per target. A provider `no_data`
  outcome is a limitation; credential, blocked, rate-limit exhaustion,
  provider-unavailable, invalid-payload, or timeout-exhausted outcomes are
  exclusion evidence that prevent full user-ready closure but do not by
  themselves stop review artifact generation when the remaining inputs are safe
  and the failed provider is in the validated required set.
- For full live pre-publish artifact closure, readiness `hold` can still produce
  `operator_review_only` report and Telegram alert artifacts for operator quality
  review. Readiness `failed`, report generation failure, or error-severity
  quality-gate failure still fails without a Telegram alert message body.
- Final `pre_publish_handoff.json` preserves the `telegram_alert` artifact
  reference and, when artifact closure is met, includes the full Telegram HTML
  body at `preview.telegram_message.text`. The handoff also reports
  `closure.full_live_pre_publish_artifact_closure` separately from
  `closure.full_user_ready_closure`.
- Final preview writes a path-safe result package under
  `data/agent_treport/live-source/daily-smoke-summaries/<run_id>/` by default.
  It contains safe handoff and summary copies, approval/preflight summary,
  external evidence summary, provider exception summary, validation command
  result placeholders, canonical-history non-mutation evidence, and retention
  evidence. Do not place raw provider payloads, raw URLs/endpoints, credentials,
  environment values, absolute paths, file URIs, stack traces, or Telegram Bot
  API delivery targets in this package.
- Same-smoke evidence reuse is allowed only when run id, approval boundary
  fingerprint, requested validated provider set, evidence category, and current
  report target identity match. It can reuse successful or normal `no_data`
  provider outcomes inside that smoke boundary; it is not a cross-run evidence
  database.
- When staged live SourceProvider holdings acquisition is run repeatedly into a
  scratch holdings history, `source_acquisition_summary.json` aggregates
  provider results path-safely instead of retaining only the last provider run.

2026-05-18 full-live scratch smoke evidence before the validated-provider
denominator change:

- Smoke root:
  `.scratch\pre-publish-preview-live-smoke\run_20260518_full_live_closure_001\`.
- Scratch approval/preflight only:
  `approval\daily_operational_external_data_preflight.json` and
  `approval\daily_operational_external_data_approval_template.json`.
- All seven live SourceProvider catalog calls and all seven selected one-ETF
  holdings acquisition calls exited 0 into the scratch holdings history.
  `source_acquisition_summary.json` aggregated `kodex`, `ace`, `hyundai`,
  `timefolio`, `tiger`, `rise`, and `sol` with
  `run_outcome="succeeded"`, `target_count=7`, `fetched=1`,
  `skipped_existing=6`, and `written_snapshot_count=1`.
- Canonical `data\agent_treport\live-source\holdings-history\` file hashes
  were unchanged.
- Final `run-pre-publish-preview --model codex` executed after explicit
  operator approval. It reused the live external evidence already collected in
  this smoke root as cached evidence to avoid repeated vendor calls.
- Final handoff:
  `preview_final_aggregate_acceptance_cached_evidence\pre_publish_handoff.json`.
  Status is `operator_review_only`, `delivery_blocked=true`, and
  `reason="external_evidence_policy_failure"`.
- External evidence provider/API outcomes: `finnhub`, `yfinance`, `dart`,
  `newsapi`, and `naver` succeeded; `sec_edgar` and `alpha_vantage` ended
  `rate_limited_exhausted`. Under the current denominator, SEC EDGAR is a
  disclosed known unvalidated provider exception and is not requested; Alpha
  Vantage remains required and still blocks if it remains rate-limited after
  policy-aware grouped handling.
- Readiness was `ready_with_warnings`, report quality passed with zero errors,
  verified operational flow acceptance passed, the TelegramSignalAlert artifact
  and full Telegram HTML message body are present, and Telegram delivery is
  `not_sent`.
- Full live pre-publish artifact closure is `met`; full user-ready closure is
  blocked by provider/API policy failures. The final handoff was checked to
  exclude file URIs, absolute workspace paths, raw URLs, Telegram API delivery
  calls, and credentials. The path-safe summary is `smoke_summary.json` under
  the smoke root.

### Daily Publish Closure Evidence

Telegram delivery closure is complete. The operator-facing proof that a daily
publish is operationally complete is now the package-local
`daily_publish_closure.json` artifact:

```powershell
..\.venv\Scripts\python.exe -m agent_treport.cli verify-daily-publish-closure `
  --package-path data\agent_treport\live-source\daily-smoke-summaries\<RUN_ID>
```

The verifier consumes an existing result package only. It reads
`pre_publish_handoff.json`, individual files under
`telegram_delivery_receipts\`, `telegram_delivery_summary.json`, and
`validation_command_results.json`, then writes `daily_publish_closure.json`.
It does not run `run-pre-publish-preview`, send Telegram, call Telegram Bot
API, call external providers, create delivery approval/preflight templates, or
overwrite `telegram_delivery_summary.json`.

Only `closure_status="closure_met"` means the daily Telegram publish is
operationally complete. Closure requires a user-ready handoff, live matching
`sent` receipt evidence, matching duplicate-block evidence, identity
consistency across run id, Telegram alert artifact id, message fingerprint,
target alias, and idempotency key when present, plus passed validation command
results. `telegram_delivery_summary.json` is supporting evidence; matching
individual receipts are canonical, so a latest summary status of
`duplicate_blocked` can still close the day.

The verifier writes warning-only approval evidence status from
`data\agent_treport\approvals\operator_approved_daily_publish_flow.json` when
present. That approval covers the current manually executed flow across
external evidence collection, model export, actual Telegram delivery,
duplicate-send check, and closure verification. It does not approve scheduler
or autonomous delivery, forced `operator_review_only` delivery,
correction/reannouncement workflows, target-alias expansion, provider-set
expansion, or changed credential/raw-payload storage policy.

2026-05-19 closure evidence:

- Running the verifier against
  `data\agent_treport\live-source\daily-smoke-summaries\run_20260519_validated_provider_closure_live_evidence_001`
  exited 0 and wrote `daily_publish_closure.json`.
- The artifact reports `closure_status="closure_met"` and
  `closure_met=true`, with one matching live `sent` receipt and one matching
  `duplicate_blocked` receipt.
- The matching receipts satisfy closure even though
  `telegram_delivery_summary.json` has
  `latest_delivery_status="duplicate_blocked"`.
- A scoped scan of the closure and operator-approved flow evidence found no
  absolute workspace path, `file://`, Telegram Bot API endpoint, Telegram HTML
  body fragment, raw chat id marker, credential assignment, raw
  request/response marker, `.env`, or stack trace.

### MLflow External Delivery Review Projection

Agent TReport can project Telegram delivery and daily publish closure evidence
into the generic `agent-pack` MLflow review surface through
`build_external_delivery_review_summary(...)`. The projector consumes already
path-safe `telegram_delivery_summary.json` and `daily_publish_closure.json`
payloads and returns one `agent_pack_review_summaries` item for stored runtime
state. The caller must provide `subject_id` explicitly; the projector does not
guess whether the business subject should be a run id, idempotency key, target
alias, or Telegram alert artifact id.

The projection is review evidence only. It does not approve delivery, send
Telegram, mutate receipts, mutate `daily_publish_closure.json`, or make MLflow
canonical. It maps only common fields for MLflow search and review: workflow,
run id, subject id, operation kind, review surface, review status, approval
status, permission status, delivery status, closure status, blocker count,
evidence-reference count, safe artifact references, schema version, projector
version, source fingerprint, and compact safe details.

For a completed daily publish, the expected review status is `passed` only when
`closure_status="closure_met"`. Other closure statuses are projected as
`blocked`; blocker count is derived from failed or unavailable evidence checks
plus closure limitations, with a minimum of one blocker for a non-closed result.
Safe artifact references are limited to `daily_publish_closure.json`,
`telegram_delivery_summary.json`, and package-relative Telegram receipt JSON
files. The projection must not include Telegram message text, raw chat id, bot
token, raw request/response payloads, provider URLs, credentials, `.env`
content, stack traces, file URIs, or absolute workspace paths.

### Delivery Closure RunStore Review Projection

Use `agent-treport project-delivery-closure-review` when an existing result
package should be indexed into the runtime review projection path:

```text
agent-treport project-delivery-closure-review --package-path <result-package> --sqlite-path <runtime.sqlite3> --run-id <run-id> --subject-id <subject-id>
```

This command is separate from `verify-daily-publish-closure`. The verifier
continues to consume package evidence and write `daily_publish_closure.json`
only. The projection command reads the existing package-local
`telegram_delivery_summary.json` and `daily_publish_closure.json`, calls
`build_external_delivery_review_summary(...)`, and merges the resulting
`agent_pack_review_summaries` item into the target run's latest RunStore
snapshot state. Re-running the command for the same operation, review surface,
and subject replaces the matching review summary instead of appending
duplicates.

The command requires explicit `run_id` and `subject_id`; it does not infer
whether the business subject should be a run id, idempotency key, target alias,
or Telegram alert artifact id. It fails closed when the target run or snapshot
does not exist, because review indexing must not create new runtime execution
state. It does not approve delivery, send Telegram, retry delivery, call live
providers, mutate receipts, mutate `telegram_delivery_summary.json`, mutate
`daily_publish_closure.json`, or make RunStore/MLflow canonical delivery
authority.

### MLflow Delivery Closure Review Guide

Use this guide when a reviewer needs to inspect completed delivery closure
evidence through MLflow after the package evidence has already been verified.
The canonical closure proof remains the result package. RunStore, trace export,
and MLflow are review projections only.

Preconditions:

- A completed result package contains `telegram_delivery_summary.json` and
  `daily_publish_closure.json`.
- The target SQLite RunStore already contains the run and at least one snapshot.
- The reviewer has explicit `run_id` and `subject_id` values. For delivery
  closure review, `subject_id` is usually the Telegram alert artifact id or
  another operator-chosen stable business subject.
- The MLflow tracking URI points to an already running or explicitly approved
  local MLflow server, or to a local file-store path for non-UI smoke.

Index the package evidence into the runtime review path:

```text
agent-treport project-delivery-closure-review --package-path <result-package> --sqlite-path <runtime.sqlite3> --run-id <run-id> --subject-id <subject-id>
```

Export the stored run to MLflow:

```text
agent-pack export-trace --sqlite-path <runtime.sqlite3> --run-id <run-id> --tracking-uri <mlflow-tracking-uri> --experiment-name <experiment-name> --run-name <optional-review-run-name>
```

Expected MLflow review observations:

- Searchable run or trace labels include `agent_pack.workflow`,
  `agent_pack.subject_id`, `agent_pack.operation_kind=external_delivery`,
  `agent_pack.review_surface=delivery_closure`,
  `agent_pack.review_status`, `agent_pack.delivery_status`, and
  `agent_pack.closure_status`.
- The native trace contains one review span named
  `agent_pack.review.delivery_closure.external_delivery`.
- The review span attributes include the same subject, operation, review
  surface, review status, approval status, permission status, delivery status,
  closure status, blocker count, evidence-reference count, schema version, and
  projector version.
- The review span inputs list only safe package-relative artifact references and
  a source fingerprint. Outputs contain compact review details.
- Artifacts include `review/review_surfaces.json` and
  `review/external_delivery_review_summary.json`; raw audit fallback remains in
  `raw/trace_export_record.json`.

If a live MLflow UI screenshot or observation is separately approved, record the
review date, tracking URI, experiment, `run_id`, exported MLflow run id,
MLflow trace id, command used, screenshot or observation artifact path, and the
checked tag/span/artifact names above. The screenshot or observation is evidence
that the review projection is visible; it is not approval evidence and does not
replace `daily_publish_closure.json` or Telegram delivery receipts.

Do not start or contact MLflow, export externally, expose credentials, include
raw Telegram message text, raw chat ids, provider URLs, absolute workspace paths,
or mutate result-package artifacts unless the corresponding approval boundary is
explicitly satisfied outside this guide.

### Durability/Substrate Evidence Review Projection

Agent TReport durability/substrate evidence is a review projection, not a
production durability claim. `review.durability_substrate` records which current
local runtime evidence surfaces are preserved well enough to inform later
production durability gates or a substrate ADR.

The projection is Agent TReport-owned and emits a generic
`TraceExportReviewSummary` through `agent_pack_review_summaries`. The current
matrix can include stored `RunStore` snapshot evidence, trace export evidence
summaries, the operational readiness artifact reference, generic
approval/permission governance evidence, and classified failure event evidence.
It also lists unsupported production claims such as crash/restart resume or
duplicate worker locking as gaps, not guarantees.

Inspect the projection through `TraceExportRecord.review_summaries`, MLflow
`review/review_surfaces.json`, or the per-summary review artifact when a trace
export sink is used. The projection must keep only path-safe artifact refs such
as `operational_readiness.json` and must not include raw provider payloads,
provider URLs, credentials, absolute local paths, raw Telegram bodies, stack
traces, or approval comments.

### Daily External Data Approval

Daily pre-publish operation uses a durable approval profile rather than a
one-off yes flag. The default profile path is:

```text
data/agent_treport/approvals/daily_operational_external_data_approval.json
```

Guarded commands also accept `--approval-path <path>` and
`--write-preflight [path]`. When a guarded path needs approval, it writes a
path-safe preflight disclosure and a sibling pending approval template before
any live provider call or real Codex call. The preflight discloses provider and
model identities, required scopes, evidence categories, maximum target count,
live SourceProvider cohort, focus ETF ids, capped safe identifier samples,
credential variable names only, data classes, excluded raw fields, and
report/model export scope. It must not include raw holdings rows, raw report
text, raw provider payloads or envelopes, raw URLs/endpoints, headers,
credentials, environment values, stack traces, absolute local paths, or raw
approval comments.

Approval scopes are independent:

- `live_source_catalog` for `collect-source-catalog --live`.
- `live_holdings_acquisition` for `update-holdings-history-source --live`.
- `live_external_evidence` for live external evidence collection.
- `model_export` for real Codex report/commentary or claim-alignment export.
- `live_source_baseline` for initial, recovery, or bulk baseline backfill; this
  scope is separate from normal daily pre-publish smoke closure.

The approval profile is Agent TReport domain evidence only. It does not grant
network access to the process runner. In Codex or any sandboxed runner, every
command that actually performs `--live` SourceProvider acquisition, live
external evidence collection, or real model export must also run with the
runner's explicit network/export permission. A sandboxed `--live` command can
produce `provider_unavailable` evidence even when the provider site and adapter
are healthy; that evidence is a failed smoke for that execution boundary, not
proof of provider data absence.

Approval is valid only when `status="approved"`, optional `expires_at` has not
passed, the boundary fingerprint matches the approved fields, and the approved
scope/provider/model/data-class/target-count/cohort bounds cover the requested
run. `pending`, `revoked`, expired, tampered, or too-narrow profiles stop before
export. Provider subsets, lower target counts, and narrower data-class subsets
inside the approved boundary do not require reapproval.

Path-dependent approval examples:

- Cached evidence replay plus fake model: no approval.
- Cached evidence replay plus real Codex: `model_export`.
- Live external evidence plus fake model: `live_external_evidence`.
- Live external evidence plus real Codex preview:
  `live_external_evidence` and `model_export`.

If approval is missing or invalid, `run-pre-publish-preview` writes
`pre_publish_handoff.json` with `status="failed"`,
`delivery_blocked=true`, `reason="external_data_approval_required"`, approval
summary, missing/unapproved scopes, and preflight/template references. Staged
commands write command-specific `*_approval_block.json` summaries near their
destination or evidence directory.

When `run-pre-publish-preview` has a runtime store boundary, it also records
path-safe generic governance evidence for the same requested approval boundary:
an `ApprovalLifecycleRecord`, a `PermissionDecisionRecord`, and corresponding
run events. These records support trace/export review through
`approval_permission_boundary` evidence summaries. They do not replace the
`PrePublishExternalDataApproval` profile and do not grant network, export, model,
or Telegram permissions.

Cached replay mode is available by supplying explicit normalized evidence and
summary paths. Cached evidence can support `user_ready` only when the evidence
file is schema-valid, claim scopes are identity-safe for the current SignalBoard
targets, and the summary includes financial, disclosure, and news category
coverage. This first slice does not add a hard cache freshness gate; generated
time, provider/category status, published dates, missing dates, and older
evidence must remain visible as limitations.

Preview status mapping:

- `user_ready`: readiness allows delivery, report quality passes, external
  evidence attempted financial/disclosure/news coverage, and no external
  evidence policy failure is present. `no_data` is a coverage limitation, not a
  delivery blocker.
- `operator_review_only`: readiness `hold` was explicitly overridden, external
  evidence was `not_run`, or an external evidence policy failure occurred.
  Policy-failure review output uses `delivery_blocked=true` and
  `reason="external_evidence_policy_failure"` while preserving partial report
  artifacts when safe.
- `failed`: readiness `failed`, readiness `hold` without
  `--allow-operator-review-output`, report generation cannot safely produce
  artifacts, quality fails, cached evidence is malformed or identity-unsafe, or
  required handoff artifacts cannot be referenced.

Final preview handoffs should preserve generated artifacts and references when
available: canonical payload, Markdown report, HTML report, Telegram alert,
quality report, readiness artifact, collection/latestness or source
acquisition summary, external evidence file, external evidence summary,
provider/ETF exclusion summary, and inspect command. Normal `user_ready`
preview does not require Workbench server startup.

Manual smoke for this command must use a scratch copy of
`data\agent_treport\live-source\holdings-history\`. If live holdings fetch is
explicitly needed, fetch only into that scratch copy. Run the preview once with
real `--model codex`, confirm the final handoff status, inspect command,
artifact references, quality pass, Telegram alert artifact, and external
evidence summary, and record whether live evidence ran and which credentials or
provider failures limited the run.

If the environment disallows real Codex or live provider data export, record
the denial as the manual-smoke result and do not work around it. Deterministic
automated preview tests should continue to use fake model clients and provider
overrides.

## Native Fixture Collection

`collect-universe-fixture` and `collect-holdings-fixture` are the current
Agent TReport-owned native fixture entrypoints. They do not call live
providers, load credentials, schedule work, publish externally, or read an ETF
Tracker source manifest.

Collect the tracked ETF universe and ETF brand metadata first:

```powershell
..\.venv\Scripts\python.exe -m agent_treport.cli collect-universe-fixture `
  --fixture-path <NATIVE_UNIVERSE_FIXTURE_JSON> `
  --dest .scratch\native-collection\universe
```

The command writes:

- `universe_state.json`
- `universe_summary.json`

`universe_state.json` is the canonical local state for tracked ETFs and
ETF brand metadata. ETF records contain `etf_id`, `etf_name`, `brand_id`,
`source_provider_id`, and `status`. ETF brand records contain `brand_id`,
`brand_name`, `source_provider_id`, and `status`. Removed records remain in state
with `status="removed"`; they are excluded from default holdings collection
targets. If the same removed `etf_id` or `brand_id` becomes active again,
the universe summary reports changed evidence rather than added evidence.

`universe_summary.json` is path-safe operator evidence for the metadata run. It
includes schema version, source type, collection timestamp, active and removed
ETF counts, active and removed brand counts, added/changed/removed/unchanged
counts, canonical ids, display names, changed field names, and source provider ids. It
excludes raw provider payloads, holdings rows, URLs, credentials, local paths,
and provider envelopes.

The forward holdings path updates native history from active tracked ETFs:

```powershell
..\.venv\Scripts\python.exe -m agent_treport.cli update-holdings-history-fixture `
  --fixture-path <NATIVE_COLLECTION_FIXTURE_JSON> `
  --universe-state-path .scratch\native-collection\universe\universe_state.json `
  --history-dir .scratch\native-collection\holdings-history `
  --observed-partitions 2
```

The command writes:

- `holdings_history.json`
- `holdings_history.json.parts\<YYYY-MM-DD>.jsonl`

`HoldingsSnapshot` identity is `etf_id + observed_date`. Provider/source
version is provenance, not identity. If an incoming snapshot matches the stored
snapshot, the command skips it and does not mutate the store. If an incoming
snapshot differs, the command fails with refresh-required evidence unless that
exact snapshot is named with `--refresh-snapshot <ETF_ID>:<YYYY-MM-DD>`.
Refresh replaces only the named ETF/date snapshot; unrelated ETF/date
snapshots remain unchanged.

Removed ETFs remain in `universe_state.json`, and their previously stored
holdings remain in `holdings_history.json` for audit, backtest, and explicit
manual analysis. The default latest comparison export below includes active
ETFs only.

Backfill an existing normalized operational export into the same history store
with identical duplicate/conflict/refresh rules:

```powershell
..\.venv\Scripts\python.exe -m agent_treport.cli import-holdings-history `
  --manifest-path <NORMALIZED_OPERATIONAL_HOLDINGS_EXPORT_JSON> `
  --history-dir .scratch\native-collection\holdings-history
```

Then export the report-facing latest comparison window:

```powershell
..\.venv\Scripts\python.exe -m agent_treport.cli export-holdings-comparison `
  --history-dir .scratch\native-collection\holdings-history `
  --universe-state-path .scratch\native-collection\universe\universe_state.json `
  --dest .scratch\native-collection\operational-holdings `
  --security-resolution-path data\agent_treport\security-master\security_resolution.json
```

The command writes:

- `url_holdings_cumulative.json`
- `url_holdings_cumulative.json.parts\<YYYY-MM-DD>.jsonl`
- `collection_summary.json`

The export selects the latest valid current and previous snapshot per active
ETF. When all ETFs share the same two dates, the manifest remains a two-date
comparison. When publication timing differs, the manifest includes every
selected per-ETF date and `collection_summary.json` records
`mixed_comparison_windows` plus capped per-ETF current/previous selections.
Active ETFs missing either side of their own window are coverage gaps, not
silently hidden. `collection_summary.json` records selected dates, active ETF
count, complete active ETF count, missing active ETF ids, coverage ratio,
reviewed security-resolution availability, ticker-candidate coverage, unknown
and non-ticker exclusion counts, aggregate recovery samples, and normalized
output fingerprint. Reviewed security resolution affects only this export
view; `holdings_history.json` keeps the originally observed ticker and
classification fields.

`check-operational-readiness` consumes this normalized export, not the history
store directly. The preferred live handoff input is a FocusETFSet file passed
with `--focus-etf-set-path`; the compatibility `--focus-etf-id` argument maps
to a one-item focus set. A FocusETFSet handoff is user-ready when at least
three focus ETFs have two valid snapshots and the safety contracts pass. Fewer
than three eligible focus ETFs is `hold`, not `failed`. Non-focus active ETF
coverage is diagnostic disclosure and recovery evidence; it no longer blocks
the user-ready handoff when focus coverage and safety contracts are satisfied.
The default focus set is stored at
`data/agent_treport/focus-etf-sets/default_focus_etf_set.json`.

The older direct fixture export remains available as a compatibility path:

```powershell
..\.venv\Scripts\python.exe -m agent_treport.cli collect-holdings-fixture `
  --fixture-path <NATIVE_COLLECTION_FIXTURE_JSON> `
  --dest .scratch\native-collection\operational-holdings `
  --observed-partitions 2 `
  --universe-state-path .scratch\native-collection\universe\universe_state.json
```

The command writes:

- `url_holdings_cumulative.json`
- `url_holdings_cumulative.json.parts\<YYYY-MM-DD>.jsonl`
- `collection_summary.json`

It deliberately does not write `sync_metadata.json`. The normalized manifest
and partitions use the same row shape consumed by the operational report path:
`etf_id`, `etf_name`, `brand_id`, `source_provider_id`, `as_of_date`,
`security_id`, `ticker`, `name`, `weight_percent`, `shares`,
`market_value_krw`, `is_cash`, and `security_classification`, plus the existing
optional enrichment columns.

When `--universe-state-path` is supplied, holdings metadata comes from active
ETF records in `universe_state.json`. A holdings fixture row that references an
untracked ETF or a removed ETF fails before a normalized holdings manifest is
written. The older embedded `brands` and `etf_universe` holdings fixture
fields are kept only for Phase 1 compatibility and are not the ownership
source in the native history flow.

`collection_summary.json` is path-safe native readiness evidence. It includes
schema version, collection source type, collection timestamp, requested
observed partitions, observed dates, ETF count, brand count, partition count,
row count, quality warnings or limitations, and the normalized output
fingerprint. Native history summaries also include reviewed security-resolution
availability, mapped and unresolved ticker-candidate counts, unknown counts,
non-ticker excluded counts, reviewed mapping/exclusion application counts,
ticker coverage ratio, and capped aggregate recovery samples. Recovery samples
contain only security id, representative name, observed row/date/ETF counts,
alias count, and `security_classification` when needed to distinguish
`unknown`; they exclude raw rows, ETF ids, observed dates, absolute source
paths, URLs, credentials, environment values, provider envelopes, and local
provider diagnostics.

## SourceProvider Acquisition

SourceProvider acquisition is staged and explicit:

```text
collect-source-catalog
-> update-holdings-history-source
-> export-holdings-comparison
-> check-operational-readiness
-> run-report
```

The default/fake path never calls the network and is the automated regression
path. Live provider execution requires `--live` and `--source-provider`.
Registered live SourceProviders are `kodex`, `ace`, `hyundai`, `timefolio`,
`tiger`, `rise`, and `sol`.

The report handoff starts only after `export-holdings-comparison` writes the
normalized native-history export. Readiness and `run-report` bind to the
adjacent `collection_summary.json` and the normalized export fingerprint, not
to `source_acquisition_summary.json`. Source acquisition summaries remain
operator evidence for catalog/holdings acquisition only.

Collect a deterministic fake source catalog:

```powershell
..\.venv\Scripts\python.exe -m agent_treport.cli collect-source-catalog `
  --fixture-path <SOURCE_PROVIDER_FIXTURE_JSON> `
  --dest .scratch\source-acquisition\catalog
```

The command stages the complete provider `source_catalog.json`, validates it,
classifies each entry for ActiveStrategyETF eligibility, then updates
`universe_state.json` only when the catalog is complete and valid. The full
source catalog is preserved. The SourceProvider-created universe state includes
ActiveStrategyETF entries only by default; passive and unknown strategy entries
remain in source catalog and summary evidence as review candidates. Incomplete,
invalid, or path-unsafe catalogs fail before mutating existing universe state.
The summary file is `source_acquisition_summary.json`.

ActiveStrategyETF classification uses provider catalog metadata, passive
evidence, exact local seed matches, TIMEFOLIO provider default, and the
`액티브` product-name token. Passive evidence is applied before active evidence.
Unknown classification is not treated as a holdings target by default.

Update native holdings history from a fake provider:

```powershell
..\.venv\Scripts\python.exe -m agent_treport.cli update-holdings-history-source `
  --fixture-path <SOURCE_PROVIDER_FIXTURE_JSON> `
  --source-catalog-path .scratch\source-acquisition\catalog\source_catalog.json `
  --universe-state-path .scratch\source-acquisition\catalog\universe_state.json `
  --history-dir .scratch\source-acquisition\holdings-history `
  --requested-date <YYYY-MM-DD>
```

`--requested-date` is optional. When omitted, the command defaults to the
execution date's previous Korean weekday. This is a weekday approximation only;
a KRX holiday calendar remains future work.

Source-backed history updates preserve native `HoldingsSnapshot` identity and
duplicate handling. Matching duplicates are skipped. Changed duplicate
ETF/date snapshots require `--refresh-snapshot <ETF_ID>:<YYYY-MM-DD>` and are
not silently overwritten. Partial acquisition is allowed: successful ETF/date
snapshots are written even when other targets fail, rate-limit, or are
unsupported.

Run bounded live catalog smoke explicitly. Replace `<SOURCE_PROVIDER>` with
one of `kodex`, `ace`, `hyundai`, `timefolio`, `tiger`, `rise`, or `sol`:

```powershell
..\.venv\Scripts\python.exe -m agent_treport.cli collect-source-catalog `
  --live `
  --source-provider <SOURCE_PROVIDER> `
  --dest .scratch\source-acquisition-smoke\<SOURCE_PROVIDER>\catalog
```

Then fetch one selected ETF/date target. Choose an ActiveStrategyETF candidate
from the staged catalog. Candidate priority is high-confidence provider
metadata, then high-confidence seed evidence, then TIMEFOLIO provider default,
then low-confidence name-token evidence; ties preserve provider catalog order.
For live provider rollout evidence, first inspect the local OperationalETFDataSource
when available and use up to three provider-local samples to choose the
provider-specific requested date. Prefer ActiveStrategyETF samples when
feasible; otherwise use the first available provider samples and select the
latest observed date among them. If that discovery fails, omit
`--requested-date` and report that operational provider date discovery was not
available.

```powershell
..\.venv\Scripts\python.exe -m agent_treport.cli update-holdings-history-source `
  --live `
  --source-provider <SOURCE_PROVIDER> `
  --source-catalog-path .scratch\source-acquisition-smoke\<SOURCE_PROVIDER>\catalog\source_catalog.json `
  --universe-state-path .scratch\source-acquisition-smoke\<SOURCE_PROVIDER>\catalog\universe_state.json `
  --history-dir .scratch\source-acquisition-smoke\<SOURCE_PROVIDER>\history `
  --requested-date <YYYY-MM-DD> `
  --provider-etf-id <PROVIDER_ETF_ID>
```

Do not run live holdings smoke without `--provider-etf-id`. This keeps the smoke
operator-bounded. An explicit provider ETF id is an exclusive selection set for
that command: a failed or stale selected target must not trigger an alternate
ActiveStrategyETF request. Do not exceed one catalog plus the explicitly
selected holdings target per provider in the smoke path. Use the live
replacement baseline flow below for gated bulk planning and backfill.

A holdings smoke upgrades provider rollout status to `supported` only when the
target outcome is fetched or live-confirmed skipped as an existing matching
snapshot, rows are non-empty, exactly one observed date is present, and the
observed date is accepted as latest evidence. If `observed_date >=
requested_date`, the target may be accepted as fresh latest evidence. If
`observed_date < requested_date`, only same-day, prior-day, or prior-business-
day freshness relative to `provider_query_date` is acceptable. Older fetched
snapshots are still stored in native `holdings_history`, but the summary
records `stale_latest_holdings` warning evidence and the rollout status remains
`catalog_only`. If two attempted ActiveStrategyETF candidates fail, rollout
status is `active_holdings_failed`. If both attempted candidates are stale-only,
rollout status remains `catalog_only` with stale warning evidence.

Operator-bounded ActiveStrategyETF live smoke recorded on 2026-05-15:

| Provider | Status | Catalog entries | ActiveStrategyETF candidates | Attempted provider ETF ids | Requested/provider query/observed date | Outcome |
| --- | --- | ---: | ---: | --- | --- | --- |
| KODEX | `supported` | 234 | 18 | `2ETFH5` | `2026-05-14` / `2026-05-14` / `2026-05-14` | fetched 34 rows; one snapshot written. |
| ACE | `supported` | 108 | 22 | `K55101DH7878` | `2026-05-14` / `2026-05-14` / `2026-05-14` | fetched 50 rows; one snapshot written. |
| HYUNDAI | `supported` | 5 | 5 | `2338258` | `2026-05-13` / `2026-05-13` / `2026-05-13` | fetched 39 rows; one snapshot written. |
| TIMEFOLIO | `supported` | 18 | 17 | `5` | `2026-05-14` / `2026-05-14` / `2026-05-14` | parser closure in-memory smoke fetched 56 rows; no live rows persisted. |
| TIGER | `supported` | 226 | 27 | `KR7471780007` | `2026-05-13` / `2026-05-13` / `2026-05-13` | fetched 29 rows; one snapshot written. |
| RISE | `supported` | 138 | 18 | `44H6` | `2026-05-14` / `2026-05-14` / `2026-05-14` | parser closure in-memory smoke fetched 39 rows; no live rows persisted. |
| SOL | `supported` | 79 | 17 | `211099` | `2026-05-14` / `2026-05-14` / `2026-05-14` | fetched 21 rows; one snapshot written. |

### Live Source Replacement Baseline

The live replacement baseline promotes SourceProvider holdings from parser-seam
evidence toward the operational handoff. Scope is tracked ActiveStrategyETF
entries only. Passive and unknown-strategy entries remain out of the product
scope for this flow.

The operator flow is:

```text
run-live-source-baseline
-> export-holdings-comparison
-> check-operational-readiness
-> optional run-report --model codex
```

`run-live-source-baseline` first verifies one representative ActiveStrategyETF
per provider against the current operational copy. Bulk live backfill starts
only when every representative passes. If any representative fails, stop before
bulk, export, readiness, and report generation.

Example command:

```powershell
..\.venv\Scripts\python.exe -m agent_treport.cli run-live-source-baseline `
  --live `
  --config-path data\agent_treport\live-source\evidence\<RUN_ID>\baseline_config.json `
  --operational-holdings-path data\agent_treport\live-source\evidence\<RUN_ID>\operational-baseline\url_holdings_cumulative.json `
  --history-dir data\agent_treport\live-source\holdings-history
```

The canonical SourceProvider-collected cumulative history is:

```text
data/agent_treport/live-source/holdings-history/
```

It is preserved project data and is commit-eligible. It contains normalized
history rows only. Do not store raw provider payloads, response envelopes,
URLs, endpoints, headers, credentials, raw rows, or absolute local paths in
that cumulative history.

Reproducible per-run output belongs under `data/agent_treport/live-source/`
subdirectories such as `evidence/`, `artifacts/`, `daily-smoke-summaries/`, and
`daily-health/`. These roots use rolling retention with the latest 10 run
directories by default. Retention never prunes `holdings-history/`.

Representative equivalence compares the same provider, ETF, and observed date.
Blockers are security code set, weight, and market value. Security code must
match exactly. Weight tolerance is absolute difference `<= 0.01` percentage
points. Market value tolerance is absolute difference `<= 1` KRW. Shares are
compared only when present on both sides with absolute tolerance
`<= 0.000001`; one-sided missing shares are diagnostic warnings. Display names,
ticker display values, and classification labels are diagnostic unless they
change code, amount, or weight comparison.

The equivalence summary is path-safe. It includes provider id, canonical ETF id,
provider ETF id, observed date, row counts, matched constituent count, mismatch
counts, tolerance values, fetch outcome, warnings, and capped mismatch samples.

Bulk planning is gap-first. It inspects existing live history before making
network requests, then plans only missing ETF/date snapshots. The required
baseline window is each tracked ActiveStrategyETF's latest observed holdings
date plus the nearest available prior business-date snapshot. Exact prior
business day is preferred; when the provider cannot return it, record the
nearest prior observed business-date alignment. When the current operational
copy has no rows for a tracked ActiveStrategyETF, existing live history becomes
the planning source. If no live history exists yet, the planner creates
`latest_discovery` and `prior_discovery` requests from the provider's latest
known operational anchor date instead of treating the ETF as a passive gap.

Same-host live pacing is request spacing, not ETF-count spacing. Default
spacing is `1.2s` plus `0.0s` to `0.4s` jitter per request. SOL and RISE use
more conservative reference-level overrides unless later evidence supports
lowering them. A blocked holdings target gets three total attempts: initial
request, one retry after 2 minutes, and one retry after 10 minutes. If all
three attempts remain blocked, that provider stops. Other stop classes still
stop the provider on rate-limit, anti-bot, credential-required, or blocked
exhaustion behavior. The summary records cooldown or blocked evidence without
provider internals.

`bulk_completed` is true for a provider cohort only when the representative
gate passes, every planned provider in that cohort has no window gaps, no
provider in that cohort stops, and every attempted bulk target avoids `failed`,
`rate_limited`, and `unsupported` outcomes. A provider with documented
host-level block or rate-limit evidence may be excluded from the active cohort
instead of blocking all other providers. The excluded provider remains in daily
health evidence and next-backfill planning.

### Operational Failure Isolation

In steady-state operations, a single failed asset manager or ETF target is an
exclusion event, not a global stop event. Do not keep the report or analysis
waiting for that provider or ETF once the current run has recorded path-safe
failure evidence.

Required behavior:

- stop further same-run requests only for the affected provider when host-level
  blocked, anti-bot, credential-required, or rate-limit evidence appears;
- exclude only the affected ETF when the failure is ETF-specific and other ETFs
  for the provider are still fetchable;
- continue export, readiness, and report analysis with the remaining eligible
  providers and ETFs when at least three FocusETFSet members have valid
  per-ETF comparison windows and the hard safety contracts still pass;
- disclose the provider/ETF exclusion as data-quality warning evidence without
  raw network locators, response content, machine paths, auth material, or
  holdings rows;
- retry excluded providers or ETFs on the next daily collection cycle with the
  normal pacing/backfill rules; and
- naturally re-include a provider or ETF once the retry succeeds and the
  required current/prior comparison window exists in `holdings-history/`.

Do not convert a transient provider failure into an indefinite operator wait.
Do not run aggressive same-day retry loops after blocked/rate-limit evidence.
If fewer than three FocusETFSet members have valid comparison windows, mark
readiness `hold` and record the specific focus-data gaps. Otherwise, proceed
with available eligible holdings.

Daily collection health evidence is written as a path-safe summary. Each
provider record includes tracked ActiveStrategyETF count, current/up-to-date
count, missing snapshot count, failed target count, stale target count, next
backfill target count, window gap count, last successful observed date, and
capped ETF id samples. Window gaps are tracked ActiveStrategyETF entries that
cannot yet be converted into a dated fetch request. Discovery requests count as
missing snapshots and next backfill targets until the two-date live window is
stored.

Live baseline execution on 2026-05-16 first reached representative verification
and exposed three normalization mismatches. Follow-up offline fixes aligned
SourceProvider cash handling, bounded representative refreshes updated the
selected KODEX, TIMEFOLIO, and SOL snapshots, and all seven providers then
passed the representative gate.

Bulk baseline then started and stopped KODEX on blocked/rate-limit evidence.
KODEX attempted 12 of the 23 then-planned requests, fetched 11 snapshots, and
stopped with one blocked target. Other providers attempted their planned
requests within the run. Later classification adjudication excluded bond-like
active products from the ActiveStrategyETF scope and excluded delisted SOL
`210920`. The latest live history under
`data/agent_treport/live-source/holdings-history/` has 21,967 rows across 445
snapshots. For the 2026-05-11 through 2026-05-15 target week, 73
EquityAnalysisETFs are eligible and 364 of 365 expected latest-week snapshots
exist. The single remaining gap is HYUNDAI canonical ETF
`etf_hyundai_2912753` on 2026-05-11, with repeated
`invalid_provider_payload` and no observed date. That gap is path-safe
handoff exclusion evidence and retry/backfill work; it is not a global
handoff failure while the default FocusETFSet remains covered.

Source acquisition summaries are operator evidence, not report payloads. They
may include source provider id, brand id, canonical ETF id, scope, requested
dates, observed dates, date-alignment status, target outcomes,
latest-upload freshness, EquityAnalysisETF classification counts,
classification source/confidence, stale-latest warnings, rollout status, row
counts, reason code, retry attempt count, cooldown timestamp, missing observed
dates, next backfill date count, run outcome, and aggregate counts. They
exclude provider-local ETF keys, raw network locators, response metadata/content,
machine paths, auth material, environment values, raw holdings rows, and raw
provider wrappers.

Automated source-acquired report handoff coverage uses only
`FakeSourceProvider`. It proves:

- complete current and previous source snapshots for all active ETFs plus a
  reviewed `SecurityResolutionExport` produce `ready` and final
  `output.user_ready`;
- missing reviewed security resolution produces `ready_with_warnings`, a
  readiness/data-quality disclosure, and still produces `output.user_ready`;
- a compatibility single focus ETF missing one side of the selected comparison
  window is `failed` and blocks `run-report` before model calls or artifact
  creation;
- FocusETFSet handoff is `ready` or `ready_with_warnings` when at least three
  requested focus ETFs have valid per-ETF comparison windows and safety
  contracts pass;
- FocusETFSet handoff is `hold` when fewer than three focus ETFs are eligible;
- non-focus active ETF coverage gaps are disclosure-valid diagnostics and are
  not a user-ready blocker.

Report-visible readiness/data-quality projections are intentionally concise.
They may expose readiness warning/reason codes, messages, metric names, values,
and thresholds. They must not expose provider-local ETF keys, source target
outcomes, failure classes, retry counts, raw network locators, provider
payloads, machine paths, auth material, raw holdings rows, or the source
acquisition summary itself.

## Sync

Use `sync-operational-holdings` for migration and backfill from already-crawled
ETF Tracker data. Do not add new native collection behavior to this bridge.

`--source` is required. Pass the current local upstream manifest explicitly:

```powershell
..\.venv\Scripts\python.exe -m agent_treport.cli sync-operational-holdings `
  --source <ETF_TRACKER_URL_HOLDINGS_CUMULATIVE_JSON> `
  --dest .scratch\operational-live-run\operational-holdings `
  --observed-partitions 30
```

When a reviewed mapping exists, prefer:

```powershell
..\.venv\Scripts\python.exe -m agent_treport.cli sync-operational-holdings `
  --source <ETF_TRACKER_URL_HOLDINGS_CUMULATIVE_JSON> `
  --dest .scratch\operational-live-run\operational-holdings `
  --observed-partitions 30 `
  --security-mapping-path <security_mapping.json>
```

When a reviewed `SecurityResolutionExport` exists, prefer it over the legacy
minimal mapping:

```powershell
..\.venv\Scripts\python.exe -m agent_treport.cli sync-operational-holdings `
  --source <ETF_TRACKER_URL_HOLDINGS_CUMULATIVE_JSON> `
  --dest .scratch\operational-live-run\operational-holdings `
  --observed-partitions 30 `
  --security-resolution-path data\agent_treport\security-master\security_resolution.json
```

`--security-resolution-path` and `--security-mapping-path` are mutually
exclusive. The resolution export supplies both approved ticker mappings and
explicit non-ticker exclusions. `ticker_mapping_coverage_ratio` is calculated
over `ticker_candidate` rows only; `cash_like` and `non_equity` rows are
excluded from the denominator and counted in
`non_ticker_excluded_security_count`.

No repository-default security mapping file is created. The current temporary
upstream example on the local workstation is:

```text
C:\Users\YS\Desktop\python\ETF_tracker\data\url_holdings_cumulative.json
```

Use it only when it exists and is the intended source for the day.

## Security Master Recovery

Generated SecurityMaster, review queue, and SecurityResolutionExport files stay
local under `data/agent_treport/security-master/`, which is gitignored.

Seed from the temporary ETF Tracker mapping:

```powershell
..\.venv\Scripts\python.exe -m agent_treport.cli import-security-master-seed `
  --stock-mapping-csv C:\Users\YS\Desktop\python\ETF_tracker\stock_mapping.csv `
  --workspace data\agent_treport\security-master `
  --output-path data\agent_treport\security-master\security_master.json
```

Resolve observed holdings:

```powershell
..\.venv\Scripts\python.exe -m agent_treport.cli resolve-security-master `
  --holdings-path .scratch\operational-live-run\operational-holdings\url_holdings_cumulative.json `
  --security-master-path data\agent_treport\security-master\security_master.json `
  --output-path data\agent_treport\security-master\security_master.resolved.json `
  --review-queue-path data\agent_treport\security-master\review_queue.json `
  --observed-partitions 30
```

OpenFIGI lookup is enabled by default. Use `--disable-openfigi-lookup` for an
offline structural-only resolver run. The command loads `OPENFIGI_API_KEY` from
`main/.env` through `python-dotenv`, sends it only as the `X-OPENFIGI-APIKEY`
header, and never writes or prints the key. Conservative defaults are
`batch_size=50`, `min_interval_seconds=1.0`, and `max_requests=20`; when no API
key is present, the effective batch size is lowered to 10 jobs to stay inside
the official unauthenticated limit. A 429 stops further OpenFIGI calls for that
run, records a warning, and exits `0`.

Before OpenFIGI, the resolver auto-verifies structural ticker candidates when
the identifier itself is sufficient: six-digit KRX codes export as themselves,
Korean ISINs export their six-digit display ticker, and Bloomberg-style
`<ticker> <market> [Equity]` identifiers export the leading ticker and market.

When the resolver reports `openfigi_request_limit_reached`, rerun
`resolve-security-master` with the previous `security_master.resolved.json` as
`--security-master-path` to continue lookup from the remaining unresolved
entries. Existing `verified`, `auto_verified`, `excluded`, `review_required`,
`proposed`, and `conflict` entries are preserved; only `unresolved` entries are
retried.

OpenFIGI evidence checked on 2026-05-14: official docs at
`https://www.openfigi.com/api/documentation` document `/v3/mapping`, the
`X-OPENFIGI-APIKEY` header, mapping limits of 25 requests/minute and 10 jobs
without an API key, 25 requests/6 seconds and 100 jobs with an API key, and HTTP
429 for rate-limit exhaustion.

Export the compiled export-facing contract:

```powershell
..\.venv\Scripts\python.exe -m agent_treport.cli export-security-resolution `
  --security-master-path data\agent_treport\security-master\security_master.resolved.json `
  --output-path data\agent_treport\security-master\security_resolution.json
```

Only `verified` and `auto_verified` ticker candidates become mappings.
`excluded` `cash_like` and `non_equity` entries become exclusions. `proposed`,
`review_required`, `unresolved`, and `conflict` entries stay out of the export
and remain operator review work.

For native history, rerun only `export-holdings-comparison
--security-resolution-path <security_resolution.json>` after reviewed recovery.
No holdings history refresh is required when only reviewed security identity
changes. The re-export updates normalized rows, the normalized output
fingerprint, security coverage evidence, and recovery samples.

## Readiness

Run readiness for one focus ETF:

```powershell
..\.venv\Scripts\python.exe -m agent_treport.cli check-operational-readiness `
  --holdings-path .scratch\operational-live-run\operational-holdings\url_holdings_cumulative.json `
  --focus-etf-id <FOCUS_ETF_ID> `
  --observed-partitions 30
```

Run readiness for a FocusETFSet:

```powershell
..\.venv\Scripts\python.exe -m agent_treport.cli check-operational-readiness `
  --holdings-path .scratch\operational-live-run\operational-holdings\url_holdings_cumulative.json `
  --focus-etf-set-path data\agent_treport\focus-etf-sets\default_focus_etf_set.json `
  --observed-partitions 30
```

Optional arguments:

- `--sync-metadata-path <sync_metadata.json>` when checking a non-adjacent
  metadata file.
- `--max-observed-age-days <N>`, default `3`. `0` is strict same-observed-date
  mode.
- `--operator-timezone <IANA_NAME>`, default `Asia/Seoul`.
- `--focus-etf-id <FOCUS_ETF_ID>` remains a compatibility single-focus input.

Readiness writes one compact JSON object to stdout and exits `0` for all
readiness statuses. Redirect stdout to save the result:

```powershell
..\.venv\Scripts\python.exe -m agent_treport.cli check-operational-readiness `
  --holdings-path <copied_manifest> `
  --focus-etf-id <FOCUS_ETF_ID> > .scratch\operational-live-run\readiness.json
```

CLI input errors, such as invalid JSON, invalid timezone, non-positive
`--observed-partitions`, negative `--max-observed-age-days`, or an explicit
missing `--sync-metadata-path`, exit `2` and print
`agent-treport: error: ...` to stderr.

Readiness evidence differs by producer:

- Native `collect-holdings-fixture` output uses adjacent
  `collection_summary.json`.
- Native history `export-holdings-comparison` output uses adjacent
  `collection_summary.json` with active ETF and security coverage evidence.
- Source-acquired native history uses the same
  `export-holdings-comparison` `collection_summary.json`; readiness does not
  consume `source_acquisition_summary.json`.
- Legacy `sync-operational-holdings` output uses adjacent `sync_metadata.json`,
  or the explicit `--sync-metadata-path` when supplied.
- Native missing or incomplete collection summary is `hold`.
- Legacy missing or incomplete sync metadata is `hold`.
- Manifest/evidence mismatches are `failed`.

## Statuses

Severity order is:

```text
ready < ready_with_warnings < hold < failed
```

- `ready`: readiness found no blocking reasons or warnings.
- `ready_with_warnings`: `run-report` is allowed, and artifacts can still be
  user-ready if `run-report` succeeds and `ReportQualityGate` passes. Disclose
  the warnings.
- `hold`: technically runnable artifacts may exist, but the result is
  operator-review-only until required actions are resolved.
- `failed`: the copied input contract is broken enough that `run-report` is
  expected to fail before model execution.

`sync_quality.status="risk_failed"` still does not block sync or
`ReportQualityGate`. Operational `run-report` uses readiness to decide whether
the result may be final `user_ready` or only operator-review output.

## Key Checks

Readiness keeps sync recency and holdings observed-date freshness separate:

- `synced_at` is stored as UTC and compared to the operator-local date.
- Latest observed-date age is compared to the operator-local date.
- Age `0` is fresh.
- Age `1..max_observed_age_days` is `ready_with_warnings`.
- Age above the limit is `hold`.

Evidence checks:

- Missing native `collection_summary.json` is `hold`.
- Missing legacy auto-discovered `sync_metadata.json` is `hold`.
- Explicit missing `--sync-metadata-path` is CLI input error `2`.
- Invalid metadata JSON is CLI input error `2`.
- Manifest/metadata mismatch is `failed`.
- Missing or incomplete sync quality metadata is `hold`.

Mapping checks reuse the sync-quality thresholds:

- Coverage `< 0.50` is `hold` with a required recovery action.
- Coverage `0.50 <= ratio < 0.80` is `ready_with_warnings`.
- Coverage `>= 0.80` is OK.
- Coverage `null` is `ready_with_warnings`.
- Native history `unknown_count > 0` is `ready_with_warnings`.
- Native history export without reviewed `SecurityResolutionExport` is
  `ready_with_warnings`; direct fixture compatibility output is unaffected.

Native history active ETF coverage is checked separately from ticker mapping
coverage:

- FocusETFSet readiness requires at least three focus ETFs with their own valid
  current and previous snapshots.
- Fewer than three eligible focus ETFs is `hold`.
- A compatibility single focus ETF missing the selected current or previous
  snapshot is `failed`.
- Non-focus active ETF comparison gaps are `ready_with_warnings` diagnostics
  and are not user-ready blockers for provider-flexible handoff.
- A focus-only active universe has no non-focus coverage gap.

Readiness fingerprinting reads the copied manifest plus every partition
referenced by the manifest `dates` array before emitting a handoff. If the
fingerprint cannot be computed because a referenced partition is missing,
unsafe, unreadable, or invalid JSONL, `check-operational-readiness` exits `2`
without writing readiness JSON. After that binding is established, readiness
scans the copied partitions needed to select current and previous focus-ETF
snapshots. Missing data that prevents previous snapshot selection is `failed`.

## Run Report

Proceed with final user-ready delivery only when readiness status is `ready` or
`ready_with_warnings`. First save the readiness handoff:

```powershell
..\.venv\Scripts\python.exe -m agent_treport.cli check-operational-readiness `
  --holdings-path .scratch\operational-live-run\operational-holdings\url_holdings_cumulative.json `
  --focus-etf-id <FOCUS_ETF_ID> `
  --observed-partitions 30 > .scratch\operational-live-run\readiness.json
```

Then pass it explicitly to `run-report`:

```powershell
..\.venv\Scripts\python.exe -m agent_treport.cli run-report `
  --run-id <RUN_ID> `
  --holdings-source operational `
  --holdings-path .scratch\operational-live-run\operational-holdings\url_holdings_cumulative.json `
  --focus-etf-id <FOCUS_ETF_ID> `
  --observed-partitions 30 `
  --readiness-path .scratch\operational-live-run\readiness.json `
  --evidence-path .scratch\operational-live-run\external_evidence.json `
  --sqlite-path .scratch\operational-live-run\runtime.sqlite3 `
  --artifact-root .scratch\operational-live-run\artifacts `
  --model codex
```

Final user-ready requirements are:

- readiness allows user-ready output.
- `run-report` status is `succeeded`.
- `ReportQualityGate` status is `passed`.
- readiness warnings, when present, are disclosed.

Operational user-ready output includes `user_ready.readiness` with status,
focus ETF, current date, previous date, disclosures, and the readiness artifact
id. It also includes `user_ready.artifacts.readiness`, pointing at
`artifact_treport_operational_readiness` stored as `operational_readiness.json`.
The readiness artifact includes the export fingerprint and is path-safe; it
omits raw source paths and sample rows. The compact `user_ready.readiness`
summary does not include the fingerprint.

`ready` produces an empty disclosure list. `ready_with_warnings` can produce
`user_ready` only when the readiness warnings project to disclosures; those same
warnings are also projected into `ReportPayload.data_quality` as
`operational_readiness` medium-severity issues with `readiness_` code prefixes
and `readiness_<metric>=<value>` coverage notes.
For source-acquired native history, this projection is the only report-visible
data-quality path for source collection limitations; raw SourceProvider
diagnostics stay out of report payloads and `user_ready`.

## External Evidence

The active evidence-enrichment goal adds Agent TReport-owned financial,
disclosure, and news evidence collection for target securities selected from the
holdings comparison. The collector output is a path-safe evidence file consumed
by the existing `run-report --evidence-path` seam.

Use the manual/operator command before `run-report`:

```powershell
..\.venv\Scripts\python.exe -m agent_treport.cli collect-external-evidence `
  --holdings-source operational `
  --holdings-path .scratch\operational-live-run\operational-holdings\url_holdings_cumulative.json `
  --focus-etf-id <FOCUS_ETF_ID> `
  --observed-partitions 30 `
  --providers fixture_financial,fixture_disclosure,fixture_news `
  --evidence-path .scratch\operational-live-run\external_evidence.json `
  --summary-path .scratch\operational-live-run\external_evidence_summary.json `
  --cooldown-path .scratch\operational-live-run\external_evidence_cooldowns.json
```

Live calls require both explicit provider selection and `--live`:

```powershell
..\.venv\Scripts\python.exe -m agent_treport.cli collect-external-evidence `
  --holdings-source operational `
  --holdings-path .scratch\operational-live-run\operational-holdings\url_holdings_cumulative.json `
  --focus-etf-id <FOCUS_ETF_ID> `
  --providers finnhub,sec_edgar,newsapi `
  --live `
  --max-targets 2 `
  --evidence-path .scratch\operational-live-run\external_evidence.json `
  --summary-path .scratch\operational-live-run\external_evidence_summary.json `
  --cooldown-path .scratch\operational-live-run\external_evidence_cooldowns.json
```

Evidence collection is report support, not readiness. Do not add external
evidence checks to `check-operational-readiness`, and do not change the holdings
export fingerprint when evidence is collected. Missing or skipped evidence must
remain visible as coverage/data-quality limitations in the final
`SignalIntelligenceReport`.

The evidence collection command keeps live calls bounded: explicit live opt-in,
explicit provider set, capped target ticker count (`--max-targets`, default
`2`), timeout, minimum request interval, maximum three total attempts per
request, retry only for transient network/timeout/408/429/5xx failures plus
SEC EDGAR's official 403 rate-threshold page, and provider-level stop/cooldown
on blocked or rate-limited outcomes. SEC EDGAR requests are paced with at least
0.11 seconds between official SEC requests so the adapter stays below 10
requests per second even if the operator supplies a lower interval. SEC EDGAR
cooldowns last 15 minutes; other external evidence provider cooldowns remain 24
hours. `--ignore-cooldown` is available for a bounded operator smoke run that
must ignore an existing local cooldown entry; it does not bypass provider-side
rate limits and may write a new cooldown if the provider still fails. Automated
tests use fixture-backed or mocked adapters only.

Provider IDs:

- Financial: `fixture_financial`, `finnhub`, `yfinance`.
- Disclosure: `fixture_disclosure`, `dart`, `sec_edgar`.
- News: `fixture_news`, `alpha_vantage`, `newsapi`, `naver`.

Live credential/config expectations:

- Finnhub: `FINNHUB_API_KEY`.
- DART/OpenDART: `DART_API_KEY`; the adapter uses the official corp-code
  download to map Korean stock tickers to DART corp codes.
- SEC EDGAR: `SEC_USER_AGENT`; set a descriptive user agent as required by SEC
  fair-access guidance. The adapter first tries SEC's official
  `company_tickers.json` mapping and can fall back to the official `ticker.txt`
  mapping before reading `data.sec.gov/submissions/CIK##########.json`.
- Alpha Vantage: `ALPHAVANTAGE_API_KEY`.
- NewsAPI: `NEWS_API_KEY`.
- Naver: `NAVER_CLIENT_ID` and `NAVER_CLIENT_SECRET`.
- yfinance: optional Python package; use only for operator-approved manual
  smoke because its upstream terms are personal-use oriented.

The evidence file may include normalized source names, titles, published dates,
safe provider-neutral source references, stance, strength, relevance, novelty,
claim scope, and interpretation basis. It must not include raw API payloads,
provider response envelopes, headers, credentials, environment values, local
paths, stack traces, or raw provider-specific URLs unless a later explicit URL
visibility decision allows a narrower report-safe reference.

`external_evidence_summary.json` is the scheduler-facing contract. It includes:

- `target_selection.selected_targets`, `excluded_targets`, and `max_targets`.
- `provider_outcomes[]` with `provider_id`, `category`, `status`,
  `error_code`, `retryable`, `attempt_count`, `stopped_reason`,
  `target_tickers`, `safe_message`, `deduped_count`, and path-safe provider
  metadata such as disclosed target caps.
- `provider_limitations[]` for policy-aware caps such as Alpha Vantage grouped
  `NEWS_SENTIMENT` target limits.
- Pre-publish summaries additionally include `required_provider_ids`,
  `known_unvalidated_provider_exceptions`, `evidence_reuse`, and
  `smoke_boundary` fields so final closure can distinguish validated required
  provider failures from disclosed provider exceptions.
- `category_coverage` with financial, disclosure, and news coverage ratios,
  target ticker states, provider states, and notes.
- `dedupe.deduped_count`, per-category counts, and provider overlap.
- `policy_failure` when an explicitly selected provider hits
  `credential_required`, `blocked`, `rate_limited_exhausted`,
  `provider_unavailable`, `invalid_provider_payload`, or `timeout_exhausted`.
- `evidence_path` and `cooldown_path`.

Policy failures still write `external_evidence.json` and
`external_evidence_summary.json` before the CLI exits nonzero. If partial
evidence exists, pass it to `run-report`; if no evidence exists, run the report
without `--evidence-path`. Pass the summary explicitly or place it next to the
evidence path as `external_evidence_summary.json` so category coverage projects
into the report payload:

```powershell
..\.venv\Scripts\python.exe -m agent_treport.cli run-report `
  --run-id <RUN_ID> `
  --holdings-source operational `
  --holdings-path .scratch\operational-live-run\operational-holdings\url_holdings_cumulative.json `
  --focus-etf-id <FOCUS_ETF_ID> `
  --observed-partitions 30 `
  --readiness-path .scratch\operational-live-run\readiness.json `
  --evidence-path .scratch\operational-live-run\external_evidence.json `
  --evidence-summary-path .scratch\operational-live-run\external_evidence_summary.json `
  --sqlite-path .scratch\operational-live-run\runtime.sqlite3 `
  --artifact-root .scratch\operational-live-run\artifacts `
  --model codex
```

Optional claim alignment is bounded:

```powershell
..\.venv\Scripts\python.exe -m agent_treport.cli collect-external-evidence `
  --holdings-source operational `
  --holdings-path .scratch\operational-live-run\operational-holdings\url_holdings_cumulative.json `
  --focus-etf-id <FOCUS_ETF_ID> `
  --providers fixture_financial,fixture_disclosure,fixture_news `
  --align-claims `
  --model codex `
  --evidence-path .scratch\operational-live-run\external_evidence.json `
  --summary-path .scratch\operational-live-run\external_evidence_summary.json
```

The classifier sees only SignalBoard claim summaries and normalized evidence
candidates. It returns structured JSON decisions only. Low-confidence or
ambiguous alignment remains context evidence and does not affect score.

Official provider documentation consulted for this implementation:

- Finnhub API docs and official Python client README:
  `https://finnhub.io/docs/api`,
  `https://github.com/Finnhub-Stock-API/finnhub-python`.
- yfinance documentation: `https://ranaroussi.github.io/yfinance/`.
- OpenDART API guide:
  `https://opendart.fss.or.kr/guide/detail.do?apiGrpCd=DS001&apiId=2019001`.
- SEC EDGAR API and fair-access guidance:
  `https://www.sec.gov/edgar/sec-api-documentation`,
  `https://www.sec.gov/edgar/searchedgar/accessing-edgar-data`.
- Alpha Vantage documentation and support/limits:
  `https://www.alphavantage.co/documentation/`,
  `https://www.alphavantage.co/support/`.
- NewsAPI documentation:
  `https://newsapi.org/docs`,
  `https://newsapi.org/docs/endpoints/everything`.
- Naver Search News API:
  `https://developers.naver.com/docs/serviceapi/search/news/news.md`.

Missing readiness stops before model calls by default. A mismatched readiness
handoff always stops before model calls and artifact creation, even with an
override. `failed` readiness also stops before model calls and artifact
creation, even with an override. Supplying `--allow-operator-review-output`
with `ready` or disclosure-valid `ready_with_warnings` is also invalid; the
override is only for `hold` or missing-readiness review output.

The readiness handoff is bound to the exact normalized holdings export content
inspected by `check-operational-readiness`. Re-run readiness after any
`collect-holdings-fixture` run, any `export-holdings-comparison` run, any
`sync-operational-holdings` run, or any manifest or partition change. A
missing, malformed, unsupported, or mismatched `export_fingerprint` is a
readiness mismatch and cannot be bypassed with
`--allow-operator-review-output`.

## Operator Review Only

Use `--allow-operator-review-output` only when the operator explicitly wants
diagnostic artifacts that must not be delivered as final user-ready output:

```powershell
..\.venv\Scripts\python.exe -m agent_treport.cli run-report `
  --run-id <RUN_ID> `
  --holdings-source operational `
  --holdings-path .scratch\operational-live-run\operational-holdings\url_holdings_cumulative.json `
  --focus-etf-id <FOCUS_ETF_ID> `
  --observed-partitions 30 `
  --readiness-path .scratch\operational-live-run\readiness.json `
  --allow-operator-review-output `
  --sqlite-path .scratch\operational-live-run\runtime.sqlite3 `
  --artifact-root .scratch\operational-live-run\artifacts `
  --model codex
```

For readiness `hold`, the command can return `status="succeeded"` with
`output.operator_review_only.reason="readiness_hold"` and no
`output.user_ready`. Hold reasons are projected into
`ReportPayload.data_quality` as high-severity `operational_readiness` issues.
The commands under `output.operator_review_only.commands` are internal local
review commands for inspect workflows, not final
LocalFollowUpContract delivery commands.

When `--allow-operator-review-output` is supplied without `--readiness-path`,
the command can create review-only artifacts with
`reason="readiness_not_provided"` and a synthetic path-safe readiness artifact.
That readiness artifact uses status `not_provided`, records
`readiness_not_provided` as the reason, includes only focus ETF, requested
observed partitions, selected dates, and final user-ready requirements, and
omits holdings paths, sync metadata paths, export fingerprints, source paths,
and sample rows. The same reason projects into `ReportPayload.data_quality` as
high-severity `operational_readiness` issue
`readiness_readiness_not_provided`.

## Manual Live Final Review

Run manual live review only after deterministic tests pass. Use an isolated
smoke copy of canonical
`data\agent_treport\live-source\holdings-history\`; do not mutate canonical
live history unless the operator explicitly approves.

Manual scope is bounded to one representative SourceProvider, one
EquityAnalysisETF, and one requested date. A single catalog request is allowed
only if that provider requires a catalog to identify the selected ETF target.
Stop and ask before expanding beyond that scope.

Recommended review sequence:

1. Copy `data\agent_treport\live-source\holdings-history\` to a scratch smoke
   directory.
2. Run one `collect-source-catalog --live --source-provider <PROVIDER>` only if
   needed for target selection.
3. Run one `update-holdings-history-source --live --source-provider <PROVIDER>
   --provider-etf-id <PROVIDER_ETF_ID> --requested-date <YYYY-MM-DD>` into the
   scratch history copy.
4. Run `run-native-operational-handoff` against the scratch history copy with
   reviewed security resolution and any available external evidence summary.
5. Use `--model codex` when available. If Codex/model execution is unavailable,
   record provider/live handoff evidence separately from model/report evidence.
6. Run the handoff's inspect command and confirm references for
  final handoff summary, canonical payload, reports, readiness evidence,
  quality evidence, source acquisition summary, collection summary, external
  evidence summary, and provider/ETF exclusion summary.

2026-05-17 bounded smoke evidence:

- The review used `.scratch\verified-operational-flow-live-smoke\` as an
  isolated copy root for provider and handoff evidence. Canonical
  `data\agent_treport\live-source\holdings-history\` remained unchanged.
- The accepted live SourceProvider boundary is the escalated HYUNDAI command:
  `update-holdings-history-source --live --source-provider hyundai
  --requested-date 2026-05-13 --provider-etf-id 2338258`. It exited 0 with
  `run_outcome="succeeded"`, target outcome `skipped_existing`, observed date
  `2026-05-13`, row count `39`, and written snapshots `0`.
- Earlier sandboxed HYUNDAI and TIGER attempts returned provider response
  failures and remain failure evidence only. The post-fix sandbox attempts were
  bounded to one selected target each.
- Native HYUNDAI export/readiness preflight against the successful isolated
  history reached `ready_with_warnings` with `user_ready_allowed=true`,
  eligible focus ETF count `5`, reviewed security resolution available, and the
  live source target present in the eligible HYUNDAI cohort.
- After explicit user approval for the described Codex/OpenAI export,
  `run-native-operational-handoff --model codex
  --require-verified-operational-flow-acceptance` succeeded under
  `.scratch\verified-operational-flow-live-smoke\escalated-hyundai\handoff-codex-html-escape-fix\`
  with `status="user_ready"`, `delivery_blocked=false`, exit code `0`, and
  `verified_operational_flow_acceptance.status="passed"`.
- `inspect` against
  `run_verified_operational_flow_hyundai_codex_html_escape_fix` succeeded and
  generated artifact references were checked. The quality artifact
  passed with zero violations, and canonical Markdown, HTML, and Telegram
  artifacts were created.
