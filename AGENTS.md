# AGENTS.md

## Scope

- Applies to the `agent_etf_report` repository.
- This repo is the standalone extraction target for the current Agent TReport
  domain application.
- Global agent instructions remain authoritative; this file records repo facts,
  source-of-truth docs, dependency rules, and verification expectations.

## Current State

- Repository: `young-sub/agent_etf_report`.
- Default branch: `main`.
- Current contents include the migrated extraction-era `agent_treport` domain
  implementation, tests, docs, fixtures, and Agent TReport data.
- The repository name is `agent_etf_report`, but extraction-era Python package,
  CLI, data path, schema, and event names stay `agent_treport` until the domain
  is explicitly renamed in a later Work Packet.

## Source Of Truth

- Migration plan: `docs/plans/agent-treport-extraction-migration.md`
- Agent workflow: `docs/agents/workflow.md`
- Tracker config: `docs/agents/issue-tracker.md`
- Triage labels/states: `docs/agents/triage-labels.md`
- Domain/dependency map: `docs/agents/domain.md`
- Upstream runtime docs: `../agent_pack/CONTEXT.md` and
  `../agent_pack/docs/doc-parser-integration-strategy.md`
- Document adapter docs: `../agent_pack_docs/AGENTS.md` and
  `../agent_pack_docs/docs/tool-contracts.md`

## Commands

- Setup/control-plane verification:
  `git status --short --branch`
  `git ls-files`
- Package install: `..\.venv\Scripts\python.exe -m pip install -e .`
- Tests: `..\.venv\Scripts\python.exe -m pytest`
- Lint: `..\.venv\Scripts\python.exe -m ruff check .`
- Typecheck: `..\.venv\Scripts\python.exe -m pyright`

## Work Tracking

- Tracker: GitHub Issues in `young-sub/agent_etf_report`.
- Branch convention: `issue-<issue-number>-<slug>` or
  `wp-<work-packet-id>-<slug>`.
- `.scratch/work-packets/` and `.scratch/archive/` are local operating state
  only and stay ignored.
- Cross-repo cleanup in `young-sub/agent_pack` must be tracked separately when
  it touches that repo.
- User-facing completion reports and result summaries should be written in
  Korean by default. Keep code identifiers, commands, paths, issue/PR canonical
  sections, and source-of-truth titles in their original language.

## Dependency Rules

Allowed:

- `agent_treport -> agent_pack`
- `agent_treport -> agent_pack_docs`
- `agent_treport -> yfinance`
- `agent_pack_docs -> agent_pack`
- `agent_pack_docs -> doc_parser`

Forbidden:

- `agent_pack -> agent_treport`
- `agent_pack -> agent_pack_docs`
- `agent_pack -> doc_parser`
- `agent_treport -> doc_parser` direct imports
- vendoring or nesting `agent_pack/`, `agent_pack_docs/`, or `doc_parser/`
  inside this repository

## Naming Rule

- Do not rename import package, CLI, data paths, schema versions, event names,
  or artifact contract identifiers to `agent_etf_report` during extraction.
- The `agent_etf_report` rename is a post-separation Work Packet after
  `agent_pack` no longer contains the domain source, tests, data, or active docs.

## Verification And Done

- Extraction cleanup requires this repo to install independently, domain tests
  to pass here, `agent_pack` runtime tests to pass there, and active docs in
  both repos to point at the current ownership boundary.
- Report unrun checks, legacy compatibility assumptions, and remaining rename
  risks explicitly in Korean by default.
