# Issue Tracker

## Tracker

- Mode: `github`
- Primary tracker: GitHub Issues in `young-sub/agent_etf_report`.
- Use `young-sub/agent_pack#<n>` references for source cleanup or runtime-side
  issues in the sibling runtime repo.
- Use `young-sub/agent_pack_docs#<n>` only for adapter-owned changes.
- Do not migrate trackers or create, rename, or delete remote labels without
  explicit approval.

## Issue Shape

Issue titles and canonical sections should be English. Work Packet-created or
updated issues should include a Korean summary at the top for review speed.

Issues should include:

- context and source-of-truth links;
- dependency and ownership boundary;
- acceptance criteria;
- verification commands;
- blocked-by or follow-up references;
- explicit compatibility notes for `agent_treport` names and persisted
  contracts.

## PR Shape

PRs should include:

- linked issue or Work Packet;
- implemented slices;
- scope and out-of-scope notes;
- decisions made;
- verification evidence;
- architecture review result for touched boundaries;
- docs updated;
- remaining risks.

Use closing keywords only when the PR fully resolves the issue and targets
`main`. Otherwise use `Related to` or `Part of`.

## Korean Summary Template

```md
## Korean Summary (non-normative)

- ...

> This Korean summary is for review speed only. If it conflicts with the English canonical sections, linked source-of-truth docs, or repository rules, the English canonical sections and source-of-truth docs prevail.
```

## Branches

Use slash-free branch names:

- `issue-<issue-number>-<slug>`
- `wp-<work-packet-id>-<slug>`

## Durable Records

Durable decisions live in GitHub Issues, PR bodies, tracked docs, or ADRs. This
repo currently has no ADR directory; use tracked docs or tracker records until
one is introduced by a Work Packet.

`.scratch/work-packets/` and `.scratch/archive/` are local-only drafting state
and are ignored by git. Mirror durable summaries into GitHub issues, PR bodies,
or tracked docs before claiming completion.

## CLI Fallback

`gh` is the configured GitHub CLI. If it is unavailable, output the exact
command, title, labels, and body instead of claiming an issue or PR was created.
