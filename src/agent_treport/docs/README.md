# Agent TReport Documentation Index

This documentation set captures domain decisions and implementation evidence for the Agent TReport rewrite on top of `agent_pack`.

## Start Here

- `../CONTEXT.md`: domain glossary, reusable capability boundaries, and reference roles.
- `signal-intelligence-report.md`: current target product structure for ETF brand behavior signal reporting, including the completed evaluator-only harness review slice over `SignalReportWorkflow` output artifacts.
- `evidence-ingestion-priority-record.md`: accepted adopt/defer/reject
  guidance for RSS evidence, permissioned Telegram report ingestion, novelty
  scoring, report structure, and evidence-bound LLM commentary Work Packets.
- `data-collection-independence-roadmap.md`: completed v1 roadmap from
  temporary sync bridge to Agent TReport-owned collection, enrichment, native
  end-to-end operation, daily publish closure, and release evidence.
- Current closure record:
  `../../../docs/agent-pack-v1-release-evidence.md` closes the Agent-Pack v1
  evidence milestone against the native ETF Agent TReport application.
- `source-provider-audit.md`: provider audit, registered live SourceProvider rollout evidence, and remaining provider-load hardening notes.
- `operational-live-runbook.md`: explicit local native fixture, legacy sync, readiness, and run-report flow for operational holdings runs.

## Decisions

- `adr/0001-capabilities-use-ports-and-adapters.md`: domain capabilities use owned ports and injected adapters.
- `adr/0002-use-two-reference-roles.md`: use separate breadth/operations and depth/product-quality reference roles.
- `adr/0004-operational-holdings-export-input-contract.md`: temporary copied operational holdings export contract and sync metadata.
- `adr/0005-security-resolution-export-sync-classification.md`: SecurityResolutionExport owns normalized export ticker and exclusion classification.
- `adr/0006-operational-readiness-gates-user-ready-delivery.md`: operational readiness gates user-ready delivery.
- `adr/0007-operational-export-fingerprint-binds-readiness-handoffs.md`: readiness handoffs are bound to copied export content by fingerprint.
- `adr/0008-source-provider-acquisition.md`: staged SourceProvider acquisition, path-safe evidence, and explicit live opt-in.
- `adr/0011-use-domain-stable-operational-acceptance-names.md`: operational acceptance names must use stable domain language rather than planning milestone labels.
- `adr/0012-daily-operational-external-data-approval.md`: durable daily approval profiles authorize disclosed live source, external evidence, and model export boundaries.
- `adr/0013-full-live-pre-publish-default.md`: pre-publish preview defaults to the full implemented live provider/API surface with approval, pacing, target-cap, and no-send constraints.

## Archive

- `archive/plans/first-usable-agent.md`: completed fixture-first Agent TReport workflow plan.
- `archive/plans/canonical-signal-report-payload-v1.md`: completed canonical multi-ETF signal report payload slice.
- `archive/plans/user-ready-local-agent.md`: completed historical `UserReadyLocalAgent` slice for `agent-treport run-report`, `inspect`, and the former dashboard wrapper.
- `archive/plans/report-quality-gate.md`: completed deterministic `ReportQualityGate` slice for payload-plus-Markdown quality evidence and release blocking.
- `archive/plans/etf-brand-source-provider-terminology.md`: completed terminology cleanup from ETF manager/provider wording to ETF brand/source-provider contracts.
- `archive/plans/data-collection-independence-foundation.md`: completed Phase 1 native collection tracer bullet.
- `archive/plans/native-etf-source-acquisition-foundation.md`: completed SourceProvider acquisition foundation and bounded live KODEX smoke record.
- `archive/plans/agent-pack-v1-closure-etf-release-evidence.md`: completed
  Agent-Pack v1 closure and ETF Agent release evidence record.
