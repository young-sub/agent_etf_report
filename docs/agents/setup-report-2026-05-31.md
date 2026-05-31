# Project Agent Bootstrap Setup Report - 2026-05-31

## Summary

- Repo: `young-sub/agent_etf_report`
- Path: `C:\Users\YS\Desktop\python\agent_platform\agent_etf_report`
- Branch: `issue-4-bootstrap-contract-freeze`
- Base classification: `EXISTING_PARTIAL`
- Modifiers: `MULTI_SURFACE`, `DOC_DRIFT`, `TOOLING_GAP`
- Mode: normal reconcile/init

## Evidence

- Remote: `git@github.com:young-sub/agent_etf_report.git`
- Default branch from origin: `origin/main`
- Existing control plane was present but missing explicit Work Packet policies
  for Korean result reporting, CLI fallback, auto gates, delegated-skill
  fallback, and local `.scratch/` handling.
- Current repo includes an extraction-era `agent_treport` package skeleton,
  `pyproject.toml`, and `tests/test_contract_freeze.py`.
- Active plan: `docs/plans/agent-treport-extraction-migration.md`

## Files Created Or Changed

| Path | Action | Reason |
|---|---|---|
| `.gitignore` | updated | Ignore `.scratch/` local operating state. |
| `AGENTS.md` | updated | Reflect package skeleton state, current verification commands, `.scratch/` policy, and Korean result reporting. |
| `docs/agents/workflow.md` | updated | Add control-plane map, intake modes, delegated-skill fallback, issue/PR policy, Korean result reporting, auto gates, and archive hygiene. |
| `docs/agents/issue-tracker.md` | updated | Define `github` mode, PR shape, Korean Summary template, durable record policy, and `gh` fallback. |
| `docs/agents/triage-labels.md` | updated | Convert recommended labels into Work Packet state/type/area mappings. |
| `docs/agents/domain.md` | updated | Add source-of-truth pointers, verification sources, and unresolved domain gaps. |
| `.scratch/work-packets/` | created local-only | Draft Work Packet operating state; ignored by git. |
| `.scratch/archive/` | created local-only | Local archive operating state; ignored by git. |
| `docs/agents/setup-report-2026-05-31.md` | updated | Record this reconcile/init evidence and verification. |

## Existing Instructions

- Preserved: root `AGENTS.md` repo-specific extraction rules, dependency
  direction, naming freeze, and verification expectations.
- Preserved: active migration plan and existing setup report path.
- Moved out of `AGENTS.md`: no content moved.
- Legacy agent docs: none found.
- Wrapper prompt candidates: none found.

## Control Plane

- Root `AGENTS.md`: present, 88 lines, under 100-line limit.
- Nested `AGENTS.md`: not needed; no materially different subtree policy found.
- `docs/agents/workflow.md`: present and reconciled.
- `docs/agents/issue-tracker.md`: present and reconciled.
- `docs/agents/domain.md`: present and reconciled.
- `docs/agents/triage-labels.md`: present and reconciled as recommended
  vocabulary until remote labels are verified.
- `.scratch/work-packets/`: present, ignored, local-only.
- `.scratch/archive/`: present, ignored, local-only.

## Work Packet Compatibility

| Requirement | Status | Notes |
|---|---|---|
| Root `AGENTS.md` under 100 lines | pass | 88 lines. |
| Required `docs/agents` config | pass | Workflow, tracker, domain, and triage files present. |
| Tracker mode defined | pass | `github`, `young-sub/agent_etf_report`. |
| Durable record policy | pass | GitHub issues/PRs, tracked docs, ADRs when introduced. |
| Korean Summary policy | pass | Required for Work Packet-created or updated GitHub issues/PRs. |
| Korean result reporting | pass | User-facing completion reports and summaries default to Korean. |
| CLI fallback policy | pass | If `gh` is unavailable, output exact command/title/body. |
| Verification evidence format | pass | Commands, results, unrun checks, assumptions, risks. |
| Auto/approval gates | pass | Stop conditions documented in `workflow.md`. |

## Tracker And Durable Records

- Tracker mode: `github`
- Canonical tracker: `young-sub/agent_etf_report`
- Published tracker evidence: `young-sub/agent_etf_report#1`
- Durable records: GitHub issues, PR bodies, tracked docs, and ADRs when added.
- Tracker migration needed: no.
- CLI availability: `gh` available, `glab` unavailable.

## Commands And Verification

| Purpose | Command | Result |
|---|---|---|
| inspect files | `rg --files` | pass; repo files enumerated. |
| git status | `git status --short --branch` | pass; only bootstrap reconcile files are modified. |
| tracked files | `git ls-files` | pass; required control-plane files are tracked. |
| AGENTS line count | `(Get-Content AGENTS.md).Count` | pass; 88 lines. |
| bootstrap audit | `python C:\Users\YS\.agents\skills\project-agent-bootstrap\scripts\audit-agent-bootstrap.py C:\Users\YS\Desktop\python\agent_platform\agent_etf_report` | pass; required docs present, `.scratch` present, no legacy docs. |

Package tests, lint, typecheck, and install smoke were not run during this
control-plane reconcile because no package source behavior changed.

## Branch / PR Convention

- Default branch: `main`
- Current branch: `issue-4-bootstrap-contract-freeze`
- Implementation branch convention: `issue-<issue-number>-<slug>` or
  `wp-<work-packet-id>-<slug>`
- Draft PR policy: use PR body to record verification and remaining risks.
- `--no-auto-pr` behavior: stop with exact command/title/body after local
  implementation and verification.

## Delegated Skills

Configured fallback policy is in `docs/agents/workflow.md`.

| Skill/method | Availability in current Codex session | Fallback |
|---|---|---|
| `triage` | available | Minimal triage recommendation. |
| `diagnose` | available | Deterministic local reproduction/evidence loop. |
| `grill-with-docs` | available | Ask only blocking domain/product questions. |
| `to-prd` | available | Concise tracked PRD summary. |
| `to-issues` | available | Validate one PR-sized packet. |
| `tdd` | available | Failing test or documented verification plan. |
| `zoom-out` | available | Read-only orientation summary. |
| `handoff` | available | Compact tracked handoff note. |

## Audit Results

- Audit helper run: yes.
- Summary: required `docs/agents` files present; root `AGENTS.md` under 100
  lines; `.scratch` present; `git` and `gh` available; `glab` unavailable; no
  legacy agent docs or wrapper prompt candidates found.

## Remaining Risks And Unverified Assumptions

- Remote GitHub labels are not verified; `triage-labels.md` remains a
  recommended vocabulary, not confirmed remote state.
- Full extraction implementation is still pending in later Work Packets.
- Package install, tests, lint, and typecheck were not rerun for this
  docs/control-plane-only reconcile.
- Final distribution publishing policy, final CLI alias policy, and persisted
  schema/event rename policy remain open domain decisions.

## Next Action

- For the next implementation slice, run:
  `..\.venv\Scripts\python.exe -m pip install -e .`
  and then `..\.venv\Scripts\python.exe -m pytest`.
