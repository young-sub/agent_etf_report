from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path

from agent_pack.models import JsonValue

APPROVAL_PROFILE_SCHEMA_VERSION = (
    "agent_treport.daily_operational_external_data_approval.v1"
)
PREFLIGHT_SCHEMA_VERSION = (
    "agent_treport.daily_operational_external_data_preflight.v1"
)
DEFAULT_APPROVAL_PROFILE_PATH = (
    "data/agent_treport/approvals/daily_operational_external_data_approval.json"
)
DEFAULT_PREFLIGHT_FILENAME = "daily_operational_external_data_preflight.json"
DEFAULT_APPROVAL_TEMPLATE_FILENAME = (
    "daily_operational_external_data_approval_template.json"
)

EXCLUDED_RAW_FIELDS = (
    "raw_holdings_rows",
    "raw_report_text",
    "raw_provider_payloads",
    "provider_response_envelopes",
    "raw_urls_or_endpoints",
    "headers",
    "credentials",
    "environment_values",
    "stack_traces",
    "absolute_local_paths",
    "raw_approval_comments",
)
REPORT_MODEL_EXPORT_SCOPE = (
    "path_safe_pre_publish_report_context_for_commentary_generation"
)

EXTERNAL_PROVIDER_ENV_VARS: Mapping[str, tuple[str, ...]] = {
    "alpha_vantage": ("ALPHAVANTAGE_API_KEY",),
    "dart": ("DART_API_KEY",),
    "finnhub": ("FINNHUB_API_KEY",),
    "naver": ("NAVER_CLIENT_ID", "NAVER_CLIENT_SECRET"),
    "newsapi": ("NEWS_API_KEY",),
}


def normalize_scope_list(values: Sequence[str]) -> list[str]:
    return sorted(dict.fromkeys(values))


def default_preflight_path(base_dir: Path) -> Path:
    return base_dir / DEFAULT_PREFLIGHT_FILENAME


def approval_template_path(preflight_path: Path) -> Path:
    return preflight_path.with_name(DEFAULT_APPROVAL_TEMPLATE_FILENAME)


def external_provider_credential_expectations(
    provider_ids: Sequence[str],
) -> list[str]:
    names: list[str] = []
    for provider_id in provider_ids:
        names.extend(EXTERNAL_PROVIDER_ENV_VARS.get(provider_id, ()))
    return sorted(dict.fromkeys(names))


def build_daily_approval_boundary(
    *,
    required_scopes: Sequence[str],
    external_evidence_provider_ids: Sequence[str] = (),
    known_unvalidated_provider_exceptions: Sequence[Mapping[str, JsonValue]] = (),
    model_exports: Sequence[Mapping[str, JsonValue]] = (),
    live_source_provider_ids: Sequence[str] = (),
    approved_max_target_count: int = 0,
    data_classes: Sequence[str] = (),
    live_source_cohort: Sequence[str] = (),
) -> dict[str, JsonValue]:
    return {
        "required_scopes": normalize_scope_list(required_scopes),
        "external_evidence_provider_ids": list(dict.fromkeys(external_evidence_provider_ids)),
        "known_unvalidated_provider_exceptions": [
            dict(item) for item in known_unvalidated_provider_exceptions
        ],
        "model_exports": [dict(item) for item in model_exports],
        "live_source_provider_ids": list(dict.fromkeys(live_source_provider_ids)),
        "live_source_cohort": list(dict.fromkeys(live_source_cohort)),
        "approved_max_target_count": approved_max_target_count,
        "data_classes": sorted(dict.fromkeys(data_classes)),
        "excluded_raw_fields": list(EXCLUDED_RAW_FIELDS),
        "report_model_export_scope": REPORT_MODEL_EXPORT_SCOPE,
    }


def boundary_fingerprint(boundary: Mapping[str, JsonValue]) -> str:
    canonical = json.dumps(boundary, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def build_preflight_document(
    *,
    command: str,
    boundary: Mapping[str, JsonValue],
    focus_etf_ids: Sequence[str] = (),
    safe_security_identifiers: Sequence[str] = (),
    safe_ticker_identifiers: Sequence[str] = (),
    generated_at: datetime | None = None,
) -> dict[str, JsonValue]:
    required_scopes = _text_list(boundary.get("required_scopes"))
    provider_ids = _text_list(boundary.get("external_evidence_provider_ids"))
    provider_exceptions = _json_list(
        boundary.get("known_unvalidated_provider_exceptions")
    )
    exception_provider_ids = [
        item["provider_id"]
        for item in provider_exceptions
        if isinstance(item, Mapping) and isinstance(item.get("provider_id"), str)
    ]
    fingerprint = boundary_fingerprint(boundary)
    return {
        "schema_version": PREFLIGHT_SCHEMA_VERSION,
        "generated_at": (generated_at or datetime.now(UTC)).isoformat(),
        "command": command,
        "approval": {
            "required_scopes": required_scopes,
            "boundary_fingerprint": fingerprint,
        },
        "boundary": dict(boundary),
        "disclosure": {
            "provider_identities": {
                "external_evidence": provider_ids,
                "known_unvalidated_external_evidence": exception_provider_ids,
                "live_source": _text_list(boundary.get("live_source_provider_ids")),
                "model_exports": _json_list(boundary.get("model_exports")),
            },
            "provider_exception_summary": [
                {
                    "provider_id": item.get("provider_id"),
                    "exception_type": item.get("exception_type"),
                    "required_for_user_ready_closure": item.get(
                        "required_for_user_ready_closure"
                    ),
                    "execution_status": item.get("execution_status"),
                }
                for item in provider_exceptions
                if isinstance(item, Mapping)
            ],
            "evidence_categories": ["financial", "disclosure", "news"],
            "approved_max_target_count": boundary.get("approved_max_target_count", 0),
            "live_source_cohort": _text_list(boundary.get("live_source_cohort")),
            "focus_etf_ids": list(dict.fromkeys(focus_etf_ids)),
            "selected_security_identifiers": _sampled_identity_summary(
                safe_security_identifiers
            ),
            "selected_ticker_identifiers": _sampled_identity_summary(
                safe_ticker_identifiers
            ),
            "data_classes": _text_list(boundary.get("data_classes")),
            "credential_expectations": external_provider_credential_expectations(
                provider_ids
            ),
            "excluded_raw_fields": list(EXCLUDED_RAW_FIELDS),
            "report_model_export_scope": REPORT_MODEL_EXPORT_SCOPE,
        },
    }


def build_approval_template(
    *,
    boundary: Mapping[str, JsonValue],
) -> dict[str, JsonValue]:
    return {
        "schema_version": APPROVAL_PROFILE_SCHEMA_VERSION,
        "status": "pending",
        "required_scopes": _text_list(boundary.get("required_scopes")),
        "boundary_fingerprint": boundary_fingerprint(boundary),
        "approved_external_evidence_provider_ids": _text_list(
            boundary.get("external_evidence_provider_ids")
        ),
        "known_unvalidated_provider_exceptions": _json_list(
            boundary.get("known_unvalidated_provider_exceptions")
        ),
        "approved_model_exports": _json_list(boundary.get("model_exports")),
        "approved_live_source_provider_ids": _text_list(
            boundary.get("live_source_provider_ids")
        ),
        "approved_live_source_cohort": _text_list(boundary.get("live_source_cohort")),
        "approved_max_target_count": boundary.get("approved_max_target_count", 0),
        "data_classes": _text_list(boundary.get("data_classes")),
        "excluded_raw_fields": list(EXCLUDED_RAW_FIELDS),
        "report_model_export_scope": REPORT_MODEL_EXPORT_SCOPE,
        "expires_at": None,
        "instructions": [
            "Review the sibling preflight disclosure before approving.",
            "Set status to approved only when this boundary is acceptable.",
            "Set status to revoked to stop future daily export under this profile.",
        ],
    }


def write_preflight_and_template(
    *,
    preflight_path: Path,
    command: str,
    boundary: Mapping[str, JsonValue],
    focus_etf_ids: Sequence[str] = (),
    safe_security_identifiers: Sequence[str] = (),
    safe_ticker_identifiers: Sequence[str] = (),
    generated_at: datetime | None = None,
) -> tuple[dict[str, JsonValue], Path, dict[str, JsonValue]]:
    preflight = build_preflight_document(
        command=command,
        boundary=boundary,
        focus_etf_ids=focus_etf_ids,
        safe_security_identifiers=safe_security_identifiers,
        safe_ticker_identifiers=safe_ticker_identifiers,
        generated_at=generated_at,
    )
    template_path = approval_template_path(preflight_path)
    template = build_approval_template(boundary=boundary)
    _write_json(preflight_path, preflight)
    _write_json(template_path, template)
    return preflight, template_path, template


def evaluate_approval_profile(
    *,
    approval_path: Path,
    boundary: Mapping[str, JsonValue],
    now: datetime | None = None,
) -> dict[str, JsonValue]:
    required_scopes = _text_list(boundary.get("required_scopes"))
    if not approval_path.is_file():
        return {
            "valid": False,
            "status": "missing",
            "approval_path": str(approval_path),
            "required_scopes": required_scopes,
            "missing_scopes": required_scopes,
            "unapproved_scopes": required_scopes,
            "outside_approved_bounds": [],
            "approved_boundary_fingerprint": None,
            "requested_boundary_fingerprint": boundary_fingerprint(boundary),
        }
    try:
        raw = json.loads(approval_path.read_text(encoding="utf-8"))
    except Exception:
        return _invalid_summary(
            approval_path=approval_path,
            boundary=boundary,
            status="invalid",
            required_scopes=required_scopes,
            reason="approval profile is unreadable or invalid JSON",
        )
    if not isinstance(raw, Mapping):
        return _invalid_summary(
            approval_path=approval_path,
            boundary=boundary,
            status="invalid",
            required_scopes=required_scopes,
            reason="approval profile must be a JSON object",
        )

    status = raw.get("status")
    approved_scopes = _text_list(raw.get("approved_scopes") or raw.get("required_scopes"))
    unapproved_scopes = sorted(set(required_scopes).difference(approved_scopes))
    expired = _is_expired(raw.get("expires_at"), now=now)
    outside_bounds = _approval_bound_mismatches(profile=raw, boundary=boundary)
    approved_fingerprint = raw.get("boundary_fingerprint")
    valid = (
        status == "approved"
        and not expired
        and not unapproved_scopes
        and not outside_bounds
        and isinstance(approved_fingerprint, str)
    )
    if status == "approved" and expired:
        result_status = "expired"
    elif status in {"pending", "revoked"}:
        result_status = status
    elif status == "approved" and outside_bounds:
        result_status = "outside_approved_bounds"
    elif status == "approved" and unapproved_scopes:
        result_status = "missing_scope"
    elif status == "approved":
        result_status = "approved"
    else:
        result_status = "invalid"
    missing_scopes = (
        unapproved_scopes if unapproved_scopes else ([] if valid else required_scopes)
    )
    return {
        "valid": valid,
        "status": result_status,
        "approval_path": str(approval_path),
        "required_scopes": required_scopes,
        "missing_scopes": missing_scopes,
        "unapproved_scopes": unapproved_scopes,
        "outside_approved_bounds": outside_bounds,
        "approved_boundary_fingerprint": approved_fingerprint
        if isinstance(approved_fingerprint, str)
        else None,
        "requested_boundary_fingerprint": boundary_fingerprint(boundary),
        "expires_at": raw.get("expires_at") if isinstance(raw.get("expires_at"), str) else None,
    }


def _approval_bound_mismatches(
    *,
    profile: Mapping[str, object],
    boundary: Mapping[str, JsonValue],
) -> list[str]:
    mismatches: list[str] = []
    approved_scopes = _text_list(
        profile.get("approved_scopes") or profile.get("required_scopes")
    )
    approved_max_target_count = profile.get("approved_max_target_count")
    if (
        not isinstance(approved_max_target_count, int)
        or isinstance(approved_max_target_count, bool)
    ):
        approved_max_target_count = 0
    approved_boundary = build_daily_approval_boundary(
        required_scopes=approved_scopes,
        external_evidence_provider_ids=_text_list(
            profile.get("approved_external_evidence_provider_ids")
        ),
        known_unvalidated_provider_exceptions=_json_list(
            profile.get("known_unvalidated_provider_exceptions")
        ),
        model_exports=_json_list(profile.get("approved_model_exports")),
        live_source_provider_ids=_text_list(
            profile.get("approved_live_source_provider_ids")
        ),
        live_source_cohort=_text_list(profile.get("approved_live_source_cohort")),
        approved_max_target_count=approved_max_target_count,
        data_classes=_text_list(profile.get("data_classes")),
    )
    if profile.get("boundary_fingerprint") != boundary_fingerprint(approved_boundary):
        mismatches.append("boundary_fingerprint")
    if not _is_subset(
        _text_list(boundary.get("external_evidence_provider_ids")),
        _text_list(profile.get("approved_external_evidence_provider_ids")),
    ):
        mismatches.append("external_evidence_provider_ids")
    if not _json_items_covered(
        _json_list(boundary.get("known_unvalidated_provider_exceptions")),
        _json_list(profile.get("known_unvalidated_provider_exceptions")),
    ):
        mismatches.append("known_unvalidated_provider_exceptions")
    if not _is_subset(
        _text_list(boundary.get("live_source_provider_ids")),
        _text_list(profile.get("approved_live_source_provider_ids")),
    ):
        mismatches.append("live_source_provider_ids")
    if not _is_subset(
        _text_list(boundary.get("live_source_cohort")),
        _text_list(profile.get("approved_live_source_cohort")),
    ):
        mismatches.append("live_source_cohort")
    requested_target_count = boundary.get("approved_max_target_count")
    approved_target_count = profile.get("approved_max_target_count")
    if (
        isinstance(requested_target_count, int)
        and not isinstance(requested_target_count, bool)
        and isinstance(approved_target_count, int)
        and not isinstance(approved_target_count, bool)
    ):
        if requested_target_count > approved_target_count:
            mismatches.append("approved_max_target_count")
    elif requested_target_count:
        mismatches.append("approved_max_target_count")
    if not _is_subset(
        _text_list(boundary.get("data_classes")),
        _text_list(profile.get("data_classes")),
    ):
        mismatches.append("data_classes")
    if _text_list(boundary.get("excluded_raw_fields")) != _text_list(
        profile.get("excluded_raw_fields")
    ):
        mismatches.append("excluded_raw_fields")
    if boundary.get("report_model_export_scope") != profile.get(
        "report_model_export_scope"
    ):
        mismatches.append("report_model_export_scope")
    if not _model_exports_covered(
        _json_list(boundary.get("model_exports")),
        _json_list(profile.get("approved_model_exports")),
    ):
        mismatches.append("model_exports")
    return mismatches


def _model_exports_covered(
    requested: Sequence[JsonValue],
    approved: Sequence[JsonValue],
) -> bool:
    return _json_items_covered(requested, approved)


def _json_items_covered(
    requested: Sequence[JsonValue],
    approved: Sequence[JsonValue],
) -> bool:
    approved_keys = {
        json.dumps(item, ensure_ascii=False, sort_keys=True)
        for item in approved
        if isinstance(item, Mapping)
    }
    return all(
        json.dumps(item, ensure_ascii=False, sort_keys=True) in approved_keys
        for item in requested
        if isinstance(item, Mapping)
    )


def _invalid_summary(
    *,
    approval_path: Path,
    boundary: Mapping[str, JsonValue],
    status: str,
    required_scopes: Sequence[str],
    reason: str,
) -> dict[str, JsonValue]:
    return {
        "valid": False,
        "status": status,
        "approval_path": str(approval_path),
        "required_scopes": list(required_scopes),
        "missing_scopes": list(required_scopes),
        "unapproved_scopes": list(required_scopes),
        "outside_approved_bounds": [],
        "approved_boundary_fingerprint": None,
        "requested_boundary_fingerprint": boundary_fingerprint(boundary),
        "reason": reason,
    }


def _is_expired(value: object, *, now: datetime | None) -> bool:
    if value is None:
        return False
    if not isinstance(value, str) or not value:
        return True
    try:
        expires_at = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return True
    current = now or datetime.now(UTC)
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=UTC)
    if current.tzinfo is None:
        current = current.replace(tzinfo=UTC)
    return expires_at <= current


def _sampled_identity_summary(
    values: Sequence[str],
    *,
    sample_limit: int = 20,
) -> dict[str, JsonValue]:
    unique = list(dict.fromkeys(values))
    return {
        "count": len(unique),
        "sample_limit": sample_limit,
        "sample": unique[:sample_limit],
        "fingerprint": hashlib.sha256(
            json.dumps(unique, ensure_ascii=False, sort_keys=True).encode("utf-8")
        ).hexdigest(),
    }


def _is_subset(requested: Sequence[str], approved: Sequence[str]) -> bool:
    return set(requested).issubset(set(approved))


def _text_list(value: object) -> list[str]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        return []
    return [item for item in value if isinstance(item, str)]


def _json_list(value: object) -> list[JsonValue]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        return []
    return [item for item in value if _is_json_value(item)]


def _is_json_value(value: object) -> bool:
    if value is None or isinstance(value, str | int | float | bool):
        return True
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return all(_is_json_value(item) for item in value)
    if isinstance(value, Mapping):
        return all(isinstance(key, str) and _is_json_value(item) for key, item in value.items())
    return False


def _write_json(path: Path, payload: Mapping[str, JsonValue]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )
