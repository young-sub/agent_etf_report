# Triage Labels And States

Remote labels are not yet verified in GitHub. Treat this file as the local
vocabulary until the tracker is audited.

## State Labels

| Work Packet state | Recommended tracker label | Notes |
|---|---|---|
| needs-triage | `needs-triage` | Raw item or unclear actionability |
| draft | `status:draft` | Blocking decisions remain |
| ready-for-agent | `status:ready` | Ready for implementation |
| blocked | `status:blocked` | Waiting on human, dependency, or approval |
| in-progress | `status:in-progress` | Implementation started |
| verification | `status:verification` | Implementation done, checks pending |
| done | `status:done` | Closed with evidence |

## Type Labels

| Type | Recommended tracker label | Notes |
|---|---|---|
| plan | `type:plan` | Planning or Work Packet setup |
| docs | `type:docs` | Documentation/source-of-truth update |
| migration | `type:migration` | Extraction or ownership movement |
| bug | `type:bug` | Use `diagnose_first` when root cause is unclear |
| feature | `type:feature` | Product capability |
| architecture | `type:architecture` | Boundary/design change or ADR-level work |
| test/eval | `type:test` | Tests, evals, or verification harness |
| refactor | `type:refactor` | Behavior-preserving local cleanup |

## Area Labels

- `area:packaging`
- `area:domain`
- `area:document-evidence`
- `area:compatibility`
- `area:agent-pack-cleanup`

## Rules

- Do not create, rename, or delete remote labels without explicit approval.
- If labels conflict with issue or PR body status, report the conflict.
- Work Packet state is derived from acceptance criteria, blocking decisions, and
  verification plan, not labels alone.
