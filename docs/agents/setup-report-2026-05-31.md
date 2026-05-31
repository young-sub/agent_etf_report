# Setup Report - 2026-05-31

## Classification

- Base: `NEW_UNCONFIGURED`
- Modifiers: `MULTI_SURFACE`, `DOC_DRIFT`, `TOOLING_GAP`

## Evidence

- Repo exists at `agent_platform/agent_etf_report`.
- Remote is `young-sub/agent_etf_report`.
- Initial contents were `.gitignore` and `README.md`.
- Source domain currently lives under sibling `agent_pack/src/agent_treport`.

## Created Control Plane

- `AGENTS.md`
- `docs/agents/workflow.md`
- `docs/agents/issue-tracker.md`
- `docs/agents/triage-labels.md`
- `docs/agents/domain.md`
- `docs/plans/agent-treport-extraction-migration.md`

## Published Tracker

- `young-sub/agent_etf_report#1`

## Key Decision

The repo name is already `agent_etf_report`, but extraction keeps the active
package, CLI, data root, schema namespace, event namespace, and artifact
contracts as `agent_treport` until `agent_pack` is fully cleaned up.

## Verification

Docs-only verification is expected for this setup slice:

- inspect files;
- `git status --short --branch`;
- `git ls-files` after files are added.

No package, test, lint, or type command exists until the package skeleton slice.
