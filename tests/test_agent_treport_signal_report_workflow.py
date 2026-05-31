from __future__ import annotations

import asyncio
import json
from pathlib import Path

from agent_pack.artifacts import LocalArtifactManager
from agent_pack.context import ContextManager
from agent_pack.inspection import RunInspectionService
from agent_pack.models import JsonBlock, Message, ModelRequest, ModelResponse, TextBlock
from agent_pack.models_client import FakeModelClient
from agent_pack.store import SQLiteRunStore

from agent_treport.signal_report import (
    HTMLResearchReportRenderer,
    MarkdownSignalReportRenderer,
    ReportQualityGate,
    TelegramSignalAlertRenderer,
)
from agent_treport.signal_report.domain.inputs import SignalReportInputs
from agent_treport.workflows.signal_report import (
    CachedSignalReportInputProvider,
    FixtureSignalReportInputProvider,
    run_signal_report,
)


def run_async(awaitable):
    return asyncio.run(awaitable)


def test_signal_report_workflow_persists_payload_and_markdown_artifacts(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        sqlite_path = tmp_path / "treport.sqlite3"
        artifact_root = tmp_path / "artifacts"
        store = SQLiteRunStore(str(sqlite_path))
        artifacts = LocalArtifactManager(artifact_root)
        model = FakeModelClient(
            [
                ModelResponse(
                    message=Message(
                        role="assistant",
                        content=(
                            TextBlock(
                                text="Model commentary: explain the fixed signal payload."
                            ),
                        ),
                    )
                )
            ]
        )

        result = await run_signal_report(
            run_id="run_treport_signal",
            store=store,
            context_manager=ContextManager(store=store),
            artifact_manager=artifacts,
            model_client=model,
        )
        await store.close()

        state = result.output["state"]
        assert result.status == "succeeded"
        assert state["signal_payload_artifact_id"] == "artifact_treport_signal_payload"
        assert state["signal_report_inputs_artifact_id"] == (
            "artifact_treport_signal_report_inputs"
        )
        assert state["report_artifact_id"] == "artifact_treport_report"
        assert state["html_report_artifact_id"] == "artifact_treport_html_report"
        assert state["telegram_alert_artifact_id"] == "artifact_treport_telegram_alert"
        assert state["report_quality_artifact_id"] == "artifact_treport_quality"
        assert state["report_quality_status"] == "passed"
        assert state["report_quality_summary"]["blocking"] is False
        assert state["harness_evaluator_artifact_id"] == (
            "artifact_treport_harness_evaluator_review"
        )
        assert state["harness_evaluator_verdict"] == "pass"
        assert len(state["agent_pack_review_summaries"]) == 1
        evaluator = state["agent_pack_review_summaries"][0]
        assert evaluator["id"] == "review.signal_report_evaluator"
        assert evaluator["review_status"] == "passed"
        assert evaluator["operation_kind"] == "evaluator_harness_review"
        assert evaluator["details"]["verdict"] == "pass"
        assert evaluator["details"]["uncertainty"] == {"level": "low", "reasons": ()}
        assert len(model.requests) == 1
        assert any(
            "canonical SignalReportPayload is fixed" in block.text
            for message in model.requests[0].messages
            for block in message.content
            if isinstance(block, TextBlock)
        )
        model_contexts = [
            block.value
            for message in model.requests[0].messages
            for block in message.content
            if isinstance(block, JsonBlock)
            and isinstance(block.value, dict)
            and "signal_payload_model_context" in block.value
        ]
        assert len(model_contexts) == 1
        assert "signal_payload" not in model_contexts[0]

        payload = json.loads(
            (await artifacts.read_bytes("artifact_treport_signal_payload")).decode("utf-8")
        )
        model_context = model_contexts[0]["signal_payload_model_context"]
        assert model_context["canonical_payload_artifact_id"] == (
            "artifact_treport_signal_payload"
        )
        assert model_context["signal_board"][0]["ticker"] == "NVDA"
        assert len(json.dumps(model_context, ensure_ascii=False)) < len(
            json.dumps(payload, ensure_ascii=False)
        )
        signal_inputs = json.loads(
            (await artifacts.read_bytes("artifact_treport_signal_report_inputs")).decode("utf-8")
        )
        quality = json.loads(
            (await artifacts.read_bytes("artifact_treport_quality")).decode("utf-8")
        )
        report = (await artifacts.read_bytes("artifact_treport_report")).decode("utf-8")
        html = (await artifacts.read_bytes("artifact_treport_html_report")).decode("utf-8")
        telegram_alert = (await artifacts.read_bytes("artifact_treport_telegram_alert")).decode(
            "utf-8"
        )
        evaluator_review = json.loads(
            (await artifacts.read_bytes("artifact_treport_harness_evaluator_review")).decode(
                "utf-8"
            )
        )

        assert payload["signal_board"][0]["ticker"] == "NVDA"
        assert payload["signal_board"][0]["review_label"] == "focus"
        assert signal_inputs["snapshots"]["current_date"] == "2026-05-08"
        assert signal_inputs["focus_etf_id"] == "etf_focus_ai"
        assert quality["status"] == "passed"
        assert quality["summary"] == state["report_quality_summary"]
        assert evaluator_review["review_summary"]["id"] == evaluator["id"]
        assert evaluator_review["review_summary"]["details"]["verdict"] == "pass"
        assert evaluator_review["review_summary"]["details"]["uncertainty"] == {
            "level": "low",
            "reasons": [],
        }
        assert quality["summary"]["scopes"] == {
            "payload": 0,
            "markdown": 0,
            "html": 0,
            "telegram_alert": 0,
        }
        assert "Model commentary: explain the fixed signal payload." in report
        assert "<title>Signal Intelligence Report</title>" in html
        assert '<dl class="metadata-grid">' in html
        assert "<dt>As of</dt><dd>2026-05-08</dd>" in html
        assert "JavaScript\uac00 \uaebc\uc838 \uc788\uc5b4 \ud544\ud130\uc640" in html
        assert "focus / \uc911\uc810 \ubaa8\ub2c8\ud130\ub9c1" in html
        assert "Confirmed / \ud655\uc778" in html
        assert "Open / \uc5f4\uae30" in html
        assert "Analyst coverage" in html
        assert "Model commentary: explain the fixed signal payload." in html
        assert "<b>ETF 시그널 브리핑</b>" in telegram_alert
        assert "HTML artifact: <code>artifact_treport_html_report</code>" in telegram_alert
        assert "Model commentary" not in telegram_alert
        assert "중점 모니터링" in report
        assert sorted(path.name for path in artifact_root.iterdir()) == [
            "artifact_treport_changes.json",
            "artifact_treport_harness_evaluator_review.json",
            "artifact_treport_holdings.json",
            "artifact_treport_html_report.html",
            "artifact_treport_quality.json",
            "artifact_treport_report.md",
            "artifact_treport_signal_payload.json",
            "artifact_treport_signal_report_inputs.json",
            "artifact_treport_summary.json",
            "artifact_treport_telegram_alert.txt",
        ]

        reopened = SQLiteRunStore(str(sqlite_path))
        try:
            inspection = await RunInspectionService(reopened).build_snapshot("run_treport_signal")
        finally:
            await reopened.close()

        artifact_ids = {artifact.artifact_id for artifact in inspection.artifacts}
        assert inspection.run.status == "succeeded"
        assert "artifact_treport_signal_report_inputs" in artifact_ids
        assert "artifact_treport_signal_payload" in artifact_ids
        assert "artifact_treport_html_report" in artifact_ids
        assert "artifact_treport_harness_evaluator_review" in artifact_ids
        assert "artifact_treport_telegram_alert" in artifact_ids
        quality_ref = next(
            artifact
            for artifact in inspection.artifacts
            if artifact.artifact_id == "artifact_treport_quality"
        )
        report_ref = next(
            artifact
            for artifact in inspection.artifacts
            if artifact.artifact_id == "artifact_treport_report"
        )
        html_ref = next(
            artifact
            for artifact in inspection.artifacts
            if artifact.artifact_id == "artifact_treport_html_report"
        )
        telegram_ref = next(
            artifact
            for artifact in inspection.artifacts
            if artifact.artifact_id == "artifact_treport_telegram_alert"
        )
        evaluator_ref = next(
            artifact
            for artifact in inspection.artifacts
            if artifact.artifact_id == "artifact_treport_harness_evaluator_review"
        )
        assert quality_ref.metadata["report_quality_status"] == "passed"
        assert report_ref.metadata["report_quality_status"] == "passed"
        assert report_ref.metadata["report_quality_summary"] == state["report_quality_summary"]
        assert html_ref.name == "report.html"
        assert html_ref.media_type == "text/html"
        assert html_ref.metadata["capability"] == "html_research_report"
        assert html_ref.metadata["report_quality_status"] == "passed"
        assert html_ref.metadata["report_quality_summary"] == state["report_quality_summary"]
        assert telegram_ref.name == "telegram_alert.txt"
        assert telegram_ref.media_type == "text/plain"
        assert telegram_ref.metadata["capability"] == "telegram_signal_alert"
        assert telegram_ref.metadata["telegram_parse_mode"] == "HTML"
        assert telegram_ref.metadata["full_report_artifact_id"] == (
            "artifact_treport_html_report"
        )
        assert telegram_ref.metadata["report_quality_status"] == "passed"
        assert telegram_ref.metadata["report_quality_summary"] == state["report_quality_summary"]
        assert evaluator_ref.name == "harness_evaluator_review.json"
        assert evaluator_ref.media_type == "application/json"
        assert evaluator_ref.metadata["capability"] == "harness_evaluator_review"
        assert evaluator_ref.metadata["harness_evaluator_verdict"] == "pass"
        assert inspection.latest_snapshot is not None
        assert inspection.latest_snapshot.state["signal_report_inputs_artifact_id"] == (
            "artifact_treport_signal_report_inputs"
        )
        assert inspection.latest_snapshot.state["signal_payload_artifact_id"] == (
            "artifact_treport_signal_payload"
        )
        assert inspection.latest_snapshot.state["harness_evaluator_verdict"] == "pass"

    run_async(scenario())


def test_fixture_signal_report_provider_returns_domain_inputs() -> None:
    inputs = FixtureSignalReportInputProvider().load()

    assert isinstance(inputs, SignalReportInputs)
    assert inputs.snapshots.etfs
    assert inputs.evidence


def test_cached_signal_report_provider_returns_preflight_inputs_and_provenance() -> None:
    inputs = FixtureSignalReportInputProvider().load()
    provider = CachedSignalReportInputProvider(
        inputs=inputs,
        provenance={"source": "preflight"},
    )

    assert provider.load() is inputs
    assert provider.provenance == {"source": "preflight"}


def test_signal_report_workflow_persists_provider_provenance_artifact(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        sqlite_path = tmp_path / "treport.sqlite3"
        artifact_root = tmp_path / "artifacts"
        store = SQLiteRunStore(str(sqlite_path))
        artifacts = LocalArtifactManager(artifact_root)
        provider = CachedSignalReportInputProvider(
            inputs=FixtureSignalReportInputProvider().load(),
            provenance={
                "schema_version": "agent_treport.operational_holdings.provenance.v1",
                "selected_current_date": "2026-05-11",
            },
        )

        result = await run_signal_report(
            run_id="run_treport_signal_provenance",
            store=store,
            context_manager=ContextManager(store=store),
            artifact_manager=artifacts,
            model_client=FakeModelClient(
                [
                    ModelResponse(
                        message=Message(
                            role="assistant",
                            content=(TextBlock(text="safe provenance commentary"),),
                        )
                    )
                ]
            ),
            provider=provider,
        )
        await store.close()

        state = result.output["state"]
        provenance = json.loads(
            (
                await artifacts.read_bytes(
                    "artifact_treport_operational_holdings_provenance"
                )
            ).decode("utf-8")
        )

        assert result.status == "succeeded"
        assert state["operational_holdings_provenance_artifact_id"] == (
            "artifact_treport_operational_holdings_provenance"
        )
        assert provenance["schema_version"] == (
            "agent_treport.operational_holdings.provenance.v1"
        )
        assert provenance["selected_current_date"] == "2026-05-11"

    run_async(scenario())


def test_signal_report_workflow_projects_provider_provenance_into_saved_payload(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        sqlite_path = tmp_path / "treport.sqlite3"
        artifact_root = tmp_path / "artifacts"
        store = SQLiteRunStore(str(sqlite_path))
        artifacts = LocalArtifactManager(artifact_root)
        provider = CachedSignalReportInputProvider(
            inputs=FixtureSignalReportInputProvider().load(),
            provenance={
                "sync_metadata_available": True,
                "sync_quality": {
                    "schema_version": "agent_treport.operational_holdings.sync_quality.v1",
                    "status": "warning",
                    "metrics": {
                        "cash_derivation_failure_ratio": 0.06,
                        "fit_failure_ratio": None,
                        "unusable_cash_weight_ratio": 0.02,
                        "ticker_mapping_coverage_ratio": 0.79,
                        "missing_source_date_count": 0,
                        "skipped_missing_security_id_count": 3,
                    },
                    "warnings": [
                        {
                            "code": "low_ticker_mapping_coverage",
                            "message": "Ticker mapping coverage was below the warning threshold.",
                            "metric": "ticker_mapping_coverage_ratio",
                            "value": 0.79,
                            "threshold": 0.8,
                        }
                    ],
                    "risk_failures": [],
                },
            },
        )

        result = await run_signal_report(
            run_id="run_treport_signal_projected_provenance",
            store=store,
            context_manager=ContextManager(store=store),
            artifact_manager=artifacts,
            model_client=FakeModelClient(
                [
                    ModelResponse(
                        message=Message(
                            role="assistant",
                            content=(TextBlock(text="safe projected provenance commentary"),),
                        )
                    )
                ]
            ),
            provider=provider,
        )
        await store.close()

        payload = json.loads(
            (await artifacts.read_bytes("artifact_treport_signal_payload")).decode("utf-8")
        )
        operational_issues = [
            issue
            for issue in payload["data_quality"]["issues"]
            if issue["code"] == "operational_low_ticker_mapping_coverage"
        ]

        assert result.status == "succeeded"
        assert operational_issues == [
            {
                "code": "operational_low_ticker_mapping_coverage",
                "severity": "medium",
                "scope": "operational_holdings",
                "message": "Ticker mapping coverage was below the warning threshold.",
            }
        ]
        assert "operational_ticker_mapping_coverage_ratio=0.79" in (
            payload["data_quality"]["coverage_notes"]
        )

    run_async(scenario())


def test_signal_report_workflow_preserves_payload_when_model_commentary_fails(
    tmp_path: Path,
) -> None:
    class FailingModelClient:
        async def complete(self, request: ModelRequest) -> ModelResponse:
            _ = request
            raise RuntimeError("model unavailable")

    async def scenario() -> None:
        sqlite_path = tmp_path / "treport.sqlite3"
        artifact_root = tmp_path / "artifacts"
        store = SQLiteRunStore(str(sqlite_path))
        artifacts = LocalArtifactManager(artifact_root)

        result = await run_signal_report(
            run_id="run_treport_signal_model_failure",
            store=store,
            context_manager=ContextManager(store=store),
            artifact_manager=artifacts,
            model_client=FailingModelClient(),
        )
        await store.close()

        assert result.status == "failed"
        assert result.output["reason"] == "model_analysis_failed"
        assert result.output["state"]["signal_payload_artifact_id"] == (
            "artifact_treport_signal_payload"
        )
        assert result.output["runtime_failure"]["failed_step"] == "analyze-data"
        payload = json.loads(
            (await artifacts.read_bytes("artifact_treport_signal_payload")).decode("utf-8")
        )
        assert payload["signal_board"][0]["ticker"] == "NVDA"

        reopened = SQLiteRunStore(str(sqlite_path))
        try:
            events = await reopened.list_events("run_treport_signal_model_failure")
            inspection = await RunInspectionService(reopened).build_snapshot(
                "run_treport_signal_model_failure"
            )
        finally:
            await reopened.close()

        assert "artifact_treport_report" not in {
            artifact.artifact_id for artifact in inspection.artifacts
        }
        assert any(
            event.type == "agent_treport.failure_classified"
            and event.payload["reason"] == "model_analysis_failed"
            and event.payload["failed_step"] == "analyze-data"
            for event in events
        )

    run_async(scenario())


def test_signal_report_workflow_omits_unsafe_model_commentary_and_records_policy(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        sqlite_path = tmp_path / "treport.sqlite3"
        artifact_root = tmp_path / "artifacts"
        store = SQLiteRunStore(str(sqlite_path))
        artifacts = LocalArtifactManager(artifact_root)
        model = FakeModelClient(
            [
                ModelResponse(
                    message=Message(
                        role="assistant",
                        content=(
                            TextBlock(
                                text="BUY rating with a target price. score should be adjusted."
                            ),
                        ),
                    )
                )
            ]
        )

        result = await run_signal_report(
            run_id="run_treport_signal_unsafe_commentary",
            store=store,
            context_manager=ContextManager(store=store),
            artifact_manager=artifacts,
            model_client=model,
        )
        await store.close()

        state = result.output["state"]
        report = (await artifacts.read_bytes("artifact_treport_report")).decode("utf-8")
        reopened = SQLiteRunStore(str(sqlite_path))
        try:
            inspection = await RunInspectionService(reopened).build_snapshot(
                "run_treport_signal_unsafe_commentary"
            )
        finally:
            await reopened.close()
        report_ref = next(
            artifact
            for artifact in inspection.artifacts
            if artifact.artifact_id == "artifact_treport_report"
        )

        assert result.status == "succeeded"
        assert state["report_quality_status"] == "passed"
        assert state["report_quality_summary"]["blocking"] is False
        assert state["model_commentary_policy"] == {
            "status": "omitted",
            "reason": "prohibited_or_canonical_conflict",
        }
        assert report_ref.metadata["model_commentary_policy"] == state["model_commentary_policy"]
        assert "모델 코멘터리는 report commentary policy 위반으로 생략되었습니다." in report
        assert "BUY rating" not in report
        assert "target price" not in report

    run_async(scenario())


def test_signal_report_workflow_blocks_unsafe_rendered_markdown_with_quality_evidence(
    tmp_path: Path,
) -> None:
    class UnsafeRenderer:
        def render(self, *, payload, model_commentary: str | None = None) -> str:
            markdown = MarkdownSignalReportRenderer().render(
                payload=payload,
                model_commentary=model_commentary,
            )
            return f"{markdown}\n\nBUY rating with a target price of 500."

    async def scenario() -> None:
        sqlite_path = tmp_path / "treport.sqlite3"
        artifact_root = tmp_path / "artifacts"
        store = SQLiteRunStore(str(sqlite_path))
        artifacts = LocalArtifactManager(artifact_root)

        result = await run_signal_report(
            run_id="run_treport_signal_quality_failure",
            store=store,
            context_manager=ContextManager(store=store),
            artifact_manager=artifacts,
            model_client=FakeModelClient(
                [
                    ModelResponse(
                        message=Message(
                            role="assistant",
                            content=(TextBlock(text="safe commentary"),),
                        )
                    )
                ]
            ),
            markdown_renderer=UnsafeRenderer(),
        )
        await store.close()

        assert result.status == "failed"
        assert result.output["reason"] == "report_quality_failed"
        assert result.output["state"]["report_quality_artifact_id"] == (
            "artifact_treport_quality"
        )
        assert result.output["state"]["report_quality_status"] == "failed"
        assert result.output["state"]["report_quality_summary"]["blocking"] is True
        assert "report_artifact_id" not in result.output["state"]
        assert result.output["report_quality"]["status"] == "failed"
        assert any(
            violation["code"] == "prohibited_investment_language"
            for violation in result.output["report_quality"]["violations"]
        )
        assert (artifact_root / "artifact_treport_quality.json").is_file()
        assert not (artifact_root / "artifact_treport_report.md").exists()

        quality = json.loads(
            (await artifacts.read_bytes("artifact_treport_quality")).decode("utf-8")
        )
        assert quality["status"] == "failed"
        assert any(
            violation["code"] == "prohibited_investment_language"
            for violation in quality["violations"]
        )

        reopened = SQLiteRunStore(str(sqlite_path))
        try:
            inspection = await RunInspectionService(reopened).build_snapshot(
                "run_treport_signal_quality_failure"
            )
        finally:
            await reopened.close()

        artifact_ids = {artifact.artifact_id for artifact in inspection.artifacts}
        assert "artifact_treport_quality" in artifact_ids
        assert "artifact_treport_report" not in artifact_ids
        quality_items = [
            item
            for item in inspection.context.items
            if item.metadata.get("artifact_id") == "artifact_treport_quality"
        ]
        assert len(quality_items) == 1
        assert quality_items[0].visibility == "internal"
        assert "report_quality_summary" not in quality_items[0].metadata

    run_async(scenario())


def test_signal_report_workflow_blocks_missing_target_section_with_quality_evidence(
    tmp_path: Path,
) -> None:
    class MissingTargetRenderer:
        def render(self, *, payload, model_commentary: str | None = None) -> str:
            markdown = MarkdownSignalReportRenderer().render(
                payload=payload,
                model_commentary=model_commentary,
            )
            return markdown.replace("## Evidence Ledger", "## Evidence Notes")

    async def scenario() -> None:
        sqlite_path = tmp_path / "treport.sqlite3"
        artifact_root = tmp_path / "artifacts"
        store = SQLiteRunStore(str(sqlite_path))
        artifacts = LocalArtifactManager(artifact_root)

        result = await run_signal_report(
            run_id="run_treport_signal_missing_target_section",
            store=store,
            context_manager=ContextManager(store=store),
            artifact_manager=artifacts,
            model_client=FakeModelClient(
                [
                    ModelResponse(
                        message=Message(
                            role="assistant",
                            content=(TextBlock(text="safe commentary"),),
                        )
                    )
                ]
            ),
            markdown_renderer=MissingTargetRenderer(),
        )
        await store.close()

        assert result.status == "failed"
        assert result.output["reason"] == "report_quality_failed"
        assert result.output["state"]["report_quality_status"] == "failed"
        assert result.output["state"]["report_quality_summary"]["blocking"] is True
        assert "report_artifact_id" not in result.output["state"]
        assert any(
            violation["code"] == "missing_markdown_section"
            and violation["details"] == {"required_heading": "## Evidence Ledger"}
            for violation in result.output["report_quality"]["violations"]
        )
        assert (artifact_root / "artifact_treport_quality.json").is_file()
        assert not (artifact_root / "artifact_treport_report.md").exists()

    run_async(scenario())


def test_signal_report_workflow_blocks_invalid_html_with_quality_evidence(
    tmp_path: Path,
) -> None:
    class MissingHTMLSectionRenderer:
        def render(self, *, payload, model_commentary: str | None = None) -> str:
            html = HTMLResearchReportRenderer().render(
                payload=payload,
                model_commentary=model_commentary,
            )
            return html.replace('id="evidence-ledger"', 'id="evidence-notes"')

    async def scenario() -> None:
        sqlite_path = tmp_path / "treport.sqlite3"
        artifact_root = tmp_path / "artifacts"
        store = SQLiteRunStore(str(sqlite_path))
        artifacts = LocalArtifactManager(artifact_root)

        result = await run_signal_report(
            run_id="run_treport_signal_html_quality_failure",
            store=store,
            context_manager=ContextManager(store=store),
            artifact_manager=artifacts,
            model_client=FakeModelClient(
                [
                    ModelResponse(
                        message=Message(
                            role="assistant",
                            content=(TextBlock(text="safe commentary"),),
                        )
                    )
                ]
            ),
            html_renderer=MissingHTMLSectionRenderer(),
        )
        await store.close()

        assert result.status == "failed"
        assert result.output["reason"] == "report_quality_failed"
        assert result.output["state"]["report_quality_status"] == "failed"
        assert result.output["state"]["report_quality_summary"]["blocking"] is True
        assert "report_artifact_id" not in result.output["state"]
        assert "html_report_artifact_id" not in result.output["state"]
        assert any(
            violation["code"] == "missing_html_section"
            and violation["scope"] == "html"
            and violation["details"] == {"required_section_id": "evidence-ledger"}
            for violation in result.output["report_quality"]["violations"]
        )
        assert (artifact_root / "artifact_treport_quality.json").is_file()
        assert not (artifact_root / "artifact_treport_report.md").exists()
        assert not (artifact_root / "artifact_treport_html_report.html").exists()

    run_async(scenario())


def test_signal_report_workflow_blocks_invalid_telegram_alert_with_quality_evidence(
    tmp_path: Path,
) -> None:
    class UnsafeTelegramAlertRenderer:
        def render(self, *, payload, full_report_reference: str) -> str:
            return (
                TelegramSignalAlertRenderer().render(
                    payload=payload,
                    full_report_reference=full_report_reference,
                )
                + "\nBUY rating"
            )

    async def scenario() -> None:
        artifact_root = tmp_path / "artifacts"
        store = SQLiteRunStore(str(tmp_path / "treport.sqlite3"))
        artifacts = LocalArtifactManager(artifact_root)

        result = await run_signal_report(
            run_id="run_treport_signal_telegram_quality_failure",
            store=store,
            context_manager=ContextManager(store=store),
            artifact_manager=artifacts,
            model_client=FakeModelClient(
                [
                    ModelResponse(
                        message=Message(
                            role="assistant",
                            content=(TextBlock(text="safe commentary"),),
                        )
                    )
                ]
            ),
            telegram_alert_renderer=UnsafeTelegramAlertRenderer(),
        )
        await store.close()

        assert result.status == "failed"
        assert result.output["reason"] == "report_quality_failed"
        assert result.output["state"]["report_quality_status"] == "failed"
        assert result.output["state"]["report_quality_summary"]["blocking"] is True
        assert "report_artifact_id" not in result.output["state"]
        assert "html_report_artifact_id" not in result.output["state"]
        assert "telegram_alert_artifact_id" not in result.output["state"]
        assert any(
            violation["code"] == "prohibited_investment_language"
            and violation["scope"] == "telegram_alert"
            for violation in result.output["report_quality"]["violations"]
        )
        assert (artifact_root / "artifact_treport_quality.json").is_file()
        assert not (artifact_root / "artifact_treport_report.md").exists()
        assert not (artifact_root / "artifact_treport_html_report.html").exists()
        assert not (artifact_root / "artifact_treport_telegram_alert.txt").exists()

    run_async(scenario())


def test_signal_report_workflow_hides_report_ids_when_html_storage_fails(
    tmp_path: Path,
) -> None:
    class FailingHTMLArtifactManager(LocalArtifactManager):
        async def store_bytes(self, **kwargs):
            if kwargs.get("artifact_id") == "artifact_treport_html_report":
                raise OSError("html storage failed")
            return await super().store_bytes(**kwargs)

    async def scenario() -> None:
        artifact_root = tmp_path / "artifacts"
        store = SQLiteRunStore(str(tmp_path / "treport.sqlite3"))
        artifacts = FailingHTMLArtifactManager(artifact_root)

        result = await run_signal_report(
            run_id="run_treport_signal_html_storage_failure",
            store=store,
            context_manager=ContextManager(store=store),
            artifact_manager=artifacts,
            model_client=FakeModelClient(
                [
                    ModelResponse(
                        message=Message(
                            role="assistant",
                            content=(TextBlock(text="safe commentary"),),
                        )
                    )
                ]
            ),
        )
        await store.close()

        assert result.status == "failed"
        assert result.output["reason"] == "artifact_persistence_failed"
        assert "report_artifact_id" not in result.output["state"]
        assert "html_report_artifact_id" not in result.output["state"]
        assert "telegram_alert_artifact_id" not in result.output["state"]
        assert (artifact_root / "artifact_treport_quality.json").is_file()
        assert (artifact_root / "artifact_treport_report.md").is_file()
        assert not (artifact_root / "artifact_treport_html_report.html").exists()
        assert not (artifact_root / "artifact_treport_telegram_alert.txt").exists()

    run_async(scenario())


def test_signal_report_workflow_hides_rendered_ids_when_telegram_storage_fails(
    tmp_path: Path,
) -> None:
    class FailingTelegramArtifactManager(LocalArtifactManager):
        async def store_bytes(self, **kwargs):
            if kwargs.get("artifact_id") == "artifact_treport_telegram_alert":
                raise OSError("telegram storage failed")
            return await super().store_bytes(**kwargs)

    async def scenario() -> None:
        artifact_root = tmp_path / "artifacts"
        store = SQLiteRunStore(str(tmp_path / "treport.sqlite3"))
        artifacts = FailingTelegramArtifactManager(artifact_root)

        result = await run_signal_report(
            run_id="run_treport_signal_telegram_storage_failure",
            store=store,
            context_manager=ContextManager(store=store),
            artifact_manager=artifacts,
            model_client=FakeModelClient(
                [
                    ModelResponse(
                        message=Message(
                            role="assistant",
                            content=(TextBlock(text="safe commentary"),),
                        )
                    )
                ]
            ),
        )
        await store.close()

        assert result.status == "failed"
        assert result.output["reason"] == "artifact_persistence_failed"
        assert result.output["state"]["report_quality_artifact_id"] == (
            "artifact_treport_quality"
        )
        assert "report_artifact_id" not in result.output["state"]
        assert "html_report_artifact_id" not in result.output["state"]
        assert "telegram_alert_artifact_id" not in result.output["state"]
        assert (artifact_root / "artifact_treport_quality.json").is_file()
        assert (artifact_root / "artifact_treport_report.md").is_file()
        assert (artifact_root / "artifact_treport_html_report.html").is_file()
        assert not (artifact_root / "artifact_treport_telegram_alert.txt").exists()

    run_async(scenario())


def test_signal_report_workflow_classifies_quality_gate_evaluation_exception(
    tmp_path: Path,
) -> None:
    class RaisingQualityGate(ReportQualityGate):
        def evaluate(
            self,
            *,
            payload,
            markdown: str,
            html: str | None = None,
            telegram_alert: str | None = None,
        ):
            _ = payload
            _ = markdown
            _ = html
            _ = telegram_alert
            raise RuntimeError("unsafe original text should not leak")

    async def scenario() -> None:
        store = SQLiteRunStore(str(tmp_path / "treport.sqlite3"))
        artifacts = LocalArtifactManager(tmp_path / "artifacts")

        result = await run_signal_report(
            run_id="run_treport_signal_quality_exception",
            store=store,
            context_manager=ContextManager(store=store),
            artifact_manager=artifacts,
            model_client=FakeModelClient(
                [
                    ModelResponse(
                        message=Message(
                            role="assistant",
                            content=(TextBlock(text="safe commentary"),),
                        )
                    )
                ]
            ),
            quality_gate=RaisingQualityGate(),
        )
        await store.close()

        assert result.status == "failed"
        assert result.output["reason"] == "report_quality_failed"
        assert result.output["error"] == {
            "code": "report_quality_evaluation_failed",
            "type": "ReportQualityEvaluationFailed",
            "message": "report quality evaluation failed",
        }
        assert "report_quality" not in result.output
        assert "unsafe original text" not in json.dumps(result.output)

    run_async(scenario())


def test_signal_report_workflow_classifies_provider_failure_as_data_preparation_failed(
    tmp_path: Path,
) -> None:
    class FailingProvider:
        def load(self):
            raise RuntimeError("credential=secret")

    async def scenario() -> None:
        store = SQLiteRunStore(str(tmp_path / "treport.sqlite3"))
        artifacts = LocalArtifactManager(tmp_path / "artifacts")

        result = await run_signal_report(
            run_id="run_treport_signal_provider_failure",
            store=store,
            context_manager=ContextManager(store=store),
            artifact_manager=artifacts,
            model_client=FakeModelClient([]),
            provider=FailingProvider(),
        )
        events = await store.list_events("run_treport_signal_provider_failure")
        await store.close()

        assert result.status == "failed"
        assert result.output["reason"] == "data_preparation_failed"
        assert result.output["state"] == {}
        assert result.output["runtime_failure"]["failed_step"] == "collect-data"
        assert result.output["runtime_failure"]["step_output"]["error"]["message"] == (
            "data preparation failed"
        )
        assert any(
            event.type == "agent_treport.failure_classified"
            and event.payload["reason"] == "data_preparation_failed"
            and event.payload["failed_step"] == "collect-data"
            for event in events
        )

    run_async(scenario())


def test_signal_report_workflow_classifies_renderer_failure_as_report_render_failed(
    tmp_path: Path,
) -> None:
    class FailingRenderer:
        def render(self, *, payload, model_commentary: str | None = None) -> str:
            _ = payload
            _ = model_commentary
            raise RuntimeError("raw_payload should not persist")

    async def scenario() -> None:
        store = SQLiteRunStore(str(tmp_path / "treport.sqlite3"))
        artifacts = LocalArtifactManager(tmp_path / "artifacts")

        result = await run_signal_report(
            run_id="run_treport_signal_renderer_failure",
            store=store,
            context_manager=ContextManager(store=store),
            artifact_manager=artifacts,
            model_client=FakeModelClient(
                [
                    ModelResponse(
                        message=Message(
                            role="assistant",
                            content=(TextBlock(text="analysis before render"),),
                        )
                    )
                ]
            ),
            markdown_renderer=FailingRenderer(),
        )
        events = await store.list_events("run_treport_signal_renderer_failure")
        await store.close()

        assert result.status == "failed"
        assert result.output["reason"] == "report_render_failed"
        assert result.output["state"]["signal_payload_artifact_id"] == (
            "artifact_treport_signal_payload"
        )
        assert result.output["runtime_failure"]["failed_step"] == "render-report"
        assert result.output["runtime_failure"]["step_output"]["error"]["message"] == (
            "report render failed"
        )
        assert any(event.type == "agent_treport.failure_classified" for event in events)

    run_async(scenario())


def test_signal_report_workflow_classifies_telegram_renderer_failure_as_report_render_failed(
    tmp_path: Path,
) -> None:
    class FailingTelegramRenderer:
        def render(self, *, payload, full_report_reference: str) -> str:
            _ = payload
            _ = full_report_reference
            raise RuntimeError("telegram_raw_payload should not persist")

    async def scenario() -> None:
        store = SQLiteRunStore(str(tmp_path / "treport.sqlite3"))
        artifacts = LocalArtifactManager(tmp_path / "artifacts")

        result = await run_signal_report(
            run_id="run_treport_signal_telegram_renderer_failure",
            store=store,
            context_manager=ContextManager(store=store),
            artifact_manager=artifacts,
            model_client=FakeModelClient(
                [
                    ModelResponse(
                        message=Message(
                            role="assistant",
                            content=(TextBlock(text="analysis before render"),),
                        )
                    )
                ]
            ),
            telegram_alert_renderer=FailingTelegramRenderer(),
        )
        events = await store.list_events("run_treport_signal_telegram_renderer_failure")
        await store.close()

        assert result.status == "failed"
        assert result.output["reason"] == "report_render_failed"
        assert result.output["runtime_failure"]["failed_step"] == "render-report"
        assert result.output["runtime_failure"]["step_output"]["error"]["message"] == (
            "report render failed"
        )
        assert any(event.type == "agent_treport.failure_classified" for event in events)

    run_async(scenario())


def test_signal_report_workflow_classifies_artifact_failure_as_artifact_persistence_failed(
    tmp_path: Path,
) -> None:
    class FailingArtifactManager(LocalArtifactManager):
        async def store_bytes(self, **_kwargs):
            raise OSError("token should not persist")

    async def scenario() -> None:
        store = SQLiteRunStore(str(tmp_path / "treport.sqlite3"))

        result = await run_signal_report(
            run_id="run_treport_signal_artifact_failure",
            store=store,
            context_manager=ContextManager(store=store),
            artifact_manager=FailingArtifactManager(tmp_path / "artifacts"),
            model_client=FakeModelClient([]),
        )
        await store.close()

        assert result.status == "failed"
        assert result.output["reason"] == "artifact_persistence_failed"
        assert result.output["state"] == {}
        assert result.output["runtime_failure"]["failed_step"] == "collect-data"
        assert result.output["runtime_failure"]["step_output"]["error"]["message"] == (
            "artifact persistence failed"
        )

    run_async(scenario())
