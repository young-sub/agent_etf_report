# SecurityResolutionExport Owns Ticker-Candidate Export Classification

Accepted. Agent TReport will not let normalized holdings exports read the rich `SecurityMaster` review ledger directly; instead, `SecurityMaster` is compiled into a `SecurityResolutionExport` containing only export-eligible ticker mappings and explicit non-ticker exclusions. This keeps review/proposal state out of sync and native history export, preserves the existing minimal `SecurityMapping` as a backward-compatible export, and lets normalized holdings carry `security_classification` so ticker coverage uses ticker-candidate rows rather than all non-cash rows.

## Consequences

- `SecurityMaster` can store verified, proposed, unresolved, conflict, and excluded entries without risking unreviewed data in operational sync.
- `SecurityResolutionExport` is the shared normalized-holdings-export contract for combined ticker mappings and exclusions.
- Normalized operational holdings gain `security_classification` values so `cash_like`, `non_equity`, `ticker_candidate`, and `unknown` rows have explicit denominator and report-quality semantics.
- `ticker_mapping_coverage_ratio` is redefined over ticker-candidate rows while cash-like and confirmed non-equity rows are tracked separately.

## Implementation Notes

- `SecurityMaster` is stored as schema-versioned JSON first. SQLite is deferred.
- `import-security-master-seed` imports ETF Tracker `stock_mapping.csv` as
  `auto_verified` seed entries and preserves existing `verified` or
  `auto_verified` entries on conflicts.
- `resolve-security-master` observes normalized holdings, applies structural
  cash-like/non-equity rules and clear ticker rules for KRX codes, Korean ISIN
  display tickers, and Bloomberg-style equity codes, writes unresolved or
  conflicting entries to a review queue, and optionally calls OpenFIGI.
- OpenFIGI is enabled by default and disabled with `--disable-openfigi-lookup`.
  The implementation loads `OPENFIGI_API_KEY` from `main/.env` through
  `python-dotenv`, sends it only in the `X-OPENFIGI-APIKEY` header, and does
  not print or persist it.
- OpenFIGI official docs checked on 2026-05-14:
  `https://www.openfigi.com/api/documentation`. The implementation uses
  `/v3/mapping`. Documented limits are 25 mapping requests/minute and 10 jobs
  without an API key, 25 mapping requests/6 seconds and 100 jobs with an API
  key, and HTTP 429 when rate limits are reached. Runtime defaults are more
  conservative: `batch_size=50`, `min_interval_seconds=1.0`,
  `max_requests=20`; unauthenticated clients lower the effective batch size to
  10 jobs to stay inside the official limit. 429 stops further OpenFIGI calls
  for the run, records a warning, and exits `0`.
