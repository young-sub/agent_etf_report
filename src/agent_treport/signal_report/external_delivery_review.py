from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence

from agent_pack.models import JsonValue
from agent_pack.trace_export import TraceExportReviewSummary

EXTERNAL_DELIVERY_REVIEW_PROJECTOR_VERSION = (
    "agent_treport.external_delivery_review.v1"
)


def build_external_delivery_review_summary(
    *,
    delivery_summary: Mapping[str, JsonValue],
    daily_publish_closure: Mapping[str, JsonValue],
    subject_id: str,
    workflow: str = "agent_treport.signal_report",
    permission_status: str = "not_applicable",
) -> dict[str, JsonValue]:
    run_id = _consistent_run_id(delivery_summary, daily_publish_closure)
    delivery_status = _text(delivery_summary.get("latest_delivery_status"), "unknown")
    closure_status = _text(daily_publish_closure.get("closure_status"), "unknown")
    review_status = "passed" if closure_status == "closure_met" else "blocked"
    evidence_checks = _mapping(daily_publish_closure.get("evidence_checks"))
    receipt_summary = _mapping(daily_publish_closure.get("receipt_summary"))
    limitations = _text_list(daily_publish_closure.get("limitations"))
    warnings = _text_list(daily_publish_closure.get("warnings"))
    safe_artifact_refs = _safe_artifact_refs(
        delivery_summary=delivery_summary,
        daily_publish_closure=daily_publish_closure,
        receipt_summary=receipt_summary,
    )
    summary = TraceExportReviewSummary(
        id="review.external_delivery",
        workflow=workflow,
        run_id=run_id,
        subject_id=subject_id,
        operation_kind="external_delivery",
        review_surface="delivery_closure",
        review_status=review_status,
        approval_status=_approval_status(delivery_summary),
        permission_status=permission_status,
        delivery_status=delivery_status,
        closure_status=closure_status,
        blocker_count=_blocker_count(
            closure_status=closure_status,
            evidence_checks=evidence_checks,
            limitations=limitations,
        ),
        evidence_ref_count=len(safe_artifact_refs),
        safe_artifact_refs=tuple(safe_artifact_refs),
        schema_version="agent_pack.review_summary.v1",
        projector_version=EXTERNAL_DELIVERY_REVIEW_PROJECTOR_VERSION,
        source_fingerprint=_source_fingerprint(
            workflow=workflow,
            run_id=run_id,
            subject_id=subject_id,
            delivery_status=delivery_status,
            closure_status=closure_status,
            evidence_checks=evidence_checks,
            receipt_summary=receipt_summary,
            limitations=limitations,
            warnings=warnings,
            safe_artifact_refs=safe_artifact_refs,
        ),
        details={
            "target_alias": _text(
                daily_publish_closure.get("target_alias")
                or delivery_summary.get("target_alias"),
                "unknown",
            ),
            "live_sent_receipt": evidence_checks.get("live_sent_receipt") == "passed",
            "duplicate_blocked": evidence_checks.get("duplicate_blocked") == "passed",
            "identity_consistency": evidence_checks.get("identity_consistency") == "passed",
            "validation_passed": evidence_checks.get("validation_passed") == "passed",
            "warning_count": len(warnings),
            "limitation_count": len(limitations),
            "matching_sent_receipt_count": _int(
                receipt_summary.get("matching_sent_receipt_count")
            ),
            "matching_duplicate_blocked_receipt_count": _int(
                receipt_summary.get("matching_duplicate_blocked_receipt_count")
            ),
        },
    )
    return summary.model_dump(mode="json")


def _consistent_run_id(
    delivery_summary: Mapping[str, JsonValue],
    daily_publish_closure: Mapping[str, JsonValue],
) -> str:
    delivery_run_id = _text(delivery_summary.get("run_id"), "")
    closure_run_id = _text(daily_publish_closure.get("run_id"), "")
    if delivery_run_id and closure_run_id and delivery_run_id != closure_run_id:
        raise ValueError("delivery summary and daily publish closure run_id differ")
    run_id = delivery_run_id or closure_run_id
    if not run_id:
        raise ValueError("external delivery review summary requires run_id")
    return run_id


def _approval_status(delivery_summary: Mapping[str, JsonValue]) -> str:
    approval = _mapping(delivery_summary.get("approval"))
    status = _text(approval.get("status"), "unknown")
    if approval.get("valid") is False and status == "approved":
        return "invalid"
    return status


def _blocker_count(
    *,
    closure_status: str,
    evidence_checks: Mapping[str, JsonValue],
    limitations: Sequence[str],
) -> int:
    if closure_status == "closure_met":
        return 0
    failed_checks = len(
        [
            value
            for value in evidence_checks.values()
            if value in {"failed", "not_available"}
        ]
    )
    return max(1, failed_checks + len(limitations))


def _safe_artifact_refs(
    *,
    delivery_summary: Mapping[str, JsonValue],
    daily_publish_closure: Mapping[str, JsonValue],
    receipt_summary: Mapping[str, JsonValue],
) -> list[str]:
    refs: list[str] = []
    _append_safe_ref(refs, "daily_publish_closure.json")
    _append_safe_ref(refs, _text(delivery_summary.get("delivery_summary_path"), ""))
    for key in ("selected_sent_receipt_path", "selected_duplicate_blocked_receipt_path"):
        _append_safe_ref(refs, _text(receipt_summary.get(key), ""))
    for path in _text_list(delivery_summary.get("receipt_paths")):
        _append_safe_ref(refs, path)
    for path in _text_list(daily_publish_closure.get("source_files")):
        if path.endswith(".json") and (
            path == "telegram_delivery_summary.json"
            or path.startswith("telegram_delivery_receipts/")
        ):
            _append_safe_ref(refs, path)
    return refs


def _append_safe_ref(refs: list[str], value: str) -> None:
    if not value or value in refs:
        return
    if _is_safe_ref(value):
        refs.append(value)


def _is_safe_ref(value: str) -> bool:
    if "://" in value:
        return False
    if value.startswith(("/", "\\")):
        return False
    if len(value) >= 3 and value[1] == ":" and value[2] in {"/", "\\"}:
        return False
    return True


def _source_fingerprint(
    *,
    workflow: str,
    run_id: str,
    subject_id: str,
    delivery_status: str,
    closure_status: str,
    evidence_checks: Mapping[str, JsonValue],
    receipt_summary: Mapping[str, JsonValue],
    limitations: Sequence[str],
    warnings: Sequence[str],
    safe_artifact_refs: Sequence[str],
) -> str:
    payload = {
        "workflow": workflow,
        "run_id": run_id,
        "subject_id": subject_id,
        "delivery_status": delivery_status,
        "closure_status": closure_status,
        "evidence_checks": dict(evidence_checks),
        "receipt_summary": {
            key: value
            for key, value in receipt_summary.items()
            if key.endswith("_count") or key.endswith("_path")
        },
        "limitations": list(limitations),
        "warnings": list(warnings),
        "safe_artifact_refs": list(safe_artifact_refs),
    }
    canonical = json.dumps(
        payload,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    return "sha256:" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _mapping(value: object) -> Mapping[str, JsonValue]:
    return value if isinstance(value, Mapping) else {}


def _text(value: object, default: str) -> str:
    return value if isinstance(value, str) and value else default


def _text_list(value: object) -> list[str]:
    if not isinstance(value, Sequence) or isinstance(value, str | bytes | bytearray):
        return []
    return [item for item in value if isinstance(item, str)]


def _int(value: object) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) else 0
