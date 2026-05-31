# SourceProvider acquisition is staged and path-safe

Accepted. Agent TReport source acquisition is scoped by `SourceProvider`, not
by `ETFBrand`, because one external provider can expose multiple brands and
provider site/API structure is the boundary that controls catalog and holdings
access. A holdings fetch target is identified by
`source_provider_id + provider_etf_id`; canonical `etf_id` remains Agent
TReport-owned universe identity, and URL or endpoint locators are internal
provider details rather than identity.

Live catalog acquisition stages a provider catalog first. Agent TReport mutates
`universe_state.json` only after the staged catalog is complete, valid, and
path-safe, so incomplete provider responses cannot remove or rewrite existing
ETF or brand state. Holdings acquisition can partially update history: fetched
ETF/date snapshots are written while failed, rate-limited, or unsupported
targets are recorded as target outcomes. Existing duplicate skip and explicit
refresh-required semantics still apply.

Default operator evidence is path-safe. Source acquisition summaries may expose
source provider ids, brand ids, canonical ETF ids, provider ETF ids, requested
and observed dates, target outcomes, row counts, failure code classes, retry
attempt counts, run outcome, and aggregate counts. They must not expose raw
URLs, endpoints, response bodies, response headers, local paths, credentials,
raw holdings rows, or raw provider envelopes. Report-visible output receives
only readiness and data-quality projections derived from that evidence.

Live providers require explicit opt-in. Fake providers are the automated test
path and must not call the network; live commands require `--live` and an
explicit SourceProvider selection. KODEX was the initial live smoke provider
because the audited catalog and one ETF/date holdings surfaces were JSON,
credential-free for bounded smoke, and could stay behind the provider-neutral
contract. The current registered live SourceProvider cohort is KODEX, ACE,
HYUNDAI, TIMEFOLIO, TIGER, RISE, and SOL.

This avoids copying the reference projects' provider-specific sprawl. The
reference implementations remain behavioral evidence only: their URLs, local
paths, raw payload handling, cooldown state, and broad multi-provider crawler
structure do not define Agent TReport domain contracts.
