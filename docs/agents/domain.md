# Domain And Dependency Map

## Domain

This repository owns the Agent TReport domain application after extraction from
`agent_pack`.

Primary source-of-truth docs:

- Active migration plan: `docs/plans/agent-treport-extraction-migration.md`
- Repo operating index: `AGENTS.md`
- Upstream runtime context: `../agent_pack/CONTEXT.md`
- Runtime integration strategy:
  `../agent_pack/docs/doc-parser-integration-strategy.md`
- Document adapter contracts: `../agent_pack_docs/docs/tool-contracts.md`

Extraction-era domain names remain:

- Python package: `agent_treport`
- CLI: `agent-treport`
- data root: `data/agent_treport`
- schema/event namespace: `agent_treport.*`

The repository-level name `agent_etf_report` is the future target name, not the
initial migration name.

## Dependency Direction

```text
agent_treport  -> agent_pack
agent_treport  -> agent_pack_docs
agent_pack_docs -> agent_pack
agent_pack_docs -> doc_parser

agent_pack     -/-> agent_treport
agent_pack     -/-> agent_pack_docs
agent_pack     -/-> doc_parser
agent_treport  -/-> doc_parser direct imports
```

## Ownership

`agent_treport` owns:

- ETF report workflows and CLI commands;
- ETF/source-provider/domain adapters;
- report renderers, approval policy, delivery policy, and evidence policy;
- document evidence composition, artifact/index roots, and read permission.

Sibling packages own:

- `agent_pack`: runtime primitives, workflow execution, tools, artifacts,
  context, stores, model clients, generic Workbench;
- `agent_pack_docs`: document tools and adapter contracts over `doc_parser`;
- `doc_parser`: parsing/index/evidence engine internals.

## Rename Gate

Rename to `agent_etf_report` only after:

- domain source/tests/data/docs no longer live in `agent_pack`;
- standalone package/install/test gates pass in this repo;
- `agent_pack` runtime-only verification passes;
- legacy data/schema compatibility policy is explicitly accepted.

## Verification Sources

- Current focused tests: `tests/test_contract_freeze.py`
- Package commands: `pyproject.toml`
- Expected extraction verification matrix:
  `docs/plans/agent-treport-extraction-migration.md`

## Unresolved Domain Gaps

- Final distribution publishing policy for extraction-era `agent-treport`.
- Whether `agent-treport` remains as a temporary CLI alias after final rename.
- Whether persisted `agent_treport.*` schema/event namespaces are renamed in the
  post-separation phase or retained permanently.
