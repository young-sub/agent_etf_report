# Operational Holdings Export Input Contract

## Status

Accepted.

## Context

Agent TReport needs local operational ETF holdings data for `SignalReportWorkflow`
without mutating or depending on the user's live ETF Tracker operational folder.
The live export manifest stores absolute `partitions.file` paths that point back
to the ETF Tracker data directory, so reading those paths directly would make a
local Agent TReport run depend on mutable external state.

The workflow also needs deterministic comparison windows. ETF holdings data is
observed on business or crawl dates rather than every calendar day, so a
calendar-day cutoff can produce unstable or surprising local comparisons.

## Decision

Use a gitignored copied **OperationalHoldingsExport** as the local input contract.

- Default copied location: `data/agent_treport/operational-holdings/`.
- Manifest file name: `url_holdings_cumulative.json` for CLI continuity, but its
  copied contents use the Agent TReport normalized operational schema.
- Partition directory: `url_holdings_cumulative.json.parts/` next to the copied
  manifest. Copied partition rows use Agent TReport domain field names, not ETF
  Tracker source row field names.
- The sync window is the latest `30` observed partitions from the manifest's
  ordered `dates`, not the latest 30 calendar days.
- `sync-operational-holdings` reads the source manifest and selected source
  partition files, then writes a normalized copied manifest and normalized copied
  partition files into the destination directory. It owns raw-to-domain field
  mapping, required non-cash weight parsing, cash-weight derivation, optional
  numeric null normalization, duplicate aggregation, per-code display-name
  normalization, cash detection, and optional security-to-ticker mapping.
- `sync-operational-holdings --security-mapping-path <path>` optionally loads a
  deterministic fixture-backed **SecurityMapping**. If the flag is omitted, sync
  uses no mapping adapter; non-cash copied rows get `ticker=null` instead of
  falling back to `security_id`. If the flag is provided and the file is missing
  or invalid, sync fails with an input contract error and CLI exit code `2`.
- `sync-operational-holdings` writes `sync_metadata.json` containing source and
  copied manifest paths, requested observed partition count, source dates, copied
  normalized dates, field mappings, and sync time.
- `sync_metadata.json` contains `schema_version`, `source_manifest_path`,
  `copied_manifest_path`, `requested_observed_partitions`, `source_dates`,
  `copied_dates`, `missing_source_dates`, `source_record_count`,
  `copied_partition_count`, `copied_record_count`,
  `security_mapping_available`, `security_mapping_path`,
  `mapped_security_count`, `unmapped_security_count`,
  `unmapped_security_samples`,
  `skipped_missing_security_id_count`,
  `numeric_null_normalized_count`, `duplicate_aggregated_count`,
  `derived_cash_weight_count`, `derived_cash_weight_fit_failed_count`,
  `skipped_unusable_cash_weight_count`, `uncoded_cash_holding_count`,
  `renamed_security_count`, `security_name_aliases`,
  `cash_identification_counts`, `source_quality_samples`,
  `sync_quality`, `source_file_strategy_counts`, `field_mappings`, and
  `synced_at`.
  `security_name_aliases` is capped at 20 samples.
  `source_quality_samples` is capped at 20 samples and excludes local source
  paths, source URLs, fetched timestamps, brand names, and other provider
  envelope fields that are not needed to diagnose normalization decisions.
  `unmapped_security_samples` is always present and is capped at 20 samples.
  The metadata schema version is
  `agent_treport.operational_holdings.sync_metadata.v1`.
- `unmapped_security_samples` is a top-level sync metadata recovery input for
  **SecurityMapping** work. It contains path-safe
  **SecurityMappingRecoverySample** objects for final copied normalized rows
  where `is_cash=false` and `ticker == null`; if no rows match, it is `[]`.
  Samples are built after duplicate aggregation and cash handling, so duplicate
  source rows count once when they collapse into one copied normalized row. Cash
  rows never appear in this list.
- Each `unmapped_security_samples` item has exactly `security_id`, `name`,
  `observed_row_count`, `observed_etf_count`, `observed_date_count`, and
  `name_alias_count`. Samples aggregate by `security_id`. `observed_row_count`
  counts final copied normalized rows, `observed_etf_count` counts distinct
  final copied normalized `etf_id` values, and `observed_date_count` counts
  distinct final copied normalized `as_of_date` values. `name` is the first
  observed canonical name. `name_alias_count` is
  `max(0, distinct_name_count - 1)` using names observed among unmapped
  non-cash sample rows only.
- `unmapped_security_samples` sorts by `observed_row_count` descending, then
  `observed_etf_count` descending, then `security_id` ascending, and then caps
  at 20. It excludes local paths, URLs, fetched timestamps, provider envelope
  fields, source line numbers, ETF ids, and date values. It is not part of
  `sync_quality`; `sync_quality.metrics` remains count/ratio oriented.
- `sync_quality` is an **OperationalSyncQualityDiagnostic** evidence object, not
  report readiness logic. It has
  `schema_version="agent_treport.operational_holdings.sync_quality.v1"`,
  `status`, `metrics`, `warnings`, and `risk_failures`. Status values are `ok`,
  `warning`, and `risk_failed`. `risk_failed` means sync completed but a
  denominator-backed source-data quality ratio is too high to trust the export
  as operational input without review. It does not change the
  `sync-operational-holdings` exit code, block `run-report`, or create a
  `ReportQualityGate` failure.
- `sync_quality.metrics` contains `cash_derivation_attempt_count`,
  `cash_derivation_failure_count`, `cash_derivation_failure_ratio`,
  `fit_failure_ratio`, `unusable_cash_weight_ratio`, `cash_like_row_count`,
  `missing_source_date_count`, `missing_source_dates`,
  `skipped_missing_security_id_count`, `security_mapping_available`,
  `mapped_security_count`, `unmapped_security_count`,
  `ticker_mapping_coverage_ratio`, and
  `cash_derivation_failure_distribution`. Ratios are rounded with
  `round(value, 6)` and are null when their denominator is zero. Missing source
  dates and distribution dates use copied/export ISO `YYYY-MM-DD` dates.
- `cash_derivation_attempt_count` is
  `derived_cash_weight_count + derived_cash_weight_fit_failed_count +
  skipped_unusable_cash_weight_count`. `cash_derivation_failure_count` is
  `derived_cash_weight_fit_failed_count + skipped_unusable_cash_weight_count`.
  `cash_like_row_count` is the sum of `cash_identification_counts` values.
  `cash_derivation_failure_distribution` has `by_reason` and `by_date` maps;
  the known reasons are `no_weight_fit_sample`,
  `weight_fit_tolerance_exceeded`, `invalid_cash_market_value`, and
  `invalid_snapshot_market_value_total`.
- `mapped_security_count` counts final copied rows where `is_cash=false` and
  `ticker != null`. `unmapped_security_count` counts final copied rows where
  `is_cash=false` and `ticker == null`. Cash rows are never counted as mapped or
  unmapped. `ticker_mapping_coverage_ratio` is
  `mapped_security_count / (mapped_security_count + unmapped_security_count)`;
  it is null when the denominator is zero.
- `cash_derivation_failure_ratio >= 0.20` sets status `risk_failed`;
  `cash_derivation_failure_ratio >= 0.05` and `< 0.20` sets status `warning`.
  `ticker_mapping_coverage_ratio < 0.50` sets status `risk_failed`;
  `ticker_mapping_coverage_ratio < 0.80` and `>= 0.50` sets status `warning`.
  Higher severity wins, so a risk-failed metric does not also add a warning for
  that metric. Isolated non-zero counts can create warnings but not risk
  failures. `skipped_missing_security_id_count > 0` and
  `missing_source_date_count > 0` are warning-only. `numeric_null_normalized_count`,
  `duplicate_aggregated_count`, `renamed_security_count`, and
  `uncoded_cash_holding_count` remain summary counts and do not affect
  `sync_quality.status`.
- `sync_quality` is path-safe. It must not include live or copied absolute paths,
  `security_mapping_path`, partition `source_file_used` values, source URLs,
  fetched timestamps, or full source row envelopes, even though the full
  `sync_metadata.json` may retain path provenance for local operator
  diagnostics.
- The normalized copied manifest has top-level `schema_version`,
  `storage_format`, `source_storage_format`, `source_updated_at`, `synced_at`,
  `dates`, `record_count`, and `partitions`. Its `dates` are ISO dates in
  descending observed-date order. Each `partitions[date]` entry contains `file`,
  `record_count`, `source_date`, `source_file_used`, and `source_file_strategy`;
  `file` is relative to the copied manifest and must not escape the copied export
  directory. Manifest and partition `record_count` values count copied normalized
  rows, not raw source rows.
- The operational holdings provider ignores source-manifest absolute
  `partitions.file` values. For normalized copied manifests, it reads
  `partitions[date].file` only when the value is a relative path that stays under
  the copied manifest directory; absolute paths and `..` escapes are input
  errors.
- `run-report --holdings-source operational` accepts only normalized copied
  exports with `schema_version="agent_treport.operational_holdings.v1"`, requires
  a focus ETF id, and uses the latest available focus ETF partition as `current`,
  then the nearest prior observed partition with focus ETF holdings as
  `previous`.
- `run-report --observed-partitions` is interpreted only against normalized
  manifest ISO `dates`. Source raw dates are provenance only.
- The run provider validates that normalized manifest `dates` are ISO strings in
  descending observed-date order. It does not sort or repair normalized manifests.
- Selection scans the latest requested normalized dates, skipping missing
  partition files while recording `missing_partition_dates`. Once focus ETF
  `current` and `previous` dates are selected, final `SignalReportInputs` are
  built from those two partitions only.
- The provider includes only ETFs that have holdings rows on both selected dates.
  It sets `universe` to
  `operational_holdings:{current_date}:{previous_date}`.
- Operational provenance is persisted separately from normalized
  `signal_report_inputs.json`; raw rows are not duplicated into run artifacts.
  When `sync_metadata.json` exists next to the copied manifest, run provenance
  includes `sync_metadata_available=true`, sync quality counts, capped source
  quality samples, and path-safe `sync_quality` when the metadata contains it.
  It does not duplicate live source paths or full sync envelope fields.
  `unmapped_security_samples` remains sync metadata and CLI stdout recovery
  input only; it is not copied into run provenance. If sync metadata is old and
  lacks `sync_quality`, provenance keeps the counts and samples and omits
  `sync_quality`. If sync metadata is absent, provenance records
  `sync_metadata_available=false` and loading continues.
- `SignalReportWorkflow` may project this path-safe operational provenance
  subset into `ReportPayload.data_quality`: unavailable sync metadata becomes a
  medium operational issue, `sync_quality.warnings` become medium operational
  issues, `sync_quality.risk_failures` become high operational issues, and
  selected scalar sync-quality metrics become coverage notes. This projection is
  user-facing report truth only; it does not duplicate raw sync metadata, source
  paths, URLs, timestamps, samples, or distribution objects into the payload and
  does not make `ReportQualityGate` parse operational sync metadata.

The sync side of the operational adapter translates source row vocabulary into
Agent TReport vocabulary before copied test or run data is written:

- Source `fund_id` becomes `ETFHoldingsSnapshots.etf_id` and CLI
  `--focus-etf-id` values match this normalized ETF id.
- Source `fund_name` becomes `ETFHoldingsSnapshots.etf_name`.
- Source `brand_id` and `source_provider_id` keep the same names because they already
  match `ETFHoldingsSnapshots` fields.
- Source `code` becomes `SecurityHolding.security_id`. `SecurityHolding.ticker`
  is optional display data resolved only through an explicit
  **SecurityMapping**. Sync keeps `security_id` stable and does not replace it
  with a ticker.
- Source `name` becomes `SecurityHolding.name` after per-code display-name
  normalization.
- Source `weight_pct`, `quantity`, and `eval_amount_krw` become
  `weight_percent`, `shares`, and `market_value_krw`.
- Source `as_of_date` values are raw observed partition dates. Domain snapshot
  dates, copied manifest dates, and copied partition row dates are normalized to
  ISO `YYYY-MM-DD` strings.
- Source fields with no current domain target, including `brand_name`,
  `source_url`, `fetched_at`, return fields, and `listing_date`, stay out of the
  normalized snapshot. They may appear in provenance or later enrichment slices
  only.
- Current domain fields without reliable operational row sources, including
  `market`, `sector`, `theme`, `country`, and `price_krw`, are set to `None` in
  this slice.

Security mapping files have this schema:

```json
{
  "schema_version": "agent_treport.security_mapping.v1",
  "mappings": [
    {
      "security_id": "US67066G1040",
      "ticker": "NVDA"
    }
  ]
}
```

`security_id` and `ticker` are required non-empty strings. Sync trims leading and
trailing whitespace only; it does not uppercase or otherwise normalize tickers.
Duplicate `security_id` values are input contract errors. Duplicate `ticker`
values across different security ids are allowed. The mapping schema contains no
ISIN, market, alias, provider, sector, theme, country, price, or enrichment
fields in this slice.

Security mapping recovery uses two additional local artifact contracts without
changing the sync or run-report payload contracts.

`agent-treport propose-security-mapping-recovery` reads only
`--sync-metadata-path`, requires `--model codex`, accepts optional
`--codex-model`, `--model-timeout-seconds`, and `--overwrite`, and requires
`--output-path`. The command validates `unmapped_security_samples` strictly from
`sync_metadata.json`, validates the output path before any model call, never
overwrites the sync metadata path, and does not create or call a model client
when samples are empty. Model prompts include only each sample's `security_id`,
`name`, `observed_row_count`, `observed_etf_count`, `observed_date_count`, and
`name_alias_count`; prompts exclude paths, source URLs, ETF ids, dates, existing
mappings, and partition rows.

The saved proposal artifact schema is:

```json
{
  "schema_version": "agent_treport.security_mapping.recovery_proposal.v1",
  "source_sync_metadata_path": "data/agent_treport/operational-holdings/sync_metadata.json",
  "proposals": [
    {
      "security_id": "US67066G1040",
      "name": "NVIDIA Corp.",
      "proposed_ticker": "NVDA",
      "status": "proposed",
      "confidence": "high",
      "rationale": "Human review required before use."
    }
  ]
}
```

The model response is an internal untrusted boundary and must be exactly one
assistant text block containing one JSON object with exactly one top-level
`proposals` field. The command sets the saved artifact's `schema_version` and
`source_sync_metadata_path` itself. Each sample must have exactly one proposal by
`security_id`; missing, duplicate, or extra proposal ids fail the operation.
Proposal entries contain exactly `security_id`, `name`, `proposed_ticker`,
`status`, `confidence`, and `rationale`. `status="proposed"` requires a
non-empty string `proposed_ticker`; `status="unresolved"` requires
`proposed_ticker=null`; `confidence` is `high`, `medium`, or `low`. Tickers are
trimmed only and case is preserved. Invalid sync metadata is an input error with
CLI exit code `2`; invalid model output or proposal schema is an operational
failure with sanitized JSON stderr reason
`security_mapping_recovery_proposal_failed`.

The reviewed patch schema is:

```json
{
  "schema_version": "agent_treport.security_mapping.patch.v1",
  "mappings": [
    {
      "security_id": "US67066G1040",
      "ticker": "NVDA"
    }
  ]
}
```

`SecurityMappingPatch` contains no confidence, rationale, reviewer, timestamp,
or proposal metadata. Validation trims `security_id` and `ticker`, preserves
ticker case, rejects duplicate patch `security_id` values, allows duplicate
ticker values, and rejects an empty patch.

`agent-treport apply-security-mapping-patch` requires
`--security-mapping-path`, `--patch-path`, and `--output-path`, with optional
`--overwrite` and `--allow-replacements`. The existing mapping file must exist;
the command does not create or initialize mappings. The output parent directory
must already exist. Existing output files fail unless `--overwrite` is supplied.
`--output-path == --patch-path` fails even with `--overwrite`.
`--output-path == --security-mapping-path` is allowed only with `--overwrite`.
Same-`security_id`, same-ticker entries are idempotent no-ops; different-ticker
replacements fail by default and require `--allow-replacements`. Conflict
messages include the `security_id` and `existing mapping conflict`, but not the
old or new ticker values. The merged output is only the existing
`agent_treport.security_mapping.v1` schema with mappings sorted by
`security_id`; it includes no patch path, timestamps, counts, or metadata.
Input errors return exit code `2` with `agent-treport: error: ...`; unexpected
write failures return exit code `1` with sanitized JSON stderr.

Successful security mapping recovery commands emit a schema-versioned stdout
"operator automation result". This stdout result is not a saved artifact. It is
compact one-line JSON with one trailing newline and is emitted only after a
successful command. Failed commands keep the existing exit-code and stderr
behavior and emit no success result on stdout.

Stdout path fields are exact user-supplied CLI argument strings echoed after
success. They are not resolved paths, canonicalized paths, security evidence, or
provenance. The saved proposal artifact keeps `source_sync_metadata_path`;
stdout uses `sync_metadata_path`. Saved proposal and mapping artifacts must not
gain stdout-only fields.

The `apply-security-mapping-patch` stdout result schema is:

```json
{
  "schema_version": "agent_treport.security_mapping.patch_apply_result.v1",
  "status": "succeeded",
  "security_mapping_path": "data/agent_treport/security_mapping.json",
  "patch_path": "patches/reviewed_patch.json",
  "output_path": "data/agent_treport/merged_security_mapping.json",
  "added_mapping_count": 1,
  "replaced_mapping_count": 0,
  "unchanged_mapping_count": 1,
  "total_mapping_count": 3
}
```

`unchanged_mapping_count` counts patch entries that were same-ticker no-ops, not
untouched existing mappings. `replaced_mapping_count > 0` is possible only on a
successful apply path where `--allow-replacements` was supplied.

The `propose-security-mapping-recovery` stdout result schema is:

```json
{
  "schema_version": "agent_treport.security_mapping.recovery_proposal_result.v1",
  "status": "succeeded",
  "sync_metadata_path": "data/agent_treport/operational-holdings/sync_metadata.json",
  "output_path": "data/agent_treport/security_mapping_recovery_proposal.json",
  "sample_count": 2,
  "proposal_count": 2,
  "proposed_count": 1,
  "unresolved_count": 1,
  "model_called": true
}
```

For proposal result v1, the invariant is
`proposed_count + unresolved_count == proposal_count == sample_count`.
`model_called=true` means a non-empty sample set caused a successful model
completion on a successful command path. Empty samples produce `sample_count=0`,
`proposal_count=0`, and `model_called=false`.

Normalized copied partition rows require `etf_id`, `etf_name`, `brand_id`,
`source_provider_id`, `as_of_date`, `security_id`, `ticker`, `name`, `weight_percent`,
`shares`, `market_value_krw`, `price_krw`, and `is_cash`. `market`, `sector`,
`theme`, `country`, `shares`, `market_value_krw`, `price_krw`, and `ticker` may
be null. `security_id`, `name`, `weight_percent`, and `is_cash` must not be null.

If a source row lacks `code` and is not identifiable as cash, sync skips that row
because sync has no stable security identity. If a source row lacks `code` but is
identifiable as cash, sync preserves it as an uncoded cash holding with
`security_id="CASH_UNCODED:{source_provider_id}:{fund_id}"`, `ticker=null`,
`name="Cash"`, and `is_cash=true`. This keeps uncoded cash distinguishable from
coded cash rows while allowing cash summary calculations to aggregate all
`is_cash=true` holdings.

Cash rows are never ticker candidates. Coded cash rows and uncoded cash rows
always have `ticker=null`, even when a mapping file contains an entry for their
source code.

Cash detection covers source rows whose `code` starts with `CASH`, whose `code`
is one of `KRD010010001`, `010010`, or `USDZZ0000001`, whose `code` starts with
`KRW`, `USD`, `EUR`, or `JPY`, or whose name contains cash, currency, or deposit
keywords such as `현금`, `예금`, `설정현금`, `현금성자산`, `예수금`, `Cash`, or
`MMDA`. Broader cash-equivalent instruments such as MMF, REPO, RP, T-BILL,
short-term bonds, commercial paper, and CP are out of scope for this contract.
`cash_identification_counts` records the first matching rule per row using the
priority order `code_exact_cash`, `code_prefix_cash`, `code_prefix_currency`,
`uncoded_cash_keyword`, then `name_cash_keyword`.

If a non-cash source `weight_pct` cannot be parsed, sync fails with an input
error. If a cash source row has `weight_pct=null`, sync derives `weight_percent`
only after duplicate aggregation by using that ETF/date snapshot's valid net
`eval_amount_krw` total as the denominator and the cash row's valid
`eval_amount_krw` as the numerator. The derived value is stored as
`round(value, 6)`. Negative cash market values can derive negative weights when
the ETF/date net denominator remains positive. If the denominator is unavailable
or not positive, or the cash market value is unavailable, the affected cash row is
skipped and counted as `skipped_unusable_cash_weight_count`.

Before accepting derived cash weights for an ETF/date, sync verifies the same
market-value denominator against aggregated rows in that ETF/date that have both
valid source `weight_pct` and valid `eval_amount_krw`. If the median absolute
fit error is greater than `0.5` percentage points, or no fit sample exists, the
ETF/date's null-weight cash rows are skipped and counted as
`derived_cash_weight_fit_failed_count`. Fit failures do not fail sync by
themselves. `derived_cash_weight_fit_failed_count` and
`skipped_unusable_cash_weight_count` are row-level counts and are not double
counted. Diagnostic sample reasons include `no_weight_fit_sample`,
`weight_fit_tolerance_exceeded`, `invalid_cash_market_value`,
`invalid_snapshot_market_value_total`, `missing_security_id`, and
`non_cash_null_weight`.

If source `quantity` or `eval_amount_krw` cannot be parsed for a copied row, sync
normalizes that field to null and records numeric-null counts in metadata.
Numeric-null counts do not include skipped cash derivation rows. Copied rows are
aggregated by `(etf_id, as_of_date, security_id)` during sync; duplicate copied
rows are a normalized contract violation and make run-report fail before model
execution. Copied `name` and `is_cash` are final sync outputs trusted by the run
provider.

The run provider validates copied partition row contracts instead of repairing
them. A row `as_of_date` must match the manifest partition date and file date.
Duplicate `(etf_id, as_of_date, security_id)` rows fail with
`duplicate normalized holding: etf_id=<...> date=<...> security_id=<...>`.
Read partition row counts must match `partitions[date].record_count`. Because
`run-report` may read only a date subset, it does not validate top-level
manifest `record_count`; sync validates the full copied manifest count when it
writes the export.

Source field names may appear in the source-reading sync parser, field-mapping
metadata, and provenance only. Active domain code, committed tests, committed
fixtures, copied operational exports, and docs should use ETF and
`SecurityHolding` vocabulary after the source parser boundary.
Raw source-vocabulary committed fixtures are limited to
`tests/fixtures/operational_holdings_source/` for sync parser tests. Normalized
copied-export fixtures live under `tests/fixtures/operational_holdings/` and use
Agent TReport vocabulary.

## Consequences

Agent TReport runs stay deterministic and local after sync. The live ETF Tracker
folder remains read-only, old copied partitions can remain without changing the
run window, and provenance can explain exactly which source dates and normalized
copied dates were used.

The provider must fail before model execution when the copied manifest,
partition directory, focus ETF, or comparable previous snapshot is unavailable.
Missing non-focus rows or skipped non-focus invalid records are reported through
provenance rather than silently changing focus ETF validation.

## Alternatives Considered

- Read the live ETF Tracker manifest and absolute partition paths directly:
  rejected because it couples Agent TReport runs to mutable external state and
  risks accidental writes or hidden dependencies.
- Keep copied partition rows in ETF Tracker source vocabulary: rejected because it
  spreads provider terminology into Agent TReport tests and run-time loading.
  Source vocabulary is normalized once during sync.
- Copy the source manifest unchanged: rejected because the copied export is an
  Agent TReport input contract, not a second live ETF Tracker data directory.
  Source provenance is preserved in `sync_metadata.json` instead.
- Use a 30-calendar-day window: rejected because holdings observations follow
  business or crawl dates, not calendar-day continuity.
- Commit real operational holdings data as fixtures: rejected for the real
  export; committed tests use minimal normalized synthetic copied-export fixtures
  while real operational data stays gitignored.
