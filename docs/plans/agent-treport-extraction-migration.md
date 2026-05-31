# Agent TReport Extraction Migration Plan

Status: draft accepted for issue tracking
Owner repo: `young-sub/agent_etf_report`
Source repo: `young-sub/agent_pack`
Tracker issue: `young-sub/agent_etf_report#1`
Date: 2026-05-31

## Decision Summary

Move the current Agent TReport domain application out of `agent_pack` into this
standalone sibling repository without changing runtime behavior.

Important naming decision:

- The repository is named `agent_etf_report`.
- The extraction-era package remains `agent_treport`.
- Rename to `agent_etf_report` happens only after `agent_pack` no longer owns
  domain source, tests, data, or active docs.

This avoids mixing extraction risk with public/import/schema/data namespace
migration risk.

## Correct Workspace Shape

```text
agent_platform/
  agent_pack/          # runtime repo
  agent_pack_docs/     # document adapter repo
  doc_parser/          # document engine repo
  agent_etf_report/    # standalone domain repo, initially containing agent_treport
```

`agent_etf_report` must not vendor, nest, or absorb sibling repos.

## Dependency Direction

Extraction-era direction:

```text
agent_treport  -> agent_pack
agent_treport  -> agent_pack_docs
agent_pack_docs -> agent_pack
agent_pack_docs -> doc_parser

agent_pack     -/-> agent_treport
agent_pack     -/-> agent_pack_docs
agent_pack     -/-> doc_parser
agent_treport  -/-> doc_parser direct imports
```

The domain uses document parsing through `agent_pack_docs` tools and ports. If
ETF report behavior needs a missing document capability, add a domain-neutral
surface to `agent_pack_docs` first instead of importing `doc_parser` directly.

## Current Evidence

- `agent_pack` wheel packaging already includes only `src/agent_pack`.
- `agent_pack` sdist already includes only `src/agent_pack`, `CONTEXT.md`, and
  `pyproject.toml`.
- Runtime-side tests already assert `agent_treport` is not in built artifacts.
- Embedded domain inventory is non-trivial: source package, domain tests,
  domain data, fixtures, docs, runbooks, and ADRs still live under `agent_pack`.
- `SignalReportWorkflow` currently creates an empty `ToolRegistry()` and uses
  `max_tool_rounds=0`; this keeps model-driven tools closed today.

## 2026-05-31 Completion Update

Completed extraction-era slices:

- Phase 1 domain source, tests, fixtures, docs, and `data/agent_treport/**`
  are present in `agent_etf_report`.
- Phase 2 keeps `agent_treport` package, CLI, schema, event, artifact, and
  `data/agent_treport` paths as the extraction-era compatibility contract.
- Phase 3 cleanup PR #241 removed the domain from `agent_pack`, but
  2026-05-31 re-verification of current `agent_pack` HEAD found tracked
  Agent TReport files still present: 81 under `src/agent_treport`, 19
  `tests/test_agent_treport*.py` files, and 11 under `data/agent_treport`.
  Current-head runtime-only cleanup remains open.
- Phase 4 deterministic document evidence composition is implemented.
- A 2026-05-31 live pre-publish probe stored reusable ACE SourceProvider
  operational cache under
  `data/agent_treport/live-source/source-provider-operational/ace/`.
- The same probe verified `.env`-injected live external API execution without
  reading or printing secret values. Final API targets were `NVDA`, `AAPL`,
  `AVGO`, `GOOGL`, and `LRCX`; `finnhub`, `yfinance`, `newsapi`, and `naver`
  succeeded, while `dart` and `alpha_vantage` returned normal `no_data`.

Remaining post-extraction work is current-head `agent_pack` runtime cleanup,
operational source-provider expansion, optional model-driven document tools, and
the later rename packet.

## Non-Goals

- Do not rename package/import/CLI/data/schema/event names to
  `agent_etf_report` during extraction.
- Do not rewrite persisted `agent_treport.*` schema versions or event types.
- Do not expose model-driven document search/read during the extraction slice.
- Do not move runtime primitives from `agent_pack`.
- Do not move parser or adapter implementation from `doc_parser` or
  `agent_pack_docs`.
- Do not copy reference project implementation code.

## Phase 0 - Bootstrap And Contract Freeze

Goal: make this repo ready to receive the domain without changing behavior.

Build:

- repo control-plane docs;
- package skeleton for extraction-era `agent_treport`;
- explicit contract freeze record;
- initial packaging tests.

Acceptance:

- `pyproject.toml` installs an extraction-era package without sibling source path
  hacks.
- distribution/import/CLI names remain `agent-treport`, `agent_treport`, and
  `agent-treport` unless an explicit compatibility alias decision says
  otherwise.
- dependencies are `agent-pack` and `agent-pack-docs`; no direct `doc_parser`
  dependency.
- package includes `py.typed` when source exists.
- package data policy for fixtures/docs/data is tested before code movement.

## Phase 1 - Move Domain Source Without Rename

Goal: copy or move current domain implementation into this repo while preserving
observable behavior.

Move from `agent_pack`:

- `src/agent_treport/**`
- `tests/test_agent_treport_*.py`
- domain fixtures under `tests/fixtures/`
- `data/agent_treport/**`
- `src/agent_treport/docs/**`

Rules:

- Rewrite imports only as needed to run from this repository.
- Do not global-replace `agent_treport` strings.
- Keep schema versions, event types, artifact ids, fixture schemas, and trace
  subjects unchanged.
- Keep CLI outputs compatible unless the issue explicitly accepts a break.

Acceptance:

- migrated tests pass in this repo;
- fixture-backed CLI smoke passes;
- no direct `doc_parser` import exists under `src/agent_treport`;
- wheel/sdist include required package fixtures/data or tests prove default
  fixture loading works from an installed package.

## Phase 2 - Path And CLI Compatibility Policy

Goal: prevent default path breakage after extraction.

Current CLI defaults point at `data/agent_treport/...`. The extraction must make
one explicit choice:

1. keep `data/agent_treport/...` as the extraction-era default; or
2. write new outputs under a repo-local configured root while retaining
   read-only legacy fallback; or
3. provide a migration command that moves or copies local state.

Chosen extraction-era policy for issue #6: keep `data/agent_treport/...` as the
default path family and keep `agent-treport` as the supported CLI entrypoint.
The default path contract is:

- holdings: `data/agent_treport/operational-holdings/url_holdings_cumulative.json`
- native holdings history: `data/agent_treport/live-source/holdings-history`
- focus ETF set: `data/agent_treport/focus-etf-sets/default_focus_etf_set.json`
- reviewed security resolution:
  `data/agent_treport/security-master/security_resolution.json`

No `agent_etf_report` package, CLI, data root, schema, event, or artifact
rename is part of this phase. A migration command is deferred until a later
rename packet explicitly accepts that compatibility behavior.

Acceptance:

- tests cover default holdings/history/focus/security-resolution paths;
- legacy fixture/read paths remain readable or fail with a documented migration
  message;
- no local absolute paths or credentials leak into persisted evidence.

## Phase 3 - Runtime Repo Cleanup

Goal: make `agent_pack` runtime-only without changing runtime behavior.

Remove from `agent_pack` only after this repo passes Phase 1:

- `src/agent_treport/**`
- domain tests;
- domain fixtures and `data/agent_treport/**`;
- active docs that describe `src/agent_treport` as current ownership.

Keep or strengthen in `agent_pack`:

- packaging tests proving no domain package/script ships;
- source tests proving no runtime import/reference of domain packages;
- active docs pointing to this sibling repo as the owner.

Acceptance:

- `agent_pack` runtime tests pass;
- `agent_pack` build still includes only runtime package and static workbench
  assets;
- `rg` over `src/agent_pack` finds no Agent TReport/ETF/Telegram/domain terms
  except explicitly allowed historical test fixtures, if any.

## Phase 4 - Deterministic Document Evidence Composition

Status: implemented for issue #7 on 2026-05-31. The workflow-owned path
composes document evidence deterministically and keeps model-driven document
tools disabled until Phase 5.

Goal: let the domain use document evidence through `agent_pack_docs` without
opening free model document access.

Design:

- domain composition layer creates a private `ToolRegistry`;
- domain registers document tools through `agent_pack_docs`;
- workflow-origin code calls only:
  `document.parse -> document.index -> document.evidence`;
- domain maps bounded document evidence into typed `EvidenceItemInput`;
- `ModelStep` still receives no document registry unless a later issue enables
  it.

Important constraint:

`register_document_tools(...)` registers `document.search` and
`document.read_excerpt` too. The safety boundary is therefore not registration
alone; the private registry must not be handed to model-driven steps in this
phase.

Acceptance:

- deterministic DOCX/PDF fixture smoke produces bounded evidence;
- mapped evidence passes `EvidenceItemInput` validation;
- report input, artifacts, rendered outputs, events, and logs do not expose raw
  document text, local paths, credentials, or parser envelopes;
- no direct `doc_parser` import exists in the domain package.

## Phase 5 - Optional Model-Driven Document Tools

Goal: decide whether the model may call document tools.

This is intentionally after deterministic evidence works.

Acceptance before enabling:

- explicit read permission policy;
- source-document allowlist;
- artifact/index root ownership;
- small `max_tool_rounds`;
- tests for denied reads, stale handles, local path leaks, and bounded excerpts.

## Phase 6 - Post-Separation Rename To Agent ETF Report

Goal: rename the standalone domain only after extraction is complete.

Eligible only when:

- this repo owns all domain source/tests/data/docs;
- `agent_pack` no longer contains active domain code or current domain docs;
- both repos pass their verification gates;
- compatibility policy for existing `agent_treport.*` data is accepted.

Rename candidates:

- Python import package: `agent_treport` -> `agent_etf_report`
- distribution: `agent-treport` -> `agent-etf-report`
- CLI: `agent-treport` -> `agent-etf-report`
- data root: `data/agent_treport` -> `data/agent_etf_report`
- docs/runbooks/commands
- optionally schema/event namespaces, only with migration tests and explicit
  compatibility policy

Do the rename as one focused Work Packet, not mixed into extraction.

## Verification Matrix

This repo:

- package install smoke;
- import smoke;
- CLI fixture smoke;
- migrated domain pytest suite;
- packaging test for fixtures/data;
- `ruff` and `pyright` after package metadata exists;
- direct-import guard against `doc_parser`.

`agent_pack`:

- packaging tests;
- runtime pytest subset;
- `ruff check src/agent_pack tests`;
- `pyright`;
- source grep/import guard against domain packages.

Document evidence:

- deterministic parse/index/evidence fixture smoke;
- mapper validation into `EvidenceItemInput`;
- report artifact/render/log leak tests;
- permission/read policy tests before any model-driven access.

## Open Decisions

- Whether the extraction-era distribution should publish as `agent-treport` or
  stay unpublished/editable-only until the final `agent-etf-report` rename.
- Whether `agent-treport` CLI remains as a temporary alias after final rename.
- Whether persisted schema/event namespace is renamed in Phase 6 or retained as
  historical contract forever.
