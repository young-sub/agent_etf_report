from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
from agent_pack.artifacts import LocalArtifactManager
from agent_pack.context import ContextManager
from agent_pack.models import (
    ArtifactRef,
    ContextView,
    Message,
    ModelResponse,
    Run,
    RunEvent,
    RunInput,
    RunSnapshot,
    TextBlock,
)
from agent_pack.models_client import FakeModelClient
from agent_pack.store import SQLiteRunStore
from agent_pack.trace_export import TraceExportEvidenceSummary, build_trace_export_record

from agent_treport.signal_report.durability_substrate_evidence import (
    build_durability_substrate_review_summary,
    build_durability_substrate_review_summary_from_trace_record,
)
from agent_treport.signal_report.evaluator_review import (
    build_signal_report_evaluator_review_summary,
)
from agent_treport.signal_report.external_delivery_review import (
    build_external_delivery_review_summary,
)
from agent_treport.workflows.signal_report import (
    CachedSignalReportInputProvider,
    FixtureSignalReportInputProvider,
    run_signal_report,
)


def run_async(awaitable):
    return asyncio.run(awaitable)


def test_agent_treport_signal_report_exports_generic_trace_record(tmp_path: Path) -> None:
    async def scenario() -> None:
        sqlite_path = tmp_path / "treport.sqlite3"
        artifact_root = tmp_path / "artifacts"
        store = SQLiteRunStore(str(sqlite_path))
        provider = CachedSignalReportInputProvider(
            inputs=FixtureSignalReportInputProvider().load(),
            provenance={
                "schema_version": "agent_treport.operational_holdings.provenance.v1",
                "selected_current_date": "2026-05-11",
            },
        )

        result = await run_signal_report(
            run_id="run_treport_trace_export",
            store=store,
            context_manager=ContextManager(store=store),
            artifact_manager=LocalArtifactManager(artifact_root),
            model_client=FakeModelClient(
                [
                    ModelResponse(
                        message=Message(
                            role="assistant",
                            content=(TextBlock(text="trace export commentary"),),
                        )
                    )
                ]
            ),
            provider=provider,
        )

        record = await build_trace_export_record(
            store=store,
            run_id="run_treport_trace_export",
            source={"source": "agent_treport_signal_report_fixture"},
        )
        await store.close()

        artifact_ids = {artifact.artifact_id for artifact in record.artifacts}
        event_types = {event.type for event in record.events}

        assert result.status == "succeeded"
        assert record.run.id == "run_treport_trace_export"
        assert record.run.status == "succeeded"
        assert record.summary.event_count >= 1
        assert record.summary.context_view_count >= 1
        assert "run_succeeded" in event_types
        assert "artifact_treport_signal_payload" in artifact_ids
        assert "artifact_treport_quality" in artifact_ids
        assert "artifact_treport_harness_evaluator_review" in artifact_ids
        summary_by_id = {summary.id: summary for summary in record.evidence_summaries}
        quality = summary_by_id["quality.report_quality"]
        provenance = summary_by_id["provenance.operational_holdings"]
        final = summary_by_id["final.run_result"]
        assert len(record.review_summaries) == 1
        evaluator = record.review_summaries[0]
        assert evaluator.id == "review.signal_report_evaluator"
        assert evaluator.operation_kind == "evaluator_harness_review"
        assert evaluator.review_surface == "signal_report_behavior"
        assert evaluator.review_status == "passed"
        assert evaluator.blocker_count == 0
        assert evaluator.evidence_ref_count == 5
        assert evaluator.safe_artifact_refs == (
            "signal_payload.json",
            "report.md",
            "report.html",
            "telegram_alert.txt",
            "quality.json",
        )
        assert evaluator.details["verdict"] == "pass"
        assert evaluator.details["checked_artifact_ids"] == (
            "artifact_treport_signal_payload",
            "artifact_treport_report",
            "artifact_treport_html_report",
            "artifact_treport_telegram_alert",
            "artifact_treport_quality",
        )
        assert evaluator.details["uncertainty"] == {
            "level": "low",
            "reasons": (),
        }
        stage_ids = {
            summary.id
            for summary in record.evidence_summaries
            if summary.kind == "stage_io"
        }
        assert record.latest_snapshot is not None
        assert record.latest_snapshot.state["report_quality_status"] == "passed"
        assert record.latest_snapshot.state["harness_evaluator_artifact_id"] == (
            "artifact_treport_harness_evaluator_review"
        )
        assert record.latest_snapshot.state["harness_evaluator_verdict"] == "pass"
        assert quality.status == "passed"
        assert quality.summary["highest_severity"] == "none"
        assert quality.inputs["evaluated_artifact_ids"] == (
            "artifact_treport_signal_payload",
        )
        assert quality.outputs["quality_artifact_id"] == "artifact_treport_quality"
        assert provenance.outputs["artifact_ids"] == (
            "artifact_treport_operational_holdings_provenance",
        )
        assert final.status == "succeeded"
        assert "artifact_treport_report" in final.outputs["artifact_ids"]
        assert {
            "stage.collect-data",
            "stage.analyze-data",
            "stage.render-report",
        }.issubset(stage_ids)
        json.dumps(record.model_dump(mode="json"), ensure_ascii=False)

    run_async(scenario())


def test_signal_report_evaluator_review_summary_supports_pass_fail_and_uncertain() -> None:
    base_state = {
        "signal_payload_artifact_id": "artifact_treport_signal_payload",
        "report_artifact_id": "artifact_treport_report",
        "html_report_artifact_id": "artifact_treport_html_report",
        "telegram_alert_artifact_id": "artifact_treport_telegram_alert",
        "report_quality_artifact_id": "artifact_treport_quality",
    }

    passed = build_signal_report_evaluator_review_summary(
        run_id="run_evaluator_pass",
        state={
            **base_state,
            "report_quality_status": "passed",
            "report_quality_summary": {
                "error_count": 0,
                "warning_count": 0,
                "scopes": {
                    "payload": 0,
                    "markdown": 0,
                    "html": 0,
                    "telegram_alert": 0,
                },
                "blocking": False,
            },
        },
    )
    failed = build_signal_report_evaluator_review_summary(
        run_id="run_evaluator_fail",
        state={
            **base_state,
            "report_quality_status": "failed",
            "report_quality_summary": {
                "error_count": 1,
                "warning_count": 0,
                "scopes": {
                    "payload": 0,
                    "markdown": 1,
                    "html": 0,
                    "telegram_alert": 0,
                },
                "blocking": True,
            },
        },
    )
    uncertain = build_signal_report_evaluator_review_summary(
        run_id="run_evaluator_uncertain",
        state={
            "signal_payload_artifact_id": "artifact_treport_signal_payload",
            "report_artifact_id": "artifact_treport_report",
        },
    )

    assert passed["review_status"] == "passed"
    assert passed["closure_status"] == "evidence_recorded"
    assert passed["details"]["verdict"] == "pass"
    assert passed["details"]["rationale"] == (
        "All required SignalReportWorkflow artifacts are present and "
        "ReportQualityGate evidence is non-blocking."
    )
    assert passed["details"]["uncertainty"] == {"level": "low", "reasons": []}
    assert failed["review_status"] == "failed"
    assert failed["closure_status"] == "blocked"
    assert failed["blocker_count"] == 1
    assert failed["details"]["verdict"] == "fail"
    assert "report_quality_failed" in failed["details"]["reason_codes"]
    assert uncertain["review_status"] == "uncertain"
    assert uncertain["closure_status"] == "evidence_incomplete"
    assert uncertain["blocker_count"] == 0
    assert uncertain["details"]["verdict"] == "uncertain"
    assert set(uncertain["details"]["reason_codes"]) >= {
        "missing_report_quality_status",
        "missing_report_quality_summary",
        "missing_html_report_artifact_id",
        "missing_telegram_alert_artifact_id",
        "missing_report_quality_artifact_id",
    }


def test_external_delivery_review_summary_projects_closure_met_without_sensitive_values() -> None:
    delivery_summary = {
        "schema_version": "agent_treport.telegram_delivery_summary.v1",
        "latest_delivery_status": "duplicate_blocked",
        "run_id": "run_delivery_review",
        "telegram_alert_artifact_id": "artifact_treport_telegram_alert",
        "target_alias": "default",
        "message_fingerprint": "sha256:message",
        "approval": {"status": "approved", "valid": True},
        "live": True,
        "receipt_paths": [
            "telegram_delivery_receipts/sent.json",
            "telegram_delivery_receipts/duplicate.json",
        ],
        "delivery_summary_path": "telegram_delivery_summary.json",
        "message_text": "raw body must not leak",
    }
    daily_publish_closure = {
        "schema_version": "agent_treport.daily_publish_closure.v1",
        "closure_status": "closure_met",
        "closure_met": True,
        "run_id": "run_delivery_review",
        "telegram_alert_artifact_id": "artifact_treport_telegram_alert",
        "target_alias": "default",
        "evidence_checks": {
            "pre_publish_user_ready": "passed",
            "live_sent_receipt": "passed",
            "duplicate_blocked": "passed",
            "identity_consistency": "passed",
            "validation_passed": "passed",
        },
        "receipt_summary": {
            "matching_sent_receipt_count": 1,
            "matching_duplicate_blocked_receipt_count": 1,
            "selected_sent_receipt_path": "telegram_delivery_receipts/sent.json",
            "selected_duplicate_blocked_receipt_path": (
                "telegram_delivery_receipts/duplicate.json"
            ),
        },
        "warnings": [],
        "limitations": [],
        "source_files": [
            "pre_publish_handoff.json",
            "telegram_delivery_summary.json",
            "telegram_delivery_receipts/sent.json",
            "telegram_delivery_receipts/duplicate.json",
        ],
        "message_text": "raw body must not leak",
    }

    summary = build_external_delivery_review_summary(
        delivery_summary=delivery_summary,
        daily_publish_closure=daily_publish_closure,
        subject_id="artifact_treport_telegram_alert",
    )

    assert summary["id"] == "review.external_delivery"
    assert summary["workflow"] == "agent_treport.signal_report"
    assert summary["run_id"] == "run_delivery_review"
    assert summary["subject_id"] == "artifact_treport_telegram_alert"
    assert summary["operation_kind"] == "external_delivery"
    assert summary["review_surface"] == "delivery_closure"
    assert summary["review_status"] == "passed"
    assert summary["approval_status"] == "approved"
    assert summary["permission_status"] == "not_applicable"
    assert summary["delivery_status"] == "duplicate_blocked"
    assert summary["closure_status"] == "closure_met"
    assert summary["blocker_count"] == 0
    assert summary["evidence_ref_count"] == 4
    assert summary["safe_artifact_refs"] == [
        "daily_publish_closure.json",
        "telegram_delivery_summary.json",
        "telegram_delivery_receipts/sent.json",
        "telegram_delivery_receipts/duplicate.json",
    ]
    assert summary["schema_version"] == "agent_pack.review_summary.v1"
    assert summary["projector_version"] == "agent_treport.external_delivery_review.v1"
    assert summary["source_fingerprint"].startswith("sha256:")
    assert summary["details"]["live_sent_receipt"] is True
    assert summary["details"]["duplicate_blocked"] is True
    assert "raw body must not leak" not in json.dumps(summary, ensure_ascii=False)


def test_durability_substrate_review_summary_projects_safe_evidence_matrix() -> None:
    summary = build_durability_substrate_review_summary(
        run_id="run_durability_matrix",
        subject_id="agent_treport.signal_report",
        evidence_surfaces=[
            {
                "surface_id": "run_store.sqlite_snapshot",
                "evidence_kind": "stored_runtime_state",
                "status": "supported",
                "current_claim": "run snapshots reopen through SQLiteRunStore",
                "preserves_agent_pack_evidence": True,
                "artifact_refs": [
                    "runtime.sqlite3",
                    "C:/Users/YS/secret/runtime.sqlite3",
                    "https://provider.example/raw",
                ],
                "raw_provider_payload": {"secret": "must not leak"},
                "absolute_path": "C:/Users/YS/secret/runtime.sqlite3",
            },
            {
                "surface_id": "trace_export.review_surfaces",
                "evidence_kind": "review_projection",
                "status": "supported",
                "current_claim": "review summaries export to MLflow artifacts",
                "preserves_agent_pack_evidence": True,
                "artifact_refs": [
                    "review/review_surfaces.json",
                    "file:///tmp/raw.json",
                ],
            },
            {
                "surface_id": "unsafe.claim",
                "evidence_kind": "path_safety",
                "status": "supported",
                "current_claim": "https://provider.example/raw should not leak",
                "preserves_agent_pack_evidence": True,
            },
        ],
        unsupported_production_claims=[
            "crash_restart_resume",
            "duplicate_worker_locking",
        ],
    )

    assert summary["id"] == "review.durability_substrate"
    assert summary["workflow"] == "agent_treport.signal_report"
    assert summary["run_id"] == "run_durability_matrix"
    assert summary["subject_id"] == "agent_treport.signal_report"
    assert summary["operation_kind"] == "runtime_evidence"
    assert summary["review_surface"] == "durability_substrate"
    assert summary["review_status"] == "passed"
    assert summary["approval_status"] == "not_applicable"
    assert summary["permission_status"] == "not_applicable"
    assert summary["delivery_status"] == "not_applicable"
    assert summary["closure_status"] == "evidence_recorded"
    assert summary["blocker_count"] == 0
    assert summary["evidence_ref_count"] == 2
    assert summary["safe_artifact_refs"] == [
        "runtime.sqlite3",
        "review/review_surfaces.json",
    ]
    assert summary["schema_version"] == "agent_pack.review_summary.v1"
    assert (
        summary["projector_version"]
        == "agent_treport.durability_substrate_evidence.v1"
    )
    assert summary["source_fingerprint"].startswith("sha256:")
    assert summary["details"]["evidence_rows"] == [
        {
            "surface_id": "run_store.sqlite_snapshot",
            "evidence_kind": "stored_runtime_state",
            "status": "supported",
            "current_claim": "run snapshots reopen through SQLiteRunStore",
            "preserves_agent_pack_evidence": True,
        },
        {
            "surface_id": "trace_export.review_surfaces",
            "evidence_kind": "review_projection",
            "status": "supported",
            "current_claim": "review summaries export to MLflow artifacts",
            "preserves_agent_pack_evidence": True,
        },
        {
            "surface_id": "unsafe.claim",
            "evidence_kind": "path_safety",
            "status": "supported",
            "current_claim": "unspecified",
            "preserves_agent_pack_evidence": True,
        },
    ]
    assert summary["details"]["unsupported_production_claims"] == [
        "crash_restart_resume",
        "duplicate_worker_locking",
    ]
    serialized = json.dumps(summary, ensure_ascii=False)
    assert "must not leak" not in serialized
    assert "C:/Users/YS/secret" not in serialized
    assert "https://provider.example/raw" not in serialized
    assert "file:///tmp/raw.json" not in serialized


def test_durability_substrate_review_summary_survives_sqlite_trace_export(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        from agent_pack.models import ApprovalLifecycleRecord, PermissionDecisionRecord

        sqlite_path = tmp_path / "runtime.sqlite3"
        store = SQLiteRunStore(str(sqlite_path))
        await store.create_run(Run(id="run_durable_treport", status="succeeded"))
        approval = ApprovalLifecycleRecord(
            id="approval_durable",
            run_id="run_durable_treport",
            subject="external_data",
            action="model_export",
            boundary_fingerprint="sha256:durable-boundary",
            status="approved",
            required_scopes=("model_export",),
            approved_scopes=("model_export",),
            actor_id="operator_1",
            expires_at=datetime(2026, 5, 9, tzinfo=UTC),
        )
        permission = PermissionDecisionRecord(
            id="permission_durable",
            run_id="run_durable_treport",
            subject=approval.subject,
            action=approval.action,
            boundary_fingerprint=approval.boundary_fingerprint,
            decision="allowed",
            enforcement_mode="enforce",
            approval_record_id=approval.id,
        )
        await store.append_approval_record(approval)
        await store.append_permission_decision(permission)
        await store.append_event(
            RunEvent(
                id="evt_approval_durable",
                run_id="run_durable_treport",
                type="approval_lifecycle_recorded",
                payload={"approval_record_id": approval.id},
            )
        )
        await store.append_event(
            RunEvent(
                id="evt_permission_durable",
                run_id="run_durable_treport",
                type="permission_decision_recorded",
                payload={"permission_decision_id": permission.id},
            )
        )
        await store.append_event(
            RunEvent(
                id="evt_failure_classified",
                run_id="run_durable_treport",
                type="agent_treport.failure_classified",
                payload={"reason": "run_store_failed", "stack_trace": "must not leak"},
            )
        )
        await store.append_event(
            RunEvent(
                run_id="run_durable_treport",
                type="run_succeeded",
                payload={"run_id": "run_durable_treport"},
            )
        )
        await store.save_snapshot(
            RunSnapshot(
                run_id="run_durable_treport",
                step_index=1,
                state={
                    "report_quality_status": "passed",
                    "artifact_treport_operational_readiness_artifact_id": (
                        "artifact_treport_operational_readiness"
                    ),
                },
            )
        )
        first_record = await build_trace_export_record(
            store=store,
            run_id="run_durable_treport",
        )
        summary = build_durability_substrate_review_summary_from_trace_record(
            record=first_record,
            subject_id="agent_treport.signal_report",
            unsupported_production_claims=(
                "crash_restart_resume",
                "duplicate_worker_locking",
            ),
        )
        assert summary["details"]["evidence_rows"] == [
            {
                "surface_id": "run_store.latest_snapshot",
                "evidence_kind": "stored_runtime_state",
                "status": "supported",
                "current_claim": "latest snapshot is exported from persisted RunStore state",
                "preserves_agent_pack_evidence": True,
            },
            {
                "surface_id": "trace_export.evidence_summaries",
                "evidence_kind": "trace_export_record",
                "status": "supported",
                "current_claim": "trace export projects stored Agent TReport evidence summaries",
                "preserves_agent_pack_evidence": True,
            },
            {
                "surface_id": "readiness.operational_artifact",
                "evidence_kind": "readiness_evidence",
                "status": "supported",
                "current_claim": "operational readiness artifact id is preserved in snapshot state",
                "preserves_agent_pack_evidence": True,
            },
            {
                "surface_id": "governance.approval_permission_boundary",
                "evidence_kind": "approval_permission_evidence",
                "status": "supported",
                "current_claim": (
                    "approval and permission records are exported from persisted "
                    "governance state"
                ),
                "preserves_agent_pack_evidence": True,
            },
            {
                "surface_id": "failure.classified_event",
                "evidence_kind": "failure_evidence",
                "status": "supported",
                "current_claim": (
                    "classified failure events are exported without raw diagnostic "
                    "payloads"
                ),
                "preserves_agent_pack_evidence": True,
            },
        ]

        assert first_record.latest_snapshot is not None
        await store.save_snapshot(
            RunSnapshot(
                run_id="run_durable_treport",
                step_index=2,
                state={
                    **dict(first_record.latest_snapshot.state),
                    "agent_pack_review_summaries": [summary],
                },
            )
        )
        await store.close()

        reopened = SQLiteRunStore(str(sqlite_path))
        exported = await build_trace_export_record(
            store=reopened,
            run_id="run_durable_treport",
        )
        await reopened.close()

        assert len(exported.review_summaries) == 1
        review = exported.review_summaries[0]
        assert review.id == "review.durability_substrate"
        assert review.run_id == "run_durable_treport"
        assert review.review_surface == "durability_substrate"
        assert review.review_status == "passed"
        assert review.closure_status == "evidence_recorded"
        assert review.safe_artifact_refs == ("operational_readiness.json",)
        assert review.details["unsupported_production_claims"] == (
            "crash_restart_resume",
            "duplicate_worker_locking",
        )
        assert "must not leak" not in json.dumps(exported.model_dump(mode="json"))

    run_async(scenario())


def test_external_delivery_review_summary_projects_blocked_closure_counts_blockers() -> None:
    delivery_summary = {
        "latest_delivery_status": "sent",
        "run_id": "run_delivery_blocked",
        "telegram_alert_artifact_id": "artifact_treport_telegram_alert",
        "approval": {"status": "approved", "valid": True},
        "live": False,
        "receipt_paths": ["telegram_delivery_receipts/sent.json"],
        "delivery_summary_path": "telegram_delivery_summary.json",
    }
    daily_publish_closure = {
        "closure_status": "not_sent",
        "closure_met": False,
        "run_id": "run_delivery_blocked",
        "telegram_alert_artifact_id": "artifact_treport_telegram_alert",
        "evidence_checks": {
            "pre_publish_user_ready": "passed",
            "live_sent_receipt": "failed",
            "duplicate_blocked": "failed",
            "identity_consistency": "failed",
            "validation_passed": "passed",
        },
        "receipt_summary": {
            "matching_sent_receipt_count": 1,
            "matching_duplicate_blocked_receipt_count": 0,
            "selected_sent_receipt_path": "telegram_delivery_receipts/sent.json",
            "selected_duplicate_blocked_receipt_path": None,
        },
        "warnings": [],
        "limitations": ["live_delivery_evidence_missing"],
        "source_files": [
            "pre_publish_handoff.json",
            "telegram_delivery_summary.json",
            "telegram_delivery_receipts/sent.json",
        ],
    }

    summary = build_external_delivery_review_summary(
        delivery_summary=delivery_summary,
        daily_publish_closure=daily_publish_closure,
        subject_id="artifact_treport_telegram_alert",
    )

    assert summary["review_status"] == "blocked"
    assert summary["delivery_status"] == "sent"
    assert summary["closure_status"] == "not_sent"
    assert summary["blocker_count"] == 4
    assert summary["evidence_ref_count"] == 3
    assert summary["details"]["live_sent_receipt"] is False
    assert summary["details"]["duplicate_blocked"] is False


@pytest.mark.filterwarnings("ignore:The filesystem tracking backend.*:FutureWarning")
def test_mlflow_trace_export_sink_writes_local_file_store(tmp_path: Path) -> None:
    mlflow = pytest.importorskip("mlflow")
    mlflow_tracking = pytest.importorskip("mlflow.tracking")
    from agent_pack.integrations.mlflow import MLflowTraceExportSink

    async def scenario() -> None:
        store = SQLiteRunStore(str(tmp_path / "runtime.sqlite3"))
        await store.create_run(Run(id="run_mlflow_export", status="succeeded"))
        await store.append_event(
            RunEvent(
                run_id="run_mlflow_export",
                type="run_created",
                payload={"run_id": "run_mlflow_export"},
            )
        )
        record = await build_trace_export_record(store=store, run_id="run_mlflow_export")

        tracking_uri = (tmp_path / "mlruns").resolve().as_uri()
        sink = MLflowTraceExportSink(
            tracking_uri=tracking_uri,
            experiment_name="agent-pack-trace-export-test",
            run_name="run_mlflow_export",
        )
        result = await sink.export(record)

        client = mlflow_tracking.MlflowClient(tracking_uri=tracking_uri)
        run = client.get_run(result.external_id)
        downloaded = Path(
            client.download_artifacts(
                result.external_id,
                "raw/trace_export_record.json",
                str(tmp_path / "downloaded"),
            )
        )
        payload = json.loads(downloaded.read_text(encoding="utf-8"))

        assert result.status == "succeeded"
        assert result.sink == "mlflow"
        assert run.data.tags["agent_pack.run_id"] == "run_mlflow_export"
        assert run.data.metrics["event_count"] == 1
        assert payload["run"]["id"] == "run_mlflow_export"
        assert payload["schema_version"] == "agent_pack.trace_export.v1"

        mlflow.end_run()
        await store.close()

    run_async(scenario())


@pytest.mark.filterwarnings("ignore:The filesystem tracking backend.*:FutureWarning")
def test_mlflow_trace_export_sink_writes_native_genai_traces_with_unique_ids(
    tmp_path: Path,
) -> None:
    mlflow = pytest.importorskip("mlflow")
    mlflow_tracking = pytest.importorskip("mlflow.tracking")
    from agent_pack.integrations.mlflow import MLflowTraceExportSink

    async def scenario() -> None:
        store = SQLiteRunStore(str(tmp_path / "runtime.sqlite3"))
        report_artifact = ArtifactRef(
            artifact_id="artifact_report",
            name="report.md",
            uri=(tmp_path / "report.md").resolve().as_uri(),
            media_type="text/markdown",
            size=8,
        )
        await store.create_run(
            Run(
                id="run_mlflow_genai_export",
                status="succeeded",
                input=RunInput(artifacts=(report_artifact,)),
            )
        )
        await store.append_event(
            RunEvent(
                run_id="run_mlflow_genai_export",
                type="run_created",
                payload={"run_id": "run_mlflow_genai_export"},
            )
        )
        await store.append_event(
            RunEvent(
                run_id="run_mlflow_genai_export",
                type="run_succeeded",
                payload={"run_id": "run_mlflow_genai_export"},
            )
        )
        collect_state = {
            "operational_holdings_provenance_artifact_id": "artifact_provenance",
        }
        analyze_state = {
            **collect_state,
            "signal_payload_artifact_id": "artifact_payload",
        }
        quality_state = {
            **analyze_state,
            "report_quality_status": "passed",
            "report_quality_summary": {
                "error_count": 0,
                "warning_count": 0,
                "scopes": {
                    "payload": 0,
                    "markdown": 0,
                    "html": 0,
                    "telegram_alert": 0,
                },
                "blocking": False,
            },
            "report_quality_evaluated_artifact_ids": ("artifact_payload",),
            "report_quality_artifact_id": "artifact_quality",
            "report_artifact_id": "artifact_report",
            "daily_external_data_approval_status": "approved",
            "daily_external_data_approval_summary": {
                "subject": "pre_publish_external_data",
                "action": "model_export",
                "required_scopes": ("model_export",),
                "approved_scopes": ("model_export",),
                "missing_scopes": (),
                "outside_approved_bounds": (),
                "boundary_fingerprint": "sha256:approval-boundary",
                "approved_boundary_fingerprint": "sha256:approval-boundary",
                "runner_permission_status": "required_separately",
                "excluded_raw_fields": (
                    "raw_approval_comments",
                    "credentials",
                    "absolute_local_paths",
                ),
                "preflight_artifact_id": "artifact_approval_preflight",
                "template_artifact_id": "artifact_approval_template",
            },
            "agent_pack_review_summaries": [
                {
                    "id": "review.external_delivery",
                    "workflow": "agent_treport.signal_report",
                    "run_id": "run_mlflow_genai_export",
                    "subject_id": "artifact_treport_telegram_alert",
                    "operation_kind": "external_delivery",
                    "review_surface": "delivery_closure",
                    "review_status": "passed",
                    "approval_status": "approved",
                    "permission_status": "not_applicable",
                    "delivery_status": "duplicate_blocked",
                    "closure_status": "closure_met",
                    "blocker_count": 0,
                    "evidence_ref_count": 3,
                    "safe_artifact_refs": (
                        "telegram_delivery_summary.json",
                        "telegram_delivery_receipts/sent.json",
                        "daily_publish_closure.json",
                    ),
                    "schema_version": "agent_pack.review_summary.v1",
                    "projector_version": "agent_treport.external_delivery_review.v1",
                    "source_fingerprint": "sha256:external-delivery-review",
                    "details": {
                        "live_sent_receipt": True,
                        "duplicate_blocked": True,
                    },
                }
            ],
        }
        await store.append_event(
            RunEvent(
                run_id="run_mlflow_genai_export",
                type="state_updated",
                payload={"step_id": "collect-data", "state": collect_state},
            )
        )
        await store.append_event(
            RunEvent(
                run_id="run_mlflow_genai_export",
                type="state_updated",
                payload={"step_id": "analyze-data", "state": analyze_state},
            )
        )
        await store.append_event(
            RunEvent(
                run_id="run_mlflow_genai_export",
                type="state_updated",
                payload={"step_id": "render-report", "state": quality_state},
            )
        )
        await store.save_context_view(
            ContextView(
                id="view_mlflow_genai_export",
                run_id="run_mlflow_genai_export",
                selected_item_ids=("ctx_user",),
                messages=(
                    Message(role="user", content=(TextBlock(text="review trace"),)),
                ),
                policy_version="context_policy.v1",
                token_estimate=2,
                model_request_id="model_review_trace",
            )
        )
        await store.save_snapshot(
            RunSnapshot(
                run_id="run_mlflow_genai_export",
                step_index=2,
                state=quality_state,
            )
        )
        first_record = await build_trace_export_record(
            store=store,
            run_id="run_mlflow_genai_export",
        )
        first_record = first_record.model_copy(
            update={
                "evidence_summaries": first_record.evidence_summaries
                + (
                    TraceExportEvidenceSummary(
                        id="governance.model_export",
                        kind="approval_permission_boundary",
                        title="model export boundary",
                        status="approved",
                        summary={"status": "approved"},
                    ),
                    TraceExportEvidenceSummary(
                        id="custom.signal",
                        kind="custom_signal",
                        title="custom signal evidence",
                        status="available",
                    ),
                )
            }
        )
        second_record = await build_trace_export_record(
            store=store,
            run_id="run_mlflow_genai_export",
        )

        tracking_uri = (tmp_path / "mlruns").resolve().as_uri()
        sink = MLflowTraceExportSink(
            tracking_uri=tracking_uri,
            experiment_name="agent-pack-genai-trace-export-test",
            run_name="operator-visible-export",
        )
        first_result = await sink.export(first_record)
        second_result = await sink.export(second_record)

        client = mlflow_tracking.MlflowClient(tracking_uri=tracking_uri)
        run = client.get_run(first_result.external_id)
        first_trace = client.get_trace(
            first_result.metadata["mlflow_trace_id"],
            flush=True,
        )
        second_trace = client.get_trace(
            second_result.metadata["mlflow_trace_id"],
            flush=True,
        )
        first_root_span = next(
            span for span in first_trace.data.spans if span.parent_id is None
        )
        first_span_names = {span.name for span in first_trace.data.spans}
        first_span_types_by_name = {
            span.name: span.span_type for span in first_trace.data.spans
        }
        quality_span = next(
            span
            for span in first_trace.data.spans
            if span.name == "agent_pack.evidence.quality_eval.report_quality"
        )
        stage_span = next(
            span
            for span in first_trace.data.spans
            if span.name == "agent_pack.stage.render-report"
        )
        approval_span = next(
            span
            for span in first_trace.data.spans
            if span.name == "agent_pack.evidence.approval_trace.daily_external_data"
        )
        review_span = next(
            span
            for span in first_trace.data.spans
            if span.name == "agent_pack.review.delivery_closure.external_delivery"
        )
        raw_payload = json.loads(
            Path(
                client.download_artifacts(
                    first_result.external_id,
                    "raw/trace_export_record.json",
                    str(tmp_path / "downloaded-raw"),
                )
            ).read_text(encoding="utf-8")
        )
        review_evidence = json.loads(
            Path(
                client.download_artifacts(
                    first_result.external_id,
                    "review/evidence_summaries.json",
                    str(tmp_path / "downloaded-evidence"),
                )
            ).read_text(encoding="utf-8")
        )
        review_stage_io = json.loads(
            Path(
                client.download_artifacts(
                    first_result.external_id,
                    "review/stage_io_summary.json",
                    str(tmp_path / "downloaded-stage-io"),
                )
            ).read_text(encoding="utf-8")
        )
        review_approval_traces = json.loads(
            Path(
                client.download_artifacts(
                    first_result.external_id,
                    "review/approval_traces.json",
                    str(tmp_path / "downloaded-approval-traces"),
                )
            ).read_text(encoding="utf-8")
        )
        review_final = json.loads(
            Path(
                client.download_artifacts(
                    first_result.external_id,
                    "review/final_outcome.json",
                    str(tmp_path / "downloaded-final"),
                )
            ).read_text(encoding="utf-8")
        )
        review_surfaces = json.loads(
            Path(
                client.download_artifacts(
                    first_result.external_id,
                    "review/review_surfaces.json",
                    str(tmp_path / "downloaded-review-surfaces"),
                )
            ).read_text(encoding="utf-8")
        )
        external_delivery_review = json.loads(
            Path(
                client.download_artifacts(
                    first_result.external_id,
                    "review/external_delivery_review_summary.json",
                    str(tmp_path / "downloaded-external-delivery-review"),
                )
            ).read_text(encoding="utf-8")
        )

        assert first_record.export_id != second_record.export_id
        assert first_result.external_id != second_result.external_id
        assert first_result.metadata["mlflow_run_id"] == first_result.external_id
        assert first_result.metadata["mlflow_trace_id"] != second_result.metadata[
            "mlflow_trace_id"
        ]
        assert first_result.metadata["agent_pack.trace_export_id"] == first_record.export_id
        assert first_result.metadata["agent_pack.run_id"] == "run_mlflow_genai_export"
        assert first_trace.info.trace_id == first_result.metadata["mlflow_trace_id"]
        assert second_trace.info.trace_id == second_result.metadata["mlflow_trace_id"]
        assert first_trace.info.client_request_id == first_record.export_id
        assert second_trace.info.client_request_id == second_record.export_id
        assert first_trace.info.tags["agent_pack.run_id"] == "run_mlflow_genai_export"
        assert first_trace.info.tags["agent_pack.review_surface"] == "delivery_closure"
        assert first_trace.info.tags["agent_pack.review_status"] == "passed"
        assert first_trace.info.tags["agent_pack.delivery_status"] == "duplicate_blocked"
        assert first_trace.info.tags["agent_pack.closure_status"] == "closure_met"
        assert (
            first_trace.info.trace_metadata["agent_pack.trace_export_id"]
            == first_record.export_id
        )
        assert (
            first_trace.info.trace_metadata["agent_pack.review_surface"]
            == "delivery_closure"
        )
        assert (
            first_trace.info.trace_metadata["agent_pack.source_fingerprint"]
            == "sha256:external-delivery-review"
        )
        assert first_root_span.name == "agent_pack.run.run_mlflow_genai_export"
        assert first_root_span.span_type == "AGENT"
        assert first_span_types_by_name[
            "agent_pack.review.delivery_closure.external_delivery"
        ] == "EVALUATOR"
        assert first_span_types_by_name["agent_pack.stage.collect-data"] == "CHAIN"
        assert first_span_types_by_name["agent_pack.stage.analyze-data"] == "CHAIN"
        assert first_span_types_by_name["agent_pack.stage.render-report"] == "CHAIN"
        assert first_span_types_by_name[
            "agent_pack.evidence.quality_eval.report_quality"
        ] == "EVALUATOR"
        assert first_span_types_by_name[
            "agent_pack.evidence.evidence_provenance.operational_holdings"
        ] == "TASK"
        assert first_span_types_by_name[
            "agent_pack.evidence.approval_trace.daily_external_data"
        ] == "GUARDRAIL"
        assert first_span_types_by_name[
            "agent_pack.evidence.approval_permission_boundary.model_export"
        ] == "GUARDRAIL"
        assert first_span_types_by_name[
            "agent_pack.evidence.final_outcome.run_result"
        ] == "TASK"
        assert first_span_types_by_name["agent_pack.evidence.custom_signal.signal"] == (
            "UNKNOWN"
        )
        assert first_span_types_by_name["agent_pack.debug.event.run_created"] == (
            "WORKFLOW"
        )
        assert first_span_types_by_name["agent_pack.debug.context_view"] == "MEMORY"
        assert first_span_types_by_name["agent_pack.debug.latest_snapshot"] == "MEMORY"
        assert first_span_types_by_name["agent_pack.debug.artifact"] == "TOOL"
        assert first_root_span.attributes["agent_pack.trace_export_id"] == (
            first_record.export_id
        )
        assert first_root_span.attributes["agent_pack.review_surface_count"] == 1
        assert "agent_pack.stage.collect-data" in first_span_names
        assert "agent_pack.stage.analyze-data" in first_span_names
        assert "agent_pack.stage.render-report" in first_span_names
        assert "agent_pack.debug.event.run_created" in first_span_names
        assert "agent_pack.debug.event.run_succeeded" in first_span_names
        assert "agent_pack.debug.artifact" in first_span_names
        assert "agent_pack.review.delivery_closure.external_delivery" in first_span_names
        assert "agent_pack.evidence.stage_io.render-report" not in first_span_names
        assert "agent_pack.evidence.final_outcome.run_result" in first_span_names
        assert stage_span.attributes["agent_pack.evidence_id"] == "stage.render-report"
        assert stage_span.attributes["agent_pack.evidence_kind"] == "stage_io"
        assert stage_span.attributes["agent_pack.review_path"] == "stage"
        assert quality_span.attributes["agent_pack.evidence_id"] == (
            "quality.report_quality"
        )
        assert quality_span.attributes["agent_pack.evidence_kind"] == "quality_eval"
        assert quality_span.attributes["agent_pack.evidence_status"] == "passed"
        assert quality_span.attributes["agent_pack.evidence.violation_count"] == 0
        assert quality_span.attributes["agent_pack.evidence.highest_severity"] == "none"
        assert approval_span.attributes["agent_pack.evidence_id"] == (
            "approval.daily_external_data"
        )
        assert approval_span.attributes["agent_pack.evidence_kind"] == "approval_trace"
        assert approval_span.attributes["agent_pack.evidence_status"] == "approved"
        assert approval_span.attributes["agent_pack.evidence.missing_scope_count"] == 0
        assert approval_span.attributes["agent_pack.evidence.runner_permission_status"] == (
            "required_separately"
        )
        assert review_span.attributes["agent_pack.review_id"] == "review.external_delivery"
        assert review_span.attributes["agent_pack.review_path"] == "review_surface"
        assert review_span.attributes["agent_pack.review_surface"] == "delivery_closure"
        assert review_span.attributes["agent_pack.operation_kind"] == "external_delivery"
        assert review_span.attributes["agent_pack.review_status"] == "passed"
        assert review_span.attributes["agent_pack.delivery_status"] == "duplicate_blocked"
        assert review_span.attributes["agent_pack.closure_status"] == "closure_met"
        assert review_span.attributes["agent_pack.blocker_count"] == 0
        assert review_span.attributes["agent_pack.evidence_ref_count"] == 3
        assert raw_payload["schema_version"] == "agent_pack.trace_export.v1"
        assert raw_payload["run"]["id"] == "run_mlflow_genai_export"
        assert {item["id"] for item in review_evidence} >= {
            "stage.collect-data",
            "stage.analyze-data",
            "stage.render-report",
            "quality.report_quality",
            "provenance.operational_holdings",
            "approval.daily_external_data",
            "final.run_result",
        }
        assert [item["id"] for item in review_stage_io] == [
            "stage.collect-data",
            "stage.analyze-data",
            "stage.render-report",
        ]
        assert [item["id"] for item in review_approval_traces] == [
            "approval.daily_external_data"
        ]
        assert review_approval_traces[0]["summary"]["runner_permission_status"] == (
            "required_separately"
        )
        assert review_approval_traces[0]["outputs"]["artifact_ids"] == [
            "artifact_approval_preflight",
            "artifact_approval_template",
        ]
        assert review_stage_io[-1]["outputs"]["new_artifact_ids"] == [
            "artifact_quality",
            "artifact_report",
        ]
        assert review_final["id"] == "final.run_result"
        assert review_final["outputs"]["artifact_ids"] == [
            "artifact_payload",
            "artifact_provenance",
            "artifact_quality",
            "artifact_report",
        ]
        assert review_surfaces[0]["id"] == "review.external_delivery"
        assert review_surfaces[0]["review_surface"] == "delivery_closure"
        assert external_delivery_review["operation_kind"] == "external_delivery"
        assert external_delivery_review["safe_artifact_refs"] == [
            "telegram_delivery_summary.json",
            "telegram_delivery_receipts/sent.json",
            "daily_publish_closure.json",
        ]
        assert run.data.tags["agent_pack.review_surface"] == "delivery_closure"
        assert run.data.tags["agent_pack.delivery_status"] == "duplicate_blocked"
        assert run.data.metrics["review_blocker_count"] == 0
        assert run.data.metrics["review_evidence_ref_count"] == 3

        mlflow.end_run()
        await store.close()

    run_async(scenario())
