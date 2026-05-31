# AGENTS.md

## Scope

- Applies to `src/agent_treport`.
- Inherits the root `AGENTS.md`; this file records only Agent TReport package deltas.

## Repo Map

`src/agent_treport` is a domain application package for the Agent TReport rewrite.

It uses `agent_pack` to build concrete workflows for ETF and investment-report tasks. It is not part of the reusable runtime package.

- Entrypoints: `agent_treport.cli` and `agent_treport.workflows.signal_report`.
- Active workflow: `SignalReportWorkflow`.
- Current product surface: canonical `SignalReportPayload`, external evidence enrichment, Markdown/HTML/Telegram report artifacts, pre-publish preview, Telegram delivery, native operational handoff, quality evidence, and local inspection outputs.

## Source Of Truth

- `CONTEXT.md`: Agent TReport domain language, capability boundaries, and reference roles.
- `docs/README.md`: domain documentation index.
- `docs/signal-intelligence-report.md`: report payload, renderer, external evidence, and quality principles.
- `docs/evidence-ingestion-priority-record.md`: RSS, Telegram report ingestion, novelty, commentary, feedback, and outcome-learning intake guidance.
- `docs/data-collection-independence-roadmap.md`: native collection and enrichment roadmap/status.
- `docs/operational-live-runbook.md`: live operation, approval, and delivery boundaries.
- `docs/source-provider-audit.md`: SourceProvider rollout evidence and provider-load notes.
- `docs/adr/`: Agent TReport domain decisions.

## Commands

- Run commands from the repo root with the same environment and broad
  verification commands as the root `AGENTS.md`.
- Focused Agent TReport tests: `../.venv/Scripts/python.exe -m pytest tests/test_agent_treport_*.py`
- CLI entrypoint: `../.venv/Scripts/python.exe -m agent_treport.cli`

## Work Tracking

- Same GitHub Issues tracker and `.scratch/` staging rules as the root
  `AGENTS.md` and `docs/agents/workflow.md`.
- For RSS, Telegram report ingestion, novelty/repetition scoring, Telegram report structure, or evidence-bound commentary Work Packets, include `docs/evidence-ingestion-priority-record.md` as an intake source.

## Constraints

Allowed:

- `agent_treport -> agent_pack`
- `agent_treport -> agent_pack_docs`
- `agent_treport -> its own subpackages`
- `agent_treport -> approved external libraries when added to project dependencies`

Forbidden:

- changes that require `agent_pack` to import `agent_treport`
- copying code from `../references/Agent_TReport-main`
- importing from `../references/Agent_TReport-main` at runtime

`../references/Agent_TReport-main` is read-only behavioral reference material.

Use it to understand workflows, data concepts, and expected outputs. Reimplement behavior with new code and the `agent_pack` runtime boundaries.

- Live provider calls, model export, Telegram delivery, credential handling, and external exports require the approvals documented in local runbooks and ADRs.
- Raw provider payloads, full RSS items, full Telegram posts, credentials, local paths, and provider exceptions must not become model-visible context or user-ready report artifacts.
- Current near-term out of scope: Threads publishing, autonomous scheduler operation, broad web or social scraping, raw provider payload storage, generic cross-run evidence databases, and full legacy reference parity.

## What Belongs Here

- ETF report workflows and steps
- domain tools for ETF data preparation, target selection, and report rendering
- report-writing skills and prompts
- domain CLI commands
- fixtures or deterministic adapters needed for local usable runs

## Verification And Done

- Follow root `docs/verification.md` and Agent TReport-specific docs for behavior changes.
- Add or update focused tests for changed report, provider, publishing, or quality behavior.
- Update the relevant domain source-of-truth docs when product scope, approval boundaries, or evidence semantics change.
