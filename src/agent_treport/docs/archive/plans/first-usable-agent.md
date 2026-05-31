# First Usable Agent Plan

## Status

Implemented. The fixture-first domain workflow produces Markdown and JSON
artifacts with inspectable SQLite runtime evidence through `agent_pack`, and the
domain CLI can select and execute the generic Codex provider path.

## Goal

Build the first locally runnable Agent TReport workflow on top of `agent_pack`. It produces an ETF change report Markdown artifact and inspectable SQLite execution state without live external integrations.

## Reference Roles

- `ETF_tracker-main` is the breadth and operations reference: ETF universe operations, cumulative holdings, security normalization, delivery hardening, and evaluation discipline.
- `Agent_TReport-main` is the depth and product-quality reference: high-density analysis, product surfaces, personas, social workflows, and report quality.

## Pre-Work

Before this domain slice, complete the runtime hardening slices that make tool
feedback durable across model turns:

- Add public behavior coverage that a model-originated missing tool failure is stored as failed tool-result feedback and appears in the next model request.
- Add public behavior coverage that model-originated invalid tool arguments are stored as failed `validation_error` tool-result feedback and appear in the next model request.

## Scope

The first domain slice is fixture-first and live-ready:

- Use deterministic fixture data for ETF holdings and changes.
- Keep data collection behind ports/adapters so live providers can be added later.
- Use Codex CLI as a real selectable model provider through the `agent_pack` model client boundary.
- Use plain-text model analysis as the primary analysis output.
- Generate structured Markdown sections from domain data and model analysis.
- Persist a final Markdown artifact plus core intermediate JSON artifacts.
- Provide a domain CLI command in `agent_treport`; do not attach domain commands to `agent-pack` CLI.

## Out Of Scope

- Telegram publishing.
- Threads publishing.
- Reddit workflow.
- Tavily, News API, and other live enrichment integrations.
- Chart generation and HTML dashboards.
- Full reference parity.
- Structured model-output parsing beyond plain text.
- Codex structured tool-call parsing.
- Gemini, OpenAI, Claude, or other API model adapters.

## Capability Boundaries

Implement four domain capabilities as separate boundaries:

- Data collection and parsing.
- Data aggregation and filtering.
- Data analysis.
- Document generation.

Boundary outputs should use Pydantic domain models. Internal calculations can use simple dict/list structures until behavior requires deeper models.

## Runtime Boundary Decisions

- `agent_treport` may depend on `agent_pack`; `agent_pack` must not import or encode Agent TReport concepts.
- Model provider selection belongs to `agent_pack` as a generic runtime capability.
- The first provider-selection surface in the domain CLI is simple: `--model codex`.
- The internal runtime model config should be extensible for later providers and options.
- Codex CLI is a real model provider option, not a fake fallback.
- The first Codex provider implementation should execute Codex as a subprocess text-generation transport.
- Tool-call structured parsing for Codex is deferred.
- `AnalyzeData` should use `agent_pack` workflow/model execution rather than adding domain-specific helpers to `agent_pack`.

## Failure Semantics

- If model-generated analysis fails, preserve completed intermediate JSON artifacts and fail the run.
- Do not silently generate a successful fallback report when model analysis failed.
- A final Markdown artifact may be absent or explicitly marked failed in that case.

## Acceptance Criteria

- Public behavior tests cover the four capability boundaries through fixture data. Done in `tests/test_agent_treport_first_usable_agent.py`.
- Public behavior tests verify the domain workflow produces a Markdown artifact and core intermediate JSON artifacts. Done in `tests/test_agent_treport_first_usable_agent.py`.
- Public behavior tests verify SQLite run evidence is persisted and inspectable. Done in `tests/test_agent_treport_first_usable_agent.py`.
- CLI smoke verification runs the domain command with `--model codex` in the local environment. Stubbed CLI path is covered in `tests/test_agent_treport_cli.py`; real Codex smoke succeeded for `run_treport_codex_smoke` after explicit user approval.
- Artifact inspection confirms the Markdown report includes structured sections and model-generated analysis text. Done in `tests/test_agent_treport_first_usable_agent.py` and `tests/test_agent_treport_cli.py`.
- SQLite inspection confirms run status, events, snapshots, context views, and artifact references needed to understand the run. Done in `tests/test_agent_treport_first_usable_agent.py`.
- Tests use deterministic fixture or stub model transport where needed; the CLI smoke exercises the real Codex path. Deterministic and subprocess-boundary tests are done, and one real external Codex model completion succeeded for the first workflow.

## Follow-Up Slices

- Add live holdings provider adapters.
- Add structured model-output parsing and validation.
- Add report content and tone refinement through a dedicated interview.
- Add Telegram preview and later delivery.
- Add enrichment adapters for news, web search, disclosures, and financial metrics.
- Add eval casebooks and report quality gates.
