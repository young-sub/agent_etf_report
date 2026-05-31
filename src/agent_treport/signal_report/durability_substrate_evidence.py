from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence

from agent_pack.models import JsonValue
from agent_pack.trace_export import TraceExportRecord, TraceExportReviewSummary

DURABILITY_SUBSTRATE_EVIDENCE_PROJECTOR_VERSION = (
    "agent_treport.durability_substrate_evidence.v1"
)


def build_durability_substrate_review_summary(
    *,
    run_id: str,
    subject_id: str,
    evidence_surfaces: Sequence[Mapping[str, JsonValue]],
    unsupported_production_claims: Sequence[str] = (),
    workflow: str = "agent_treport.signal_report",
) -> dict[str, JsonValue]:
    rows = tuple(_evidence_row(surface) for surface in evidence_surfaces)
    unsupported_claims = tuple(_text_list(unsupported_production_claims))
    safe_artifact_refs = tuple(_safe_artifact_refs(evidence_surfaces))
    blocker_count = sum(1 for row in rows if row["status"] in {"missing", "unsupported"})
    review_status = "blocked" if blocker_count else "passed"
    closure_status = "blocked" if blocker_count else "evidence_recorded"
    summary = TraceExportReviewSummary(
        id="review.durability_substrate",
        workflow=workflow,
        run_id=run_id,
        subject_id=subject_id,
        operation_kind="runtime_evidence",
        review_surface="durability_substrate",
        review_status=review_status,
        approval_status="not_applicable",
        permission_status="not_applicable",
        delivery_status="not_applicable",
        closure_status=closure_status,
        blocker_count=blocker_count,
        evidence_ref_count=len(safe_artifact_refs),
        safe_artifact_refs=safe_artifact_refs,
        schema_version="agent_pack.review_summary.v1",
        projector_version=DURABILITY_SUBSTRATE_EVIDENCE_PROJECTOR_VERSION,
        source_fingerprint=_source_fingerprint(
            workflow=workflow,
            run_id=run_id,
            subject_id=subject_id,
            rows=rows,
            unsupported_claims=unsupported_claims,
            safe_artifact_refs=safe_artifact_refs,
        ),
        details={
            "evidence_rows": rows,
            "unsupported_production_claims": unsupported_claims,
        },
    )
    return summary.model_dump(mode="json")


def build_durability_substrate_review_summary_from_trace_record(
    *,
    record: TraceExportRecord,
    subject_id: str,
    unsupported_production_claims: Sequence[str] = (),
) -> dict[str, JsonValue]:
    evidence_surfaces: list[dict[str, JsonValue]] = [
        {
            "surface_id": "run_store.latest_snapshot",
            "evidence_kind": "stored_runtime_state",
            "status": "supported" if record.latest_snapshot is not None else "missing",
            "current_claim": "latest snapshot is exported from persisted RunStore state",
            "preserves_agent_pack_evidence": record.latest_snapshot is not None,
        },
        {
            "surface_id": "trace_export.evidence_summaries",
            "evidence_kind": "trace_export_record",
            "status": "supported" if record.evidence_summaries else "missing",
            "current_claim": (
                "trace export projects stored Agent TReport evidence summaries"
            ),
            "preserves_agent_pack_evidence": bool(record.evidence_summaries),
        },
    ]
    if _has_operational_readiness_artifact(record):
        evidence_surfaces.append(
            {
                "surface_id": "readiness.operational_artifact",
                "evidence_kind": "readiness_evidence",
                "status": "supported",
                "current_claim": (
                    "operational readiness artifact id is preserved in snapshot state"
                ),
                "preserves_agent_pack_evidence": True,
                "artifact_refs": ("operational_readiness.json",),
            }
        )
    if _has_governance_evidence(record):
        evidence_surfaces.append(
            {
                "surface_id": "governance.approval_permission_boundary",
                "evidence_kind": "approval_permission_evidence",
                "status": "supported",
                "current_claim": (
                    "approval and permission records are exported from persisted "
                    "governance state"
                ),
                "preserves_agent_pack_evidence": True,
            }
        )
    if _has_classified_failure_event(record):
        evidence_surfaces.append(
            {
                "surface_id": "failure.classified_event",
                "evidence_kind": "failure_evidence",
                "status": "supported",
                "current_claim": (
                    "classified failure events are exported without raw diagnostic "
                    "payloads"
                ),
                "preserves_agent_pack_evidence": True,
            }
        )
    return build_durability_substrate_review_summary(
        run_id=record.run.id,
        subject_id=subject_id,
        evidence_surfaces=evidence_surfaces,
        unsupported_production_claims=unsupported_production_claims,
        workflow=record.review_summaries[0].workflow
        if record.review_summaries
        else "agent_treport.signal_report",
    )


def _evidence_row(surface: Mapping[str, JsonValue]) -> dict[str, JsonValue]:
    return {
        "surface_id": _text(surface.get("surface_id"), "unknown"),
        "evidence_kind": _text(surface.get("evidence_kind"), "unknown"),
        "status": _text(surface.get("status"), "unknown"),
        "current_claim": _safe_text(surface.get("current_claim"), "unspecified"),
        "preserves_agent_pack_evidence": _bool(
            surface.get("preserves_agent_pack_evidence")
        ),
    }


def _has_operational_readiness_artifact(record: TraceExportRecord) -> bool:
    if record.latest_snapshot is None:
        return False
    return any(
        value == "artifact_treport_operational_readiness"
        for key, value in record.latest_snapshot.state.items()
        if key.endswith("_artifact_id")
    )


def _has_governance_evidence(record: TraceExportRecord) -> bool:
    return any(
        summary.kind == "approval_permission_boundary"
        for summary in record.evidence_summaries
    )


def _has_classified_failure_event(record: TraceExportRecord) -> bool:
    return any(event.type == "agent_treport.failure_classified" for event in record.events)


def _safe_artifact_refs(
    evidence_surfaces: Sequence[Mapping[str, JsonValue]],
) -> list[str]:
    refs: list[str] = []
    for surface in evidence_surfaces:
        for ref in _text_list(surface.get("artifact_refs")):
            if ref not in refs and _is_safe_ref(ref):
                refs.append(ref)
    return refs


def _is_safe_ref(value: str) -> bool:
    if "://" in value:
        return False
    if value.startswith(("/", "\\")):
        return False
    if len(value) >= 3 and value[1] == ":" and value[2] in {"/", "\\"}:
        return False
    return bool(value.strip())


def _source_fingerprint(
    *,
    workflow: str,
    run_id: str,
    subject_id: str,
    rows: Sequence[Mapping[str, JsonValue]],
    unsupported_claims: Sequence[str],
    safe_artifact_refs: Sequence[str],
) -> str:
    payload = {
        "workflow": workflow,
        "run_id": run_id,
        "subject_id": subject_id,
        "rows": [dict(row) for row in rows],
        "unsupported_production_claims": list(unsupported_claims),
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


def _safe_text(value: object, default: str) -> str:
    text = _text(value, default)
    if "://" in text:
        return default
    if len(text) >= 3 and text[1] == ":" and text[2] in {"/", "\\"}:
        return default
    return text


def _text_list(value: object) -> list[str]:
    if not isinstance(value, Sequence) or isinstance(value, str | bytes | bytearray):
        return []
    return [item for item in value if isinstance(item, str) and item]


def _bool(value: object) -> bool:
    return bool(value) if isinstance(value, bool) else False
