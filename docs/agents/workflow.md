# Agent Workflow

This repo uses issue-first Work Packets for non-trivial changes.

## Control Plane

- Root instructions: `AGENTS.md`
- Tracker config: `docs/agents/issue-tracker.md`
- Triage vocabulary: `docs/agents/triage-labels.md`
- Domain/source-of-truth pointers: `docs/agents/domain.md`
- Active migration plan: `docs/plans/agent-treport-extraction-migration.md`

## Flow

1. Record the intended behavior, boundary, risks, and verification plan in a
   GitHub Issue or tracked plan.
2. Keep each implementation PR reviewable and tied to one capability slice.
3. Preserve extraction-era `agent_treport` names until the post-separation
   rename packet.
4. Update docs and verification evidence before closing a packet.

## Work Packet Rules

- Bootstrap/control-plane changes may be docs-only.
- Extraction, package metadata, persisted contracts, data paths, document tool
  permissions, and `agent_pack` cleanup require a tracked issue.
- Cross-repo work must name the owning repo for each change.
- Do not mix unrelated dirty work from sibling repos into this repo's packets.
- Use `$work-packet auto` only while auto gates remain clear.

## Intake Modes

- `skip_interview`: narrow, reversible, evidence-backed work.
- `triage_first`: raw issue, backlog item, or conflicting status.
- `diagnose_first`: bug, failing verification, flaky behavior, or regression.
- `targeted_grill`: 1-3 blocking product, state, permission, or compatibility
  decisions remain.
- `architecture_first`: dependency direction, contracts, diagnostics, or
  verification seams are unclear.

## Delegated Skill Routing

- `triage`: raw or conflicting tracker items; fallback is a minimal triage
  recommendation in the issue or report.
- `diagnose`: bugs and failing checks; fallback is a deterministic local
  reproduction and evidence loop.
- `grill-with-docs`: unresolved domain or compatibility decisions; fallback is
  asking only the blocking questions.
- `to-prd` / `to-issues`: requirements and slice shaping; fallback is a concise
  tracked plan update.
- `tdd`: behavior implementation; fallback is a failing test or documented
  verification plan before implementation.
- `zoom-out`: cross-repo orientation; fallback is a read-only source summary.
- `handoff`: session transfer; fallback is a compact tracked handoff note.

If a delegated skill or CLI is unavailable, report the intended use, failure or
unavailability, and fallback. Do not claim unavailable actions occurred.

## Issue And PR Policy

- Titles and canonical body sections should be English.
- Issues and PRs created or updated by Work Packet should include a short
  Korean summary at the top for review speed.
- Korean summaries are non-normative; English canonical sections and linked
  source-of-truth docs prevail on conflict.
- Use closing keywords only when the PR fully resolves the issue and targets
  `main`. Otherwise use `Related to` or `Part of`.
- If `gh` is unavailable, output the exact command, title, labels, and body
  instead of claiming tracker changes.

## Result Reporting Language

- User-facing completion reports, setup reports, and result summaries should be
  written in Korean by default.
- Preserve commands, paths, package names, schema names, issue/PR canonical
  headings, and quoted source text in their original language.
- GitHub issue and PR canonical sections remain English with a non-normative
  Korean Summary, so tracker records stay consistent with cross-repo policy.

## Auto Gates

`$work-packet auto` must stop on more than 3 blocking decisions, tracker
migration, unrelated dirty changes, destructive actions, secret handling, live
provider calls, external export, deployment, irreversible migration, delegated
skill failure without a safe fallback, or verification failure without a clear
next diagnostic step.

## Verification Evidence

Every close report should include:

- commands run and pass/fail result;
- exact package/import/CLI behavior checked;
- persisted contract compatibility checked;
- `agent_pack` reverse-import/runtime-only checks;
- unrun checks and remaining risks.

## Archive Hygiene

Keep active plans in `docs/plans/`. Archive completed or stale plans only after
their decision history and verification evidence are captured in a durable issue,
PR, or tracked doc.
