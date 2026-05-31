# Report Quality Gate Plan

## Status

Implemented and archived.

Implementation evidence:

- Domain capability commit: `41c9893 Add report quality gate domain capability`.
- Workflow and CLI integration commit:
  `63731b7 Integrate report quality gate into Agent TReport`.
- Public domain tests:
  `tests/test_agent_treport_signal_report_quality.py`.
- Workflow integration tests:
  `tests/test_agent_treport_signal_report_workflow.py`.
- CLI user-ready artifact tests: `tests/test_agent_treport_cli.py`.

Verification evidence:

- `../.venv/Scripts/python.exe -m pytest tests/test_agent_treport_signal_report_quality.py`: 11 passed.
- `../.venv/Scripts/python.exe -m pytest tests/test_agent_treport_signal_report_quality.py tests/test_agent_treport_signal_report_workflow.py tests/test_agent_treport_cli.py`: 33 passed.
- `../.venv/Scripts/python.exe -m pytest`: 139 passed.
- `../.venv/Scripts/python.exe -m ruff check .`: all checks passed.
- `../.venv/Scripts/python.exe -m pyright`: 0 errors.

Real Codex smoke was not run because this slice is deterministic and model
transport behavior is unchanged.

## Problem

`SignalReportWorkflow` could produce a user-ready Markdown report from a
canonical `SignalReportPayload`, but the workflow did not have deterministic
product-quality enforcement between final Markdown rendering and exposing the
report as user-ready.

`ReportCommentaryPolicy` already omitted unsafe optional model commentary. The
missing boundary was a payload-plus-rendered-output gate that checked the actual
final Markdown artifact candidate before it was stored as the user-facing
report.

## Context

This was an Agent TReport domain slice, not a generic runtime slice. The
implementation stayed under `agent_treport` and did not add Agent TReport
concepts or artifact propagation changes to `agent_pack`.

The first slice evaluates:

- canonical `SignalReportPayload`
- final rendered Markdown preview

The first slice does not evaluate HTML, Telegram, PDF, live adapters, external
publishing, subjective scoring, or LLM judge output.

## Decisions

- Use the canonical terms `ReportQualityContract`, `ReportQualityGate`,
  `ReportQualityResult`, `ReportQualityViolation`, and
  `ProhibitedInvestmentLanguagePolicy`.
- `ReportQualityGate().evaluate(payload=payload, markdown=markdown)` is the
  public object-based API.
- `ReportQualityContract.default()` supplies the default contract.
- The workflow default allows zero error-severity violations.
- Warning-only results keep the run succeeded and the local output user-ready.
- Error-severity results block the Markdown report artifact from being stored.
- A quality artifact is stored for every report attempt that reaches quality
  evaluation and returns a result.
- Quality evidence is internal runtime evidence and is not model-visible.
- Unsafe original matched text, full Markdown, full payload, and tracebacks are
  not stored in quality details, metadata, context, or failure output.

## Implemented Scope

Domain capability:

- Added shared prohibited investment-language detection in
  `investment_language_policy.py`.
- Moved prohibited investment-language regex ownership out of
  `ReportCommentaryPolicy`.
- Added `ReportQualityContract`, `ReportQualityGate`, `ReportQualityResult`, and
  `ReportQualityViolation`.
- Evaluated required payload sections, required Markdown sections, target
  Markdown coverage warnings, prohibited investment language, forbidden custom
  fragments, raw claim-scope exposure, and Signal Board value reflection.

Workflow and CLI integration:

- Evaluates quality after Markdown render and before storing `report.md`.
- Always stores `quality.json` after a quality result exists.
- Stores `report.md` only when the quality result passes.
- Adds successful-run state and artifact metadata for quality status and
  summary.
- Exposes `quality_report` in `output.user_ready.artifacts`.
- Classifies blocking quality results and quality evaluation exceptions as
  `report_quality_failed`.

Documentation:

- Documented first-slice quality gate rules and warning-only target coverage
  gaps in `signal-intelligence-report.md`.
- Updated Agent TReport and root documentation indexes.
- Recorded implementation and verification evidence in
  `docs/implementation-plan.md`.

## Acceptance Criteria

- The default fixture payload plus normal Markdown returns a passing
  `ReportQualityResult` with zero errors and target coverage warnings.
- Prohibited investment language returns an error without exposing original
  matched text.
- Missing Markdown and payload sections are deterministic error violations.
- Raw claim-scope exposure aggregates unique scopes into one error.
- Signal Board reflection reports per-row missing values.
- Payload warnings include missing data-quality limitations, missing primary
  risks, and no signals when coverage exists.
- Workflow quality failures persist `quality.json`, omit `report.md`, preserve
  quality state, and return `reason: report_quality_failed`.
- Quality evaluation exceptions return fixed sanitized error fields without a
  `report_quality` result.
- Successful CLI user-ready output includes canonical payload, Markdown report,
  and quality report artifact entries.
