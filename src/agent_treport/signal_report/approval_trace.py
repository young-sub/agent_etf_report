from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import UTC, datetime

from agent_pack.models import (
    ApprovalLifecycleRecord,
    ApprovalLifecycleStatusValue,
    JsonValue,
    PermissionDecisionRecord,
    PermissionDecisionValue,
)

from agent_treport.signal_report.approval import EXCLUDED_RAW_FIELDS

DAILY_APPROVAL_PREFLIGHT_ARTIFACT_ID = "artifact_treport_daily_approval_preflight"
DAILY_APPROVAL_TEMPLATE_ARTIFACT_ID = "artifact_treport_daily_approval_template"
AGENT_TREPORT_APPROVAL_PROFILE_ACTOR_ID = "agent_treport.approval_profile"
AGENT_TREPORT_NO_EXPIRY_SENTINEL = datetime(9999, 12, 31, tzinfo=UTC)


def daily_external_data_approval_trace_state(
    approval: Mapping[str, object] | None,
) -> dict[str, JsonValue]:
    if approval is None:
        return {}
    summary = approval.get("summary")
    if not isinstance(summary, Mapping):
        return {}

    required_scopes = _text_tuple(summary.get("required_scopes"))
    missing_scopes = _text_tuple(summary.get("missing_scopes"))
    unapproved_scopes = _text_tuple(summary.get("unapproved_scopes"))
    outside_approved_bounds = _text_tuple(summary.get("outside_approved_bounds"))
    excluded_scopes = set(missing_scopes).union(unapproved_scopes)
    approved_scopes = tuple(
        scope for scope in required_scopes if scope not in excluded_scopes
    )
    status = summary.get("status")
    trace_summary: dict[str, JsonValue] = {
        "subject": "pre_publish_external_data",
        "action": _approval_action(required_scopes),
        "status": status if isinstance(status, str) else "unknown",
        "valid": bool(summary.get("valid")),
        "required_scopes": required_scopes,
        "approved_scopes": approved_scopes,
        "missing_scopes": missing_scopes,
        "outside_approved_bounds": outside_approved_bounds,
        "runner_permission_status": "required_separately",
        "platform_permission_status": "not_granted_by_domain_approval",
        "excluded_raw_fields": tuple(EXCLUDED_RAW_FIELDS),
        "preflight_artifact_id": DAILY_APPROVAL_PREFLIGHT_ARTIFACT_ID,
        "template_artifact_id": DAILY_APPROVAL_TEMPLATE_ARTIFACT_ID,
    }
    for trace_key, summary_key in (
        ("boundary_fingerprint", "requested_boundary_fingerprint"),
        ("approved_boundary_fingerprint", "approved_boundary_fingerprint"),
    ):
        value = summary.get(summary_key)
        if isinstance(value, str):
            trace_summary[trace_key] = value

    return {
        "daily_external_data_approval_status": trace_summary["status"],
        "daily_external_data_approval_summary": trace_summary,
    }


def daily_external_data_governance_records(
    *,
    run_id: str,
    approval: Mapping[str, object] | None,
) -> tuple[ApprovalLifecycleRecord, PermissionDecisionRecord] | tuple[()]:
    if approval is None:
        return ()
    summary = approval.get("summary")
    if not isinstance(summary, Mapping):
        return ()
    requested_boundary_fingerprint = summary.get("requested_boundary_fingerprint")
    approved_boundary_fingerprint = summary.get("approved_boundary_fingerprint")
    boundary_fingerprint = (
        requested_boundary_fingerprint
        if isinstance(requested_boundary_fingerprint, str)
        else approved_boundary_fingerprint
        if isinstance(approved_boundary_fingerprint, str)
        else None
    )
    if boundary_fingerprint is None:
        return ()

    required_scopes = _text_tuple(summary.get("required_scopes"))
    missing_scopes = _text_tuple(summary.get("missing_scopes"))
    unapproved_scopes = _text_tuple(summary.get("unapproved_scopes"))
    excluded_scopes = set(missing_scopes).union(unapproved_scopes)
    approved_scopes = tuple(
        scope for scope in required_scopes if scope not in excluded_scopes
    )
    status = summary.get("status")
    status_text = status if isinstance(status, str) else "unknown"
    subject = "pre_publish_external_data"
    action = _approval_action(required_scopes)
    approval_record = ApprovalLifecycleRecord(
        run_id=run_id,
        subject=subject,
        action=action,
        boundary_fingerprint=boundary_fingerprint,
        status=_approval_lifecycle_status(status_text, valid=bool(summary.get("valid"))),
        actor_id=AGENT_TREPORT_APPROVAL_PROFILE_ACTOR_ID,
        expires_at=_approval_expires_at(summary),
        required_scopes=required_scopes,
        approved_scopes=approved_scopes,
        reason=status_text,
        metadata={
            "agent_treport_approval_status": status_text,
            "agent_treport_approval_valid": bool(summary.get("valid")),
            "agent_treport_projection_only": True,
        },
    )
    permission_decision = PermissionDecisionRecord(
        run_id=run_id,
        subject=subject,
        action=action,
        boundary_fingerprint=boundary_fingerprint,
        decision=_permission_decision(status_text, valid=bool(summary.get("valid"))),
        enforcement_mode="observe",
        approval_record_id=approval_record.id,
        reason_code=status_text,
        reason=status_text,
        metadata={
            "agent_treport_approval_status": status_text,
            "agent_treport_approval_valid": bool(summary.get("valid")),
        },
    )
    return approval_record, permission_decision


def _approval_action(required_scopes: Sequence[str]) -> str:
    if required_scopes == ("model_export",):
        return "model_export"
    if required_scopes == ("live_external_evidence",):
        return "live_external_evidence"
    return "daily_operational_external_data"


def _text_tuple(value: object) -> tuple[str, ...]:
    if not isinstance(value, Sequence) or isinstance(value, str | bytes | bytearray):
        return ()
    return tuple(item for item in value if isinstance(item, str))


def _approval_expires_at(summary: Mapping[str, object]) -> datetime:
    value = summary.get("expires_at")
    if isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return AGENT_TREPORT_NO_EXPIRY_SENTINEL
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=UTC)
        return parsed.astimezone(UTC)
    return AGENT_TREPORT_NO_EXPIRY_SENTINEL


def _approval_lifecycle_status(
    status: str,
    *,
    valid: bool,
) -> ApprovalLifecycleStatusValue:
    if valid and status == "approved":
        return "approved"
    if status == "expired":
        return "expired"
    if status == "revoked":
        return "revoked"
    if status in {"missing", "pending", "missing_scope", "outside_approved_bounds"}:
        return "requested"
    return "rejected"


def _permission_decision(
    status: str,
    *,
    valid: bool,
) -> PermissionDecisionValue:
    if valid and status == "approved":
        return "allowed"
    if status in {"missing", "pending", "missing_scope", "outside_approved_bounds", "expired"}:
        return "approval_required"
    return "blocked"
