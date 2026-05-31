# Canonical Signal Report Payload V1 Plan

## Status

Implemented and archived.

Implementation evidence:

- Public payload tests: `tests/test_agent_treport_signal_report.py`.
- Markdown renderer tests:
  `tests/test_agent_treport_signal_report_rendering.py`.
- Workflow tests: `tests/test_agent_treport_signal_report_workflow.py`.
- CLI coverage: `tests/test_agent_treport_cli.py`.
- Production entrypoints:
  `agent_treport.signal_report.build_signal_report_payload`,
  `agent_treport.signal_report.load_fixture_signal_report_inputs`,
  `agent_treport.signal_report.MarkdownSignalReportRenderer`,
  `agent_treport.workflows.signal_report.run_signal_report`, and
  `agent_treport.workflows.signal_report.build_signal_report_workflow`.

## Goal

Implement the first deterministic `ReportPayload` builder for Agent TReport's `SignalIntelligenceReport` product.

This slice should turn fixture holdings changes and fixture evidence into one validated JSON-compatible payload that later Telegram, HTML, PDF, and quality-gate slices can consume.

## Resolved Implementation Scope

This behavior slice includes the `ReportPayload` builder, a deterministic multi-ETF fixture, focused public tests, and integration into `SignalReportWorkflow` as a JSON artifact. The workflow should produce `artifact_treport_signal_payload.json` and record the artifact id in the run state.

This slice also renames the active workflow module and public functions from the completed `FirstUsableAgent` milestone language to `SignalReportWorkflow` language. The preferred production location is `src/agent_treport/workflows/signal_report.py`, with public functions `run_signal_report(...)` and `build_signal_report_workflow(...)`.

Active code, tests, fixtures, and docs should stop using `first_usable` naming. Keep that language only where preserving historical archived plan evidence is necessary. The new default fixture should be a multi-ETF signal report fixture, while the old single-ETF fixture should be renamed or removed if it is no longer required.

The workflow should fully transition to multi-ETF analysis as the default. Do not keep a single-ETF provider or aggregator path for backward compatibility. A focus ETF may be represented as a lens through `focus_etf_id`, but specialized single-ETF-only methodology is a later extension.

The canonical payload should be built deterministically before model commentary. Model output may explain or summarize the payload, but it must not change signal scores, review labels, evidence grades, or data-quality findings. Deterministic interpretation text should avoid stiff boilerplate: it should acknowledge uncertainty, conflicting evidence, and multiple plausible readings when the data supports them. Over-interpretation is worse than under-interpretation.

The Markdown report should also become payload-first in this slice. `MarkdownSignalReportRenderer` should render from `SignalReportPayload` and optional model commentary, without recalculating scores, labels, evidence grades, or data-quality findings. It should show the executive summary, signal board, ticker dossiers, data quality, methodology, and model commentary as a preview/report artifact while leaving Telegram, HTML, and PDF renderers for later slices.

This slice replaces the existing Markdown renderer with a payload-first Markdown renderer, but does not implement Telegram, HTML, PDF, live integrations, LLM judging, or investment recommendations. Those remain later slices after the canonical payload is observable in a real workflow run.

## Why This Slice Comes Next

The current first usable workflow already produces holdings, changes, summary, and Markdown artifacts. It proves that Agent TReport can run on `agent_pack`, but it does not yet define the durable product contract for a signal intelligence report.

Implementing renderers first would duplicate calculations and make Telegram/HTML/PDF disagree. Implementing live integrations first would expand scope before the payload contract is stable.

The payload slice is the smallest meaningful next step because it creates the source of truth without requiring live data or presentation surfaces.

## Public Interface Candidate

The public behavior should be available through two Agent TReport boundaries:

Domain/pipeline surface:

```python
from agent_treport.signal_report import (
    MarkdownSignalReportRenderer,
    build_signal_report_payload,
    load_fixture_signal_report_inputs,
)

build_signal_report_payload(
    *,
    snapshots: MultiETFHoldingsSnapshots,
    focus_etf_id: str | None = None,
    evidence: tuple[EvidenceItemInput, ...] = (),
) -> SignalReportPayload
```

Workflow surface:

```python
from agent_treport.workflows.signal_report import (
    build_signal_report_workflow,
    run_signal_report,
)
```

Do not expose scoring helpers, classification helpers, evidence ledger helpers, intermediate change builders, fixture path constants, or private formatting helpers as public API in this slice.

The public output must be serializable through Pydantic or the existing runtime model conventions:

```python
payload.model_dump(mode="json")
```

## Behavior Boundary

This is a domain capability, not a runtime feature.

Allowed dependency direction:

- `agent_treport -> agent_pack` where runtime models or JSON rules are useful.

Forbidden dependency direction:

- `agent_pack -> agent_treport`.

## Fixture Scope

Use a deterministic fixture that includes at least:

- Two ETF brands.
- Three ETFs.
- One focus ETF.
- One multi-ETF accumulation signal.
- One multi-ETF distribution signal.
- One focus-ETF-only signal.
- One evidence-supported ticker.
- One weak-evidence ticker.
- One data-quality issue such as unmapped security or missing price/news coverage.

The fixture can be small. It should prove the payload shape and signal classification, not simulate a full production universe.

The default workflow fixture should be a new multi-ETF fixture, such as `signal_report_multi_etf_holdings.json`. It should replace `first_usable` naming in active code paths.

## Payload V1 Required Sections

`SignalReportPayload` v1 should include these top-level sections:

```json
{
  "meta": {},
  "coverage": {},
  "executive_summary": {},
  "signal_board": [],
  "market_map": {},
  "etf_follow_sheets": [],
  "ticker_dossiers": [],
  "evidence_ledger": [],
  "methodology": {},
  "data_quality": {}
}
```

## Acceptance Criteria

- A deterministic multi-ETF fixture builds a strict JSON-compatible `ReportPayload`.
- `meta` records report id, as-of date, comparison period, universe, report version, and scoring version.
- `coverage` records ETF count, holding row count, security count, brand/source-provider count, mapped security ratio, and evidence coverage ratios when available.
- `signal_board` ranks at least one multi-ETF accumulation signal above a focus-ETF-only signal when all else is comparable.
- `signal_board` uses domain review labels `focus`, `monitor`, `caution`, and `defer`, not BUY/HOLD/SELL investment recommendations or trading-action labels.
- `signal_board` includes signal direction, signal type, score, confidence, evidence grade, and primary reason.
- `market_map` summarizes theme, sector, country, and cash movement when fixture data provides those fields.
- `etf_follow_sheets` includes the focus ETF with new positions, exited positions, increased positions, decreased positions, and data quality.
- `ticker_dossiers` include holding facts, why-now hypothesis, supporting evidence, counter evidence, invalidation conditions, and final label for top signals.
- `evidence_ledger` records each evidence item once and links it to the sections that used it.
- `data_quality` exposes mapping/coverage limitations without hiding them in prose.
- The payload does not include direct investment recommendations, price targets, or unsupported ETF brand-intent claims.

## Focused Failing Test Ideas

Add focused public behavior tests before production code. Split the tests by public boundary:

- Payload builder behavior in `tests/test_agent_treport_signal_report.py`.
- Markdown renderer behavior in `tests/test_agent_treport_signal_report_rendering.py` or the same signal report test file if that stays clearer.
- Workflow integration behavior in `tests/test_agent_treport_signal_report_workflow.py`, replacing active `first_usable` test naming.

Payload builder test shape:

```python
def test_signal_report_payload_builds_ranked_universe_and_focus_etf_contract():
    payload = build_signal_report_payload(
        snapshots=fixture_multi_etf_snapshots(),
        focus_etf_id="etf_focus_ai",
        evidence=fixture_evidence_items(),
    )

    data = payload.model_dump(mode="json")

    assert data["meta"]["report_type"] == "weekly_etf_signal"
    assert data["coverage"]["etf_count"] == 3
    assert data["signal_board"][0]["signal_type"] == "multi_etf_accumulation"
    assert data["signal_board"][0]["review_label"] == "focus"
    assert data["signal_board"][0]["evidence_grade"] in {"Confirmed", "Plausible"}
    assert data["etf_follow_sheets"][0]["etf_id"] == "etf_focus_ai"
    assert data["ticker_dossiers"][0]["holding_facts"]["participating_etfs"] >= 2
    assert data["evidence_ledger"][0]["used_in"]
    json.dumps(data)
```

Keep assertions focused on public payload behavior, not private helper names.

Renderer tests should verify that Markdown renders from `SignalReportPayload`, includes executive summary, signal board, ticker dossiers, data quality, methodology, and optional model commentary, includes Korean display labels, and does not introduce BUY/HOLD/SELL, price targets, or trading-action language.

Workflow tests should verify that `run_signal_report(...)` stores `artifact_treport_signal_payload.json` and `artifact_treport_report.md`, records `signal_payload_artifact_id` in run state, preserves the payload artifact when model commentary fails, and keeps CLI behavior working through the renamed workflow.

## Production Code Change Scope

Likely files:

- `src/agent_treport/signal_report/__init__.py` for public exports.
- `src/agent_treport/signal_report/domain/snapshots.py` for ETF holdings snapshot records.
- `src/agent_treport/signal_report/domain/changes.py` for holdings change calculation records and logic.
- `src/agent_treport/signal_report/domain/signals.py` for signal classification, scoring component names, evidence grade, and review label logic.
- `src/agent_treport/signal_report/domain/evidence.py` for evidence input and ledger records.
- `src/agent_treport/signal_report/domain/payload.py` for canonical `SignalReportPayload` section models.
- `src/agent_treport/signal_report/pipeline/build_payload.py` for `build_signal_report_payload(...)` orchestration.
- `src/agent_treport/signal_report/renderers/markdown.py` for payload-first Markdown rendering.
- `src/agent_treport/signal_report/adapters/fixture.py` for deterministic multi-ETF fixture helpers.
- `src/agent_treport/workflows/signal_report.py` for the renamed thin workflow orchestration.
- `src/agent_treport/fixtures/signal_report/` for deterministic multi-ETF fixture data.
- `tests/test_agent_treport_signal_report.py` for public payload behavior.
- `src/agent_treport/__init__.py` only if a public export is intentionally added.

Avoid changing `agent_pack` for this slice unless a runtime JSON/model contract issue is found.

## Documentation Updates

Update these docs with final implemented evidence:

- `src/agent_treport/docs/signal-intelligence-report.md`.
- `src/agent_treport/docs/plans/canonical-signal-report-payload-v1.md`.
- `src/agent_treport/CONTEXT.md` if new domain terms are introduced or renamed.
- `docs/README.md` only if document index entries change.

Archive this plan after implementation and verification pass.

## Verification Commands

Focused checks:

```text
../.venv/Scripts/python.exe -m pytest tests/test_agent_treport_signal_report.py
```

Relevant full checks before completion:

```text
../.venv/Scripts/python.exe -m pytest
../.venv/Scripts/python.exe -m ruff check .
../.venv/Scripts/python.exe -m pyright
```

## Out Of Scope

- Telegram signal alert rendering.
- HTML research report rendering.
- PDF snapshot generation.
- Live holdings providers.
- Live news/search/price enrichment.
- LLM judge scoring.
- Human review workflow.
- Telegram delivery.
- Investment recommendations, price targets, or portfolio allocation advice.

## Open Design Questions

These should be resolved before implementation starts:

- What is the canonical fixture shape for multi-brand/multi-ETF holdings snapshots? Resolved: use ETF language consistently. The v1 input should use `MultiETFHoldingsSnapshots`, `ETFHoldingsSnapshots`, `SecurityHolding`, and `EvidenceItemInput`; avoid mixed `fund` naming in active domain code.
- Should `ReportPayload` models live in one module or be split into payload, signal, evidence, and scoring submodules after the first green test? Resolved for v1: use a `src/agent_treport/signal_report/` package organized by process boundaries: `domain/`, `pipeline/`, `renderers/`, and `adapters/`. Keep subdomain concepts as files first (`snapshots.py`, `changes.py`, `signals.py`, `evidence.py`, `payload.py`) and promote them to folders only when behavior requires deeper splitting.
- Should the completed `FirstUsableAgent` module/function names remain active? Resolved: no. Rename the active workflow to `SignalReportWorkflow`, implemented in `src/agent_treport/workflows/signal_report.py`, with `run_signal_report(...)` and `build_signal_report_workflow(...)`.
- Should score components be fully exposed in v1, or only total score plus methodology summary? Resolved: expose `signal_score` plus a simple `score_components` object. V1 component names are `position_change_strength`, `cross_etf_confirmation`, `portfolio_materiality`, `external_evidence_support`, `recency_alignment`, `data_quality_penalty`, and `contradiction_penalty`. Each component must have a clear meaning in `methodology`, but v1 should not introduce a complex tunable scoring engine.
- Which fields are required in v1 when optional enrichment data is absent? Resolved: do not omit payload fields. Distinguish unknown values from not-applicable values explicitly. Use `null` for unknown scalar values, `[]` for empty lists, and structured `data_quality.limitations` or `data_quality.issues` entries for missing enrichment such as price, news, analyst, mapping, or classification coverage.
- How should Korean display labels and English enum values coexist in the payload? Resolved: canonical enum values use English snake_case for tests, filters, and quality gates. Korean labels live under lightweight `display` objects for renderer reuse. Korean terminology should prefer terms used by finance, ETF, research, and asset-management practitioners; avoid unnatural literal translations. Use `review_label` instead of `action_label`; v1 labels are `focus` / `중점 모니터링`, `monitor` / `모니터링`, `caution` / `유의`, and `defer` / `판단 유보`.

## Codex Goal Prompt Draft

```text
Implement Agent TReport Canonical Signal Report Payload v1 using TDD.

Read:
- AGENTS.md
- src/agent_treport/AGENTS.md
- CONTEXT-MAP.md
- src/agent_treport/CONTEXT.md
- src/agent_treport/docs/signal-intelligence-report.md
- src/agent_treport/docs/plans/canonical-signal-report-payload-v1.md
- src/agent_treport/docs/adr/0001-capabilities-use-ports-and-adapters.md
- src/agent_treport/docs/adr/0002-use-two-reference-roles.md
- tests/test_agent_treport_first_usable_agent.py
- tests/test_agent_treport_cli.py
- src/agent_treport/first_usable_agent.py
- src/agent_treport/cli.py

Goal:
Replace the historical first usable ETF change report implementation with `SignalReportWorkflow`, whose default mode is multi-ETF signal analysis. Add a deterministic canonical `SignalReportPayload` builder, deterministic multi-ETF fixtures, payload-first Markdown rendering, and workflow/CLI integration. The payload is the source of truth for later Telegram, HTML, PDF, and quality-gate slices.

Core decisions to preserve:
- Multi-ETF analysis is the default. A focus ETF is only a lens through `focus_etf_id`; single-ETF-only methodology is a later extension.
- Use ETF terminology consistently. Do not introduce active domain `fund` naming.
- Remove active `first_usable` naming from code, tests, fixtures, and active docs. Historical archive docs may keep the old milestone language.
- Build the canonical payload deterministically before model commentary. Model output may explain the payload but must not change scores, review labels, evidence grades, or data-quality findings.
- Deterministic interpretation text should not become stiff boilerplate. It should acknowledge uncertainty, conflicting evidence, and multiple plausible readings when supported. Over-interpretation is worse than under-interpretation.
- Do not add backward-compatibility code for the old single-ETF path unless a current test proves a concrete need.

Scope:
- Add focused failing public behavior tests first, then implement the smallest correct production changes.
- Keep changes in `agent_treport` only unless a runtime JSON/model contract issue is discovered.
- Rename the active workflow module/functions to `SignalReportWorkflow` language while preserving `agent-treport run-report` CLI behavior.
- Replace the active single-ETF default with a deterministic multi-ETF fixture.
- Add a strict JSON-compatible `SignalReportPayload` with top-level sections: `meta`, `coverage`, `executive_summary`, `signal_board`, `market_map`, `etf_follow_sheets`, `ticker_dossiers`, `evidence_ledger`, `methodology`, and `data_quality`.
- Integrate the payload into the workflow as `artifact_treport_signal_payload.json` and record `signal_payload_artifact_id` in run state.
- Convert Markdown rendering to payload-first rendering from `SignalReportPayload` plus optional model commentary.
- Preserve existing failure semantics under new names: provider/preparation failures classify as data preparation failures, model commentary failures classify as model analysis failures, renderer failures classify as report render failures, artifact failures classify as artifact persistence failures. If model commentary fails after payload creation, the payload artifact must remain stored and inspectable.

Target structure:
- `src/agent_treport/signal_report/__init__.py`
- `src/agent_treport/signal_report/domain/__init__.py`
- `src/agent_treport/signal_report/domain/snapshots.py`
- `src/agent_treport/signal_report/domain/changes.py`
- `src/agent_treport/signal_report/domain/signals.py`
- `src/agent_treport/signal_report/domain/evidence.py`
- `src/agent_treport/signal_report/domain/payload.py`
- `src/agent_treport/signal_report/pipeline/__init__.py`
- `src/agent_treport/signal_report/pipeline/build_payload.py`
- `src/agent_treport/signal_report/renderers/__init__.py`
- `src/agent_treport/signal_report/renderers/markdown.py`
- `src/agent_treport/signal_report/adapters/__init__.py`
- `src/agent_treport/signal_report/adapters/fixture.py`
- `src/agent_treport/workflows/__init__.py`
- `src/agent_treport/workflows/signal_report.py`
- `src/agent_treport/fixtures/signal_report/holdings.json`
- `src/agent_treport/fixtures/signal_report/evidence.json`

Public interfaces:
- Export from `agent_treport.signal_report`: `build_signal_report_payload`, `load_fixture_signal_report_inputs`, and `MarkdownSignalReportRenderer`.
- Export/use from `agent_treport.workflows.signal_report`: `run_signal_report` and `build_signal_report_workflow`.
- Do not expose scoring helpers, classification helpers, evidence ledger helpers, intermediate change builders, fixture path constants, or private formatting helpers as public API in this slice.

Canonical input models:
- `MultiETFHoldingsSnapshots`
- `ETFHoldingsSnapshots`
- `SecurityHolding`
- `EvidenceItemInput`

Fixture requirements:
- At least two ETF brands.
- At least three ETFs.
- One focus ETF.
- One multi-ETF accumulation signal.
- One multi-ETF distribution signal.
- One focus-ETF-only signal.
- One evidence-supported ticker.
- One weak-evidence ticker.
- One data-quality issue such as an unmapped security, missing ticker, missing classification, or missing enrichment coverage.

Payload rules:
- Payload models must serialize with `payload.model_dump(mode="json")` and `json.dumps(...)`.
- Do not omit payload fields when optional enrichment is absent. Use `null` for unknown scalar values, `[]` for empty lists, and structured `data_quality.limitations` or `data_quality.issues` for missing price, news, analyst, mapping, or classification coverage.
- Canonical enum values use English snake_case for tests, filters, and quality gates.
- Korean labels live under lightweight `display` objects for renderer reuse.
- Korean terminology should follow finance, ETF, research, and asset-management usage. Avoid unnatural literal translations.
- Use `review_label`, not `action_label`.
- V1 review labels are `focus` / `중점 모니터링`, `monitor` / `모니터링`, `caution` / `유의`, and `defer` / `판단 유보`.
- Do not include BUY/HOLD/SELL, price targets, portfolio allocation advice, or trading-action language.

Scoring rules:
- Expose `signal_score` plus a simple `score_components` object.
- V1 score component names are `position_change_strength`, `cross_etf_confirmation`, `portfolio_materiality`, `external_evidence_support`, `recency_alignment`, `data_quality_penalty`, and `contradiction_penalty`.
- Each component must have a clear meaning in `methodology`.
- Do not build a complex configurable scoring engine in v1.

Evidence rules:
- Keep holdings facts separate from inferred reasons.
- Record each evidence item once in `evidence_ledger`.
- Link evidence to sections that used it via `used_in` or equivalent stable references.
- Evidence grades separate holdings facts from inferred reasons: `Confirmed`, `Plausible`, `Weak`, `Conflicted`, `Unusable`.

Markdown renderer rules:
- `MarkdownSignalReportRenderer` renders from `SignalReportPayload` and optional model commentary.
- Include executive summary, signal board, ticker dossiers, data quality, methodology, and model commentary.
- Do not recalculate scores, review labels, evidence grades, or data-quality findings in Markdown.
- Markdown is a local preview/report artifact, not the source of truth.

Workflow rules:
- Suggested flow: load multi-ETF fixture -> calculate changes/build deterministic payload -> store holdings/changes/summary/payload artifacts -> call model for commentary using payload facts -> render payload-first Markdown -> store report artifact.
- The model commentary prompt/request must make clear that scores, review labels, evidence grades, and data-quality findings are fixed and must not be changed.
- Preserve artifact ids where still meaningful: `artifact_treport_holdings`, `artifact_treport_changes`, `artifact_treport_summary`, `artifact_treport_report`; add `artifact_treport_signal_payload`.
- Remove or rename `src/agent_treport/first_usable_agent.py` after migrating active imports/tests. Do not leave active `first_usable` imports.

Tests:
- Add/update payload builder tests in `tests/test_agent_treport_signal_report.py`.
- Add/update Markdown renderer tests in `tests/test_agent_treport_signal_report_rendering.py` or the same file if clearer.
- Rename/update active workflow tests to `tests/test_agent_treport_signal_report_workflow.py`.
- Update CLI tests to the new workflow import path while preserving `agent-treport run-report` behavior.
- Test through public boundaries, not private helpers.
- Verify the first ranked signal can be a multi-ETF accumulation signal with `review_label == "focus"` and a complete `score_components` object.
- Verify Korean display labels appear in payload/Markdown.
- Verify payload and Markdown do not contain direct investment recommendations, BUY/HOLD/SELL, price targets, or trading-action language.
- Verify model commentary failure leaves the signal payload artifact stored and classifies the run as `model_analysis_failed`.

Documentation:
- Update `src/agent_treport/CONTEXT.md` if terms drift during implementation.
- Update `src/agent_treport/docs/signal-intelligence-report.md` and this plan with implementation evidence.
- Archive this plan after implementation and verification pass.

Out of scope:
- Telegram signal alert rendering.
- HTML research report rendering.
- PDF snapshot generation.
- Live holdings, news, search, price, analyst, or financial metric integrations.
- LLM judge scoring.
- Human review workflow.
- Telegram delivery.
- Investment recommendations, price targets, or portfolio allocation advice.

Verify:
- ../.venv/Scripts/python.exe -m pytest tests/test_agent_treport_signal_report.py
- ../.venv/Scripts/python.exe -m pytest tests/test_agent_treport_signal_report_rendering.py
- ../.venv/Scripts/python.exe -m pytest tests/test_agent_treport_signal_report_workflow.py
- ../.venv/Scripts/python.exe -m pytest tests/test_agent_treport_cli.py
- ../.venv/Scripts/python.exe -m pytest
- ../.venv/Scripts/python.exe -m ruff check .
- ../.venv/Scripts/python.exe -m pyright
```
