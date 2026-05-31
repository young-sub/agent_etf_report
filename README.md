# agent_etf_report

Standalone repository for extracting the current Agent TReport domain
application out of `agent_pack`.

The repository name is already `agent_etf_report`, but the extraction keeps the
active package, CLI, data root, schema namespace, event namespace, and artifact
contracts as `agent_treport` until the domain is fully separated from
`agent_pack`.

Current compatibility policy:

- `agent-treport` remains the supported CLI entrypoint.
- Default local data paths stay under `data/agent_treport/...`, including
  operational holdings, native holdings history, focus ETF sets, and reviewed
  security-resolution exports.
- `agent_treport.*` schema, event, and artifact contract names are not renamed
  during extraction.

Primary plan:

- `docs/plans/agent-treport-extraction-migration.md`

Dependency direction:

```text
agent_treport  -> agent_pack
agent_treport  -> agent_pack_docs
agent_pack_docs -> agent_pack
agent_pack_docs -> doc_parser
```

`agent_treport` should use `doc_parser` capabilities through `agent_pack_docs`,
not by importing `doc_parser` directly.
