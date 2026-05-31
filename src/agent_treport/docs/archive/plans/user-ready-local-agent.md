# User Ready Local Agent Plan

## Status

Archived. Implemented as the `UserReadyLocalAgent` completion slice for
`SignalReportWorkflow` and the Agent TReport CLI. The slice added the
successful-run `LocalFollowUpContract`, `agent-treport inspect`, and
`agent-treport dashboard`, then verified the local run/inspect loop with a real
Codex smoke under `.scratch/user-ready-local-agent/`.

## Goal

Make `SignalReportWorkflow` usable as the first `UserReadyLocalAgent`: a local Agent TReport run that produces a `SignalIntelligenceReport`, records durable execution evidence, and gives the user clear ways to inspect progress, results, logs, and artifacts after the run.

## Canonical Meaning

In this slice, `agent` means the Agent TReport `UserReadyLocalAgent`, not the generic `agent_pack.Agent` runtime actor.

`Simple but complete` means the local operational loop is complete for the current fixture-first signal report product:

- Start a real run from the domain CLI.
- Use the configured model boundary, including Codex when selected.
- Persist run, event, context, context view, snapshot, and artifact evidence to SQLite and the artifact root.
- Produce canonical JSON and Markdown report artifacts.
- Inspect run progress and logs from stored runtime evidence.
- Open the local dashboard for the same run and preview safe text artifacts.

It does not mean live holdings/news/price adapters, Telegram or Threads publishing, autonomous scheduling, HTML/PDF renderers, or full `ReferenceParityTarget` completion.

## Existing Foundation

Already implemented foundations this slice should compose rather than replace:

- `agent-treport run-report --model codex` runs `SignalReportWorkflow` and writes SQLite plus artifacts.
- `SignalReportWorkflow` stores holdings, changes, summary, canonical signal payload, and Markdown artifacts.
- `ReportCommentaryPolicy` gates optional model commentary after the canonical payload is fixed.
- `agent-pack inspect` prints `RunInspectionSnapshot` JSON from SQLite evidence.
- `agent-pack dashboard` serves a generic local read-only run dashboard with safe artifact preview.
- Runtime failures are sanitized into durable failure evidence and domain failure reasons.

## Public Interfaces

The user-ready domain surface should be centered on Agent TReport commands so the user does not need to remember generic runtime commands for normal use:

- `agent-treport run-report --run-id RUN_ID --sqlite-path DB --artifact-root ARTIFACT_ROOT --model codex [--codex-model MODEL] [--model-timeout-seconds SECONDS]`
- `agent-treport inspect --run-id RUN_ID --sqlite-path DB`
- `agent-treport dashboard --run-id RUN_ID --sqlite-path DB --artifact-root ARTIFACT_ROOT --port 0 [--open]`

The domain commands may delegate to generic `agent_pack` inspection and dashboard services, but the Agent TReport CLI is the composition layer for a user-ready domain run.

`run-report` keeps the existing `RunResult` top-level JSON shape. Successful
results add a domain-owned `output.user_ready` block containing the stable
follow-up metadata a local user needs: run id, SQLite path, artifact root,
canonical payload artifact, Markdown report artifact, and ready-to-run
`agent-treport inspect` and `agent-treport dashboard` commands. Failed results
do not add `output.user_ready`; they keep the existing sanitized failure
contract with `reason`, `state`, and `runtime_failure`. This keeps the runtime
result contract stable while making successful Agent TReport runs usable without
knowing generic `agent-pack` commands.
Domain workflow failures still print the failed `RunResult` JSON to stdout and
return exit code `1`. CLI bootstrap and output-composition failures print
sanitized failure JSON to stderr, keep stdout empty, and return exit code `1`.
For `run-report`, model client configuration or factory failures that occur
before workflow execution are CLI bootstrap failures with
`reason: "model_client_failed"`, `error.code: "model_client_failed"`, and
fallback message `"model client failed"`. Model request failures during workflow
execution remain domain workflow failures classified as `model_analysis_failed`
and are returned in the stdout `RunResult`.
Also for `run-report`, SQLite parent creation, `SQLiteRunStore` construction, and
`await store.initialize()` are bootstrap work. Failures there use the
`run_store_failed` stderr JSON contract. Persistence failures after workflow
execution starts remain workflow/runtime failures and are classified by the
existing domain failure path.
Artifact root manager construction or initial artifact-root bootstrap failures
before workflow execution are CLI bootstrap failures with
`reason: "artifact_store_failed"`, `error.code: "artifact_store_failed"`, and
fallback message `"artifact store failed"`. Artifact write/read failures during
workflow execution remain domain workflow failures classified as
`artifact_persistence_failed` and are returned in the stdout `RunResult`.

The stable `output.user_ready` top-level fields are only `run_id`, `sqlite_path`,
`artifact_root`, `artifacts`, and `commands`. Do not add `dashboard_url`, because
the dashboard command stdout is the only source of truth for the resolved URL.
Do not duplicate generic `RunResult` fields such as `status`, `created_at`, or
model details in this local follow-up contract.
`sqlite_path` and `artifact_root` in `output.user_ready` and follow-up argv values
use resolved absolute strings: `str(Path(args.sqlite_path).resolve())` and
`str(Path(args.artifact_root).resolve())`.

Inside `output.user_ready.commands`, argv arrays are the stable machine-readable
contract and command strings are human-readable conveniences. Tests should assert
`commands.inspect_argv` and `commands.dashboard_argv`; `commands.inspect` and
`commands.dashboard` may be rendered from those arrays for display. Render display
strings with `shlex.join(argv)` rather than adding shell-specific quoting logic;
the argv arrays are the stable executable contract.
`commands.inspect_argv` is exactly `["agent-treport", "inspect", "--run-id",
RUN_ID, "--sqlite-path", ABS_SQLITE_PATH]`.
`commands.dashboard_argv` is exactly `["agent-treport", "dashboard", "--run-id",
RUN_ID, "--sqlite-path", ABS_SQLITE_PATH, "--artifact-root", ABS_ARTIFACT_ROOT,
"--port", "0"]`. Do not include `--open` in the default follow-up argv. The
actual dashboard URL is not known until the user runs the dashboard command and
the command prints the resolved local URL to stdout.

Inside `output.user_ready.artifacts`, use a keyed object with exactly
`canonical_payload` and `markdown_report` entries. Each entry contains
`artifact_id`, `name`, `media_type`, `uri`, and `path`. The paths should point to
actual files under the configured `artifact_root` when the artifact URI is a
local `file://` URI; otherwise `path` is `null`. Paths are local user convenience
metadata, not canonical artifact identity.
Convert artifact URIs to paths with `urllib.parse.urlparse` and `unquote`,
accepting only `file://` URIs with empty or `localhost` netloc. Return `null` for
non-file or non-local URIs. On Windows, remove the leading slash from `/C:/...`
style parsed paths before resolving the local path string.
Build these entries from the `ArtifactRef` records returned in `RunResult.artifacts`.
Use `result.output.state.signal_payload_artifact_id` and `report_artifact_id` to
select the canonical payload and Markdown report refs. The CLI must not guess
artifact filenames or URIs. If a successful workflow result is missing either
expected artifact reference, treat it as a CLI composition failure: write
sanitized failure JSON to stderr, keep stdout empty, and return exit code `1`.
This composition-layer failure uses `reason: "user_ready_contract_failed"`,
`error.code: "user_ready_contract_failed"`, and fallback message
`"user ready contract failed"`; keep `run_store_failed` scoped to SQLite or
bootstrap failures and `artifact_persistence_failed` scoped to workflow artifact
storage failures. Do not mutate the persisted run status or add runtime events
for this CLI output-composition failure; the stored workflow evidence remains the
source of truth for the run itself.

The Agent TReport CLI owns construction of `output.user_ready`. Do not add this
metadata inside `SignalReportWorkflow` or `run_signal_report`; the workflow owns
report generation and runtime evidence, while the CLI composition layer owns
local follow-up commands and paths derived from CLI arguments.

Implement `agent-treport inspect` and `agent-treport dashboard` as direct
in-process reuse of generic runtime services rather than subprocess delegation to
`agent-pack`. `inspect` uses `RunInspectionService` directly. `dashboard` reuses
the generic dashboard server/app path so behavior stays aligned while preserving
domain CLI control over bootstrap failure handling and tests.
Add `dashboard_runner` and `browser_opener` injection parameters to
`agent_treport.cli.run_cli_async`, mirroring the generic CLI test seam so tests
do not start a real server or browser. Production defaults use the generic
dashboard server and `webbrowser.open`.

`agent-treport inspect` prints the exact same `RunInspectionSnapshot` JSON as
the generic runtime inspection service. It does not add Agent TReport-specific
summary fields, because domain-specific interpretation belongs in report
artifacts or later product surfaces rather than the generic inspection snapshot.
If the run id is missing, it matches the generic inspect failure contract:
stderr `run not found: RUN_ID\n`, empty stdout, and exit code `1`. Store open or
configuration failures are Agent TReport CLI bootstrap failures and use the
sanitized stderr JSON failure contract with empty stdout and exit code `1`.
All Agent TReport CLI store/bootstrap failures use `reason: "run_store_failed"`,
`error.code: "run_store_failed"`, and fallback message `"run store failed"`.
For `inspect`, only `RunInspectionNotFoundError` is treated as the plain generic
missing-run failure; all other store, schema, or read exceptions are sanitized as
`run_store_failed`.

`agent-treport dashboard` follows the generic dashboard transport behavior: it
prints the local URL to stdout before serving, opens a browser only when `--open`
is supplied, supports `--port 0` for an available local port, and forwards
`--artifact-root` so safe JSON/Markdown artifact previews work through the
generic dashboard. It does not preflight missing runs; those surface through the
dashboard page/API as generic 404 responses. Unlike the generic CLI transport,
the Agent TReport dashboard command should prove SQLite store bootstrap can
succeed before printing the URL by constructing `SQLiteRunStore` and awaiting
`store.initialize()`. Success passes that opened store to the server runner and
closes it when the runner returns. Bootstrap failures use the sanitized stderr
JSON failure contract with empty stdout and exit code `1`. Keep `--artifact-root`
required for `agent-treport dashboard`; the domain command is the complete local
review surface and should not run with artifact previews disabled.

## Acceptance Criteria

- `run-report` remains the single command that creates a Signal Intelligence Report run.
- The successful run output includes enough stable fields for the user to locate the run id, SQLite path, artifact root, canonical payload artifact, Markdown report artifact, and inspection/dashboard follow-up commands.
- The failed run output keeps the existing sanitized domain failure contract, does not include `output.user_ready`, and still leaves any persisted evidence inspectable when SQLite creation succeeded.
- `inspect` prints the same strict JSON-compatible `RunInspectionSnapshot` that the generic runtime inspection service returns.
- `dashboard` starts the same generic read-only dashboard for the Agent TReport run and passes `artifact_root` so generated JSON/Markdown artifacts can be previewed safely.
- Stored evidence shows run progress through ordered runtime events, latest snapshot state, context item summaries, context view snapshots, and artifact references.
- No Agent TReport domain concepts are added to `agent_pack` dashboard or inspection code.
- The slice adds no live provider adapters, publisher tools, scheduler, or new renderer format.
- The slice adds no new model transport UX options such as working directory or
  sandbox flags. `run-report` keeps the existing `--model`, `--codex-model`, and
  `--model-timeout-seconds` options.
- The slice adds no `inspect` formatting options and no dashboard host option.
  `inspect` prints one strict JSON line. `dashboard` supports only `--run-id`,
  `--sqlite-path`, required `--artifact-root`, required `--port`, and optional
  `--open`; host remains fixed to `127.0.0.1`.

## Verification Plan

Use TDD and keep this as one vertical behavior slice.

The implementation can be delegated as one larger Codex goal, but it should run
as three ordered behavior slices inside that goal:

1. Add `run-report` success `output.user_ready` metadata, split CLI bootstrap
   failures from workflow failures, and add focused tests, then commit that
   completed behavior slice.
2. Add the `agent-treport inspect` wrapper and prove it returns the same
   `RunInspectionSnapshot` JSON as the generic service, then commit that
   completed behavior slice.
3. Add the `agent-treport dashboard` wrapper and prove URL printing, `--open`,
   port resolution, and `--artifact-root` delegation match the generic
   transport, then commit that completed behavior slice.

Keep implementation local to `src/agent_treport/cli.py` with small private
helpers for command handling, `user_ready` construction, artifact entry
projection, file URI path extraction, and display command rendering. Do not add a
new public module or class for this slice.

Focused tests:

- Add or update Agent TReport CLI tests for `inspect` and `dashboard` wrappers over stored SQLite evidence.
- Add or update `run-report` CLI tests for user-facing follow-up command metadata if the output contract changes.
- Keep new domain CLI behavior tests concentrated in `tests/test_agent_treport_cli.py`.
- Verify successful `output.user_ready` through the real `run-report` path with a
  fake model client and real temporary SQLite/artifact files. Assert the full
  stable shape, exact argv arrays, resolved absolute paths, artifact ids, names,
  media types, URIs, local paths, and file existence.
- Verify `run-report` CLI bootstrap failures separately from workflow failures:
  store bootstrap failures use `run_store_failed` stderr JSON, and model client
  configuration or factory failures use `model_client_failed` stderr JSON, and
  artifact store bootstrap failures use `artifact_store_failed` stderr JSON.
- Do not add a production workflow injection seam only to force
  `user_ready_contract_failed`. Cover that composition failure through a focused
  helper-level test if needed, while keeping the normal `run_cli_async` path as
  the public behavior test.
- Verify `agent-treport inspect` by comparing stdout JSON to
  `RunInspectionService(store).build_snapshot(run_id).model_dump(mode="json")`
  for the same SQLite run. Verify missing run separately with exact stderr
  `run not found: RUN_ID\n`, empty stdout, and exit code `1`.
- Verify `agent-treport dashboard` with fake `dashboard_runner` and
  `browser_opener`: stdout URL, `--open` behavior, artifact root delegation,
  resolved non-zero port for `--port 0`, and bootstrap-before-URL failure. Leave
  UI/API details to generic dashboard tests.
- Keep generic runtime dashboard and inspection tests green to prove delegation has not forked domain-specific behavior.

Relevant checks:

- `../.venv/Scripts/python.exe -m pytest tests/test_agent_treport_cli.py tests/test_cli_transport.py tests/test_dashboard_transport.py tests/test_run_inspection.py`
- `../.venv/Scripts/python.exe -m pytest`
- `../.venv/Scripts/python.exe -m ruff check .`
- `../.venv/Scripts/python.exe -m pyright`

Real Codex smoke after tests pass. This was explicitly approved for the Codex
goal that implements this slice:

- Run `agent-treport run-report` with Codex, SQLite path, and artifact root under `.scratch/`.
- Run `agent-treport inspect` for the same run id.
- Do not run the real dashboard server as part of automated goal completion,
  because successful server execution is blocking. Verify dashboard behavior
  through tests, then report the manual dashboard command for the same run id.

Documentation closeout after implementation and verification:

- Archive this active plan as
  `src/agent_treport/docs/archive/plans/user-ready-local-agent.md` without a date
  prefix.
- Update `docs/implementation-plan.md` with completed `UserReadyLocalAgent`
  evidence. Do not invent a new implementation target; state that
  `UserReadyLocalAgent` is complete and the next target requires a separate
  product-direction decision.
- Update `docs/README.md` and `src/agent_treport/docs/README.md` so active and
  archived documentation links match the final state; remove the active plan link
  and add the archive link.
- Commit each completed behavior slice after its focused tests and relevant
  verification pass. Do not wait and batch all three behavior slices into one
  final implementation commit.
- Keep documentation closeout separate from the third dashboard behavior slice.
  After all three behavior slices, full verification, and real Codex run/inspect
  smoke pass, archive this plan and update the documentation indexes in a fourth
  documentation closeout commit.
- The Codex goal should therefore produce exactly four commits: `run-report`
  `user_ready`, `agent-treport inspect`, `agent-treport dashboard`, and
  documentation closeout.

## Open Questions

None for the next slice. If a later requirement asks for live progress streaming rather than persisted progress inspection, define a separate `LiveProgressSurface` slice instead of expanding this one.
