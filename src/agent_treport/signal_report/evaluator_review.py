from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence

from agent_pack.models import JsonValue
from agent_pack.trace_export import TraceExportReviewSummary

SIGNAL_REPORT_EVALUATOR_REVIEW_PROJECTOR_VERSION = (
    "agent_treport.signal_report_evaluator_review.v1"
)

_REQUIRED_ARTIFACTS: tuple[tuple[str, str], ...] = (
    ("signal_payload_artifact_id", "signal_payload.json"),
    ("report_artifact_id", "report.md"),
    ("html_report_artifact_id", "report.html"),
    ("telegram_alert_artifact_id", "telegram_alert.txt"),
    ("report_quality_artifact_id", "quality.json"),
)


def build_signal_report_evaluator_review_summary(
    *,
    run_id: str,
    state: Mapping[str, JsonValue],
    workflow: str = "agent_treport.signal_report",
    subject_id: str = "agent_treport.signal_report",
) -> dict[str, JsonValue]:
    checked_artifact_ids = _checked_artifact_ids(state)
    safe_artifact_refs = _safe_artifact_refs(state)
    reason_codes = _reason_codes(state)
    failure_reasons = tuple(
        reason
        for reason in reason_codes
        if reason
        in {
            "report_quality_failed",
            "report_quality_blocking",
            "report_quality_errors",
        }
    )
    uncertain_reasons = tuple(reason for reason in reason_codes if reason not in failure_reasons)
    if failure_reasons:
        verdict = "fail"
        review_status = "failed"
        closure_status = "blocked"
        blocker_count = 1
        uncertainty_level = "low"
        uncertainty_reasons: tuple[str, ...] = ()
        rationale = "ReportQualityGate evidence blocks the produced SignalReportWorkflow output."
    elif uncertain_reasons:
        verdict = "uncertain"
        review_status = "uncertain"
        closure_status = "evidence_incomplete"
        blocker_count = 0
        uncertainty_level = "high"
        uncertainty_reasons = uncertain_reasons
        rationale = "Required SignalReportWorkflow evidence is incomplete for evaluator judgment."
    else:
        verdict = "pass"
        review_status = "passed"
        closure_status = "evidence_recorded"
        blocker_count = 0
        uncertainty_level = "low"
        uncertainty_reasons = ()
        rationale = (
            "All required SignalReportWorkflow artifacts are present and "
            "ReportQualityGate evidence is non-blocking."
        )

    details: dict[str, JsonValue] = {
        "schema_version": SIGNAL_REPORT_EVALUATOR_REVIEW_PROJECTOR_VERSION,
        "behavior_id": "agent_treport.signal_report",
        "verdict": verdict,
        "rationale": rationale,
        "reason_codes": reason_codes,
        "checked_artifact_ids": checked_artifact_ids,
        "rubric_checks": _rubric_checks(
            state=state,
            failure_reasons=failure_reasons,
            uncertain_reasons=uncertain_reasons,
        ),
        "uncertainty": {
            "level": uncertainty_level,
            "reasons": uncertainty_reasons,
        },
        "report_quality_status": _text(state.get("report_quality_status"), "unknown"),
        "report_quality_summary": _quality_summary_projection(state),
    }
    summary = TraceExportReviewSummary(
        id="review.signal_report_evaluator",
        workflow=workflow,
        run_id=run_id,
        subject_id=subject_id,
        operation_kind="evaluator_harness_review",
        review_surface="signal_report_behavior",
        review_status=review_status,
        approval_status="not_applicable",
        permission_status="not_applicable",
        delivery_status="not_applicable",
        closure_status=closure_status,
        blocker_count=blocker_count,
        evidence_ref_count=len(safe_artifact_refs),
        safe_artifact_refs=tuple(safe_artifact_refs),
        schema_version="agent_pack.review_summary.v1",
        projector_version=SIGNAL_REPORT_EVALUATOR_REVIEW_PROJECTOR_VERSION,
        source_fingerprint=_source_fingerprint(
            workflow=workflow,
            run_id=run_id,
            subject_id=subject_id,
            details=details,
            safe_artifact_refs=safe_artifact_refs,
        ),
        details=details,
    )
    return summary.model_dump(mode="json")


def _checked_artifact_ids(state: Mapping[str, JsonValue]) -> list[str]:
    ids: list[str] = []
    for key, _safe_ref in _REQUIRED_ARTIFACTS:
        value = state.get(key)
        if isinstance(value, str) and value:
            ids.append(value)
    return ids


def _safe_artifact_refs(state: Mapping[str, JsonValue]) -> list[str]:
    refs: list[str] = []
    for key, safe_ref in _REQUIRED_ARTIFACTS:
        value = state.get(key)
        if isinstance(value, str) and value:
            refs.append(safe_ref)
    return refs


def _reason_codes(state: Mapping[str, JsonValue]) -> tuple[str, ...]:
    reasons: list[str] = []
    for key, _safe_ref in _REQUIRED_ARTIFACTS:
        value = state.get(key)
        if not isinstance(value, str) or not value:
            reasons.append(f"missing_{key}")

    quality_status = state.get("report_quality_status")
    if not isinstance(quality_status, str) or not quality_status:
        reasons.append("missing_report_quality_status")
    elif quality_status == "failed":
        reasons.append("report_quality_failed")
    elif quality_status != "passed":
        reasons.append("unknown_report_quality_status")

    quality_summary = state.get("report_quality_summary")
    if not isinstance(quality_summary, Mapping):
        reasons.append("missing_report_quality_summary")
        return tuple(reasons)

    if quality_summary.get("blocking") is True:
        reasons.append("report_quality_blocking")
    error_count = _int(quality_summary.get("error_count"))
    if error_count > 0:
        reasons.append("report_quality_errors")
    return tuple(dict.fromkeys(reasons))


def _rubric_checks(
    *,
    state: Mapping[str, JsonValue],
    failure_reasons: Sequence[str],
    uncertain_reasons: Sequence[str],
) -> tuple[dict[str, JsonValue], ...]:
    artifact_reasons = tuple(
        reason for reason in uncertain_reasons if reason.startswith("missing_")
    )
    artifact_status = "uncertain" if artifact_reasons else "passed"
    quality_status = "passed"
    if failure_reasons:
        quality_status = "failed"
    elif any("report_quality" in reason for reason in uncertain_reasons):
        quality_status = "uncertain"
    return (
        {
            "id": "required_artifacts_present",
            "status": artifact_status,
            "reason_codes": artifact_reasons,
            "checked_artifact_ids": _checked_artifact_ids(state),
        },
        {
            "id": "report_quality_non_blocking",
            "status": quality_status,
            "reason_codes": tuple(
                reason
                for reason in (*failure_reasons, *uncertain_reasons)
                if "report_quality" in reason
            ),
        },
    )


def _quality_summary_projection(state: Mapping[str, JsonValue]) -> dict[str, JsonValue]:
    quality_summary = state.get("report_quality_summary")
    if not isinstance(quality_summary, Mapping):
        return {}
    projected: dict[str, JsonValue] = {}
    for key in ("error_count", "warning_count", "blocking", "scopes"):
        value = quality_summary.get(key)
        if value is not None:
            projected[key] = value
    return projected


def _source_fingerprint(
    *,
    workflow: str,
    run_id: str,
    subject_id: str,
    details: Mapping[str, JsonValue],
    safe_artifact_refs: Sequence[str],
) -> str:
    payload = {
        "workflow": workflow,
        "run_id": run_id,
        "subject_id": subject_id,
        "details": dict(details),
        "safe_artifact_refs": list(safe_artifact_refs),
    }
    canonical = json.dumps(
        payload,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    return "sha256:" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _text(value: object, default: str) -> str:
    return value if isinstance(value, str) and value else default


def _int(value: object) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) else 0
