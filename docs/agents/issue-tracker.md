# Issue Tracker

## Tracker

- Primary tracker: GitHub Issues in `young-sub/agent_etf_report`.
- Use `young-sub/agent_pack#<n>` references for source cleanup or runtime-side
  issues in the sibling runtime repo.
- Use `young-sub/agent_pack_docs#<n>` only for adapter-owned changes.

## Issue Shape

Issues should include:

- context and source-of-truth links;
- dependency and ownership boundary;
- acceptance criteria;
- verification commands;
- blocked-by or follow-up references;
- explicit compatibility notes for `agent_treport` names and persisted
  contracts.

## Branches

Use slash-free branch names:

- `issue-<issue-number>-<slug>`
- `wp-<work-packet-id>-<slug>`

## Durable Records

Durable decisions live in GitHub Issues, PR bodies, tracked docs, or ADRs.
`.scratch/` is local-only drafting state.
