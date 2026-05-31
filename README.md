# agent_etf_report

Standalone repository for extracting the current Agent TReport domain
application out of `agent_pack`.

The repository name is already `agent_etf_report`, but the extraction keeps the
active package, CLI, data root, schema namespace, event namespace, and artifact
contracts as `agent_treport` until the domain is fully separated from
`agent_pack`.

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
