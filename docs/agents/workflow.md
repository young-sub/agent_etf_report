# Agent Workflow

This repo uses issue-first Work Packets for non-trivial changes.

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

## Verification Evidence

Every close report should include:

- commands run and pass/fail result;
- exact package/import/CLI behavior checked;
- persisted contract compatibility checked;
- `agent_pack` reverse-import/runtime-only checks;
- unrun checks and remaining risks.
