# Security Identity Aggregation Uses Reviewed Groups, Not Tickers

Accepted. Agent TReport must not use a bare ticker as a canonical aggregation
key for ETF holdings, signal generation, or exposure rollups. The holdings
ledger preserves the source `security_id` identity. Ticker values are display,
lookup, or evidence-enrichment metadata unless a reviewed identity group
explicitly says multiple security identifiers are the same aggregate exposure.

## Context

Operational and native holdings can contain the same displayed ticker across
different security identifiers. This is not only a theoretical concern:

- Current holdings rows usually have `ticker: null`, but applying
  `data/agent_treport/security-master/security_resolution.json` creates same
  ETF/date ticker collisions.
- Alphabet appears in the same ETF/date snapshot as both `US02079K1079`
  (GOOG / Class C) and `US02079K3059` (GOOGL / Class A) for
  `etf_tiger_kr70060h0002` from 2026-05-11 through 2026-05-15. Current
  reviewed exports incorrectly map both to `GOOG`, making this a share-class
  separation regression case rather than an intended alias.
- `SAN` appears as both `ES0113900J37` and `FR0000120578` in one 2026-05-15
  ETF snapshot. These are Banco Santander and Sanofi, so this is a genuine
  cross-market ticker collision rather than one issuer alias.
- The current `security_resolution.json` contains many ticker-to-multiple-ID
  mappings. Many are benign local-code/ISIN pairs, but they are not safe to
  collapse automatically.

External symbology references support the same boundary:

- ISO describes ISIN as the global ISO standard for unique identification of
  financial and referential instruments, and frames ISIN as reducing
  mismatches and confusion in cross-border markets:
  <https://committee.iso.org/sites/tc68/home/articles/content-left-area/articles/what-is-isin.html>
- OpenFIGI mapping accepts ticker plus exchange or MIC filters and returns
  `figi`, `shareClassFIGI`, `compositeFIGI`, `ticker`, and `exchCode` as
  separate concepts:
  <https://www.openfigi.com/api/documentation>
- OMG describes FIGI as a standard for globally identifying financial
  securities and reconciling fragmented symbologies:
  <https://www.omg.org/figi/>
- ANSI X9.145 describes FIGI as a persistent primary-key standard for globally
  identifying instruments across context-bound identifiers:
  <https://webstore.ansi.org/standards/ascx9/ansix91452021>
- CIRO rules describe symbols as trading-purpose identifiers assigned in a
  marketplace context:
  <https://www.ciro.ca/rules-and-enforcement/universal-market-integrity-rules/1015-assignment-identifiers-and-symbols>

## Decision

Agent TReport uses four separate identity concepts:

- `security_id`: the observed constituent identity from the source or reviewed
  security resolution. This remains the normalized holdings ledger key.
- `analytical_identity_key`: the fallback report-analysis identity used when no
  reviewed group exists. Globally meaningful identifiers such as ISIN, KRX
  code, and exchange-qualified Bloomberg equity code use `security_id`; provider
  local or unknown identifiers are scoped by `source_provider_id`.
- `listing_key`: a market-scoped listing identity such as ticker plus exchange
  or MIC. This is suitable for display disambiguation, price lookup, and
  provider-specific enrichment, not canonical exposure aggregation.
- `security_group_id`: a reviewed aggregate identity. Only this key can
  intentionally combine multiple `security_id` values into one report exposure.
- `display_ticker`: a human-readable label. It is never a primary key.

When a reviewed group intentionally unifies multiple source identifiers for the
same share class, report surfaces should prefer the reviewed group display label
over arbitrary source names while preserving the member `security_id` values as
evidence. If a reviewed group lacks a display label, aggregation still proceeds
with a deterministic fallback label and the report records a data-quality
warning.

Aggregation defaults to the identity-safe analytical fallback, not to a bare
ticker. Report builders aggregate by `security_group_id` only when a reviewed
`SecurityMaster` or equivalent identity ledger has explicitly assigned one. If
no reviewed group exists, global identifiers can aggregate by `security_id`;
provider-local or unknown identifiers remain provider-scoped. Two different
identities remain separate even when their display tickers match.

## Consequences

- Same ticker plus different `security_id` is a data-quality/review condition,
  not an automatic merge instruction.
- Same ETF/date collisions on mapped ticker must be surfaced in security
  review evidence before they can affect signal aggregation.
- Security resolution export and holdings export coverage should create this
  review evidence, because report construction should consume reviewed identity
  projections rather than discover identity policy from raw ticker collisions.
- Ticker-only grouping in `SignalReportPayload` construction has been removed.
  Report aggregation uses `security_group_id or security_id`, while retaining
  display ticker for labels and reviewed provider lookup metadata.
- External evidence lookup may use ticker or listing metadata supplied by the
  reviewed identity projection, but evidence attachment must match the
  identity-safe claim rather than a bare ticker.
- Claim scopes should be based on `security_group_id` or `security_id`, not on
  display ticker. This implementation does not need ticker-based claim-scope
  compatibility because no cross-run evidence database is in scope.
- Existing v1 security-resolution exports may still be read, but missing new
  identity fields default to non-grouping behavior. Compatibility is limited to
  parsing old inputs; ticker-based merge behavior must not be preserved.
- Ambiguous bare tickers fail closed for external evidence enrichment: the
  holdings signal remains visible, but the target is excluded from provider
  lookup and disclosed in coverage or data-quality evidence.
- GOOG/GOOGL share-class handling is a resolved product policy decision for
  this implementation: different share classes remain separate report
  exposures even when they share the same issuer.
- `SAN` demonstrates why ticker-only merge logic is invalid even when names are
  present. Cross-market listing context or an external identifier must
  disambiguate the display value.
- Readiness and data-quality summaries should count unresolved ticker
  collisions separately from missing ticker coverage.

## Implementation Note - 2026-05-17

- Reviewed security identity inputs and exports now accept optional
  `security_group_id`, `listing_key`, and group display metadata. Existing v1
  security-resolution exports remain parseable and default to non-grouping
  behavior when identity fields are absent.
- Normalized holdings exports carry identity metadata into report
  construction. Security coverage now emits path-safe ticker-collision review
  evidence when the same ETF/date maps one display or lookup ticker to
  multiple securities without a shared reviewed group.
- `SignalReportPayload` aggregation, `SignalBoard` claim scopes,
  `TickerDossier` references, score-impacting evidence attachment, and
  evidence-ledger `used_in` references use `security_group_id` when explicitly
  reviewed and otherwise `security_id`.
- External evidence target selection carries canonical identity plus reviewed
  listing/lookup metadata. Ambiguous bare ticker targets are excluded from
  enrichment and disclosed through coverage/data-quality notes while keeping
  the holdings signal visible.
- Regression coverage proves SAN false aliases remain separate, GOOG/GOOGL
  share classes remain separate even under bad ticker mappings,
  same-share-class aliases aggregate only with a reviewed group, missing group
  display labels produce deterministic fallback labels plus warnings, and
  claim-scoped evidence affects scores only on exact identity-safe claims.
- No `agent_pack` changes were required.

## Implementation Note - 2026-06-01

- Provider-specific `SecurityMaster` and provider-specific
  `SecurityResolutionExport` are the operating records for the live
  SourceProvider cohort. Agent TReport does not flatten asset-manager-specific
  source semantics into one cohort-level master ledger.
- Provider-cache resolution can read observations from
  `source-provider-operational/<provider>/holdings-history/holdings_history.json`
  while writing the provider's own `security-master/` artifacts. Review queue
  items from provider-cache resolution retain `source_provider_id` as source
  evidence.
- Normalized holdings exports now carry `analytical_identity_key` and
  `analytical_identity_scope`. The key is `security_id` for global identifiers
  and `provider=<source_provider_id>|security=<security_id>` for provider-local
  or unknown identifiers.
- Report construction uses `security_group_id` when reviewed, then
  `analytical_identity_key`, then legacy `security_id`. This keeps ticker-facing
  analysis usable while preventing raw ticker or provider-local id collisions
  from becoming implicit merges.

Verification evidence:

- `../.venv/Scripts/python.exe -m pytest tests/test_agent_treport_signal_report.py tests/test_agent_treport_external_evidence.py tests/test_agent_treport_operational_holdings_adapter.py tests/test_agent_treport_cli.py`
  passed with 280 tests.
- `../.venv/Scripts/python.exe -m pytest` passed with 509 tests.
- `../.venv/Scripts/python.exe -m ruff check src/agent_treport tests` passed.
- `../.venv/Scripts/python.exe -m pyright` passed with 0 errors.

## Remaining Future Work

- Add a broader operator review workflow: for each
  `(etf_id, observed_date, mapped_ticker)`, if more than one `security_id`
  appears and no shared reviewed `security_group_id` exists, queue a
  human-reviewed ticker-collision decision.
- Keep stored holdings history immutable. Reviewed grouping decisions apply in
  export or report views, not by rewriting historical source observations.
- Prefer exchange-qualified display values such as `SAN:BME` or `SAN:EPA`
  when a ticker is known to be ambiguous.
- The implementation closes the generic reviewed-group mechanism and
  representative regressions only. It must not bulk-create alias groups for the
  existing security master; candidate generation and operator review workflow
  remain future work.
