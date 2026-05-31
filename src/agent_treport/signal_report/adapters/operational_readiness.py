from __future__ import annotations

import json
from collections.abc import Callable, Iterable, Mapping
from datetime import UTC, date, datetime, timedelta, timezone, tzinfo
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from agent_pack.models import JsonValue

from agent_treport.signal_report.adapters.operational_holdings import (
    COLLECTION_SUMMARY_SCHEMA_VERSION,
    OPERATIONAL_HOLDINGS_SCHEMA_VERSION,
    SYNC_METADATA_SCHEMA_VERSION,
    SYNC_QUALITY_SCHEMA_VERSION,
    OperationalHoldingsInputError,
    compute_operational_export_fingerprint,
)
from agent_treport.signal_report.domain.security_resolution import (
    validate_security_classification,
)

READINESS_SCHEMA_VERSION = "agent_treport.operational_run_readiness.v1"
DEFAULT_OPERATOR_TIMEZONE = "Asia/Seoul"
SOURCE_ACQUISITION_SUMMARY_FILENAME = "source_acquisition_summary.json"
_TICKER_MAPPING_WARNING_THRESHOLD = 0.80
_TICKER_MAPPING_RISK_THRESHOLD = 0.50
_ACTIVE_ETF_COVERAGE_THRESHOLD = 0.80
_MINIMUM_ELIGIBLE_FOCUS_ETF_COUNT = 3
_PARTITION_FIELDS = {
    "etf_id",
    "etf_name",
    "brand_id",
    "source_provider_id",
    "as_of_date",
    "security_id",
    "ticker",
    "name",
    "market",
    "sector",
    "theme",
    "country",
    "weight_percent",
    "shares",
    "market_value_krw",
    "price_krw",
    "is_cash",
    "security_classification",
}
_UNMAPPED_SAMPLE_FIELDS = {
    "security_id",
    "name",
    "observed_row_count",
    "observed_etf_count",
    "observed_date_count",
    "name_alias_count",
}


class OperationalReadinessInputError(ValueError):
    """Raised when readiness CLI inputs are invalid rather than run-readiness failures."""


class _ReadinessContractFailure(ValueError):
    def __init__(
        self,
        code: str,
        message: str,
        *,
        details: Mapping[str, JsonValue] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = dict(details or {})


def check_operational_run_readiness(
    *,
    holdings_path: str | Path,
    focus_etf_id: str | None = None,
    focus_etf_ids: Iterable[str] | None = None,
    observed_partitions: int = 30,
    sync_metadata_path: str | Path | None = None,
    max_observed_age_days: int = 3,
    operator_timezone: str = DEFAULT_OPERATOR_TIMEZONE,
    now: Callable[[], datetime] | None = None,
) -> dict[str, JsonValue]:
    if observed_partitions <= 0:
        raise OperationalReadinessInputError(
            "observed-partitions must be a positive integer"
        )
    if max_observed_age_days < 0:
        raise OperationalReadinessInputError(
            "max-observed-age-days must be a non-negative integer"
        )
    resolved_focus_etf_ids, legacy_single_focus = _resolve_focus_etf_ids(
        focus_etf_id=focus_etf_id,
        focus_etf_ids=focus_etf_ids,
    )
    primary_focus_etf_id = resolved_focus_etf_ids[0]

    timezone = _load_timezone(operator_timezone)
    current_time = _now_utc(now).astimezone(timezone)
    manifest_path = Path(holdings_path)
    resolved_sync_metadata_path = (
        Path(sync_metadata_path)
        if sync_metadata_path is not None
        else manifest_path.parent / "sync_metadata.json"
    )
    resolved_collection_summary_path = manifest_path.parent / "collection_summary.json"
    result = _base_result(
        holdings_path=str(holdings_path),
        sync_metadata_path=str(resolved_sync_metadata_path),
        collection_summary_path=str(resolved_collection_summary_path),
        focus_etf_id=primary_focus_etf_id,
        focus_etf_ids=resolved_focus_etf_ids,
        observed_partitions=observed_partitions,
        operator_timezone=operator_timezone,
        operator_date=current_time.date().isoformat(),
    )
    result["export_fingerprint"] = _compute_readiness_export_fingerprint(manifest_path)
    if not manifest_path.is_file():
        _add_reason(
            result,
            code="missing_holdings_manifest",
            severity="failed",
            message="Requested operational holdings manifest was not found.",
        )
        return _finalize_result(result)

    try:
        manifest_data = _read_json_object(manifest_path, label="holdings manifest")
    except _ReadinessContractFailure as exc:
        _add_contract_failure(result, exc, severity="failed")
        return _finalize_result(result)
    if manifest_data.get("schema_version") != OPERATIONAL_HOLDINGS_SCHEMA_VERSION:
        _add_reason(
            result,
            code="not_normalized_operational_export",
            severity="failed",
            message=(
                "Operational holdings manifest is not a normalized copied export; "
                "run sync-operational-holdings first."
            ),
        )
        return _finalize_result(result)

    try:
        dates = _validate_manifest_dates(manifest_data.get("dates"))
        partitions = _required_mapping(
            manifest_data,
            "partitions",
            code="invalid_manifest_contract",
            label="normalized manifest",
        )
        record_count = _required_non_negative_int(
            manifest_data.get("record_count"),
            code="invalid_manifest_contract",
            message="normalized manifest record_count must be a non-negative integer",
        )
    except _ReadinessContractFailure as exc:
        _add_contract_failure(result, exc, severity="failed")
        return _finalize_result(result)

    latest_observed_date = dates[0]
    result["latest_observed_date"] = latest_observed_date
    latest_observed_age_days = max(
        0,
        (current_time.date() - _parse_iso_date(latest_observed_date)).days,
    )
    result["latest_observed_age_days"] = latest_observed_age_days
    _update_summary(
        result,
        copied_record_count=record_count,
        copied_partition_count=len(partitions),
    )

    if sync_metadata_path is not None or resolved_sync_metadata_path.is_file():
        metadata = _load_sync_metadata(
            resolved_sync_metadata_path,
            explicit=sync_metadata_path is not None,
            result=result,
        )
    else:
        metadata = None

    if metadata is not None:
        result["readiness_evidence_type"] = "legacy_sync"
        _apply_metadata_checks(
            result=result,
            manifest_data=manifest_data,
            manifest_dates=dates,
            manifest_partition_count=len(partitions),
            manifest_record_count=record_count,
            metadata=metadata,
            metadata_path=resolved_sync_metadata_path,
            operator_date=current_time.date(),
        )
    else:
        collection_summary = _load_collection_summary(
            resolved_collection_summary_path,
            result=result,
            native_manifest=manifest_data.get("collection_source_type")
            in {"fixture", "native_history"},
        )
        if collection_summary is not None:
            _apply_collection_summary_checks(
                result=result,
                manifest_data=manifest_data,
                manifest_dates=dates,
                manifest_partition_count=len(partitions),
                manifest_record_count=record_count,
                collection_summary=collection_summary,
                collection_summary_path=resolved_collection_summary_path,
                operator_date=current_time.date(),
            )

    if not legacy_single_focus:
        _apply_source_acquisition_handoff_exclusions(
            result=result,
            summary_path=manifest_path.parent / SOURCE_ACQUISITION_SUMMARY_FILENAME,
        )

    if latest_observed_age_days > max_observed_age_days:
        _add_reason(
            result,
            code="observed_date_stale",
            severity="hold",
            message="Latest copied observed holdings date is stale for today's run.",
            metric="latest_observed_age_days",
            value=latest_observed_age_days,
            threshold=max_observed_age_days,
        )
    elif latest_observed_age_days > 0:
        _add_warning(
            result,
            code="observed_date_lag",
            message="Latest copied observed holdings date lags the operator date.",
            metric="latest_observed_age_days",
            value=latest_observed_age_days,
            threshold=max_observed_age_days,
        )

    try:
        _select_focus_snapshots(
            result=result,
            manifest_path=manifest_path,
            partitions=partitions,
            dates=dates[:observed_partitions],
            focus_etf_id=primary_focus_etf_id,
            focus_etf_ids=resolved_focus_etf_ids,
            legacy_single_focus=legacy_single_focus,
            shared_window=manifest_data.get("collection_source_type") == "native_history",
        )
    except _ReadinessContractFailure as exc:
        _add_contract_failure(result, exc, severity="failed")
    return _finalize_result(result)


def _resolve_focus_etf_ids(
    *,
    focus_etf_id: str | None,
    focus_etf_ids: Iterable[str] | None,
) -> tuple[tuple[str, ...], bool]:
    if focus_etf_ids is not None:
        resolved = tuple(_validated_focus_etf_ids(focus_etf_ids))
        if focus_etf_id is not None and focus_etf_id not in resolved:
            raise OperationalReadinessInputError(
                "focus_etf_id must be included in focus_etf_ids when both are supplied"
            )
        return resolved, False
    if not focus_etf_id:
        raise OperationalReadinessInputError("focus_etf_id or focus_etf_ids is required")
    return (_validated_focus_etf_id(focus_etf_id),), True


def _validated_focus_etf_ids(values: Iterable[str]) -> list[str]:
    resolved: list[str] = []
    seen: set[str] = set()
    for value in values:
        etf_id = _validated_focus_etf_id(value)
        if etf_id in seen:
            raise OperationalReadinessInputError(f"duplicate focus_etf_id: {etf_id}")
        seen.add(etf_id)
        resolved.append(etf_id)
    if not resolved:
        raise OperationalReadinessInputError("focus_etf_ids must be non-empty")
    return resolved


def _validated_focus_etf_id(value: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise OperationalReadinessInputError("focus_etf_id must be a non-empty string")
    etf_id = value.strip()
    if "://" in etf_id or "\\" in etf_id or etf_id.startswith("/") or "C:" in etf_id:
        raise OperationalReadinessInputError("focus_etf_id is not path-safe")
    return etf_id


def _compute_readiness_export_fingerprint(manifest_path: Path) -> dict[str, JsonValue]:
    try:
        return compute_operational_export_fingerprint(manifest_path)
    except OperationalHoldingsInputError as exc:
        raise OperationalReadinessInputError(
            "operational export fingerprint could not be computed: " + str(exc)
        ) from exc


def _load_timezone(value: str) -> tzinfo:
    try:
        return ZoneInfo(value)
    except ZoneInfoNotFoundError as exc:
        if value == DEFAULT_OPERATOR_TIMEZONE:
            return timezone(timedelta(hours=9), DEFAULT_OPERATOR_TIMEZONE)
        raise OperationalReadinessInputError(
            f"invalid operator timezone: {value}"
        ) from exc


def _read_json_object(path: Path, *, label: str) -> dict[str, JsonValue]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise OperationalReadinessInputError(
            f"invalid JSON input: {path}: {exc.msg}"
        ) from exc
    if not isinstance(value, dict):
        code = (
            "invalid_manifest_contract"
            if label == "holdings manifest"
            else "invalid_metadata_contract"
        )
        raise _ReadinessContractFailure(
            code,
            f"{label} input must be a JSON object.",
        )
    return value


def _validate_manifest_dates(value: JsonValue) -> list[str]:
    if not isinstance(value, list) or not value:
        raise _ReadinessContractFailure(
            "invalid_manifest_contract",
            "normalized manifest dates must be a non-empty list.",
        )
    dates: list[str] = []
    parsed_dates: list[date] = []
    for item in value:
        if not isinstance(item, str):
            raise _ReadinessContractFailure(
                "invalid_manifest_contract",
                "normalized manifest dates must be ISO strings.",
            )
        dates.append(item)
        parsed_dates.append(_parse_iso_date(item))
    if parsed_dates != sorted(parsed_dates, reverse=True):
        raise _ReadinessContractFailure(
            "invalid_manifest_contract",
            "normalized manifest dates must be in descending order.",
        )
    return dates


def _parse_iso_date(value: str) -> date:
    try:
        parsed = date.fromisoformat(value)
    except ValueError as exc:
        raise _ReadinessContractFailure(
            "invalid_manifest_contract",
            f"invalid ISO date: {value}",
        ) from exc
    if parsed.isoformat() != value:
        raise _ReadinessContractFailure(
            "invalid_manifest_contract",
            f"invalid ISO date: {value}",
        )
    return parsed


def _required_mapping(
    payload: Mapping[str, JsonValue],
    field: str,
    *,
    code: str,
    label: str,
) -> Mapping[str, JsonValue]:
    value = payload.get(field)
    if not isinstance(value, Mapping):
        raise _ReadinessContractFailure(
            code,
            f"{label} field must be an object: {field}",
        )
    return value


def _required_non_negative_int(
    value: JsonValue,
    *,
    code: str,
    message: str,
) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise _ReadinessContractFailure(code, message)
    return value


def _load_sync_metadata(
    metadata_path: Path,
    *,
    explicit: bool,
    result: dict[str, JsonValue],
) -> dict[str, JsonValue] | None:
    if not metadata_path.is_file():
        if explicit:
            raise OperationalReadinessInputError(
                f"sync metadata file not found: {metadata_path}"
            )
        _add_reason(
            result,
            code="sync_metadata_missing",
            severity="hold",
            message="Sync metadata was not found next to the copied operational export.",
        )
        return None
    try:
        return _read_json_object(metadata_path, label="sync metadata")
    except _ReadinessContractFailure:
        _add_reason(
            result,
            code="sync_metadata_incomplete",
            severity="hold",
            message="Sync metadata is not complete enough for readiness checks.",
        )
        return None


def _load_collection_summary(
    summary_path: Path,
    *,
    result: dict[str, JsonValue],
    native_manifest: bool,
) -> dict[str, JsonValue] | None:
    if not summary_path.is_file():
        if native_manifest:
            _add_reason(
                result,
                code="collection_summary_missing",
                severity="hold",
                message=(
                    "Collection summary was not found next to the native collected "
                    "operational export."
                ),
            )
            return None
        _add_reason(
            result,
            code="sync_metadata_missing",
            severity="hold",
            message="Sync metadata was not found next to the copied operational export.",
        )
        return None
    try:
        return _read_json_object(summary_path, label="collection summary")
    except _ReadinessContractFailure:
        _add_reason(
            result,
            code="collection_summary_incomplete",
            severity="hold",
            message="Collection summary is not complete enough for readiness checks.",
        )
        return None


def _apply_collection_summary_checks(
    *,
    result: dict[str, JsonValue],
    manifest_data: Mapping[str, JsonValue],
    manifest_dates: list[str],
    manifest_partition_count: int,
    manifest_record_count: int,
    collection_summary: Mapping[str, JsonValue],
    collection_summary_path: Path,
    operator_date: date,
) -> None:
    if (
        collection_summary.get("schema_version") != COLLECTION_SUMMARY_SCHEMA_VERSION
        or collection_summary.get("collection_source_type")
        not in {"fixture", "native_history"}
    ):
        _add_reason(
            result,
            code="collection_summary_incomplete",
            severity="hold",
            message="Collection summary schema or source type is incomplete.",
        )
        return

    collection_source_type = str(collection_summary["collection_source_type"])
    result["readiness_evidence_type"] = (
        "native_history"
        if collection_source_type == "native_history"
        else "native_collection"
    )
    result["collection_summary_path"] = collection_summary_path.name

    observed_dates = collection_summary.get("observed_dates")
    partition_count = collection_summary.get("partition_count")
    row_count = collection_summary.get("row_count")
    collected_at = collection_summary.get("collected_at")
    normalized_output = collection_summary.get("normalized_output")
    if (
        not isinstance(observed_dates, list)
        or not isinstance(partition_count, int)
        or isinstance(partition_count, bool)
        or not isinstance(row_count, int)
        or isinstance(row_count, bool)
        or not isinstance(collected_at, str)
        or not isinstance(normalized_output, Mapping)
    ):
        _add_reason(
            result,
            code="collection_summary_incomplete",
            severity="hold",
            message="Collection summary is missing required native collection fields.",
        )
        return

    result["collected_at"] = collected_at
    result["collection_summary"] = _collection_summary_projection(collection_summary)
    mismatched_fields: list[str] = []
    if observed_dates != manifest_dates:
        mismatched_fields.append("observed_dates")
    if partition_count != manifest_partition_count:
        mismatched_fields.append("partition_count")
    if row_count != manifest_record_count:
        mismatched_fields.append("row_count")
    if collected_at != manifest_data.get("collected_at"):
        mismatched_fields.append("collected_at")
    manifest_path = normalized_output.get("manifest_path")
    if manifest_path != Path(str(result["holdings_path"])).name:
        mismatched_fields.append("normalized_output.manifest_path")
    if normalized_output.get("fingerprint") != result.get("export_fingerprint"):
        mismatched_fields.append("normalized_output.fingerprint")
    if mismatched_fields:
        _add_reason(
            result,
            code="collection_summary_mismatch",
            severity="failed",
            message=(
                "Operational holdings manifest and collection summary do not "
                "describe the same native collected export set."
            ),
            details={"fields": mismatched_fields},
        )
        return

    collected_at_time = _parse_synced_at(collected_at)
    if collected_at_time is None:
        _add_reason(
            result,
            code="collection_summary_incomplete",
            severity="hold",
            message="Collection summary collected_at must be a valid ISO timestamp.",
        )
    elif collected_at_time.astimezone(_result_timezone(result)).date() != operator_date:
        _add_reason(
            result,
            code="collection_not_run_today",
            severity="hold",
            message="Native holdings collection was not run on the operator-local date.",
            metric="collected_at_operator_date",
            value=collected_at_time.astimezone(_result_timezone(result)).date().isoformat(),
            threshold=operator_date.isoformat(),
        )

    for item in _collection_summary_warning_items(collection_summary.get("quality_warnings")):
        _add_warning(result, **item)
    for item in _collection_summary_warning_items(collection_summary.get("limitations")):
        _add_warning(result, **item)
    if collection_source_type == "native_history":
        _apply_native_history_coverage(
            result=result,
            collection_summary=collection_summary,
        )
        _apply_native_security_coverage(
            result=result,
            collection_summary=collection_summary,
        )


def _collection_summary_projection(
    collection_summary: Mapping[str, JsonValue],
) -> dict[str, JsonValue]:
    projected: dict[str, JsonValue] = {}
    for field in (
        "schema_version",
        "collection_source_type",
        "requested_observed_partitions",
        "observed_dates",
        "etf_count",
        "brand_count",
        "partition_count",
        "row_count",
        "normalized_output",
        "active_etf_coverage",
        "security_coverage",
    ):
        if field not in collection_summary:
            continue
        value = collection_summary.get(field)
        if _is_json_value(value):
            projected[field] = value
    if "brand_count" not in projected and "manager_count" in collection_summary:
        value = collection_summary.get("manager_count")
        if _is_json_value(value):
            projected["brand_count"] = value
    return projected


def _apply_source_acquisition_handoff_exclusions(
    *,
    result: dict[str, JsonValue],
    summary_path: Path,
) -> None:
    if not summary_path.is_file():
        return
    try:
        summary = _read_json_object(summary_path, label="source acquisition summary")
    except OperationalReadinessInputError:
        _add_reason(
            result,
            code="source_acquisition_summary_unsafe",
            severity="failed",
            message="Source acquisition summary could not be parsed safely.",
        )
        return
    exclusions = _source_handoff_exclusions(summary)
    if not exclusions:
        return
    result["handoff_exclusions"] = exclusions
    _add_warning(
        result,
        code="handoff_exclusions_present",
        message=(
            "Some provider targets were excluded from the handoff denominator "
            "using path-safe source availability evidence."
        ),
        metric="handoff_exclusion_count",
        value=len(exclusions),
        threshold=0,
    )


def _source_handoff_exclusions(
    summary: Mapping[str, JsonValue],
) -> list[dict[str, JsonValue]]:
    target_outcomes = summary.get("target_outcomes")
    if not isinstance(target_outcomes, list):
        return []
    exclusions: list[dict[str, JsonValue]] = []
    for item in target_outcomes:
        if not isinstance(item, Mapping):
            continue
        outcome = item.get("outcome")
        if outcome not in {"failed", "rate_limited", "unsupported", "retry_cooldown"}:
            continue
        source_provider_id = _safe_summary_text(item.get("source_provider_id"))
        etf_id = _safe_summary_text(item.get("etf_id"))
        if source_provider_id is None or etf_id is None:
            continue
        reason_code = _safe_summary_text(
            item.get("reason_code", item.get("failure_code_class"))
        )
        if reason_code is None:
            reason_code = str(outcome)
        observed_dates_missing = _safe_date_list(item.get("observed_dates_missing"))
        if not observed_dates_missing:
            requested_date = item.get("requested_date")
            observed_date = item.get("observed_date")
            if isinstance(requested_date, str) and observed_date is None:
                observed_dates_missing = [requested_date]
        exclusion: dict[str, JsonValue] = {
            "source_provider_id": source_provider_id,
            "etf_id": etf_id,
            "scope": _safe_summary_text(item.get("scope")) or "holdings_snapshot",
            "reason_code": reason_code,
            "observed_dates_missing": observed_dates_missing,
        }
        for field in (
            "retry_after",
            "blocked_until",
            "cooldown_remaining_seconds",
            "next_backfill_date_count",
            "last_successful_observed_date",
        ):
            value = item.get(field)
            if value is not None and _is_json_value(value):
                exclusion[field] = value
        exclusions.append(exclusion)
    return exclusions


def _safe_date_list(value: JsonValue) -> list[str]:
    if not isinstance(value, list):
        return []
    dates: list[str] = []
    for item in value:
        if not isinstance(item, str):
            continue
        try:
            _parse_iso_date(item)
        except _ReadinessContractFailure:
            continue
        dates.append(item)
    return dates


def _safe_summary_text(value: object) -> str | None:
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip()
    if "://" in text or "\\" in text or text.startswith("/") or "C:" in text or "Users" in text:
        return None
    return text


def _apply_native_history_coverage(
    *,
    result: dict[str, JsonValue],
    collection_summary: Mapping[str, JsonValue],
) -> None:
    raw_coverage = collection_summary.get("active_etf_coverage")
    if not isinstance(raw_coverage, Mapping):
        _add_reason(
            result,
            code="collection_summary_incomplete",
            severity="hold",
            message="Native history collection summary is missing active ETF coverage.",
        )
        return
    selected_current_date = raw_coverage.get("selected_current_date")
    selected_previous_date = raw_coverage.get("selected_previous_date")
    active_count = raw_coverage.get("active_etf_count")
    complete_count = raw_coverage.get("complete_active_etf_count")
    missing_ids = raw_coverage.get("missing_active_etf_ids")
    coverage_ratio = raw_coverage.get("coverage_ratio")
    if (
        not isinstance(selected_current_date, str)
        or not isinstance(selected_previous_date, str)
        or not isinstance(active_count, int)
        or isinstance(active_count, bool)
        or active_count <= 0
        or not isinstance(complete_count, int)
        or isinstance(complete_count, bool)
        or complete_count < 0
        or not isinstance(missing_ids, list)
        or not all(isinstance(item, str) for item in missing_ids)
        or not isinstance(coverage_ratio, int | float)
        or isinstance(coverage_ratio, bool)
    ):
        _add_reason(
            result,
            code="collection_summary_incomplete",
            severity="hold",
            message="Native history active ETF coverage is incomplete.",
        )
        return

    focus_etf_ids = {
        str(item)
        for item in result.get("focus_etf_ids", [])
        if isinstance(item, str)
    }
    focus_etf_id = str(result["focus_etf_id"])
    if not focus_etf_ids:
        focus_etf_ids = {focus_etf_id}
    missing_active_etf_ids = sorted(str(item) for item in missing_ids)
    missing_non_focus_ids = [
        etf_id for etf_id in missing_active_etf_ids if etf_id not in focus_etf_ids
    ]
    non_focus_count = max(0, active_count - len(focus_etf_ids))
    non_focus_complete_count = max(0, non_focus_count - len(missing_non_focus_ids))
    non_focus_ratio = (
        _rounded_ratio(non_focus_complete_count, non_focus_count)
        if non_focus_count > 0
        else 1.0
    )
    coverage: dict[str, JsonValue] = {
        "selected_current_date": selected_current_date,
        "selected_previous_date": selected_previous_date,
        "active_etf_count": active_count,
        "complete_active_etf_count": complete_count,
        "missing_active_etf_ids": missing_active_etf_ids,
        "coverage_ratio": float(coverage_ratio),
        "non_focus_active_etf_count": non_focus_count,
        "non_focus_complete_etf_count": non_focus_complete_count,
        "non_focus_coverage_ratio": non_focus_ratio,
    }
    result["active_etf_coverage"] = coverage
    if non_focus_count == 0 or not missing_non_focus_ids:
        return

    details: dict[str, JsonValue] = {
        "selected_current_date": selected_current_date,
        "selected_previous_date": selected_previous_date,
        "missing_active_etf_ids": missing_non_focus_ids,
    }
    _add_warning(
        result,
        code="active_etf_coverage_gap",
        message=(
            "Some non-focus active ETFs are missing one side of the comparison "
            "window; this is operator diagnostic coverage and does not block "
            "the focus handoff."
        ),
        metric="non_focus_active_etf_coverage_ratio",
        value=non_focus_ratio,
        threshold=_ACTIVE_ETF_COVERAGE_THRESHOLD,
        details=details,
    )


def _apply_native_security_coverage(
    *,
    result: dict[str, JsonValue],
    collection_summary: Mapping[str, JsonValue],
) -> None:
    raw_coverage = collection_summary.get("security_coverage")
    if not isinstance(raw_coverage, Mapping):
        _add_warning(
            result,
            code="ticker_mapping_coverage_unavailable",
            message="Native history security coverage is unavailable.",
            metric="ticker_mapping_coverage_ratio",
            value=None,
            threshold=_TICKER_MAPPING_WARNING_THRESHOLD,
        )
        return

    security_resolution_available = raw_coverage.get("security_resolution_available")
    mapped_count = _int_or_none(raw_coverage.get("mapped_ticker_candidate_count"))
    unresolved_count = _int_or_none(raw_coverage.get("unresolved_ticker_candidate_count"))
    unknown_count = _int_or_none(raw_coverage.get("unknown_count"))
    non_ticker_excluded_count = _int_or_none(raw_coverage.get("non_ticker_excluded_count"))
    reviewed_mapping_count = _int_or_none(raw_coverage.get("reviewed_mapping_applied_count"))
    reviewed_exclusion_count = _int_or_none(
        raw_coverage.get("reviewed_exclusion_applied_count")
    )
    recovery_sample_count = _int_or_none(raw_coverage.get("recovery_sample_count"))
    ratio_value = raw_coverage.get("ticker_mapping_coverage_ratio")
    ratio: float | None
    if isinstance(ratio_value, int | float) and not isinstance(ratio_value, bool):
        ratio = float(ratio_value)
    elif ratio_value is None:
        ratio = None
    else:
        ratio = None

    if (
        not isinstance(security_resolution_available, bool)
        or mapped_count is None
        or unresolved_count is None
        or unknown_count is None
        or non_ticker_excluded_count is None
        or reviewed_mapping_count is None
        or reviewed_exclusion_count is None
        or recovery_sample_count is None
    ):
        _add_reason(
            result,
            code="collection_summary_incomplete",
            severity="hold",
            message="Native history security coverage is incomplete.",
        )
        return

    recovery_samples = _native_recovery_samples(raw_coverage.get("recovery_samples"))
    coverage: dict[str, JsonValue] = {
        "security_resolution_available": security_resolution_available,
        "mapped_ticker_candidate_count": mapped_count,
        "unresolved_ticker_candidate_count": unresolved_count,
        "unknown_count": unknown_count,
        "non_ticker_excluded_count": non_ticker_excluded_count,
        "reviewed_mapping_applied_count": reviewed_mapping_count,
        "reviewed_exclusion_applied_count": reviewed_exclusion_count,
        "ticker_mapping_coverage_ratio": ratio,
        "recovery_sample_count": recovery_sample_count,
        "recovery_samples": recovery_samples,
    }
    result["security_coverage"] = coverage
    result["top_unmapped_security_samples"] = recovery_samples
    _update_summary(
        result,
        mapped_security_count=mapped_count,
        unmapped_security_count=unresolved_count,
        ticker_mapping_coverage_ratio=ratio,
        unmapped_security_sample_count=recovery_sample_count,
        unknown_security_count=unknown_count,
        non_ticker_excluded_security_count=non_ticker_excluded_count,
        security_resolution_available=security_resolution_available,
        reviewed_mapping_applied_count=reviewed_mapping_count,
        reviewed_exclusion_applied_count=reviewed_exclusion_count,
    )

    if not security_resolution_available:
        _add_warning(
            result,
            code="security_resolution_missing",
            message="Native history export was not run with reviewed security resolution.",
            metric="security_resolution_available",
            value=False,
            threshold=True,
        )
    if ratio is None:
        _add_warning(
            result,
            code="ticker_mapping_coverage_unavailable",
            message="Ticker mapping coverage ratio is unavailable.",
            metric="ticker_mapping_coverage_ratio",
            value=None,
            threshold=_TICKER_MAPPING_WARNING_THRESHOLD,
        )
    elif ratio < _TICKER_MAPPING_RISK_THRESHOLD:
        _add_reason(
            result,
            code="low_ticker_mapping_coverage",
            severity="hold",
            message="Ticker mapping coverage is below the operational review threshold.",
            metric="ticker_mapping_coverage_ratio",
            value=ratio,
            threshold=_TICKER_MAPPING_RISK_THRESHOLD,
        )
    elif ratio < _TICKER_MAPPING_WARNING_THRESHOLD:
        _add_warning(
            result,
            code="ticker_mapping_coverage_warning",
            message="Ticker mapping coverage is below the operational warning threshold.",
            metric="ticker_mapping_coverage_ratio",
            value=ratio,
            threshold=_TICKER_MAPPING_WARNING_THRESHOLD,
        )
    if unknown_count > 0:
        _add_warning(
            result,
            code="unknown_security_classification",
            message="Native history export contains unresolved unknown security classifications.",
            metric="unknown_count",
            value=unknown_count,
            threshold=0,
        )


def _native_recovery_samples(value: JsonValue) -> list[dict[str, JsonValue]]:
    if not isinstance(value, list):
        return []
    samples: list[dict[str, JsonValue]] = []
    for item in value:
        if not isinstance(item, Mapping):
            continue
        projected: dict[str, JsonValue] = {}
        for field in (
            "security_id",
            "name",
            "observed_row_count",
            "observed_etf_count",
            "observed_date_count",
            "name_alias_count",
            "security_classification",
        ):
            field_value = item.get(field)
            if _is_json_value(field_value):
                projected[field] = field_value
        if projected:
            samples.append(projected)
    return samples


def _collection_summary_warning_items(
    value: JsonValue,
) -> list[dict[str, JsonValue]]:
    if not isinstance(value, list):
        return []
    items: list[dict[str, JsonValue]] = []
    for raw_item in value:
        if not isinstance(raw_item, Mapping):
            continue
        code = raw_item.get("code")
        message = raw_item.get("message")
        if not isinstance(code, str) or not isinstance(message, str) or not message:
            continue
        item: dict[str, JsonValue] = {
            "code": code,
            "message": message,
        }
        for field in ("metric", "value", "threshold"):
            if field not in raw_item:
                continue
            field_value = raw_item.get(field)
            if _is_json_value(field_value):
                item[field] = field_value
        items.append(item)
    return items


def _apply_metadata_checks(
    *,
    result: dict[str, JsonValue],
    manifest_data: Mapping[str, JsonValue],
    manifest_dates: list[str],
    manifest_partition_count: int,
    manifest_record_count: int,
    metadata: Mapping[str, JsonValue],
    metadata_path: Path,
    operator_date: date,
) -> None:
    sync_quality = metadata.get("sync_quality")
    if (
        metadata.get("schema_version") != SYNC_METADATA_SCHEMA_VERSION
        or not isinstance(sync_quality, Mapping)
        or sync_quality.get("schema_version") != SYNC_QUALITY_SCHEMA_VERSION
    ):
        _add_reason(
            result,
            code="sync_metadata_incomplete",
            severity="hold",
            message="Sync metadata schema or sync quality details are incomplete.",
        )
        _populate_summary_from_metadata(result, metadata)
        return

    _populate_summary_from_metadata(result, metadata)
    result["top_unmapped_security_samples"] = _top_unmapped_security_samples(metadata)
    copied_dates = metadata.get("copied_dates")
    copied_record_count = metadata.get("copied_record_count")
    copied_partition_count = metadata.get("copied_partition_count")
    synced_at = metadata.get("synced_at")
    source_dates = metadata.get("source_dates")
    required_fields_available = (
        copied_dates is not None
        and copied_record_count is not None
        and copied_partition_count is not None
        and synced_at is not None
        and source_dates is not None
    )
    if not required_fields_available:
        _add_reason(
            result,
            code="sync_metadata_incomplete",
            severity="hold",
            message="Sync metadata is missing required copied-export fields.",
        )
        return

    mismatched_fields: list[str] = []
    if copied_dates != manifest_dates:
        mismatched_fields.append("copied_dates")
    if copied_record_count != manifest_record_count:
        mismatched_fields.append("copied_record_count")
    if copied_partition_count != manifest_partition_count:
        mismatched_fields.append("copied_partition_count")
    if synced_at != manifest_data.get("synced_at"):
        mismatched_fields.append("synced_at")
    if mismatched_fields:
        _add_reason(
            result,
            code="manifest_metadata_mismatch",
            severity="failed",
            message=(
                "Operational holdings manifest and sync metadata do not describe "
                "the same copied export set."
            ),
            details={"fields": mismatched_fields},
        )
        return

    if not isinstance(synced_at, str):
        _add_reason(
            result,
            code="sync_metadata_incomplete",
            severity="hold",
            message="Sync metadata synced_at must be an ISO timestamp.",
        )
        return
    result["synced_at"] = synced_at
    synced_at_time = _parse_synced_at(synced_at)
    if synced_at_time is None:
        _add_reason(
            result,
            code="sync_metadata_incomplete",
            severity="hold",
            message="Sync metadata synced_at must be a valid ISO timestamp.",
        )
    elif synced_at_time.astimezone(_result_timezone(result)).date() != operator_date:
        _add_reason(
            result,
            code="sync_not_run_today",
            severity="hold",
            message="Operational holdings sync was not run on the operator-local date.",
            metric="synced_at_operator_date",
            value=synced_at_time.astimezone(_result_timezone(result)).date().isoformat(),
            threshold=operator_date.isoformat(),
        )

    if not isinstance(source_dates, list) or not source_dates:
        _add_reason(
            result,
            code="sync_metadata_incomplete",
            severity="hold",
            message="Sync metadata source_dates are missing.",
        )
    else:
        latest_source_date = _source_date_to_iso(source_dates[0])
        if latest_source_date is None:
            _add_reason(
                result,
                code="sync_metadata_incomplete",
                severity="hold",
                message="Sync metadata latest source date is invalid.",
            )
        elif latest_source_date != manifest_dates[0]:
            _add_reason(
                result,
                code="source_latest_not_copied",
                severity="hold",
                message="Latest source observed date was not copied into the operational export.",
                value=manifest_dates[0],
                threshold=latest_source_date,
            )

    _apply_sync_quality(result, sync_quality)
    _ = metadata_path


def _result_timezone(result: Mapping[str, JsonValue]) -> tzinfo:
    timezone_value = result.get("operator_timezone")
    assert isinstance(timezone_value, str)
    return _load_timezone(timezone_value)


def _parse_synced_at(value: str) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        return parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _source_date_to_iso(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    if len(value) == 8 and value.isdigit():
        try:
            return datetime.strptime(value, "%Y%m%d").date().isoformat()
        except ValueError:
            return None
    try:
        return date.fromisoformat(value).isoformat()
    except ValueError:
        return None


def _populate_summary_from_metadata(
    result: dict[str, JsonValue],
    metadata: Mapping[str, JsonValue],
) -> None:
    sync_quality = metadata.get("sync_quality")
    metrics = sync_quality.get("metrics") if isinstance(sync_quality, Mapping) else None
    mapped_count = _int_or_none(metadata.get("mapped_security_count"))
    unmapped_count = _int_or_none(metadata.get("unmapped_security_count"))
    ratio: JsonValue = None
    if isinstance(metrics, Mapping):
        ratio_value = metrics.get("ticker_mapping_coverage_ratio")
        if isinstance(ratio_value, int | float) and not isinstance(ratio_value, bool):
            ratio = float(ratio_value)
    missing_source_count: JsonValue = None
    if isinstance(metrics, Mapping):
        metric_count = metrics.get("missing_source_date_count")
        if isinstance(metric_count, int) and not isinstance(metric_count, bool):
            missing_source_count = metric_count
    if missing_source_count is None:
        missing_sources = metadata.get("missing_source_dates")
        missing_source_count = len(missing_sources) if isinstance(missing_sources, list) else None

    _update_summary(
        result,
        copied_record_count=_int_or_none(metadata.get("copied_record_count")),
        copied_partition_count=_int_or_none(metadata.get("copied_partition_count")),
        source_record_count=_int_or_none(metadata.get("source_record_count")),
        mapped_security_count=mapped_count,
        unmapped_security_count=unmapped_count,
        ticker_mapping_coverage_ratio=ratio,
        unmapped_security_sample_count=_list_len(metadata.get("unmapped_security_samples")),
        missing_source_date_count=missing_source_count,
        sync_quality_status=(
            str(sync_quality.get("status")) if isinstance(sync_quality, Mapping) else None
        ),
    )


def _apply_sync_quality(
    result: dict[str, JsonValue],
    sync_quality: Mapping[str, JsonValue],
) -> None:
    status = sync_quality.get("status")
    metrics = sync_quality.get("metrics")
    warnings = sync_quality.get("warnings")
    risk_failures = sync_quality.get("risk_failures")
    if status == "risk_failed":
        _add_reason(
            result,
            code="sync_quality_risk_failed",
            severity="hold",
            message="Sync quality reported review-level source-data risk.",
            details=_sync_quality_source_details(risk_failures),
        )
    elif status == "warning":
        _add_warning(
            result,
            code="sync_quality_warning",
            message="Sync quality reported source-data warnings.",
            details=_sync_quality_source_details(warnings),
        )

    coverage_ratio: JsonValue = None
    if isinstance(metrics, Mapping):
        coverage_ratio = metrics.get("ticker_mapping_coverage_ratio")
    if coverage_ratio is None:
        _add_warning(
            result,
            code="ticker_mapping_coverage_unavailable",
            message="Ticker mapping coverage ratio is unavailable.",
            metric="ticker_mapping_coverage_ratio",
            value=None,
            threshold=_TICKER_MAPPING_WARNING_THRESHOLD,
        )
    elif isinstance(coverage_ratio, int | float) and not isinstance(coverage_ratio, bool):
        ratio = float(coverage_ratio)
        if ratio < _TICKER_MAPPING_RISK_THRESHOLD:
            _add_reason(
                result,
                code="low_ticker_mapping_coverage",
                severity="hold",
                message="Ticker mapping coverage is below the operational review threshold.",
                metric="ticker_mapping_coverage_ratio",
                value=ratio,
                threshold=_TICKER_MAPPING_RISK_THRESHOLD,
            )
        elif ratio < _TICKER_MAPPING_WARNING_THRESHOLD:
            _add_warning(
                result,
                code="ticker_mapping_coverage_warning",
                message="Ticker mapping coverage is below the operational warning threshold.",
                metric="ticker_mapping_coverage_ratio",
                value=ratio,
                threshold=_TICKER_MAPPING_WARNING_THRESHOLD,
            )
    else:
        _add_warning(
            result,
            code="ticker_mapping_coverage_unavailable",
            message="Ticker mapping coverage ratio is unavailable.",
            metric="ticker_mapping_coverage_ratio",
            value=None,
            threshold=_TICKER_MAPPING_WARNING_THRESHOLD,
        )


def _sync_quality_source_details(value: JsonValue) -> dict[str, JsonValue]:
    if not isinstance(value, list):
        return {"source_codes": [], "source_items": []}
    source_codes = [
        item.get("code")
        for item in value
        if isinstance(item, Mapping) and isinstance(item.get("code"), str)
    ]
    return {
        "source_codes": source_codes,
        "source_items": [
            projected
            for item in value
            if isinstance(item, Mapping)
            if (projected := _sync_quality_source_item(item))
        ],
    }


def _sync_quality_source_item(item: Mapping[str, JsonValue]) -> dict[str, JsonValue]:
    projected: dict[str, JsonValue] = {}
    for field in ("code", "message", "metric", "value", "threshold"):
        value = item.get(field)
        if _is_json_value(value):
            projected[field] = value
    return projected


def _top_unmapped_security_samples(
    metadata: Mapping[str, JsonValue],
) -> list[dict[str, JsonValue]]:
    samples = metadata.get("unmapped_security_samples")
    if not isinstance(samples, list):
        return []
    projected: list[dict[str, JsonValue]] = []
    for sample in samples:
        if not isinstance(sample, Mapping):
            continue
        item: dict[str, JsonValue] = {}
        for field in _UNMAPPED_SAMPLE_FIELDS:
            value = sample.get(field)
            if field in {"security_id", "name"}:
                if not isinstance(value, str) or not value:
                    break
                item[field] = value
            else:
                if not isinstance(value, int) or isinstance(value, bool) or value < 0:
                    break
                item[field] = value
        else:
            projected.append(
                {
                    "security_id": item["security_id"],
                    "name": item["name"],
                    "observed_row_count": item["observed_row_count"],
                    "observed_etf_count": item["observed_etf_count"],
                    "observed_date_count": item["observed_date_count"],
                    "name_alias_count": item["name_alias_count"],
                }
            )
    return projected[:3]


def _select_focus_snapshots(
    *,
    result: dict[str, JsonValue],
    manifest_path: Path,
    partitions: Mapping[str, JsonValue],
    dates: Iterable[str],
    focus_etf_id: str,
    focus_etf_ids: tuple[str, ...],
    legacy_single_focus: bool,
    shared_window: bool = False,
) -> None:
    scan_dates = list(dates)
    scanned_dates: list[str] = []
    missing_partition_dates: list[str] = []
    rows_by_date: dict[str, list[dict[str, JsonValue]]] = {}
    current_date: str | None = None
    previous_date: str | None = None
    current_rows: list[dict[str, JsonValue]] = []
    previous_rows: list[dict[str, JsonValue]] = []

    for partition_date in scan_dates:
        scanned_dates.append(partition_date)
        partition_path = _partition_path(
            manifest_path=manifest_path,
            partitions=partitions,
            partition_date=partition_date,
        )
        if not partition_path.is_file():
            missing_partition_dates.append(partition_date)
            continue
        expected_record_count = _partition_record_count(partitions, partition_date)
        rows = _read_partition(
            partition_path=partition_path,
            partition_date=partition_date,
            expected_record_count=expected_record_count,
        )
        rows_by_date[partition_date] = rows
        if not shared_window and any(row["etf_id"] == focus_etf_id for row in rows):
            if current_date is None:
                current_date = partition_date
                current_rows = rows
            else:
                previous_date = partition_date
                previous_rows = rows
                break

    result["scanned_dates"] = scanned_dates
    result["missing_partition_dates"] = missing_partition_dates
    _update_summary(
        result,
        missing_partition_date_count=len(missing_partition_dates),
    )
    if not legacy_single_focus:
        _select_focus_set_snapshots(
            result=result,
            rows_by_date=rows_by_date,
            dates=scan_dates,
            focus_etf_ids=focus_etf_ids,
            missing_partition_dates=missing_partition_dates,
        )
        return
    if shared_window:
        _select_shared_window_snapshots(
            result=result,
            rows_by_date=rows_by_date,
            dates=scan_dates,
            missing_partition_dates=missing_partition_dates,
            focus_etf_id=focus_etf_id,
        )
        return
    if current_date is None:
        _add_reason(
            result,
            code="focus_etf_not_found",
            severity="failed",
            message="Focus ETF was not found in the scanned copied holdings partitions.",
        )
        return
    if previous_date is None:
        if missing_partition_dates:
            _add_reason(
                result,
                code="missing_partition_file",
                severity="failed",
                message="Missing copied partition files prevented snapshot selection.",
                details={"dates": missing_partition_dates},
            )
        _add_reason(
            result,
            code="previous_snapshot_not_found",
            severity="failed",
            message="Previous focus ETF snapshot was not found in scanned copied partitions.",
        )
        result["current_date"] = current_date
        return

    result["current_date"] = current_date
    result["previous_date"] = previous_date
    current_focus_count = _focus_row_count(current_rows, focus_etf_id)
    previous_focus_count = _focus_row_count(previous_rows, focus_etf_id)
    included_etf_count = len(_etf_ids(current_rows) & _etf_ids(previous_rows))
    _update_summary(
        result,
        current_record_count=len(current_rows),
        previous_record_count=len(previous_rows),
        current_focus_etf_record_count=current_focus_count,
        previous_focus_etf_record_count=previous_focus_count,
        included_etf_count=included_etf_count,
    )
    if missing_partition_dates:
        _add_warning(
            result,
            code="missing_partition_dates",
            message=(
                "Some scanned copied partitions were missing but did not block "
                "current and previous focus ETF selection."
            ),
            metric="missing_partition_date_count",
            value=len(missing_partition_dates),
            threshold=0,
            details={"dates": missing_partition_dates},
        )
    _ = rows_by_date


def _select_focus_set_snapshots(
    *,
    result: dict[str, JsonValue],
    rows_by_date: Mapping[str, list[dict[str, JsonValue]]],
    dates: list[str],
    focus_etf_ids: tuple[str, ...],
    missing_partition_dates: list[str],
) -> None:
    windows: list[dict[str, JsonValue]] = []
    eligible_ids: list[str] = []
    ineligible_ids: list[str] = []
    current_record_count = 0
    previous_record_count = 0

    for focus_etf_id in focus_etf_ids:
        matched_dates: list[str] = []
        for partition_date in dates:
            rows = rows_by_date.get(partition_date)
            if rows is None:
                continue
            if any(row["etf_id"] == focus_etf_id for row in rows):
                matched_dates.append(partition_date)
            if len(matched_dates) == 2:
                break
        if len(matched_dates) < 2:
            ineligible_ids.append(focus_etf_id)
            continue
        current_date, previous_date = matched_dates
        current_rows = rows_by_date[current_date]
        previous_rows = rows_by_date[previous_date]
        current_count = _focus_row_count(current_rows, focus_etf_id)
        previous_count = _focus_row_count(previous_rows, focus_etf_id)
        if current_count <= 0 or previous_count <= 0:
            ineligible_ids.append(focus_etf_id)
            continue
        eligible_ids.append(focus_etf_id)
        current_record_count += current_count
        previous_record_count += previous_count
        windows.append(
            {
                "etf_id": focus_etf_id,
                "selected_current_date": current_date,
                "selected_previous_date": previous_date,
            }
        )

    selected_current_dates = {
        str(window["selected_current_date"]) for window in windows
    }
    selected_previous_dates = {
        str(window["selected_previous_date"]) for window in windows
    }
    mixed_windows = (
        len(selected_current_dates) > 1
        or len(selected_previous_dates) > 1
    )
    if selected_current_dates:
        result["current_date"] = max(selected_current_dates, key=_parse_iso_date)
    if selected_previous_dates:
        result["previous_date"] = max(selected_previous_dates, key=_parse_iso_date)
    focus_eligibility: dict[str, JsonValue] = {
        "minimum_eligible_focus_etf_count": _MINIMUM_ELIGIBLE_FOCUS_ETF_COUNT,
        "eligible_focus_etf_count": len(eligible_ids),
        "eligible_focus_etf_ids": eligible_ids,
        "ineligible_focus_etf_ids": ineligible_ids,
        "mixed_comparison_windows": mixed_windows,
        "comparison_windows": windows,
        "handoff_exclusions": [
            item for item in result.get("handoff_exclusions", []) if isinstance(item, dict)
        ],
    }
    result["focus_eligibility"] = focus_eligibility
    _update_summary(
        result,
        current_record_count=current_record_count,
        previous_record_count=previous_record_count,
        current_focus_etf_record_count=current_record_count,
        previous_focus_etf_record_count=previous_record_count,
        included_etf_count=len(eligible_ids),
        eligible_focus_etf_count=len(eligible_ids),
        ineligible_focus_etf_count=len(ineligible_ids),
    )
    if len(eligible_ids) < _MINIMUM_ELIGIBLE_FOCUS_ETF_COUNT:
        _add_reason(
            result,
            code="insufficient_focus_etf_eligibility",
            severity="hold",
            message=(
                "Fewer than three focus ETFs have two valid holdings snapshots for "
                "the operational handoff."
            ),
            metric="eligible_focus_etf_count",
            value=len(eligible_ids),
            threshold=_MINIMUM_ELIGIBLE_FOCUS_ETF_COUNT,
            details={
                "eligible_focus_etf_ids": eligible_ids,
                "ineligible_focus_etf_ids": ineligible_ids,
            },
        )
    if missing_partition_dates:
        _add_warning(
            result,
            code="missing_partition_dates",
            message=(
                "Some scanned copied partitions were missing but did not block "
                "focus ETF set snapshot selection."
            ),
            metric="missing_partition_date_count",
            value=len(missing_partition_dates),
            threshold=0,
            details={"dates": missing_partition_dates},
        )


def _select_shared_window_snapshots(
    *,
    result: dict[str, JsonValue],
    rows_by_date: Mapping[str, list[dict[str, JsonValue]]],
    dates: list[str],
    missing_partition_dates: list[str],
    focus_etf_id: str,
) -> None:
    if len(dates) < 2:
        _add_reason(
            result,
            code="previous_snapshot_not_found",
            severity="failed",
            message="Native history comparison export does not include two shared dates.",
        )
        return
    current_date = dates[0]
    previous_date = dates[1]
    result["current_date"] = current_date
    result["previous_date"] = previous_date
    current_rows = rows_by_date.get(current_date)
    previous_rows = rows_by_date.get(previous_date)
    if current_rows is None or previous_rows is None:
        _add_reason(
            result,
            code="missing_partition_file",
            severity="failed",
            message="Missing copied partition files prevented shared snapshot selection.",
            details={"dates": missing_partition_dates},
        )
        return
    current_focus_count = _focus_row_count(current_rows, focus_etf_id)
    previous_focus_count = _focus_row_count(previous_rows, focus_etf_id)
    _update_summary(
        result,
        current_record_count=len(current_rows),
        previous_record_count=len(previous_rows),
        current_focus_etf_record_count=current_focus_count,
        previous_focus_etf_record_count=previous_focus_count,
        included_etf_count=len(_etf_ids(current_rows) & _etf_ids(previous_rows)),
    )
    if current_focus_count == 0:
        _add_reason(
            result,
            code="focus_current_snapshot_not_found",
            severity="failed",
            message="Focus ETF was not found in the selected current shared window.",
        )
    if previous_focus_count == 0:
        _add_reason(
            result,
            code="focus_previous_snapshot_not_found",
            severity="failed",
            message="Focus ETF was not found in the selected previous shared window.",
        )


def _partition_path(
    *,
    manifest_path: Path,
    partitions: Mapping[str, JsonValue],
    partition_date: str,
) -> Path:
    partition = partitions.get(partition_date)
    if not isinstance(partition, Mapping):
        raise _ReadinessContractFailure(
            "missing_partition_entry",
            f"Missing normalized partition entry: {partition_date}.",
            details={"date": partition_date},
        )
    file_value = partition.get("file")
    if not isinstance(file_value, str) or not file_value:
        raise _ReadinessContractFailure(
            "unsafe_partition_path",
            f"Partition file must be manifest-relative: {partition_date}.",
            details={"date": partition_date},
        )
    relative_path = Path(file_value)
    if relative_path.is_absolute():
        raise _ReadinessContractFailure(
            "unsafe_partition_path",
            f"Partition file must be manifest-relative: {partition_date}.",
            details={"date": partition_date},
        )
    manifest_dir = manifest_path.parent.resolve()
    resolved = (manifest_dir / relative_path).resolve()
    try:
        resolved.relative_to(manifest_dir)
    except ValueError as exc:
        raise _ReadinessContractFailure(
            "unsafe_partition_path",
            f"Partition file must stay inside copied export directory: {partition_date}.",
            details={"date": partition_date},
        ) from exc
    return resolved


def _partition_record_count(
    partitions: Mapping[str, JsonValue],
    partition_date: str,
) -> int:
    partition = partitions.get(partition_date)
    if not isinstance(partition, Mapping):
        raise _ReadinessContractFailure(
            "missing_partition_entry",
            f"Missing normalized partition entry: {partition_date}.",
        )
    value = partition.get("record_count")
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise _ReadinessContractFailure(
            "invalid_partition_contract",
            f"Partition record_count must be a non-negative integer: {partition_date}.",
        )
    return value


def _read_partition(
    *,
    partition_path: Path,
    partition_date: str,
    expected_record_count: int,
) -> list[dict[str, JsonValue]]:
    rows: list[dict[str, JsonValue]] = []
    seen: set[tuple[str, str, str]] = set()
    try:
        lines = partition_path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise _ReadinessContractFailure(
            "missing_partition_file",
            f"Copied partition file could not be read: {partition_date}.",
            details={"date": partition_date},
        ) from exc
    for line_number, line in enumerate(lines, 1):
        if not line.strip():
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError as exc:
            raise _ReadinessContractFailure(
                "invalid_partition_contract",
                f"Partition JSONL row is invalid: {partition_date} line {line_number}.",
                details={"date": partition_date, "line_number": line_number},
            ) from exc
        if not isinstance(payload, Mapping):
            raise _ReadinessContractFailure(
                "invalid_partition_contract",
                f"Partition row must be an object: {partition_date} line {line_number}.",
                details={"date": partition_date, "line_number": line_number},
            )
        row = _validate_partition_row(
            payload,
            partition_date=partition_date,
            line_number=line_number,
        )
        key = (str(row["etf_id"]), partition_date, str(row["security_id"]))
        if key in seen:
            raise _ReadinessContractFailure(
                "duplicate_normalized_holding",
                "Duplicate normalized holding row found in copied partition.",
                details={
                    "etf_id": key[0],
                    "date": key[1],
                    "security_id": key[2],
                },
            )
        seen.add(key)
        rows.append(row)
    if len(rows) != expected_record_count:
        raise _ReadinessContractFailure(
            "partition_record_count_mismatch",
            "Copied partition row count does not match the manifest record_count.",
            details={
                "date": partition_date,
                "expected": expected_record_count,
                "actual": len(rows),
            },
        )
    return rows


def _validate_partition_row(
    row: Mapping[str, Any],
    *,
    partition_date: str,
    line_number: int,
) -> dict[str, JsonValue]:
    normalized = dict(row)
    if "brand_id" not in normalized and "manager_id" in normalized:
        normalized["brand_id"] = normalized["manager_id"]
    if "source_provider_id" not in normalized and "provider_id" in normalized:
        normalized["source_provider_id"] = normalized["provider_id"]
    normalized.pop("manager_id", None)
    normalized.pop("provider_id", None)
    missing = sorted(field for field in _PARTITION_FIELDS if field not in normalized)
    if missing:
        raise _ReadinessContractFailure(
            "invalid_partition_contract",
            "Normalized holding row is missing required fields.",
            details={
                "date": partition_date,
                "line_number": line_number,
                "fields": missing,
            },
        )
    if normalized.get("as_of_date") != partition_date:
        raise _ReadinessContractFailure(
            "invalid_partition_contract",
            "Normalized holding row as_of_date does not match the partition date.",
            details={"date": partition_date, "line_number": line_number},
        )
    for field in (
        "etf_id",
        "etf_name",
        "brand_id",
        "source_provider_id",
        "security_id",
        "name",
    ):
        value = normalized.get(field)
        if not isinstance(value, str) or not value.strip():
            raise _ReadinessContractFailure(
                "invalid_partition_contract",
                f"Normalized holding field must be a non-empty string: {field}.",
                details={"date": partition_date, "line_number": line_number},
            )
    if normalized.get("weight_percent") is None:
        raise _ReadinessContractFailure(
            "invalid_partition_contract",
            "Normalized holding field must not be null: weight_percent.",
            details={"date": partition_date, "line_number": line_number},
        )
    if not isinstance(normalized.get("is_cash"), bool):
        raise _ReadinessContractFailure(
            "invalid_partition_contract",
            "Normalized holding field must be boolean: is_cash.",
            details={"date": partition_date, "line_number": line_number},
        )
    try:
        validate_security_classification(
            normalized.get("security_classification"),
            label="Normalized holding security_classification",
        )
    except ValueError as exc:
        raise _ReadinessContractFailure(
            "invalid_partition_contract",
            str(exc),
            details={"date": partition_date, "line_number": line_number},
        ) from exc
    return normalized


def _focus_row_count(rows: Iterable[Mapping[str, JsonValue]], focus_etf_id: str) -> int:
    return sum(1 for row in rows if row.get("etf_id") == focus_etf_id)


def _etf_ids(rows: Iterable[Mapping[str, JsonValue]]) -> set[str]:
    return {
        str(row["etf_id"])
        for row in rows
        if isinstance(row.get("etf_id"), str) and str(row["etf_id"])
    }


def _int_or_none(value: JsonValue) -> int | None:
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    return None


def _list_len(value: JsonValue) -> int | None:
    if isinstance(value, list):
        return len(value)
    return None


def _update_summary(result: dict[str, JsonValue], **values: JsonValue) -> None:
    summary = result["summary"]
    assert isinstance(summary, dict)
    for key, value in values.items():
        if value is not None or summary.get(key) is None:
            summary[key] = value


def _add_contract_failure(
    result: dict[str, JsonValue],
    exc: _ReadinessContractFailure,
    *,
    severity: str,
) -> None:
    extra: dict[str, JsonValue] = {}
    if exc.details:
        extra["details"] = exc.details
    _add_reason(
        result,
        code=exc.code,
        severity=severity,
        message=exc.message,
        **extra,
    )


def _now_utc(now: Callable[[], datetime] | None) -> datetime:
    value = now() if now is not None else datetime.now(UTC)
    if value.tzinfo is None or value.utcoffset() is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _base_result(
    *,
    holdings_path: str,
    sync_metadata_path: str,
    collection_summary_path: str,
    focus_etf_id: str,
    focus_etf_ids: tuple[str, ...],
    observed_partitions: int,
    operator_timezone: str,
    operator_date: str,
) -> dict[str, JsonValue]:
    return {
        "schema_version": READINESS_SCHEMA_VERSION,
        "status": "ready",
        "user_ready_allowed": True,
        "holdings_path": holdings_path,
        "sync_metadata_path": sync_metadata_path,
        "collection_summary_path": collection_summary_path,
        "readiness_evidence_type": "unknown",
        "focus_etf_id": focus_etf_id,
        "focus_etf_ids": list(focus_etf_ids),
        "focus_eligibility": None,
        "handoff_exclusions": [],
        "requested_observed_partitions": observed_partitions,
        "operator_timezone": operator_timezone,
        "operator_date": operator_date,
        "latest_observed_date": None,
        "latest_observed_age_days": None,
        "synced_at": None,
        "collected_at": None,
        "current_date": None,
        "previous_date": None,
        "scanned_dates": [],
        "missing_partition_dates": [],
        "reasons": [],
        "warnings": [],
        "next_actions": [],
        "summary": _empty_summary(),
        "collection_summary": None,
        "active_etf_coverage": None,
        "top_unmapped_security_samples": [],
        "final_user_ready_requirements": {
            "readiness_user_ready_allowed": True,
            "run_report_status_required": "succeeded",
            "report_quality_status_required": "passed",
            "warning_disclosure_required": False,
        },
    }


def _empty_summary() -> dict[str, JsonValue]:
    return {
        "copied_record_count": None,
        "copied_partition_count": None,
        "source_record_count": None,
        "mapped_security_count": None,
        "unmapped_security_count": None,
        "ticker_mapping_coverage_ratio": None,
        "unmapped_security_sample_count": None,
        "missing_source_date_count": None,
        "missing_partition_date_count": 0,
        "sync_quality_status": None,
        "current_record_count": None,
        "previous_record_count": None,
        "current_focus_etf_record_count": None,
        "previous_focus_etf_record_count": None,
        "included_etf_count": None,
    }


def _add_reason(
    result: dict[str, JsonValue],
    *,
    code: str,
    severity: str,
    message: str,
    **extra: Any,
) -> None:
    reasons = result["reasons"]
    assert isinstance(reasons, list)
    reason: dict[str, JsonValue] = {
        "code": code,
        "severity": severity,
        "message": message,
    }
    reason.update({key: value for key, value in extra.items() if _is_json_value(value)})
    reasons.append(reason)


def _add_warning(
    result: dict[str, JsonValue],
    *,
    code: str,
    message: str,
    **extra: Any,
) -> None:
    warnings = result["warnings"]
    assert isinstance(warnings, list)
    warning: dict[str, JsonValue] = {
        "code": code,
        "severity": "warning",
        "message": message,
    }
    warning.update({key: value for key, value in extra.items() if _is_json_value(value)})
    warnings.append(warning)


def _finalize_result(result: dict[str, JsonValue]) -> dict[str, JsonValue]:
    reasons = result["reasons"]
    warnings = result["warnings"]
    assert isinstance(reasons, list)
    assert isinstance(warnings, list)
    severities = {
        str(reason.get("severity"))
        for reason in reasons
        if isinstance(reason, dict)
    }
    if "failed" in severities:
        status = "failed"
    elif "hold" in severities:
        status = "hold"
    elif warnings:
        status = "ready_with_warnings"
    else:
        status = "ready"
    result["status"] = status
    result["user_ready_allowed"] = status in {"ready", "ready_with_warnings"}
    final_requirements = result["final_user_ready_requirements"]
    assert isinstance(final_requirements, dict)
    final_requirements["readiness_user_ready_allowed"] = result["user_ready_allowed"]
    final_requirements["warning_disclosure_required"] = bool(warnings)
    result["next_actions"] = _build_next_actions(
        reason_codes=[
            str(reason.get("code")) for reason in reasons if isinstance(reason, dict)
        ],
        risk_source_codes=_sync_quality_source_codes(
            reasons,
            parent_code="sync_quality_risk_failed",
        ),
        warning_codes=[
            str(warning.get("code")) for warning in warnings if isinstance(warning, dict)
        ],
        warning_source_codes=_sync_quality_source_codes(
            warnings,
            parent_code="sync_quality_warning",
        ),
        readiness_evidence_type=str(result.get("readiness_evidence_type")),
        allowed=bool(result["user_ready_allowed"]),
    )
    return result


def _sync_quality_source_codes(items: list[JsonValue], *, parent_code: str) -> list[str]:
    codes: list[str] = []
    for item in items:
        if not isinstance(item, Mapping) or item.get("code") != parent_code:
            continue
        details = item.get("details")
        if not isinstance(details, Mapping):
            continue
        source_codes = details.get("source_codes")
        if not isinstance(source_codes, list):
            continue
        codes.extend(code for code in source_codes if isinstance(code, str))
    return _dedupe(codes)


def _build_next_actions(
    *,
    reason_codes: list[str],
    risk_source_codes: list[str],
    warning_codes: list[str],
    warning_source_codes: list[str],
    readiness_evidence_type: str,
    allowed: bool,
) -> list[dict[str, JsonValue]]:
    actions: list[dict[str, JsonValue]] = []
    native_security_evidence = readiness_evidence_type == "native_history"
    mapping_recovery_command = (
        "agent-treport propose-security-mapping-recovery "
        "--collection-summary-path <collection_summary.json> --model codex "
        "--output-path <proposal.json>"
        if native_security_evidence
        else (
            "agent-treport propose-security-mapping-recovery "
            "--sync-metadata-path <sync_metadata.json> --model codex "
            "--output-path <proposal.json>"
        )
    )
    failed_codes = [code for code in reason_codes if code in _FAILED_INPUT_CODES()]
    hold_refresh_codes = [
        code
        for code in reason_codes
        if code
        in {
            "sync_metadata_missing",
            "sync_metadata_incomplete",
            "sync_not_run_today",
            "observed_date_stale",
            "source_latest_not_copied",
            "active_etf_coverage_below_threshold",
        }
    ]
    hold_mapping_codes = [
        code
        for code in reason_codes
        if code in {"low_ticker_mapping_coverage"}
    ]
    hold_mapping_codes.extend(
        code for code in risk_source_codes if code == "low_ticker_mapping_coverage"
    )
    cash_derivation_risk_codes = [
        code for code in risk_source_codes if code == "cash_derivation_failure_ratio"
    ]
    general_warning_codes = [
        code
        for code in warning_codes
        if code
        in {
            "observed_date_lag",
            "sync_quality_warning",
            "missing_partition_dates",
            "fixture_backed_collection",
            "active_etf_coverage_gap",
        }
    ]
    mapping_warning_codes = [
        code
        for code in warning_codes
        if code
        in {
            "ticker_mapping_coverage_warning",
            "ticker_mapping_coverage_unavailable",
        }
    ]
    cash_derivation_warning_codes = [
        code for code in warning_source_codes if code == "cash_derivation_failure_ratio"
    ]
    if failed_codes:
        actions.append(
            {
                "code": "fix_operational_export",
                "action_type": "required_before_run",
                "required": True,
                "message": (
                    "Fix the copied operational holdings export contract, then "
                    "rerun readiness."
                ),
                "command_hint": (
                    "agent-treport sync-operational-holdings --source "
                    "<source_manifest> --dest <dest>"
                ),
                "for_codes": _dedupe(failed_codes),
            }
        )
    if hold_refresh_codes:
        actions.append(
            {
                "code": "refresh_operational_sync",
                "action_type": "required_before_run",
                "required": True,
                "message": (
                    "Refresh the copied operational holdings export and sync "
                    "metadata before a user-ready run."
                ),
                "command_hint": (
                    "agent-treport sync-operational-holdings --source "
                    "<source_manifest> --dest <dest>"
                ),
                "for_codes": _dedupe(hold_refresh_codes),
            }
        )
    if hold_mapping_codes:
        actions.append(
            {
                "code": "recover_ticker_mapping",
                "action_type": "required_before_run",
                "required": True,
                "message": (
                    "Review and improve native security coverage before treating "
                    "the report as user-ready."
                    if native_security_evidence
                    else (
                        "Review and improve SecurityMapping coverage before treating "
                        "the report as user-ready."
                    )
                ),
                "command_hint": mapping_recovery_command,
                "for_codes": _dedupe(hold_mapping_codes),
            }
        )
    if cash_derivation_risk_codes:
        actions.append(
            {
                "code": "review_cash_derivation_risk",
                "action_type": "required_before_run",
                "required": True,
                "message": (
                    "Review cash derivation failures before treating the report "
                    "as user-ready."
                ),
                "for_codes": _dedupe(cash_derivation_risk_codes),
            }
        )
    if general_warning_codes:
        actions.append(
            {
                "code": "review_warnings",
                "action_type": "recommended_improvement",
                "required": False,
                "message": (
                    "Review and disclose readiness warnings if proceeding with "
                    "a user-ready run."
                ),
                "for_codes": _dedupe(general_warning_codes),
            }
        )
    if mapping_warning_codes:
        actions.append(
            {
                "code": "improve_ticker_mapping",
                "action_type": "recommended_improvement",
                "required": False,
                "message": (
                    "Improve native security coverage when available, then "
                    "re-run export and readiness."
                    if native_security_evidence
                    else (
                        "Improve SecurityMapping coverage when available, then "
                        "re-run sync and readiness."
                    )
                ),
                "command_hint": mapping_recovery_command,
                "for_codes": _dedupe(mapping_warning_codes),
            }
        )
    if cash_derivation_warning_codes:
        actions.append(
            {
                "code": "review_cash_derivation_warning",
                "action_type": "recommended_improvement",
                "required": False,
                "message": (
                    "Review cash derivation warnings and disclose the limitation "
                    "when proceeding with a user-ready run."
                ),
                "for_codes": _dedupe(cash_derivation_warning_codes),
            }
        )
    if allowed:
        actions.append(
            {
                "code": "run_report",
                "action_type": "allowed_next_step",
                "required": False,
                "message": "Readiness allows running the operational SignalReportWorkflow.",
                "command_hint": (
                    "agent-treport run-report --holdings-source operational "
                    "--holdings-path <holdings_path> --focus-etf-id <focus_etf_id>"
                ),
                "for_codes": [],
            }
        )
    return actions


def _FAILED_INPUT_CODES() -> set[str]:
    return {
        "missing_holdings_manifest",
        "not_normalized_operational_export",
        "invalid_manifest_contract",
        "invalid_metadata_contract",
        "manifest_metadata_mismatch",
        "collection_summary_mismatch",
        "unsafe_partition_path",
        "missing_partition_entry",
        "missing_partition_file",
        "invalid_partition_contract",
        "partition_record_count_mismatch",
        "duplicate_normalized_holding",
        "focus_etf_not_found",
        "focus_current_snapshot_not_found",
        "focus_previous_snapshot_not_found",
        "previous_snapshot_not_found",
    }


def _dedupe(values: Iterable[str]) -> list[str]:
    result: list[str] = []
    for value in values:
        if value not in result:
            result.append(value)
    return result


def _rounded_ratio(numerator: int, denominator: int) -> float:
    if denominator <= 0:
        return 0.0
    return round(numerator / denominator, 6)


def _is_json_value(value: object) -> bool:
    if value is None or isinstance(value, str | int | float | bool):
        return True
    if isinstance(value, list):
        return all(_is_json_value(item) for item in value)
    if isinstance(value, dict):
        return all(isinstance(key, str) and _is_json_value(item) for key, item in value.items())
    return False
