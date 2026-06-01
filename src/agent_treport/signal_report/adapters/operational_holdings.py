from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

from agent_pack.models import JsonValue

from agent_treport.signal_report.adapters.errors import SignalReportInputError
from agent_treport.signal_report.adapters.operational_universe import (
    OperationalUniverseInputError,
    load_active_universe_etfs,
)
from agent_treport.signal_report.domain.evidence import EvidenceItemInput
from agent_treport.signal_report.domain.inputs import SignalReportInputs
from agent_treport.signal_report.domain.security_resolution import (
    SecurityClassificationPolicy,
    analytical_identity_for_security,
    validate_security_classification,
    validate_security_resolution_export,
)
from agent_treport.signal_report.domain.snapshots import (
    ETFHoldingsSnapshots,
    MultiETFHoldingsSnapshots,
    SecurityHolding,
)

SYNC_METADATA_SCHEMA_VERSION = "agent_treport.operational_holdings.sync_metadata.v1"
SYNC_QUALITY_SCHEMA_VERSION = "agent_treport.operational_holdings.sync_quality.v1"
SECURITY_MAPPING_SCHEMA_VERSION = "agent_treport.security_mapping.v1"
SECURITY_MAPPING_PATCH_SCHEMA_VERSION = "agent_treport.security_mapping.patch.v1"
OPERATIONAL_HOLDINGS_SCHEMA_VERSION = "agent_treport.operational_holdings.v1"
NORMALIZED_STORAGE_FORMAT = "normalized_partitioned_jsonl_v1"
PROVENANCE_SCHEMA_VERSION = "agent_treport.operational_holdings.provenance.v1"
OPERATIONAL_EXPORT_FINGERPRINT_SCOPE = "copied_manifest_and_referenced_partitions_v1"
COLLECTION_SUMMARY_SCHEMA_VERSION = "agent_treport.native_collection.summary.v1"
NATIVE_FIXTURE_SCHEMA_VERSION = "agent_treport.native_holdings.fixture.v1"
HOLDINGS_HISTORY_SCHEMA_VERSION = "agent_treport.native_holdings.history.v1"
HOLDINGS_HISTORY_UPDATE_SCHEMA_VERSION = (
    "agent_treport.native_holdings.history_update.v1"
)
HOLDINGS_HISTORY_STORAGE_FORMAT = "native_history_partitioned_jsonl_v1"
HOLDINGS_HISTORY_MANIFEST_FILENAME = "holdings_history.json"


class OperationalHoldingsInputError(SignalReportInputError):
    """Raised when an operational holdings export violates the input contract."""


@dataclass
class _AggregateHolding:
    row: dict[str, JsonValue]
    shares_sum: float | None
    market_value_sum: float | None
    weight_sum: float | None
    source_weight_missing: bool
    cash_rule_id: str | None
    numeric_null_count: int
    line_numbers: list[int]
    sample_context: dict[str, JsonValue]
    skipped_reason: str | None = None


@dataclass(frozen=True)
class _SecurityResolution:
    mappings: Mapping[str, Mapping[str, JsonValue]]
    exclusions: Mapping[str, Mapping[str, JsonValue]]


@dataclass(frozen=True)
class NativeBrandMetadata:
    brand_id: str
    name: str


@dataclass(frozen=True)
class NativeETFUniverseEntry:
    etf_id: str
    etf_name: str
    brand_id: str
    source_provider_id: str


@dataclass(frozen=True)
class NativeHoldingSnapshot:
    as_of_date: str
    holdings: tuple[Mapping[str, JsonValue], ...]


_CASH_IDENTIFICATION_RULE_IDS = (
    "code_exact_cash",
    "code_prefix_cash",
    "code_prefix_currency",
    "uncoded_cash_keyword",
    "name_cash_keyword",
)
_CASH_EXACT_CODES = {"KRD010010001", "010010", "USDZZ0000001"}
_CURRENCY_CODE_PREFIXES = ("KRW", "USD", "EUR", "JPY")
_CASH_NAME_KEYWORDS = (
    "현금",
    "예금",
    "설정현금",
    "현금성자산",
    "예수금",
    "CASH",
    "MMDA",
)
_SOURCE_QUALITY_SAMPLE_LIMIT = 20
_UNMAPPED_SECURITY_SAMPLE_LIMIT = 20
_TICKER_COLLISION_SAMPLE_LIMIT = 20
_WEIGHT_FIT_TOLERANCE_PERCENT_POINTS = 0.5
_CASH_DERIVATION_WARNING_THRESHOLD = 0.05
_CASH_DERIVATION_RISK_THRESHOLD = 0.20
_TICKER_MAPPING_WARNING_THRESHOLD = 0.80
_TICKER_MAPPING_RISK_THRESHOLD = 0.50
_CASH_DERIVATION_FAILURE_REASONS = (
    "no_weight_fit_sample",
    "weight_fit_tolerance_exceeded",
    "invalid_cash_market_value",
    "invalid_snapshot_market_value_total",
)
_SECURITY_CLASSIFICATION_POLICY = SecurityClassificationPolicy()


def compute_operational_export_fingerprint(
    manifest_path: str | Path,
) -> dict[str, JsonValue]:
    manifest = Path(manifest_path)
    manifest_data = _read_fingerprint_manifest(manifest)
    if manifest_data.get("schema_version") != OPERATIONAL_HOLDINGS_SCHEMA_VERSION:
        raise OperationalHoldingsInputError("invalid copied export schema")
    if manifest_data.get("storage_format") != NORMALIZED_STORAGE_FORMAT:
        raise OperationalHoldingsInputError("invalid copied export storage format")

    dates = _fingerprint_manifest_dates(manifest_data.get("dates"))
    partitions = _fingerprint_manifest_partitions(manifest_data.get("partitions"))
    hasher = hashlib.sha256()
    _update_fingerprint_frame(
        hasher,
        {
            "kind": "manifest",
            "manifest": manifest_data,
        },
    )
    for partition_date in dates:
        partition = partitions.get(partition_date)
        file_value = _fingerprint_partition_file(partition, partition_date)
        partition_path = _fingerprint_partition_path(
            manifest_path=manifest,
            file_value=file_value,
            partition_date=partition_date,
        )
        rows = _read_fingerprint_partition_rows(partition_path, partition_date)
        _update_fingerprint_frame(
            hasher,
            {
                "kind": "partition",
                "date": partition_date,
                "file": file_value,
                "rows": rows,
            },
        )
    return {
        "algorithm": "sha256",
        "scope": OPERATIONAL_EXPORT_FINGERPRINT_SCOPE,
        "value": hasher.hexdigest(),
    }


def _read_fingerprint_manifest(path: Path) -> dict[str, JsonValue]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise OperationalHoldingsInputError("manifest JSON is invalid") from exc
    except OSError as exc:
        raise OperationalHoldingsInputError("manifest file could not be read") from exc
    if not isinstance(data, dict):
        raise OperationalHoldingsInputError("manifest input must be a JSON object")
    return data


def _fingerprint_manifest_dates(value: JsonValue) -> list[str]:
    if not isinstance(value, list) or not value:
        raise OperationalHoldingsInputError("manifest dates must be a non-empty list")
    dates: list[str] = []
    parsed_dates: list[date] = []
    for item in value:
        if not isinstance(item, str):
            raise OperationalHoldingsInputError("manifest dates must be ISO strings")
        dates.append(item)
        parsed_dates.append(_parse_iso_date(item))
    if parsed_dates != sorted(parsed_dates, reverse=True):
        raise OperationalHoldingsInputError("manifest dates must be in descending order")
    return dates


def _fingerprint_manifest_partitions(value: JsonValue) -> Mapping[str, JsonValue]:
    if not isinstance(value, Mapping):
        raise OperationalHoldingsInputError("manifest partitions must be an object")
    return value


def _fingerprint_partition_file(partition: JsonValue, partition_date: str) -> str:
    if not isinstance(partition, Mapping):
        raise OperationalHoldingsInputError(
            f"missing partition entry for date {partition_date}"
        )
    file_value = partition.get("file")
    if not isinstance(file_value, str) or not file_value:
        raise OperationalHoldingsInputError(
            f"partition file is missing for date {partition_date}"
        )
    return file_value


def _fingerprint_partition_path(
    *,
    manifest_path: Path,
    file_value: str,
    partition_date: str,
) -> Path:
    relative_path = Path(file_value)
    if (
        relative_path.is_absolute()
        or relative_path.drive
        or file_value.startswith(("/", "\\"))
    ):
        raise OperationalHoldingsInputError(
            f"partition file must be manifest-relative for date {partition_date}"
        )
    manifest_dir = manifest_path.parent.resolve()
    resolved = (manifest_dir / relative_path).resolve()
    try:
        resolved.relative_to(manifest_dir)
    except ValueError as exc:
        raise OperationalHoldingsInputError(
            f"partition file must stay inside copied export for date {partition_date}"
        ) from exc
    return resolved


def _read_fingerprint_partition_rows(
    partition_path: Path,
    partition_date: str,
) -> list[dict[str, JsonValue]]:
    try:
        lines = partition_path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise OperationalHoldingsInputError(
            f"partition file could not be read for date {partition_date}"
        ) from exc
    rows: list[dict[str, JsonValue]] = []
    for line in lines:
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError as exc:
            raise OperationalHoldingsInputError(
                f"partition JSONL row is invalid for date {partition_date}"
            ) from exc
        if not isinstance(row, dict):
            raise OperationalHoldingsInputError(
                f"partition JSONL row must be an object for date {partition_date}"
            )
        rows.append(row)
    return rows


def _update_fingerprint_frame(hasher: Any, value: Any) -> None:
    payload = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    hasher.update(len(payload).to_bytes(8, "big"))
    hasher.update(payload)


class OperationalSignalReportInputProvider:
    def __init__(
        self,
        manifest_path: str | Path,
        focus_etf_id: str | None = None,
        focus_etf_ids: Iterable[str] | None = None,
        observed_partitions: int = 30,
        evidence_path: str | Path | None = None,
    ) -> None:
        self._manifest_path = Path(manifest_path)
        self._focus_etf_id = focus_etf_id
        self._focus_etf_ids = tuple(focus_etf_ids) if focus_etf_ids is not None else None
        self._observed_partitions = observed_partitions
        self._evidence_path = Path(evidence_path) if evidence_path is not None else None
        self.provenance: dict[str, JsonValue] = {}

    def load(self) -> SignalReportInputs:
        inputs, provenance = load_operational_signal_report_inputs(
            manifest_path=self._manifest_path,
            focus_etf_id=self._focus_etf_id,
            focus_etf_ids=self._focus_etf_ids,
            observed_partitions=self._observed_partitions,
            evidence_path=self._evidence_path,
        )
        self.provenance = provenance
        return inputs


def load_operational_signal_report_inputs(
    *,
    manifest_path: str | Path,
    focus_etf_id: str | None = None,
    focus_etf_ids: Iterable[str] | None = None,
    observed_partitions: int = 30,
    evidence_path: str | Path | None = None,
) -> tuple[SignalReportInputs, dict[str, JsonValue]]:
    if observed_partitions <= 0:
        raise OperationalHoldingsInputError("observed-partitions must be a positive integer")
    resolved_focus_etf_ids, legacy_single_focus = _resolve_focus_etf_ids(
        focus_etf_id=focus_etf_id,
        focus_etf_ids=focus_etf_ids,
    )
    primary_focus_etf_id = resolved_focus_etf_ids[0]

    manifest = Path(manifest_path)
    manifest_data = _read_json_object(manifest)
    if manifest_data.get("schema_version") != OPERATIONAL_HOLDINGS_SCHEMA_VERSION:
        raise OperationalHoldingsInputError(
            "operational holdings manifest is not normalized; "
            "run sync-operational-holdings first"
        )

    dates = _validate_normalized_dates(manifest_data.get("dates"))
    partition_specs = _required_mapping(manifest_data, "partitions")
    scan_dates = dates[:observed_partitions]
    missing_partition_dates: list[str] = []
    rows_by_date: dict[str, list[dict[str, JsonValue]]] = {}
    partition_record_counts: dict[str, int] = {}
    current_date: str | None = None
    previous_date: str | None = None

    for partition_date in scan_dates:
        partition_path = _normalized_partition_path(
            manifest_path=manifest,
            partition_specs=partition_specs,
            partition_date=partition_date,
        )
        if not partition_path.is_file():
            missing_partition_dates.append(partition_date)
            continue

        rows = _read_normalized_partition(
            partition_path=partition_path,
            partition_date=partition_date,
            expected_record_count=_expected_partition_record_count(
                partition_specs, partition_date
            ),
        )
        rows_by_date[partition_date] = rows
        partition_record_counts[partition_date] = len(rows)
        if not legacy_single_focus:
            continue
        if any(row["etf_id"] == primary_focus_etf_id for row in rows):
            if current_date is None:
                current_date = partition_date
            else:
                previous_date = partition_date
                break

    if not legacy_single_focus:
        return _load_focus_set_operational_signal_report_inputs(
            manifest=manifest,
            dates=scan_dates,
            rows_by_date=rows_by_date,
            partition_record_counts=partition_record_counts,
            missing_partition_dates=missing_partition_dates,
            focus_etf_ids=resolved_focus_etf_ids,
            observed_partitions=observed_partitions,
            evidence_path=evidence_path,
        )

    if current_date is None:
        raise OperationalHoldingsInputError(f"focus ETF not found: {primary_focus_etf_id}")
    if previous_date is None:
        raise OperationalHoldingsInputError(
            f"previous snapshot not found for focus ETF: {primary_focus_etf_id}"
        )

    current_rows = rows_by_date[current_date]
    previous_rows = rows_by_date[previous_date]
    current_groups = _group_rows_by_etf(current_rows)
    previous_groups = _group_rows_by_etf(previous_rows)
    included_etf_ids = [
        etf_id for etf_id in current_groups if etf_id in previous_groups
    ]
    selected_etfs = tuple(
        _build_etf_snapshots(
            etf_id=etf_id,
            current_rows=current_groups[etf_id],
            previous_rows=previous_groups[etf_id],
        )
        for etf_id in included_etf_ids
    )
    current_day = _parse_iso_date(current_date)
    previous_day = _parse_iso_date(previous_date)
    evidence = _load_evidence(Path(evidence_path) if evidence_path is not None else None)
    snapshots = MultiETFHoldingsSnapshots(
        as_of_date=current_date,
        previous_date=previous_date,
        current_date=current_date,
        lookback_days=(current_day - previous_day).days,
        universe=f"operational_holdings:{current_date}:{previous_date}",
        etfs=selected_etfs,
    )
    provenance: dict[str, JsonValue] = {
        "schema_version": PROVENANCE_SCHEMA_VERSION,
        "manifest_path": str(manifest),
        "focus_etf_id": primary_focus_etf_id,
        "focus_etf_ids": list(resolved_focus_etf_ids),
        "requested_observed_partitions": observed_partitions,
        "scanned_dates": scan_dates,
        "selected_current_date": current_date,
        "selected_previous_date": previous_date,
        "missing_partition_dates": missing_partition_dates,
        "included_etf_ids": included_etf_ids,
        "partition_record_counts": partition_record_counts,
        "evidence_path": str(evidence_path) if evidence_path is not None else None,
    }
    provenance.update(_load_sync_metadata_quality_subset(manifest))
    return (
        SignalReportInputs(
            snapshots=snapshots,
            focus_etf_id=primary_focus_etf_id,
            focus_etf_ids=resolved_focus_etf_ids,
            evidence=evidence,
        ),
        provenance,
    )


def _load_focus_set_operational_signal_report_inputs(
    *,
    manifest: Path,
    dates: list[str],
    rows_by_date: Mapping[str, list[dict[str, JsonValue]]],
    partition_record_counts: Mapping[str, int],
    missing_partition_dates: list[str],
    focus_etf_ids: tuple[str, ...],
    observed_partitions: int,
    evidence_path: str | Path | None,
) -> tuple[SignalReportInputs, dict[str, JsonValue]]:
    selected_etfs: list[ETFHoldingsSnapshots] = []
    eligible_ids: list[str] = []
    ineligible_ids: list[str] = []
    windows: list[dict[str, JsonValue]] = []

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
        current_groups = _group_rows_by_etf(rows_by_date[current_date])
        previous_groups = _group_rows_by_etf(rows_by_date[previous_date])
        if focus_etf_id not in current_groups or focus_etf_id not in previous_groups:
            ineligible_ids.append(focus_etf_id)
            continue
        selected_etfs.append(
            _build_etf_snapshots(
                etf_id=focus_etf_id,
                current_rows=current_groups[focus_etf_id],
                previous_rows=previous_groups[focus_etf_id],
            )
        )
        eligible_ids.append(focus_etf_id)
        windows.append(
            {
                "etf_id": focus_etf_id,
                "selected_current_date": current_date,
                "selected_previous_date": previous_date,
            }
        )

    if not selected_etfs:
        raise OperationalHoldingsInputError(
            "no focus ETFs have two valid snapshots in scanned partitions"
        )

    selected_current_dates = {
        str(window["selected_current_date"]) for window in windows
    }
    selected_previous_dates = {
        str(window["selected_previous_date"]) for window in windows
    }
    current_date = max(selected_current_dates, key=_parse_iso_date)
    previous_date = max(selected_previous_dates, key=_parse_iso_date)
    current_day = _parse_iso_date(current_date)
    previous_day = _parse_iso_date(previous_date)
    mixed_windows = (
        len(selected_current_dates) > 1
        or len(selected_previous_dates) > 1
    )
    evidence = _load_evidence(Path(evidence_path) if evidence_path is not None else None)
    snapshots = MultiETFHoldingsSnapshots(
        as_of_date=current_date,
        previous_date=previous_date,
        current_date=current_date,
        lookback_days=(current_day - previous_day).days,
        universe=f"operational_holdings_focus_set:{current_date}:{previous_date}",
        etfs=tuple(selected_etfs),
    )
    focus_eligibility: dict[str, JsonValue] = {
        "minimum_eligible_focus_etf_count": 3,
        "eligible_focus_etf_count": len(eligible_ids),
        "eligible_focus_etf_ids": eligible_ids,
        "ineligible_focus_etf_ids": ineligible_ids,
        "mixed_comparison_windows": mixed_windows,
        "comparison_windows": windows,
        "handoff_exclusions": [],
    }
    provenance: dict[str, JsonValue] = {
        "schema_version": PROVENANCE_SCHEMA_VERSION,
        "manifest_path": str(manifest),
        "focus_etf_id": focus_etf_ids[0],
        "focus_etf_ids": list(focus_etf_ids),
        "requested_observed_partitions": observed_partitions,
        "scanned_dates": dates,
        "selected_current_date": current_date,
        "selected_previous_date": previous_date,
        "missing_partition_dates": missing_partition_dates,
        "included_etf_ids": eligible_ids,
        "partition_record_counts": dict(partition_record_counts),
        "evidence_path": str(evidence_path) if evidence_path is not None else None,
        "focus_eligibility": focus_eligibility,
        "per_etf_comparison_windows": windows,
        "mixed_comparison_windows": mixed_windows,
    }
    provenance.update(_load_sync_metadata_quality_subset(manifest))
    return (
        SignalReportInputs(
            snapshots=snapshots,
            focus_etf_id=focus_etf_ids[0],
            focus_etf_ids=focus_etf_ids,
            evidence=evidence,
        ),
        provenance,
    )


def _resolve_focus_etf_ids(
    *,
    focus_etf_id: str | None,
    focus_etf_ids: Iterable[str] | None,
) -> tuple[tuple[str, ...], bool]:
    if focus_etf_ids is not None:
        resolved = tuple(_validated_focus_etf_ids(focus_etf_ids))
        if focus_etf_id is not None and focus_etf_id not in resolved:
            raise OperationalHoldingsInputError(
                "focus_etf_id must be included in focus_etf_ids when both are supplied"
            )
        return resolved, False
    if not focus_etf_id:
        raise OperationalHoldingsInputError("focus_etf_id or focus_etf_ids is required")
    return (_validated_focus_etf_id(focus_etf_id),), True


def _validated_focus_etf_ids(values: Iterable[str]) -> list[str]:
    resolved: list[str] = []
    seen: set[str] = set()
    for value in values:
        etf_id = _validated_focus_etf_id(value)
        if etf_id in seen:
            raise OperationalHoldingsInputError(f"duplicate focus_etf_id: {etf_id}")
        seen.add(etf_id)
        resolved.append(etf_id)
    if not resolved:
        raise OperationalHoldingsInputError("focus_etf_ids must be non-empty")
    return resolved


def _validated_focus_etf_id(value: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise OperationalHoldingsInputError("focus_etf_id must be a non-empty string")
    etf_id = value.strip()
    if "://" in etf_id or "\\" in etf_id or etf_id.startswith("/") or "C:" in etf_id:
        raise OperationalHoldingsInputError("focus_etf_id is not path-safe")
    return etf_id


def sync_operational_holdings(
    *,
    source_manifest_path: str | Path,
    dest_dir: str | Path,
    observed_partitions: int = 30,
    security_mapping_path: str | Path | None = None,
    security_resolution_path: str | Path | None = None,
    now: Callable[[], datetime] | None = None,
) -> dict[str, JsonValue]:
    if observed_partitions <= 0:
        raise OperationalHoldingsInputError("observed-partitions must be a positive integer")
    if security_mapping_path is not None and security_resolution_path is not None:
        raise OperationalHoldingsInputError(
            "--security-resolution-path and --security-mapping-path cannot both be supplied"
        )

    source_manifest = Path(source_manifest_path)
    destination = Path(dest_dir)
    manifest_data = _read_json_object(source_manifest)
    security_mapping = (
        _load_security_mapping(Path(security_mapping_path))
        if security_mapping_path is not None
        else None
    )
    security_resolution = (
        _load_security_resolution(Path(security_resolution_path))
        if security_resolution_path is not None
        else None
    )
    source_dates = _latest_observed_source_dates(
        _required_list(manifest_data, "dates"),
        observed_partitions=observed_partitions,
    )
    source_partitions = _required_mapping(manifest_data, "partitions")

    copied_manifest_path = destination / source_manifest.name
    copied_parts_dir = destination / f"{source_manifest.name}.parts"
    destination.mkdir(parents=True, exist_ok=True)
    copied_parts_dir.mkdir(parents=True, exist_ok=True)

    metadata: dict[str, JsonValue] = {
        "schema_version": SYNC_METADATA_SCHEMA_VERSION,
        "source_manifest_path": str(source_manifest),
        "copied_manifest_path": str(copied_manifest_path),
        "requested_observed_partitions": observed_partitions,
        "source_dates": source_dates,
        "copied_dates": [],
        "missing_source_dates": [],
        "source_record_count": int(manifest_data.get("record_count", 0) or 0),
        "copied_partition_count": 0,
        "copied_record_count": 0,
        "security_mapping_available": security_mapping is not None,
        "security_mapping_path": (
            str(security_mapping_path) if security_mapping_path is not None else None
        ),
        "security_resolution_available": security_resolution is not None,
        "security_resolution_path": (
            str(security_resolution_path) if security_resolution_path is not None else None
        ),
        "mapped_security_count": 0,
        "unmapped_security_count": 0,
        "non_ticker_excluded_security_count": 0,
        "skipped_missing_security_id_count": 0,
        "derived_cash_weight_count": 0,
        "derived_cash_weight_fit_failed_count": 0,
        "skipped_unusable_cash_weight_count": 0,
        "uncoded_cash_holding_count": 0,
        "cash_identification_counts": _empty_cash_identification_counts(),
        "cash_derivation_failure_distribution": _empty_cash_derivation_failure_distribution(),
        "source_quality_samples": [],
        "unmapped_security_samples": [],
        "numeric_null_normalized_count": 0,
        "duplicate_aggregated_count": 0,
        "ticker_collision_review_count": 0,
        "ticker_collision_review_samples": [],
        "renamed_security_count": 0,
        "security_name_aliases": [],
        "source_file_strategy_counts": {"sibling": 0, "manifest_file": 0},
        "field_mappings": {
            "fund_id": "etf_id",
            "fund_name": "etf_name",
            "code": "security_id",
            "name": "name",
            "weight_pct": "weight_percent",
            "quantity": "shares",
            "eval_amount_krw": "market_value_krw",
            "as_of_date": "as_of_date",
        },
        "synced_at": _sync_timestamp(now),
    }

    manifest_partitions: dict[str, JsonValue] = {}
    security_names: dict[str, list[str]] = defaultdict(list)
    unmapped_security_sample_observations: list[dict[str, str]] = []
    copied_record_count = 0

    for source_date in source_dates:
        source_file, strategy = _resolve_source_partition(
            source_manifest=source_manifest,
            source_partitions=source_partitions,
            source_date=source_date,
        )
        if source_file is None:
            missing = metadata["missing_source_dates"]
            assert isinstance(missing, list)
            missing.append(source_date)
            continue

        strategy_counts = metadata["source_file_strategy_counts"]
        assert isinstance(strategy_counts, dict)
        strategy_counts[strategy] = int(strategy_counts.get(strategy, 0)) + 1

        copied_date = _raw_date_to_iso(source_date)
        normalized_rows, row_metadata = _normalize_source_partition(
            source_file=source_file,
            source_date=source_date,
            copied_date=copied_date,
            security_names=security_names,
            security_mapping=security_mapping,
            security_resolution=security_resolution,
        )
        metadata["mapped_security_count"] = (
            int(metadata["mapped_security_count"]) + row_metadata["mapped_security_count"]
        )
        metadata["unmapped_security_count"] = (
            int(metadata["unmapped_security_count"]) + row_metadata["unmapped_security_count"]
        )
        metadata["non_ticker_excluded_security_count"] = (
            int(metadata["non_ticker_excluded_security_count"])
            + row_metadata["non_ticker_excluded_security_count"]
        )
        metadata["skipped_missing_security_id_count"] = (
            int(metadata["skipped_missing_security_id_count"])
            + row_metadata["skipped_missing_security_id_count"]
        )
        metadata["derived_cash_weight_count"] = (
            int(metadata["derived_cash_weight_count"])
            + row_metadata["derived_cash_weight_count"]
        )
        metadata["derived_cash_weight_fit_failed_count"] = (
            int(metadata["derived_cash_weight_fit_failed_count"])
            + row_metadata["derived_cash_weight_fit_failed_count"]
        )
        metadata["skipped_unusable_cash_weight_count"] = (
            int(metadata["skipped_unusable_cash_weight_count"])
            + row_metadata["skipped_unusable_cash_weight_count"]
        )
        metadata["uncoded_cash_holding_count"] = (
            int(metadata["uncoded_cash_holding_count"])
            + row_metadata["uncoded_cash_holding_count"]
        )
        cash_identification_counts = metadata["cash_identification_counts"]
        assert isinstance(cash_identification_counts, dict)
        row_cash_identification_counts = row_metadata["cash_identification_counts"]
        assert isinstance(row_cash_identification_counts, dict)
        for rule_id in _CASH_IDENTIFICATION_RULE_IDS:
            cash_identification_counts[rule_id] = int(
                cash_identification_counts.get(rule_id, 0)
            ) + int(row_cash_identification_counts.get(rule_id, 0))
        _merge_cash_derivation_failure_distribution(metadata, row_metadata)
        source_quality_samples = metadata["source_quality_samples"]
        assert isinstance(source_quality_samples, list)
        row_source_quality_samples = row_metadata["source_quality_samples"]
        assert isinstance(row_source_quality_samples, list)
        for sample in row_source_quality_samples:
            if len(source_quality_samples) >= _SOURCE_QUALITY_SAMPLE_LIMIT:
                break
            source_quality_samples.append(sample)
        row_unmapped_security_sample_observations = row_metadata[
            "unmapped_security_sample_observations"
        ]
        assert isinstance(row_unmapped_security_sample_observations, list)
        unmapped_security_sample_observations.extend(row_unmapped_security_sample_observations)
        metadata["numeric_null_normalized_count"] = (
            int(metadata["numeric_null_normalized_count"])
            + row_metadata["numeric_null_normalized_count"]
        )
        metadata["duplicate_aggregated_count"] = (
            int(metadata["duplicate_aggregated_count"]) + row_metadata["duplicate_aggregated_count"]
        )
        metadata["ticker_collision_review_count"] = (
            int(metadata["ticker_collision_review_count"])
            + row_metadata["ticker_collision_review_count"]
        )
        ticker_collision_samples = metadata["ticker_collision_review_samples"]
        assert isinstance(ticker_collision_samples, list)
        row_ticker_collision_samples = row_metadata["ticker_collision_review_samples"]
        assert isinstance(row_ticker_collision_samples, list)
        for sample in row_ticker_collision_samples:
            if len(ticker_collision_samples) >= _TICKER_COLLISION_SAMPLE_LIMIT:
                break
            ticker_collision_samples.append(sample)

        if not normalized_rows:
            continue

        copied_path = copied_parts_dir / f"{copied_date}.jsonl"
        _write_jsonl(copied_path, normalized_rows)
        copied_record_count += len(normalized_rows)
        copied_dates = metadata["copied_dates"]
        assert isinstance(copied_dates, list)
        copied_dates.append(copied_date)
        manifest_partitions[copied_date] = {
            "file": f"{source_manifest.name}.parts/{copied_date}.jsonl",
            "record_count": len(normalized_rows),
            "source_date": source_date,
            "source_file_used": str(source_file),
            "source_file_strategy": strategy,
        }

    if copied_record_count == 0:
        raise OperationalHoldingsInputError("no operational holdings rows were copied")

    aliases = _security_name_aliases(security_names)
    metadata["renamed_security_count"] = sum(
        max(0, len(item["aliases"])) for item in aliases if isinstance(item["aliases"], list)
    )
    metadata["security_name_aliases"] = aliases[:20]
    metadata["copied_partition_count"] = len(manifest_partitions)
    metadata["copied_record_count"] = copied_record_count
    metadata["unmapped_security_samples"] = _build_unmapped_security_samples(
        unmapped_security_sample_observations
    )
    metadata["sync_quality"] = _build_sync_quality(metadata)

    copied_manifest = {
        "schema_version": OPERATIONAL_HOLDINGS_SCHEMA_VERSION,
        "storage_format": NORMALIZED_STORAGE_FORMAT,
        "source_storage_format": manifest_data.get("storage_format"),
        "source_updated_at": manifest_data.get("updated_at"),
        "synced_at": metadata["synced_at"],
        "dates": metadata["copied_dates"],
        "record_count": copied_record_count,
        "partitions": manifest_partitions,
    }

    _write_json(copied_manifest_path, copied_manifest)
    _write_json(destination / "sync_metadata.json", metadata)
    return metadata


def collect_holdings_fixture(
    *,
    fixture_path: str | Path,
    dest_dir: str | Path,
    observed_partitions: int = 30,
    universe_state_path: str | Path | None = None,
    now: Callable[[], datetime] | None = None,
) -> dict[str, JsonValue]:
    if observed_partitions <= 0:
        raise OperationalHoldingsInputError("observed-partitions must be a positive integer")

    fixture = _read_json_object(Path(fixture_path))
    if fixture.get("schema_version") != NATIVE_FIXTURE_SCHEMA_VERSION:
        raise OperationalHoldingsInputError("invalid native fixture schema")
    if universe_state_path is None:
        brands = _native_brands(fixture.get("brands", fixture.get("managers")))
        etfs = _native_etfs(fixture.get("etf_universe"), brands=brands)
        brand_count = len(brands)
    else:
        etfs = _native_etfs_from_universe_state(universe_state_path)
        brand_count = len({etf.brand_id for etf in etfs.values()})
    snapshots = _native_snapshots(fixture.get("snapshots"))
    ordered_dates = sorted(snapshots, key=_parse_iso_date, reverse=True)
    selected_dates = ordered_dates[:observed_partitions]

    rows_by_date: dict[str, list[dict[str, JsonValue]]] = {}
    record_count = 0
    for observed_date in selected_dates:
        rows = _native_rows_for_snapshot(snapshots[observed_date], etfs=etfs)
        if not rows:
            continue
        rows_by_date[observed_date] = rows
        record_count += len(rows)

    if record_count == 0:
        raise OperationalHoldingsInputError("no native fixture holdings rows were collected")

    destination = Path(dest_dir)
    manifest_name = "url_holdings_cumulative.json"
    manifest_path = destination / manifest_name
    parts_dir = destination / f"{manifest_name}.parts"
    destination.mkdir(parents=True, exist_ok=True)
    parts_dir.mkdir(parents=True, exist_ok=True)

    collected_at = _sync_timestamp(now)
    manifest_partitions: dict[str, JsonValue] = {}
    for observed_date, rows in rows_by_date.items():
        partition_path = parts_dir / f"{observed_date}.jsonl"
        _write_jsonl(partition_path, rows)
        manifest_partitions[observed_date] = {
            "file": f"{manifest_name}.parts/{observed_date}.jsonl",
            "record_count": len(rows),
        }

    manifest: dict[str, JsonValue] = {
        "schema_version": OPERATIONAL_HOLDINGS_SCHEMA_VERSION,
        "storage_format": NORMALIZED_STORAGE_FORMAT,
        "collection_source_type": "fixture",
        "collected_at": collected_at,
        "dates": list(manifest_partitions),
        "record_count": record_count,
        "partitions": manifest_partitions,
    }
    _write_json(manifest_path, manifest)

    fingerprint = compute_operational_export_fingerprint(manifest_path)
    summary: dict[str, JsonValue] = {
        "schema_version": COLLECTION_SUMMARY_SCHEMA_VERSION,
        "collection_source_type": "fixture",
        "collected_at": collected_at,
        "requested_observed_partitions": observed_partitions,
        "observed_dates": list(manifest_partitions),
        "etf_count": len(etfs),
        "brand_count": brand_count,
        "partition_count": len(manifest_partitions),
        "row_count": record_count,
        "quality_warnings": _native_summary_items(
            fixture.get("quality_warnings"),
            field="quality_warnings",
        ),
        "limitations": _native_summary_items(
            fixture.get("limitations"),
            field="limitations",
        ),
        "normalized_output": {
            "manifest_path": manifest_name,
            "fingerprint": fingerprint,
        },
    }
    _write_json(destination / "collection_summary.json", summary)
    return summary


def update_holdings_history_fixture(
    *,
    fixture_path: str | Path,
    universe_state_path: str | Path,
    history_dir: str | Path,
    observed_partitions: int = 30,
    refresh_snapshots: Iterable[tuple[str, str]] | None = None,
    now: Callable[[], datetime] | None = None,
) -> dict[str, JsonValue]:
    if observed_partitions <= 0:
        raise OperationalHoldingsInputError("observed-partitions must be a positive integer")

    fixture = _read_json_object(Path(fixture_path))
    if fixture.get("schema_version") != NATIVE_FIXTURE_SCHEMA_VERSION:
        raise OperationalHoldingsInputError("invalid native fixture schema")
    etfs = _native_etfs_from_universe_state(universe_state_path)
    snapshots = _native_snapshots(fixture.get("snapshots"))
    ordered_dates = sorted(snapshots, key=_parse_iso_date, reverse=True)
    selected_dates = ordered_dates[:observed_partitions]
    incoming = _history_snapshot_rows_from_fixture_snapshots(
        snapshots=snapshots,
        selected_dates=selected_dates,
        etfs=etfs,
    )
    if not incoming:
        raise OperationalHoldingsInputError("no native fixture holdings snapshots were collected")

    return _apply_history_snapshot_update(
        history_path=Path(history_dir) / HOLDINGS_HISTORY_MANIFEST_FILENAME,
        incoming=incoming,
        observed_dates=selected_dates,
        selected_active_etf_ids=sorted(etfs),
        source_type="fixture",
        refresh_snapshots=refresh_snapshots,
        now=now,
    )


def import_operational_holdings_export_to_history(
    *,
    manifest_path: str | Path,
    history_dir: str | Path,
    refresh_snapshots: Iterable[tuple[str, str]] | None = None,
    now: Callable[[], datetime] | None = None,
) -> dict[str, JsonValue]:
    manifest = Path(manifest_path)
    manifest_data = _read_json_object(manifest)
    if manifest_data.get("schema_version") != OPERATIONAL_HOLDINGS_SCHEMA_VERSION:
        raise OperationalHoldingsInputError("invalid normalized holdings export schema")
    if manifest_data.get("storage_format") != NORMALIZED_STORAGE_FORMAT:
        raise OperationalHoldingsInputError("invalid normalized holdings export storage format")
    dates = _validate_normalized_dates(manifest_data.get("dates"))
    partitions = _required_mapping(manifest_data, "partitions")
    incoming: dict[tuple[str, str], list[dict[str, JsonValue]]] = {}
    for observed_date in dates:
        partition_path = _normalized_partition_path(
            manifest_path=manifest,
            partition_specs=partitions,
            partition_date=observed_date,
        )
        rows = _read_normalized_partition(
            partition_path=partition_path,
            partition_date=observed_date,
            expected_record_count=_expected_partition_record_count(
                partitions,
                observed_date,
            ),
        )
        for etf_id, etf_rows in _group_rows_by_etf(rows).items():
            incoming[(etf_id, observed_date)] = etf_rows
    if not incoming:
        raise OperationalHoldingsInputError("no normalized holdings snapshots were imported")
    return _apply_history_snapshot_update(
        history_path=Path(history_dir) / HOLDINGS_HISTORY_MANIFEST_FILENAME,
        incoming=incoming,
        observed_dates=dates,
        selected_active_etf_ids=sorted({etf_id for etf_id, _ in incoming}),
        source_type="normalized_operational_export",
        refresh_snapshots=refresh_snapshots,
        now=now,
    )


def _apply_history_snapshot_update(
    *,
    history_path: Path,
    incoming: Mapping[tuple[str, str], list[dict[str, JsonValue]]],
    observed_dates: list[str],
    selected_active_etf_ids: list[str],
    source_type: str,
    refresh_snapshots: Iterable[tuple[str, str]] | None,
    now: Callable[[], datetime] | None,
) -> dict[str, JsonValue]:
    stored = _read_history_snapshot_rows(history_path)
    refresh_set = set(refresh_snapshots or ())
    unselected_refreshes = sorted(
        refresh_set - set(incoming),
        key=lambda item: (_parse_iso_date(item[1]), item[0]),
        reverse=True,
    )
    if unselected_refreshes:
        raise OperationalHoldingsInputError(
            "refresh snapshot was not selected: "
            + "; ".join(
                f"etf_id={etf_id} observed_date={observed_date}"
                for etf_id, observed_date in unselected_refreshes
            )
        )

    added: list[dict[str, JsonValue]] = []
    skipped: list[dict[str, JsonValue]] = []
    refreshed: list[dict[str, JsonValue]] = []
    conflicts: list[dict[str, JsonValue]] = []
    next_stored = {
        key: [dict(row) for row in rows]
        for key, rows in stored.items()
    }
    for key in sorted(incoming, key=lambda item: (_parse_iso_date(item[1]), item[0]), reverse=True):
        rows = incoming[key]
        existing_rows = stored.get(key)
        item = {
            "etf_id": key[0],
            "observed_date": key[1],
            "row_count": len(rows),
        }
        if existing_rows is None:
            next_stored[key] = [dict(row) for row in rows]
            added.append(item)
            continue
        if _history_snapshot_fingerprint(existing_rows) == _history_snapshot_fingerprint(rows):
            skipped.append(item)
            continue
        if key in refresh_set:
            next_stored[key] = [dict(row) for row in rows]
            refreshed.append(item)
            continue
        conflicts.append(item)

    if conflicts:
        raise OperationalHoldingsInputError(
            "refresh required for holdings history snapshot: "
            + "; ".join(
                "etf_id={etf_id} observed_date={observed_date} row_count={row_count}".format(
                    **item
                )
                for item in conflicts
            )
        )

    updated_at = _sync_timestamp(now)
    if added or refreshed:
        _write_history_snapshot_rows(
            history_path=history_path,
            snapshot_rows=next_stored,
            updated_at=updated_at,
        )
    affected_row_count = sum(int(item["row_count"]) for item in added + refreshed)
    return {
        "schema_version": HOLDINGS_HISTORY_UPDATE_SCHEMA_VERSION,
        "source_type": source_type,
        "history_store": {"manifest_path": HOLDINGS_HISTORY_MANIFEST_FILENAME},
        "updated_at": updated_at,
        "selected_active_etf_ids": selected_active_etf_ids,
        "observed_dates": observed_dates,
        "added_snapshot_count": len(added),
        "skipped_snapshot_count": len(skipped),
        "refreshed_snapshot_count": len(refreshed),
        "conflict_snapshot_count": len(conflicts),
        "row_count": affected_row_count,
        "added_snapshots": added,
        "skipped_snapshots": skipped,
        "refreshed_snapshots": refreshed,
        "conflict_snapshots": conflicts,
    }


def export_latest_holdings_comparison(
    *,
    history_dir: str | Path,
    universe_state_path: str | Path,
    dest_dir: str | Path,
    security_resolution_path: str | Path | None = None,
    now: Callable[[], datetime] | None = None,
) -> dict[str, JsonValue]:
    active_etfs = _native_etfs_from_universe_state(universe_state_path)
    active_etf_ids = set(active_etfs)
    security_resolution = (
        _load_security_resolution(Path(security_resolution_path))
        if security_resolution_path is not None
        else None
    )
    history_path = Path(history_dir) / HOLDINGS_HISTORY_MANIFEST_FILENAME
    snapshot_rows = _read_history_snapshot_rows(history_path)
    (
        dates,
        current_date,
        previous_date,
        complete_active_etf_ids,
        missing_active_etf_ids,
        mixed_comparison_windows,
        comparison_windows,
    ) = _latest_history_comparison_windows(
        snapshot_rows=snapshot_rows,
        active_etf_ids=active_etf_ids,
    )
    rows_by_date = {
        observed_date: _history_export_rows_for_date(
            snapshot_rows=snapshot_rows,
            active_etf_ids=active_etf_ids,
            observed_date=observed_date,
            security_resolution=security_resolution,
        )
        for observed_date in dates
    }
    exported_at = _sync_timestamp(now)
    destination = Path(dest_dir)
    manifest_name = "url_holdings_cumulative.json"
    manifest_path = destination / manifest_name
    parts_dir = destination / f"{manifest_name}.parts"
    destination.mkdir(parents=True, exist_ok=True)
    parts_dir.mkdir(parents=True, exist_ok=True)

    manifest_partitions: dict[str, JsonValue] = {}
    record_count = 0
    for observed_date in dates:
        rows = rows_by_date[observed_date]
        partition_path = parts_dir / f"{observed_date}.jsonl"
        _write_jsonl(partition_path, rows)
        record_count += len(rows)
        manifest_partitions[observed_date] = {
            "file": f"{manifest_name}.parts/{observed_date}.jsonl",
            "record_count": len(rows),
        }
    manifest: dict[str, JsonValue] = {
        "schema_version": OPERATIONAL_HOLDINGS_SCHEMA_VERSION,
        "storage_format": NORMALIZED_STORAGE_FORMAT,
        "collection_source_type": "native_history",
        "collected_at": exported_at,
        "dates": dates,
        "record_count": record_count,
        "partitions": manifest_partitions,
    }
    _write_json(manifest_path, manifest)
    fingerprint = compute_operational_export_fingerprint(manifest_path)
    active_etf_coverage: dict[str, JsonValue] = {
        "selected_current_date": current_date,
        "selected_previous_date": previous_date,
        "active_etf_count": len(active_etfs),
        "complete_active_etf_count": len(complete_active_etf_ids),
        "missing_active_etf_ids": missing_active_etf_ids,
        "coverage_ratio": _rounded_ratio(
            len(complete_active_etf_ids),
            len(active_etfs),
        ),
    }
    if mixed_comparison_windows:
        active_etf_coverage["mixed_comparison_windows"] = True
        active_etf_coverage["comparison_windows"] = comparison_windows

    summary: dict[str, JsonValue] = {
        "schema_version": COLLECTION_SUMMARY_SCHEMA_VERSION,
        "collection_source_type": "native_history",
        "collected_at": exported_at,
        "requested_observed_partitions": 2,
        "observed_dates": dates,
        "etf_count": len(active_etfs),
        "brand_count": len({etf.brand_id for etf in active_etfs.values()}),
        "partition_count": len(dates),
        "row_count": record_count,
        "quality_warnings": [],
        "limitations": [],
        "active_etf_coverage": active_etf_coverage,
        "security_coverage": _native_history_security_coverage(
            rows_by_date=rows_by_date,
            security_resolution=security_resolution,
        ),
        "normalized_output": {
            "manifest_path": manifest_name,
            "fingerprint": fingerprint,
        },
    }
    _write_json(destination / "collection_summary.json", summary)
    return summary


def _latest_history_comparison_windows(
    *,
    snapshot_rows: Mapping[tuple[str, str], list[dict[str, JsonValue]]],
    active_etf_ids: set[str],
) -> tuple[
    list[str],
    str,
    str,
    list[str],
    list[str],
    bool,
    list[dict[str, JsonValue]],
]:
    comparison_windows: list[dict[str, JsonValue]] = []
    selected_dates: set[str] = set()
    complete_active_etf_ids: list[str] = []
    missing_active_etf_ids: list[str] = []

    for etf_id in sorted(active_etf_ids):
        observed_dates = sorted(
            {
                observed_date
                for snapshot_etf_id, observed_date in snapshot_rows
                if snapshot_etf_id == etf_id
            },
            key=_parse_iso_date,
            reverse=True,
        )
        if len(observed_dates) < 2:
            missing_active_etf_ids.append(etf_id)
            continue
        current_date, previous_date = observed_dates[:2]
        selected_dates.update((current_date, previous_date))
        complete_active_etf_ids.append(etf_id)
        comparison_windows.append(
            {
                "etf_id": etf_id,
                "selected_current_date": current_date,
                "selected_previous_date": previous_date,
            }
        )

    if not comparison_windows:
        raise OperationalHoldingsInputError(
            "holdings history does not contain a latest comparison window"
        )

    dates = sorted(selected_dates, key=_parse_iso_date, reverse=True)
    current_date = max(
        (str(window["selected_current_date"]) for window in comparison_windows),
        key=_parse_iso_date,
    )
    previous_date = max(
        (str(window["selected_previous_date"]) for window in comparison_windows),
        key=_parse_iso_date,
    )
    mixed_comparison_windows = (
        len({window["selected_current_date"] for window in comparison_windows}) > 1
        or len({window["selected_previous_date"] for window in comparison_windows}) > 1
    )
    return (
        dates,
        current_date,
        previous_date,
        complete_active_etf_ids,
        missing_active_etf_ids,
        mixed_comparison_windows,
        comparison_windows,
    )


def _history_export_rows_for_date(
    *,
    snapshot_rows: Mapping[tuple[str, str], list[dict[str, JsonValue]]],
    active_etf_ids: set[str],
    observed_date: str,
    security_resolution: _SecurityResolution | None = None,
) -> list[dict[str, JsonValue]]:
    rows: list[dict[str, JsonValue]] = []
    for etf_id in sorted(active_etf_ids):
        rows.extend(
            sorted(
                snapshot_rows.get((etf_id, observed_date), []),
                key=lambda row: str(row["security_id"]),
            )
        )
    return [
        _history_export_row_with_security_resolution(
            row=row,
            security_resolution=security_resolution,
        )
        for row in rows
    ]


def _history_export_row_with_security_resolution(
    *,
    row: Mapping[str, JsonValue],
    security_resolution: _SecurityResolution | None,
) -> dict[str, JsonValue]:
    exported = dict(row)
    _apply_analytical_identity_fields(exported)
    if security_resolution is None:
        return exported
    security_id = str(exported["security_id"])
    mapping = security_resolution.mappings.get(security_id)
    if mapping is not None:
        exported["ticker"] = mapping["ticker"]
        exported["security_classification"] = "ticker_candidate"
        exported["is_cash"] = False
        _apply_optional_identity_fields(exported, mapping)
        return exported
    exclusion = security_resolution.exclusions.get(security_id)
    if exclusion is not None:
        classification = str(exclusion["security_classification"])
        exported["ticker"] = None
        exported["security_classification"] = classification
        exported["is_cash"] = classification == "cash_like"
    return exported


def _native_history_security_coverage(
    *,
    rows_by_date: Mapping[str, list[dict[str, JsonValue]]],
    security_resolution: _SecurityResolution | None,
) -> dict[str, JsonValue]:
    mapped_count = 0
    unresolved_count = 0
    unknown_count = 0
    non_ticker_excluded_count = 0
    reviewed_mapping_applied_count = 0
    reviewed_exclusion_applied_count = 0
    recovery_observations: list[dict[str, str]] = []
    all_rows = [
        row
        for observed_date in sorted(rows_by_date, key=_parse_iso_date, reverse=True)
        for row in rows_by_date[observed_date]
    ]

    for row in all_rows:
        security_id = str(row["security_id"])
        classification = str(row["security_classification"])
        if security_resolution is not None:
            if security_id in security_resolution.mappings:
                reviewed_mapping_applied_count += 1
            elif security_id in security_resolution.exclusions:
                reviewed_exclusion_applied_count += 1
        if classification == "ticker_candidate":
            if row["ticker"] is None:
                unresolved_count += 1
                recovery_observations.append(
                    _native_history_recovery_observation(
                        row=row,
                        security_classification=None,
                    )
                )
            else:
                mapped_count += 1
        elif classification == "unknown":
            unknown_count += 1
            recovery_observations.append(
                _native_history_recovery_observation(
                    row=row,
                    security_classification="unknown",
                )
            )
        elif classification in {"cash_like", "non_equity"}:
            non_ticker_excluded_count += 1

    recovery_samples = _build_unmapped_security_samples(recovery_observations)
    ticker_collision_samples = _ticker_collision_review_samples(all_rows)
    return {
        "security_resolution_available": security_resolution is not None,
        "mapped_ticker_candidate_count": mapped_count,
        "unresolved_ticker_candidate_count": unresolved_count,
        "unknown_count": unknown_count,
        "non_ticker_excluded_count": non_ticker_excluded_count,
        "reviewed_mapping_applied_count": reviewed_mapping_applied_count,
        "reviewed_exclusion_applied_count": reviewed_exclusion_applied_count,
        "ticker_mapping_coverage_ratio": _rounded_ratio(
            mapped_count,
            mapped_count + unresolved_count,
        ),
        "ticker_collision_review_count": len(ticker_collision_samples),
        "ticker_collision_review_samples": ticker_collision_samples[
            :_TICKER_COLLISION_SAMPLE_LIMIT
        ],
        "recovery_sample_count": len(recovery_samples),
        "recovery_samples": recovery_samples,
    }


def _apply_optional_identity_fields(
    row: dict[str, JsonValue],
    source: Mapping[str, JsonValue],
) -> None:
    for field in (
        "security_group_id",
        "listing_key",
        "security_group_name",
        "security_group_ticker",
    ):
        value = source.get(field)
        if isinstance(value, str) and value.strip():
            row[field] = value.strip()


def _apply_analytical_identity_fields(row: dict[str, JsonValue]) -> None:
    source_provider_id = row.get("source_provider_id")
    security_id = row.get("security_id")
    if not isinstance(source_provider_id, str) or not isinstance(security_id, str):
        return
    analytical_key, analytical_scope = analytical_identity_for_security(
        source_provider_id=source_provider_id,
        security_id=security_id,
    )
    row["analytical_identity_key"] = analytical_key
    row["analytical_identity_scope"] = analytical_scope


def _native_history_recovery_observation(
    *,
    row: Mapping[str, JsonValue],
    security_classification: str | None,
) -> dict[str, str]:
    observation = {
        "security_id": str(row["security_id"]),
        "name": str(row["name"]),
        "etf_id": str(row["etf_id"]),
        "as_of_date": str(row["as_of_date"]),
    }
    if security_classification is not None:
        observation["security_classification"] = security_classification
    return observation


def _ticker_collision_review_samples(
    rows: Iterable[Mapping[str, JsonValue]],
) -> list[dict[str, JsonValue]]:
    grouped: dict[tuple[str, str, str], list[Mapping[str, JsonValue]]] = defaultdict(list)
    for row in rows:
        ticker = row.get("ticker")
        if not isinstance(ticker, str) or not ticker:
            continue
        grouped[
            (
                str(row["etf_id"]),
                str(row["as_of_date"]),
                ticker,
            )
        ].append(row)

    samples: list[dict[str, JsonValue]] = []
    for (etf_id, as_of_date, ticker), items in grouped.items():
        security_ids = sorted({str(item["security_id"]) for item in items})
        if len(security_ids) <= 1:
            continue
        group_ids = {
            str(item["security_group_id"])
            for item in items
            if isinstance(item.get("security_group_id"), str)
            and str(item["security_group_id"]).strip()
        }
        if len(group_ids) == 1 and all(
            isinstance(item.get("security_group_id"), str)
            and str(item["security_group_id"]).strip() in group_ids
            for item in items
        ):
            continue
        samples.append(
            {
                "etf_id": etf_id,
                "as_of_date": as_of_date,
                "ticker": ticker,
                "security_ids": security_ids,
            }
        )
    samples.sort(
        key=lambda sample: (
            str(sample["as_of_date"]),
            str(sample["etf_id"]),
            str(sample["ticker"]),
        )
    )
    return samples


def _history_snapshot_rows_from_fixture_snapshots(
    *,
    snapshots: Mapping[str, NativeHoldingSnapshot],
    selected_dates: Iterable[str],
    etfs: Mapping[str, NativeETFUniverseEntry],
) -> dict[tuple[str, str], list[dict[str, JsonValue]]]:
    snapshot_rows: dict[tuple[str, str], list[dict[str, JsonValue]]] = {}
    for observed_date in selected_dates:
        rows = _native_rows_for_snapshot(snapshots[observed_date], etfs=etfs)
        for etf_id, etf_rows in _group_rows_by_etf(rows).items():
            snapshot_rows[(etf_id, observed_date)] = etf_rows
    return snapshot_rows


def _read_history_snapshot_rows(
    history_path: Path,
) -> dict[tuple[str, str], list[dict[str, JsonValue]]]:
    if not history_path.is_file():
        return {}
    manifest = _read_json_object(history_path)
    if manifest.get("schema_version") != HOLDINGS_HISTORY_SCHEMA_VERSION:
        raise OperationalHoldingsInputError("invalid holdings history schema")
    if manifest.get("storage_format") != HOLDINGS_HISTORY_STORAGE_FORMAT:
        raise OperationalHoldingsInputError("invalid holdings history storage format")
    dates = _validate_normalized_dates(manifest.get("dates"))
    partitions = _required_mapping(manifest, "partitions")
    snapshot_rows: dict[tuple[str, str], list[dict[str, JsonValue]]] = {}
    for observed_date in dates:
        partition_path = _normalized_partition_path(
            manifest_path=history_path,
            partition_specs=partitions,
            partition_date=observed_date,
        )
        rows = _read_normalized_partition(
            partition_path=partition_path,
            partition_date=observed_date,
            expected_record_count=_expected_partition_record_count(
                partitions,
                observed_date,
            ),
        )
        for etf_id, etf_rows in _group_rows_by_etf(rows).items():
            snapshot_rows[(etf_id, observed_date)] = etf_rows
    return snapshot_rows


def _write_history_snapshot_rows(
    *,
    history_path: Path,
    snapshot_rows: Mapping[tuple[str, str], list[dict[str, JsonValue]]],
    updated_at: str,
) -> None:
    history_dir = history_path.parent
    parts_dir = history_dir / f"{history_path.name}.parts"
    history_dir.mkdir(parents=True, exist_ok=True)
    parts_dir.mkdir(parents=True, exist_ok=True)
    dates = sorted(
        {observed_date for _, observed_date in snapshot_rows},
        key=_parse_iso_date,
        reverse=True,
    )
    partitions: dict[str, JsonValue] = {}
    record_count = 0
    for observed_date in dates:
        rows: list[dict[str, JsonValue]] = []
        for etf_id in sorted(
            etf_id
            for etf_id, snapshot_date in snapshot_rows
            if snapshot_date == observed_date
        ):
            rows.extend(
                sorted(
                    snapshot_rows[(etf_id, observed_date)],
                    key=lambda row: str(row["security_id"]),
                )
            )
        partition_path = parts_dir / f"{observed_date}.jsonl"
        _write_jsonl(partition_path, rows)
        record_count += len(rows)
        partitions[observed_date] = {
            "file": f"{history_path.name}.parts/{observed_date}.jsonl",
            "record_count": len(rows),
            "snapshot_count": len(
                {
                    etf_id
                    for etf_id, snapshot_date in snapshot_rows
                    if snapshot_date == observed_date
                }
            ),
        }
    manifest: dict[str, JsonValue] = {
        "schema_version": HOLDINGS_HISTORY_SCHEMA_VERSION,
        "storage_format": HOLDINGS_HISTORY_STORAGE_FORMAT,
        "updated_at": updated_at,
        "dates": dates,
        "record_count": record_count,
        "snapshot_count": len(snapshot_rows),
        "partitions": partitions,
    }
    _write_json(history_path, manifest)


def _history_snapshot_fingerprint(rows: Iterable[Mapping[str, JsonValue]]) -> str:
    hasher = hashlib.sha256()
    canonical_rows = sorted(
        (dict(row) for row in rows),
        key=lambda row: str(row["security_id"]),
    )
    _update_fingerprint_frame(hasher, canonical_rows)
    return hasher.hexdigest()


def _native_etfs_from_universe_state(
    universe_state_path: str | Path,
) -> dict[str, NativeETFUniverseEntry]:
    try:
        active_etfs = load_active_universe_etfs(universe_state_path)
    except OperationalUniverseInputError as exc:
        raise OperationalHoldingsInputError(str(exc)) from exc
    return {
        etf_id: NativeETFUniverseEntry(
            etf_id=record.etf_id,
            etf_name=record.etf_name,
            brand_id=record.brand_id,
            source_provider_id=record.source_provider_id,
        )
        for etf_id, record in active_etfs.items()
    }


def _native_brands(value: JsonValue) -> dict[str, NativeBrandMetadata]:
    if not isinstance(value, list) or not value:
        raise OperationalHoldingsInputError("native fixture brands must be a non-empty list")
    brands: dict[str, NativeBrandMetadata] = {}
    for index, item in enumerate(value, 1):
        if not isinstance(item, Mapping):
            raise OperationalHoldingsInputError(
                f"native fixture brand must be an object: index={index}"
            )
        brand_id = _native_required_field(item, "brand_id", aliases=("manager_id",))
        name = _native_required_field(
            item,
            "name",
            aliases=("brand_name", "manager_name"),
        )
        if brand_id in brands:
            raise OperationalHoldingsInputError(
                f"duplicate native fixture brand_id: {brand_id}"
            )
        brands[brand_id] = NativeBrandMetadata(brand_id=brand_id, name=name)
    return brands


def _native_etfs(
    value: JsonValue,
    *,
    brands: Mapping[str, NativeBrandMetadata],
) -> dict[str, NativeETFUniverseEntry]:
    if not isinstance(value, list) or not value:
        raise OperationalHoldingsInputError(
            "native fixture etf_universe must be a non-empty list"
        )
    etfs: dict[str, NativeETFUniverseEntry] = {}
    for index, item in enumerate(value, 1):
        if not isinstance(item, Mapping):
            raise OperationalHoldingsInputError(
                f"native fixture ETF must be an object: index={index}"
            )
        etf_id = _native_required_text(item.get("etf_id"), "etf_id")
        etf_name = _native_required_text(item.get("etf_name"), "etf_name")
        brand_id = _native_required_field(item, "brand_id", aliases=("manager_id",))
        source_provider_id = _native_required_field(
            item,
            "source_provider_id",
            aliases=("provider_id",),
        )
        if brand_id not in brands:
            raise OperationalHoldingsInputError(
                f"native fixture ETF references unknown brand_id: {brand_id}"
            )
        if etf_id in etfs:
            raise OperationalHoldingsInputError(
                f"duplicate native fixture etf_id: {etf_id}"
            )
        etfs[etf_id] = NativeETFUniverseEntry(
            etf_id=etf_id,
            etf_name=etf_name,
            brand_id=brand_id,
            source_provider_id=source_provider_id,
        )
    return etfs


def _native_snapshots(value: JsonValue) -> dict[str, NativeHoldingSnapshot]:
    if not isinstance(value, list) or not value:
        raise OperationalHoldingsInputError("native fixture snapshots must be a non-empty list")
    snapshots: dict[str, NativeHoldingSnapshot] = {}
    for index, item in enumerate(value, 1):
        if not isinstance(item, Mapping):
            raise OperationalHoldingsInputError(
                f"native fixture snapshot must be an object: index={index}"
            )
        observed_date = _native_required_text(item.get("as_of_date"), "as_of_date")
        _parse_iso_date(observed_date)
        holdings = item.get("holdings")
        if not isinstance(holdings, list):
            raise OperationalHoldingsInputError(
                f"native fixture snapshot holdings must be a list: {observed_date}"
            )
        if observed_date in snapshots:
            raise OperationalHoldingsInputError(
                f"duplicate native fixture snapshot date: {observed_date}"
            )
        snapshots[observed_date] = NativeHoldingSnapshot(
            as_of_date=observed_date,
            holdings=tuple(
                _native_required_holding(row, observed_date=observed_date)
                for row in holdings
            ),
        )
    return snapshots


def _native_required_holding(
    value: object,
    *,
    observed_date: str,
) -> Mapping[str, JsonValue]:
    if not isinstance(value, Mapping):
        raise OperationalHoldingsInputError(
            f"native fixture holding must be an object: {observed_date}"
        )
    return value


def _native_rows_for_snapshot(
    snapshot: NativeHoldingSnapshot,
    *,
    etfs: Mapping[str, NativeETFUniverseEntry],
) -> list[dict[str, JsonValue]]:
    rows: list[dict[str, JsonValue]] = []
    seen: set[tuple[str, str]] = set()
    for line_number, holding in enumerate(snapshot.holdings, 1):
        etf_id = _native_required_text(holding.get("etf_id"), "etf_id")
        etf = etfs.get(etf_id)
        if etf is None:
            raise OperationalHoldingsInputError(
                "native fixture holding references untracked or removed "
                f"etf_id: {etf_id}"
            )
        security_id = _native_required_text(holding.get("security_id"), "security_id")
        key = (etf_id, security_id)
        if key in seen:
            raise OperationalHoldingsInputError(
                "duplicate native fixture holding: "
                f"etf_id={etf_id} date={snapshot.as_of_date} security_id={security_id}"
            )
        seen.add(key)
        row = {
            "etf_id": etf.etf_id,
            "etf_name": etf.etf_name,
            "brand_id": etf.brand_id,
            "source_provider_id": etf.source_provider_id,
            "as_of_date": snapshot.as_of_date,
            "security_id": security_id,
            "ticker": _native_optional_text(holding.get("ticker"), "ticker"),
            "name": _native_required_text(holding.get("name"), "name"),
            "market": _native_optional_text(holding.get("market"), "market"),
            "sector": _native_optional_text(holding.get("sector"), "sector"),
            "theme": _native_optional_text(holding.get("theme"), "theme"),
            "country": _native_optional_text(holding.get("country"), "country"),
            "weight_percent": _native_required_number(
                holding.get("weight_percent"),
                "weight_percent",
            ),
            "shares": _native_optional_number(holding.get("shares"), "shares"),
            "market_value_krw": _native_optional_number(
                holding.get("market_value_krw"),
                "market_value_krw",
            ),
            "price_krw": _native_optional_number(holding.get("price_krw"), "price_krw"),
            "is_cash": _native_required_bool(holding.get("is_cash"), "is_cash"),
            "security_classification": _native_security_classification(
                holding.get("security_classification"),
            ),
        }
        rows.append(
            _validate_normalized_row(
                row=row,
                partition_date=snapshot.as_of_date,
                line_number=line_number,
            )
        )
    return rows


def _native_required_text(value: object, label: str) -> str:
    text = _text_value(value)
    if text is None:
        raise OperationalHoldingsInputError(f"native fixture field is required: {label}")
    _ensure_path_safe_summary_text(text, label=label)
    return text


def _native_required_field(
    item: Mapping[str, object],
    field: str,
    *,
    aliases: tuple[str, ...] = (),
) -> str:
    for candidate in (field, *aliases):
        if candidate in item:
            return _native_required_text(item.get(candidate), field)
    return _native_required_text(None, field)


def _native_optional_text(value: object, label: str) -> str | None:
    if value is None:
        return None
    text = _text_value(value)
    if text is None:
        raise OperationalHoldingsInputError(
            f"native fixture field must be a non-empty string or null: {label}"
        )
    _ensure_path_safe_summary_text(text, label=label)
    return text


def _native_required_number(value: object, label: str) -> JsonValue:
    if not isinstance(value, int | float) or isinstance(value, bool):
        raise OperationalHoldingsInputError(f"native fixture field must be numeric: {label}")
    return value


def _native_optional_number(value: object, label: str) -> JsonValue:
    if value is None:
        return None
    return _native_required_number(value, label)


def _native_required_bool(value: object, label: str) -> bool:
    if not isinstance(value, bool):
        raise OperationalHoldingsInputError(f"native fixture field must be boolean: {label}")
    return value


def _native_security_classification(value: object) -> str:
    try:
        return validate_security_classification(
            value,
            label="native fixture security_classification",
        )
    except ValueError as exc:
        raise OperationalHoldingsInputError(str(exc)) from exc


def _native_summary_items(value: JsonValue, *, field: str) -> list[dict[str, JsonValue]]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise OperationalHoldingsInputError(
            f"native fixture field must be a list: {field}"
        )
    items: list[dict[str, JsonValue]] = []
    for index, raw_item in enumerate(value, 1):
        if not isinstance(raw_item, Mapping):
            raise OperationalHoldingsInputError(
                f"native fixture summary item must be an object: {field}[{index}]"
            )
        code = _native_required_text(raw_item.get("code"), f"{field}[{index}].code")
        message = _native_required_text(
            raw_item.get("message"),
            f"{field}[{index}].message",
        )
        item: dict[str, JsonValue] = {"code": code, "message": message}
        for optional_field in ("metric", "value", "threshold"):
            optional_value = raw_item.get(optional_field)
            safe_value = _native_summary_json_value(optional_value)
            if safe_value is not None:
                item[optional_field] = safe_value
        items.append(item)
    return items


def _native_summary_json_value(value: object) -> JsonValue:
    if value is None or isinstance(value, bool | int | float):
        return value
    if isinstance(value, str):
        _ensure_path_safe_summary_text(value, label="summary value")
        return value
    return None


def _ensure_path_safe_summary_text(value: str, *, label: str) -> None:
    if "://" in value or "\\" in value or value.startswith("/"):
        raise OperationalHoldingsInputError(
            f"native fixture field is not path-safe for collection summary: {label}"
        )


def _read_json_object(path: Path) -> dict[str, JsonValue]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise OperationalHoldingsInputError(f"invalid JSON input: {path}: {exc.msg}") from exc
    if not isinstance(data, dict):
        raise OperationalHoldingsInputError(f"JSON input must be an object: {path}")
    return data


def _load_security_mapping(path: Path) -> dict[str, str]:
    if not path.is_file():
        raise OperationalHoldingsInputError(f"security mapping file not found: {path}")
    data = _read_json_object(path)
    if data.get("schema_version") != SECURITY_MAPPING_SCHEMA_VERSION:
        raise OperationalHoldingsInputError(f"invalid security mapping schema: {path}")
    mappings = data.get("mappings")
    if not isinstance(mappings, list):
        raise OperationalHoldingsInputError("security mapping field must be a list: mappings")
    mapping: dict[str, str] = {}
    for index, item in enumerate(mappings, 1):
        if not isinstance(item, Mapping):
            raise OperationalHoldingsInputError(
                f"security mapping entry must be an object: index={index}"
            )
        security_id = _mapping_text(item.get("security_id"))
        ticker = _mapping_text(item.get("ticker"))
        if security_id is None:
            raise OperationalHoldingsInputError(
                f"security mapping security_id must be a non-empty string: index={index}"
            )
        if ticker is None:
            raise OperationalHoldingsInputError(
                f"security mapping ticker must be a non-empty string: index={index}"
            )
        if security_id in mapping:
            raise OperationalHoldingsInputError(
                f"duplicate security mapping security_id: {security_id}"
            )
        mapping[security_id] = ticker
    return mapping


def _load_security_resolution(path: Path) -> _SecurityResolution:
    if not path.is_file():
        raise OperationalHoldingsInputError(f"security resolution file not found: {path}")
    try:
        data = validate_security_resolution_export(_read_json_object(path))
    except ValueError as exc:
        raise OperationalHoldingsInputError(str(exc)) from exc
    mappings = {
        str(item["security_id"]): item
        for item in data["mappings"]
        if isinstance(item, Mapping)
    }
    exclusions = {
        str(item["security_id"]): item
        for item in data["exclusions"]
        if isinstance(item, Mapping)
    }
    return _SecurityResolution(mappings=mappings, exclusions=exclusions)


def load_security_mapping(path: str | Path) -> dict[str, str]:
    return _load_security_mapping(Path(path))


def merge_security_mapping_patch(
    existing_mapping: Mapping[str, str],
    patch: Mapping[str, JsonValue],
    *,
    allow_replacements: bool = False,
) -> tuple[dict[str, JsonValue], dict[str, JsonValue]]:
    patch_mapping = _validate_security_mapping_patch(patch)
    merged = dict(existing_mapping)
    added_count = 0
    replaced_count = 0
    unchanged_count = 0

    for security_id, ticker in patch_mapping.items():
        existing_ticker = merged.get(security_id)
        if existing_ticker is None:
            merged[security_id] = ticker
            added_count += 1
            continue
        if existing_ticker == ticker:
            unchanged_count += 1
            continue
        if not allow_replacements:
            raise OperationalHoldingsInputError(
                f"security_id {security_id} existing mapping conflict"
            )
        merged[security_id] = ticker
        replaced_count += 1

    merged_document: dict[str, JsonValue] = {
        "schema_version": SECURITY_MAPPING_SCHEMA_VERSION,
        "mappings": [
            {"security_id": security_id, "ticker": ticker}
            for security_id, ticker in sorted(merged.items())
        ],
    }
    summary: dict[str, JsonValue] = {
        "added_mapping_count": added_count,
        "replaced_mapping_count": replaced_count,
        "unchanged_mapping_count": unchanged_count,
        "total_mapping_count": len(merged),
    }
    return merged_document, summary


def _validate_security_mapping_patch(patch: Mapping[str, JsonValue]) -> dict[str, str]:
    if set(patch) != {"schema_version", "mappings"}:
        raise OperationalHoldingsInputError(
            "security mapping patch fields must be exactly schema_version and mappings"
        )
    if patch.get("schema_version") != SECURITY_MAPPING_PATCH_SCHEMA_VERSION:
        raise OperationalHoldingsInputError("invalid security mapping patch schema")
    mappings = patch.get("mappings")
    if not isinstance(mappings, list):
        raise OperationalHoldingsInputError("security mapping patch field must be a list: mappings")
    if not mappings:
        raise OperationalHoldingsInputError("security mapping patch mappings must not be empty")
    mapping: dict[str, str] = {}
    for index, item in enumerate(mappings, 1):
        if not isinstance(item, Mapping):
            raise OperationalHoldingsInputError(
                f"security mapping patch entry must be an object: index={index}"
            )
        if set(item) != {"security_id", "ticker"}:
            raise OperationalHoldingsInputError(
                "security mapping patch entries must contain exactly security_id and ticker"
            )
        security_id = _mapping_text(item.get("security_id"))
        ticker = _mapping_text(item.get("ticker"))
        if security_id is None:
            raise OperationalHoldingsInputError(
                f"security mapping patch security_id must be a non-empty string: index={index}"
            )
        if ticker is None:
            raise OperationalHoldingsInputError(
                f"security mapping patch ticker must be a non-empty string: index={index}"
            )
        if security_id in mapping:
            raise OperationalHoldingsInputError(
                f"duplicate security mapping patch security_id: {security_id}"
            )
        mapping[security_id] = ticker
    return mapping


def _mapping_text(value: JsonValue) -> str | None:
    if not isinstance(value, str):
        return None
    text = value.strip()
    return text or None


def _validate_normalized_dates(value: JsonValue) -> list[str]:
    if not isinstance(value, list):
        raise OperationalHoldingsInputError("normalized manifest dates must be a list")
    dates: list[str] = []
    parsed_dates: list[date] = []
    for item in value:
        if not isinstance(item, str):
            raise OperationalHoldingsInputError("normalized manifest dates must be ISO strings")
        parsed = _parse_iso_date(item)
        dates.append(item)
        parsed_dates.append(parsed)
    if parsed_dates != sorted(parsed_dates, reverse=True):
        raise OperationalHoldingsInputError(
            "normalized manifest dates must be in descending order"
        )
    return dates


def _parse_iso_date(value: str) -> date:
    if len(value) != 10:
        raise OperationalHoldingsInputError(f"invalid ISO date: {value}")
    try:
        parsed = date.fromisoformat(value)
    except ValueError as exc:
        raise OperationalHoldingsInputError(f"invalid ISO date: {value}") from exc
    if parsed.isoformat() != value:
        raise OperationalHoldingsInputError(f"invalid ISO date: {value}")
    return parsed


def _normalized_partition_path(
    *,
    manifest_path: Path,
    partition_specs: Mapping[str, JsonValue],
    partition_date: str,
) -> Path:
    partition = partition_specs.get(partition_date)
    if not isinstance(partition, Mapping):
        raise OperationalHoldingsInputError(
            f"missing normalized partition entry: {partition_date}"
        )
    file_value = partition.get("file")
    if not isinstance(file_value, str) or not file_value:
        raise OperationalHoldingsInputError(
            f"partition file must be manifest-relative: {partition_date}"
        )
    relative_path = Path(file_value)
    if relative_path.is_absolute():
        raise OperationalHoldingsInputError(
            f"partition file must be manifest-relative: {partition_date}"
        )
    manifest_dir = manifest_path.parent.resolve()
    resolved = (manifest_dir / relative_path).resolve()
    try:
        resolved.relative_to(manifest_dir)
    except ValueError as exc:
        raise OperationalHoldingsInputError(
            f"partition file must stay inside copied export directory: {partition_date}"
        ) from exc
    if resolved.stem != partition_date:
        raise OperationalHoldingsInputError(
            f"partition filename date must match manifest date: {partition_date}"
        )
    return resolved


def _expected_partition_record_count(
    partition_specs: Mapping[str, JsonValue], partition_date: str
) -> int:
    partition = partition_specs.get(partition_date)
    if not isinstance(partition, Mapping):
        raise OperationalHoldingsInputError(
            f"missing normalized partition entry: {partition_date}"
        )
    record_count = partition.get("record_count")
    if not isinstance(record_count, int) or isinstance(record_count, bool):
        raise OperationalHoldingsInputError(
            f"partition record_count must be an integer: {partition_date}"
        )
    return record_count


def _read_normalized_partition(
    *,
    partition_path: Path,
    partition_date: str,
    expected_record_count: int,
) -> list[dict[str, JsonValue]]:
    rows: list[dict[str, JsonValue]] = []
    seen: set[tuple[str, str, str]] = set()
    for line_number, line in enumerate(partition_path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            data = json.loads(line)
        except json.JSONDecodeError as exc:
            raise OperationalHoldingsInputError(
                f"malformed normalized JSONL for date {partition_date} line {line_number}"
            ) from exc
        if not isinstance(data, dict):
            raise OperationalHoldingsInputError(
                f"normalized holding row must be an object: {partition_date} line {line_number}"
            )
        row = _validate_normalized_row(
            row=data,
            partition_date=partition_date,
            line_number=line_number,
        )
        key = (str(row["etf_id"]), partition_date, str(row["security_id"]))
        if key in seen:
            raise OperationalHoldingsInputError(
                "duplicate normalized holding: "
                f"etf_id={key[0]} date={key[1]} security_id={key[2]}"
            )
        seen.add(key)
        rows.append(row)
    if len(rows) != expected_record_count:
        raise OperationalHoldingsInputError(
            f"partition record_count mismatch: date={partition_date} "
            f"expected={expected_record_count} actual={len(rows)}"
        )
    return rows


def _validate_normalized_row(
    *,
    row: Mapping[str, Any],
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
    required_fields = {
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
    missing = sorted(field for field in required_fields if field not in normalized)
    if missing:
        raise OperationalHoldingsInputError(
            f"normalized holding missing fields for {partition_date} line {line_number}: "
            f"{', '.join(missing)}"
        )
    if normalized["as_of_date"] != partition_date:
        raise OperationalHoldingsInputError(
            f"normalized row as_of_date must match partition date: {partition_date} "
            f"line {line_number}"
        )
    for field in (
        "etf_id",
        "etf_name",
        "brand_id",
        "source_provider_id",
        "security_id",
        "name",
    ):
        if _text_value(normalized.get(field)) is None:
            raise OperationalHoldingsInputError(
                f"normalized holding field must not be null: {field}"
            )
    if normalized["weight_percent"] is None:
        raise OperationalHoldingsInputError(
            "normalized holding field must not be null: weight_percent"
        )
    if not isinstance(normalized["is_cash"], bool):
        raise OperationalHoldingsInputError(
            "normalized holding field must be boolean: is_cash"
        )
    try:
        validate_security_classification(
            normalized.get("security_classification"),
            label="normalized holding security_classification",
        )
    except ValueError as exc:
        raise OperationalHoldingsInputError(str(exc)) from exc
    return normalized


def _group_rows_by_etf(
    rows: Iterable[Mapping[str, JsonValue]],
) -> dict[str, list[dict[str, JsonValue]]]:
    groups: dict[str, list[dict[str, JsonValue]]] = {}
    for row in rows:
        etf_id = str(row["etf_id"])
        groups.setdefault(etf_id, []).append(dict(row))
    return groups


def _build_etf_snapshots(
    *,
    etf_id: str,
    current_rows: list[dict[str, JsonValue]],
    previous_rows: list[dict[str, JsonValue]],
) -> ETFHoldingsSnapshots:
    current_first = current_rows[0]
    return ETFHoldingsSnapshots(
        etf_id=etf_id,
        etf_name=str(current_first["etf_name"]),
        brand_id=str(current_first["brand_id"]),
        source_provider_id=str(current_first["source_provider_id"]),
        previous=tuple(_security_holding_from_row(row) for row in previous_rows),
        current=tuple(_security_holding_from_row(row) for row in current_rows),
    )


def _security_holding_from_row(row: Mapping[str, JsonValue]) -> SecurityHolding:
    return SecurityHolding(
        security_id=str(row["security_id"]),
        analytical_identity_key=_optional_text(row.get("analytical_identity_key")),
        security_group_id=_optional_text(row.get("security_group_id")),
        listing_key=_optional_text(row.get("listing_key")),
        security_group_name=_optional_text(row.get("security_group_name")),
        security_group_ticker=_optional_text(row.get("security_group_ticker")),
        ticker=_optional_text(row.get("ticker")),
        name=str(row["name"]),
        market=_optional_text(row.get("market")),
        sector=_optional_text(row.get("sector")),
        theme=_optional_text(row.get("theme")),
        country=_optional_text(row.get("country")),
        weight_percent=float(row["weight_percent"]),
        shares=_optional_float(row.get("shares")),
        market_value_krw=_optional_float(row.get("market_value_krw")),
        price_krw=_optional_float(row.get("price_krw")),
        is_cash=bool(row["is_cash"]),
    )


def _optional_text(value: JsonValue) -> str | None:
    if value is None:
        return None
    return str(value)


def _optional_float(value: JsonValue) -> float | None:
    if value is None:
        return None
    return float(value)


def _load_evidence(evidence_path: Path | None) -> tuple[EvidenceItemInput, ...]:
    if evidence_path is None:
        return ()
    try:
        data = json.loads(evidence_path.read_text(encoding="utf-8"))
        if not isinstance(data, list):
            raise ValueError("evidence input must be a list")
        return tuple(EvidenceItemInput.model_validate(item) for item in data)
    except SignalReportInputError:
        raise
    except Exception as exc:
        raise SignalReportInputError(
            f"invalid evidence input: {evidence_path}: {exc}"
        ) from exc


def _load_sync_metadata_quality_subset(manifest_path: Path) -> dict[str, JsonValue]:
    sync_metadata_path = manifest_path.parent / "sync_metadata.json"
    if not sync_metadata_path.is_file():
        collection_summary_path = manifest_path.parent / "collection_summary.json"
        if collection_summary_path.is_file():
            return _load_collection_summary_quality_subset(collection_summary_path)
        return {"sync_metadata_available": False}

    sync_metadata = _read_json_object(sync_metadata_path)
    if sync_metadata.get("schema_version") != SYNC_METADATA_SCHEMA_VERSION:
        raise OperationalHoldingsInputError(
            f"invalid sync metadata schema: {sync_metadata_path}"
        )
    quality_counts: dict[str, JsonValue] = {}
    for key in (
        "derived_cash_weight_count",
        "derived_cash_weight_fit_failed_count",
        "skipped_unusable_cash_weight_count",
        "uncoded_cash_holding_count",
        "skipped_missing_security_id_count",
        "numeric_null_normalized_count",
        "duplicate_aggregated_count",
        "renamed_security_count",
        "non_ticker_excluded_security_count",
        "cash_identification_counts",
        "ticker_collision_review_count",
        "ticker_collision_review_samples",
    ):
        if key in sync_metadata:
            quality_counts[key] = sync_metadata[key]

    source_quality_samples = sync_metadata.get("source_quality_samples")
    if not isinstance(source_quality_samples, list):
        source_quality_samples = []
    subset: dict[str, JsonValue] = {
        "sync_metadata_available": True,
        "sync_quality_counts": quality_counts,
        "source_quality_samples": [
            sample
            for sample in source_quality_samples[:_SOURCE_QUALITY_SAMPLE_LIMIT]
            if isinstance(sample, dict)
        ],
    }
    sync_quality = sync_metadata.get("sync_quality")
    if isinstance(sync_quality, dict):
        subset["sync_quality"] = _build_sync_quality(sync_metadata)
    return subset


def _load_collection_summary_quality_subset(
    collection_summary_path: Path,
) -> dict[str, JsonValue]:
    collection_summary = _read_json_object(collection_summary_path)
    if collection_summary.get("schema_version") != COLLECTION_SUMMARY_SCHEMA_VERSION:
        raise OperationalHoldingsInputError(
            f"invalid collection summary schema: {collection_summary_path}"
        )
    subset: dict[str, JsonValue] = {
        "collection_summary_available": True,
        "collection_source_type": collection_summary.get("collection_source_type"),
    }
    projected_summary: dict[str, JsonValue] = {}
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
        "security_coverage",
    ):
        if field not in collection_summary:
            continue
        value = collection_summary.get(field)
        if _native_summary_projection_value(value):
            projected_summary[field] = value
    if "brand_count" not in projected_summary and "manager_count" in collection_summary:
        value = collection_summary.get("manager_count")
        if _native_summary_projection_value(value):
            projected_summary["brand_count"] = value
    if projected_summary:
        subset["collection_summary"] = projected_summary
    return subset


def _native_summary_projection_value(value: object) -> bool:
    if value is None or isinstance(value, str | int | float | bool):
        return True
    if isinstance(value, list):
        return all(_native_summary_projection_value(item) for item in value)
    if isinstance(value, dict):
        return all(
            isinstance(key, str) and _native_summary_projection_value(item)
            for key, item in value.items()
        )
    return False


def _build_sync_quality(metadata: Mapping[str, JsonValue]) -> dict[str, JsonValue]:
    derived_count = _int_metadata(metadata, "derived_cash_weight_count")
    fit_failed_count = _int_metadata(metadata, "derived_cash_weight_fit_failed_count")
    unusable_count = _int_metadata(metadata, "skipped_unusable_cash_weight_count")
    attempt_count = derived_count + fit_failed_count + unusable_count
    failure_count = fit_failed_count + unusable_count
    missing_source_dates = _iso_date_values(metadata.get("missing_source_dates"))
    skipped_missing_security_id_count = _int_metadata(
        metadata, "skipped_missing_security_id_count"
    )
    ticker_collision_review_count = _int_metadata(
        metadata, "ticker_collision_review_count"
    )
    security_mapping_available = metadata.get("security_mapping_available") is True
    security_resolution_available = metadata.get("security_resolution_available") is True
    mapped_security_count = _int_metadata(metadata, "mapped_security_count")
    unmapped_security_count = _int_metadata(metadata, "unmapped_security_count")
    non_ticker_excluded_security_count = _int_metadata(
        metadata, "non_ticker_excluded_security_count"
    )
    ticker_mapping_denominator = mapped_security_count + unmapped_security_count
    cash_like_row_count = sum(
        _int_value(value)
        for value in _mapping_values(metadata.get("cash_identification_counts"))
    )
    metrics: dict[str, JsonValue] = {
        "cash_derivation_attempt_count": attempt_count,
        "cash_derivation_failure_count": failure_count,
        "cash_derivation_failure_ratio": _rounded_ratio(failure_count, attempt_count),
        "fit_failure_ratio": _rounded_ratio(fit_failed_count, attempt_count),
        "unusable_cash_weight_ratio": _rounded_ratio(unusable_count, attempt_count),
        "cash_like_row_count": cash_like_row_count,
        "missing_source_date_count": len(missing_source_dates),
        "missing_source_dates": missing_source_dates,
        "skipped_missing_security_id_count": skipped_missing_security_id_count,
        "security_mapping_available": security_mapping_available,
        "security_resolution_available": security_resolution_available,
        "mapped_security_count": mapped_security_count,
        "unmapped_security_count": unmapped_security_count,
        "non_ticker_excluded_security_count": non_ticker_excluded_security_count,
        "ticker_mapping_coverage_ratio": _rounded_ratio(
            mapped_security_count, ticker_mapping_denominator
        ),
        "ticker_collision_review_count": ticker_collision_review_count,
        "cash_derivation_failure_distribution": _sync_quality_failure_distribution(
            metadata.get("cash_derivation_failure_distribution")
        ),
    }
    warnings: list[dict[str, JsonValue]] = []
    risk_failures: list[dict[str, JsonValue]] = []
    cash_failure_ratio = metrics["cash_derivation_failure_ratio"]
    ticker_mapping_coverage_ratio = metrics["ticker_mapping_coverage_ratio"]
    if isinstance(cash_failure_ratio, int | float):
        if cash_failure_ratio >= _CASH_DERIVATION_RISK_THRESHOLD:
            risk_failures.append(
                {
                    "code": "cash_derivation_failure_ratio",
                    "message": (
                        "Cash weight derivation failures exceeded the operational "
                        "review threshold."
                    ),
                    "metric": "cash_derivation_failure_ratio",
                    "value": cash_failure_ratio,
                    "threshold": _CASH_DERIVATION_RISK_THRESHOLD,
                }
            )
        elif cash_failure_ratio >= _CASH_DERIVATION_WARNING_THRESHOLD:
            warnings.append(
                {
                    "code": "cash_derivation_failure_ratio",
                    "message": (
                        "Cash weight derivation failures reached the operational "
                        "warning threshold."
                    ),
                    "metric": "cash_derivation_failure_ratio",
                    "value": cash_failure_ratio,
                    "threshold": _CASH_DERIVATION_WARNING_THRESHOLD,
                }
            )
    if (
        isinstance(ticker_mapping_coverage_ratio, int | float)
        and ticker_mapping_coverage_ratio < _TICKER_MAPPING_RISK_THRESHOLD
    ):
        risk_failures.append(
            {
                "code": "low_ticker_mapping_coverage",
                "message": "Ticker mapping coverage fell below the operational review threshold.",
                "metric": "ticker_mapping_coverage_ratio",
                "value": ticker_mapping_coverage_ratio,
                "threshold": _TICKER_MAPPING_RISK_THRESHOLD,
            }
        )
    elif (
        isinstance(ticker_mapping_coverage_ratio, int | float)
        and ticker_mapping_coverage_ratio < _TICKER_MAPPING_WARNING_THRESHOLD
    ):
        warnings.append(
            {
                "code": "low_ticker_mapping_coverage",
                "message": "Ticker mapping coverage fell below the operational warning threshold.",
                "metric": "ticker_mapping_coverage_ratio",
                "value": ticker_mapping_coverage_ratio,
                "threshold": _TICKER_MAPPING_WARNING_THRESHOLD,
            }
        )
    if skipped_missing_security_id_count > 0:
        warnings.append(
            {
                "code": "skipped_missing_security_id",
                "message": "Non-cash source rows without a stable security id were skipped.",
                "metric": "skipped_missing_security_id_count",
                "value": skipped_missing_security_id_count,
                "threshold": 0,
            }
        )
    if ticker_collision_review_count > 0:
        warnings.append(
            {
                "code": "ticker_collision_review_required",
                "message": (
                    "Mapped ticker collisions require reviewed security group decisions."
                ),
                "metric": "ticker_collision_review_count",
                "value": ticker_collision_review_count,
                "threshold": 0,
            }
        )
    if missing_source_dates:
        warnings.append(
            {
                "code": "missing_source_dates",
                "message": "Some requested observed source dates were unavailable during sync.",
                "metric": "missing_source_date_count",
                "value": len(missing_source_dates),
                "threshold": 0,
            }
        )
    status = "risk_failed" if risk_failures else "warning" if warnings else "ok"
    return {
        "schema_version": SYNC_QUALITY_SCHEMA_VERSION,
        "status": status,
        "metrics": metrics,
        "warnings": warnings,
        "risk_failures": risk_failures,
    }


def _int_metadata(metadata: Mapping[str, JsonValue], key: str) -> int:
    return _int_value(metadata.get(key))


def _int_value(value: object) -> int:
    if isinstance(value, bool):
        return 0
    if isinstance(value, int):
        return value
    return 0


def _mapping_values(value: JsonValue) -> Iterable[JsonValue]:
    if isinstance(value, Mapping):
        return value.values()
    return ()


def _rounded_ratio(numerator: int, denominator: int) -> float | None:
    if denominator == 0:
        return None
    return round(numerator / denominator, 6)


def _iso_date_values(value: JsonValue) -> list[str]:
    if not isinstance(value, list):
        return []
    dates: list[str] = []
    for item in value:
        if not isinstance(item, str):
            continue
        if len(item) == 8 and item.isdigit():
            dates.append(_raw_date_to_iso(item))
        else:
            dates.append(item)
    return dates


def _sync_quality_failure_distribution(value: JsonValue) -> dict[str, JsonValue]:
    distribution = _empty_cash_derivation_failure_distribution()
    if not isinstance(value, Mapping):
        return distribution
    source_by_reason = value.get("by_reason")
    by_reason = distribution["by_reason"]
    assert isinstance(by_reason, dict)
    if isinstance(source_by_reason, Mapping):
        for reason in _CASH_DERIVATION_FAILURE_REASONS:
            by_reason[reason] = _int_value(source_by_reason.get(reason))
    source_by_date = value.get("by_date")
    by_date = distribution["by_date"]
    assert isinstance(by_date, dict)
    if isinstance(source_by_date, Mapping):
        for date_value, count in source_by_date.items():
            if isinstance(date_value, str):
                by_date[date_value] = _int_value(count)
    return distribution


def _required_list(mapping: Mapping[str, JsonValue], key: str) -> list[JsonValue]:
    value = mapping.get(key)
    if not isinstance(value, list):
        raise OperationalHoldingsInputError(f"source manifest field must be a list: {key}")
    return value


def _required_mapping(mapping: Mapping[str, JsonValue], key: str) -> Mapping[str, JsonValue]:
    value = mapping.get(key)
    if not isinstance(value, Mapping):
        raise OperationalHoldingsInputError(f"source manifest field must be an object: {key}")
    return value


def _latest_observed_source_dates(
    raw_dates: Iterable[JsonValue], *, observed_partitions: int
) -> list[str]:
    parsed: list[tuple[date, str]] = []
    for value in raw_dates:
        if not isinstance(value, str):
            raise OperationalHoldingsInputError("source manifest date must be YYYYMMDD")
        parsed.append((_parse_raw_date(value), value))
    parsed.sort(key=lambda item: item[0], reverse=True)
    return [value for _, value in parsed[:observed_partitions]]


def _parse_raw_date(value: str) -> date:
    if len(value) != 8 or not value.isdigit():
        raise OperationalHoldingsInputError(f"invalid source date: {value}")
    try:
        return datetime.strptime(value, "%Y%m%d").date()
    except ValueError as exc:
        raise OperationalHoldingsInputError(f"invalid source date: {value}") from exc


def _raw_date_to_iso(value: str) -> str:
    return _parse_raw_date(value).isoformat()


def _sync_timestamp(now: Callable[[], datetime] | None) -> str:
    value = now() if now is not None else datetime.now(UTC)
    if value.tzinfo is None or value.utcoffset() is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(UTC).isoformat()


def _resolve_source_partition(
    *,
    source_manifest: Path,
    source_partitions: Mapping[str, JsonValue],
    source_date: str,
) -> tuple[Path | None, str]:
    sibling = source_manifest.parent / f"{source_manifest.name}.parts" / f"{source_date}.jsonl"
    if sibling.is_file():
        return sibling, "sibling"

    partition = source_partitions.get(source_date)
    if isinstance(partition, Mapping):
        file_value = partition.get("file")
        if isinstance(file_value, str):
            manifest_file = Path(file_value)
            if manifest_file.is_file():
                return manifest_file, "manifest_file"
    return None, "manifest_file"


def _normalize_source_partition(
    *,
    source_file: Path,
    source_date: str,
    copied_date: str,
    security_names: dict[str, list[str]],
    security_mapping: Mapping[str, str] | None,
    security_resolution: _SecurityResolution | None,
) -> tuple[list[dict[str, JsonValue]], dict[str, Any]]:
    aggregates: dict[tuple[str, str, str], _AggregateHolding] = {}
    metadata: dict[str, Any] = {
        "skipped_missing_security_id_count": 0,
        "derived_cash_weight_count": 0,
        "derived_cash_weight_fit_failed_count": 0,
        "skipped_unusable_cash_weight_count": 0,
        "uncoded_cash_holding_count": 0,
        "mapped_security_count": 0,
        "unmapped_security_count": 0,
        "non_ticker_excluded_security_count": 0,
        "cash_identification_counts": _empty_cash_identification_counts(),
        "cash_derivation_failure_distribution": _empty_cash_derivation_failure_distribution(),
        "source_quality_samples": [],
        "unmapped_security_sample_observations": [],
        "numeric_null_normalized_count": 0,
        "duplicate_aggregated_count": 0,
        "ticker_collision_review_count": 0,
        "ticker_collision_review_samples": [],
    }
    for line_number, line in enumerate(source_file.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            source_row = json.loads(line)
        except json.JSONDecodeError as exc:
            raise OperationalHoldingsInputError(
                f"malformed JSONL for source date {source_date} line {line_number}"
            ) from exc
        if not isinstance(source_row, Mapping):
            raise OperationalHoldingsInputError(
                f"source row must be an object for source date {source_date} line {line_number}"
            )
        row = _normalize_source_row(
            source_row=source_row,
            source_date=source_date,
            copied_date=copied_date,
            line_number=line_number,
            metadata=metadata,
            security_mapping=security_mapping,
            security_resolution=security_resolution,
        )
        if row is None:
            continue
        security_id = str(row.row["security_id"])
        name = str(row.row["name"])
        if name not in security_names[security_id]:
            security_names[security_id].append(name)
        key = (str(row.row["etf_id"]), copied_date, security_id)
        existing = aggregates.get(key)
        if existing is None:
            aggregates[key] = row
            continue

        metadata["duplicate_aggregated_count"] += 1
        existing.source_weight_missing = (
            existing.source_weight_missing or row.source_weight_missing
        )
        existing.weight_sum = _add_optional(existing.weight_sum, row.weight_sum)
        existing.row["weight_percent"] = (
            None if existing.source_weight_missing else existing.weight_sum
        )
        existing.shares_sum = _add_optional(existing.shares_sum, row.shares_sum)
        existing.market_value_sum = _add_optional(
            existing.market_value_sum, row.market_value_sum
        )
        existing.row["shares"] = existing.shares_sum
        existing.row["market_value_krw"] = existing.market_value_sum
        existing.numeric_null_count += row.numeric_null_count
        existing.line_numbers.extend(row.line_numbers)

    _derive_missing_cash_weights(aggregates.values(), metadata)
    metadata["unmapped_security_sample_observations"].extend(
        _unmapped_security_sample_observations(aggregates.values())
    )
    _apply_security_name_canonicalization(aggregates.values(), security_names)
    normalized_rows: list[dict[str, JsonValue]] = []
    for holding in aggregates.values():
        if holding.skipped_reason is not None:
            continue
        if holding.row["weight_percent"] is None:
            raise OperationalHoldingsInputError(
                "normalized holding field must not be null: weight_percent"
            )
        metadata["numeric_null_normalized_count"] += holding.numeric_null_count
        if holding.cash_rule_id == "uncoded_cash_keyword":
            metadata["uncoded_cash_holding_count"] += 1
        security_classification = str(holding.row["security_classification"])
        if security_classification == "ticker_candidate":
            if holding.row["ticker"] is None:
                metadata["unmapped_security_count"] += 1
            else:
                metadata["mapped_security_count"] += 1
        elif security_classification in {"cash_like", "non_equity"}:
            metadata["non_ticker_excluded_security_count"] += 1
        normalized_rows.append(holding.row)
    ticker_collision_samples = _ticker_collision_review_samples(normalized_rows)
    metadata["ticker_collision_review_count"] = len(ticker_collision_samples)
    metadata["ticker_collision_review_samples"] = ticker_collision_samples[
        :_TICKER_COLLISION_SAMPLE_LIMIT
    ]
    return normalized_rows, metadata


def _normalize_source_row(
    *,
    source_row: Mapping[str, Any],
    source_date: str,
    copied_date: str,
    line_number: int,
    metadata: dict[str, Any],
    security_mapping: Mapping[str, str] | None,
    security_resolution: _SecurityResolution | None,
) -> _AggregateHolding | None:
    source_code = _text_value(source_row.get("code"))
    source_name = _text_value(source_row.get("name")) or source_code or ""
    cash_rule_id = _cash_holding_rule_id(
        security_id=source_code,
        source_name=source_name,
    )
    security_classification = _SECURITY_CLASSIFICATION_POLICY.classify(
        security_id=source_code,
        name=source_name,
        as_of_date=copied_date,
        maturity_date=_source_maturity_date(source_row),
    )
    if source_code is None and cash_rule_id is None and security_classification != "cash_like":
        metadata["skipped_missing_security_id_count"] += 1
        _add_source_quality_sample(
            metadata,
            _source_quality_sample_from_source_row(
                source_row=source_row,
                source_date=source_date,
                line_number=line_number,
                reason="missing_security_id",
            ),
        )
        return None
    source_as_of_date = source_row.get("as_of_date")
    if source_as_of_date is not None and source_as_of_date != source_date:
        raise OperationalHoldingsInputError(
            f"source row date mismatch for source date {source_date} line {line_number}"
        )
    if cash_rule_id is not None:
        cash_counts = metadata["cash_identification_counts"]
        cash_counts[cash_rule_id] = int(cash_counts.get(cash_rule_id, 0)) + 1
        security_classification = "cash_like"

    fund_id = _required_text(source_row, "fund_id", source_date, line_number)
    source_provider_id = _required_text_alias(
        source_row,
        "source_provider_id",
        source_date,
        line_number,
        aliases=("provider_id",),
    )
    if source_code is None:
        security_id = f"CASH_UNCODED:{source_provider_id}:{fund_id}"
        ticker: JsonValue = None
        identity_metadata: dict[str, JsonValue] = {}
    else:
        security_id = source_code
        identity_metadata = {}
        resolution_mapping = (
            security_resolution.mappings.get(security_id)
            if security_resolution is not None
            else None
        )
        resolution_exclusion = (
            security_resolution.exclusions.get(security_id)
            if security_resolution is not None
            else None
        )
        if resolution_mapping is not None:
            security_classification = "ticker_candidate"
            ticker = resolution_mapping["ticker"]
            _apply_optional_identity_fields(identity_metadata, resolution_mapping)
        elif resolution_exclusion is not None:
            security_classification = str(resolution_exclusion["security_classification"])
            ticker = None
        elif security_classification == "cash_like":
            ticker = None
        else:
            ticker = security_mapping.get(security_id) if security_mapping is not None else None
    is_cash = security_classification == "cash_like"

    weight_missing = _number_is_missing(source_row.get("weight_pct"))
    if weight_missing:
        if not is_cash:
            raise OperationalHoldingsInputError(
                "non-cash source row weight_pct must not be null "
                f"for source date {source_date} line {line_number}"
            )
        weight_percent = None
    else:
        weight_percent = _required_number(
            source_row.get("weight_pct"),
            field="weight_pct",
            source_date=source_date,
            line_number=line_number,
        )
    shares, shares_null_count = _optional_number_with_null_count(
        source_row.get("quantity"),
        field="quantity",
        source_date=source_date,
        line_number=line_number,
    )
    market_value, market_value_null_count = _optional_number_with_null_count(
        source_row.get("eval_amount_krw"),
        field="eval_amount_krw",
        source_date=source_date,
        line_number=line_number,
    )

    row: dict[str, JsonValue] = {
        "etf_id": fund_id,
        "etf_name": _required_text(source_row, "fund_name", source_date, line_number),
        "brand_id": _required_text_alias(
            source_row,
            "brand_id",
            source_date,
            line_number,
            aliases=("manager_id",),
        ),
        "source_provider_id": source_provider_id,
        "as_of_date": copied_date,
        "security_id": security_id,
        "ticker": ticker,
        "name": "Cash" if is_cash else source_name,
        "market": None,
        "sector": None,
        "theme": None,
        "country": None,
        "weight_percent": weight_percent,
        "shares": shares,
        "market_value_krw": market_value,
        "price_krw": None,
        "is_cash": is_cash,
        "security_classification": security_classification,
    }
    _apply_analytical_identity_fields(row)
    row.update(identity_metadata)
    return _AggregateHolding(
        row=row,
        shares_sum=shares,
        market_value_sum=market_value,
        weight_sum=weight_percent,
        source_weight_missing=weight_missing,
        cash_rule_id=cash_rule_id,
        numeric_null_count=shares_null_count + market_value_null_count,
        line_numbers=[line_number],
        sample_context=_source_quality_context_from_source_row(
            source_row=source_row,
            source_date=source_date,
            line_number=line_number,
        ),
    )


def _required_text(
    source_row: Mapping[str, Any], field: str, source_date: str, line_number: int
) -> str:
    value = _text_value(source_row.get(field))
    if value is None:
        raise OperationalHoldingsInputError(
            f"missing required source field {field} for source date {source_date} "
            f"line {line_number}"
        )
    return value


def _required_text_alias(
    source_row: Mapping[str, Any],
    field: str,
    source_date: str,
    line_number: int,
    *,
    aliases: tuple[str, ...] = (),
) -> str:
    for candidate in (field, *aliases):
        if candidate in source_row:
            value = _text_value(source_row.get(candidate))
            if value is not None:
                return value
    raise OperationalHoldingsInputError(
        f"missing required source field {field} for source date {source_date} "
        f"line {line_number}"
    )


def _text_value(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _required_number(value: Any, *, field: str, source_date: str, line_number: int) -> float:
    try:
        return float(value)
    except (TypeError, ValueError) as exc:
        raise OperationalHoldingsInputError(
            f"invalid numeric source field {field} for source date {source_date} line {line_number}"
        ) from exc


def _number_is_missing(value: Any) -> bool:
    return value is None or (isinstance(value, str) and value.strip() == "")


def _source_maturity_date(source_row: Mapping[str, Any]) -> str | None:
    for field in ("maturity_date", "maturity", "maturity_dt"):
        value = _text_value(source_row.get(field))
        if value is not None:
            return value
    return None


def _optional_number_with_null_count(
    value: Any,
    *,
    field: str,
    source_date: str,
    line_number: int,
) -> tuple[float | None, int]:
    if _number_is_missing(value):
        return None, 0
    try:
        return float(value), 0
    except (TypeError, ValueError):
        return None, 1


def _add_optional(current: float | None, incoming: float | None) -> float | None:
    if incoming is None:
        return current
    if current is None:
        return incoming
    return current + incoming


def _derive_missing_cash_weights(
    holdings: Iterable[_AggregateHolding],
    metadata: dict[str, Any],
) -> None:
    grouped: dict[tuple[str, str], list[_AggregateHolding]] = defaultdict(list)
    for holding in holdings:
        grouped[(str(holding.row["etf_id"]), str(holding.row["as_of_date"]))].append(holding)

    for group in grouped.values():
        denominator_values = [
            holding.market_value_sum
            for holding in group
            if holding.market_value_sum is not None
        ]
        denominator = sum(denominator_values)
        denominator_is_valid = bool(denominator_values) and denominator > 0
        fit_failure_reason: str | None = None
        if denominator_is_valid:
            fit_errors = [
                abs(
                    float(holding.weight_sum)
                    - (float(holding.market_value_sum) / denominator * 100)
                )
                for holding in group
                if (
                    not holding.source_weight_missing
                    and holding.weight_sum is not None
                    and holding.market_value_sum is not None
                )
            ]
            if not fit_errors:
                fit_failure_reason = "no_weight_fit_sample"
            elif _median(fit_errors) > _WEIGHT_FIT_TOLERANCE_PERCENT_POINTS:
                fit_failure_reason = "weight_fit_tolerance_exceeded"

        for holding in group:
            if (
                holding.skipped_reason is not None
                or not bool(holding.row["is_cash"])
                or not holding.source_weight_missing
            ):
                continue
            if holding.market_value_sum is None:
                _skip_cash_derivation_holding(
                    holding=holding,
                    metadata=metadata,
                    reason="invalid_cash_market_value",
                    count_field="skipped_unusable_cash_weight_count",
                )
                continue
            if not denominator_is_valid:
                _skip_cash_derivation_holding(
                    holding=holding,
                    metadata=metadata,
                    reason="invalid_snapshot_market_value_total",
                    count_field="skipped_unusable_cash_weight_count",
                )
                continue
            if fit_failure_reason is not None:
                _skip_cash_derivation_holding(
                    holding=holding,
                    metadata=metadata,
                    reason=fit_failure_reason,
                    count_field="derived_cash_weight_fit_failed_count",
                )
                continue

            derived_weight = float(holding.market_value_sum) / denominator * 100
            holding.weight_sum = derived_weight
            holding.row["weight_percent"] = round(derived_weight, 6)
            metadata["derived_cash_weight_count"] += 1


def _skip_cash_derivation_holding(
    *,
    holding: _AggregateHolding,
    metadata: dict[str, Any],
    reason: str,
    count_field: str,
) -> None:
    holding.skipped_reason = reason
    metadata[count_field] += 1
    _record_cash_derivation_failure(
        metadata=metadata,
        reason=reason,
        copied_date=str(holding.row["as_of_date"]),
    )
    _add_source_quality_sample(
        metadata,
        _source_quality_sample_from_holding(holding=holding, reason=reason),
    )


def _record_cash_derivation_failure(
    *,
    metadata: dict[str, Any],
    reason: str,
    copied_date: str,
) -> None:
    distribution = metadata["cash_derivation_failure_distribution"]
    by_reason = distribution["by_reason"]
    by_date = distribution["by_date"]
    by_reason[reason] = int(by_reason.get(reason, 0)) + 1
    by_date[copied_date] = int(by_date.get(copied_date, 0)) + 1


def _median(values: list[float]) -> float:
    ordered = sorted(values)
    midpoint = len(ordered) // 2
    if len(ordered) % 2 == 1:
        return ordered[midpoint]
    return (ordered[midpoint - 1] + ordered[midpoint]) / 2


def _empty_cash_identification_counts() -> dict[str, int]:
    return {rule_id: 0 for rule_id in _CASH_IDENTIFICATION_RULE_IDS}


def _empty_cash_derivation_failure_distribution() -> dict[str, JsonValue]:
    return {
        "by_reason": {reason: 0 for reason in _CASH_DERIVATION_FAILURE_REASONS},
        "by_date": {},
    }


def _merge_cash_derivation_failure_distribution(
    metadata: dict[str, JsonValue],
    row_metadata: Mapping[str, Any],
) -> None:
    target = metadata["cash_derivation_failure_distribution"]
    source = row_metadata.get("cash_derivation_failure_distribution")
    if not isinstance(target, dict) or not isinstance(source, Mapping):
        return
    target_by_reason = target.get("by_reason")
    source_by_reason = source.get("by_reason")
    if isinstance(target_by_reason, dict) and isinstance(source_by_reason, Mapping):
        for reason in _CASH_DERIVATION_FAILURE_REASONS:
            target_by_reason[reason] = int(target_by_reason.get(reason, 0)) + _int_value(
                source_by_reason.get(reason)
            )
    target_by_date = target.get("by_date")
    source_by_date = source.get("by_date")
    if isinstance(target_by_date, dict) and isinstance(source_by_date, Mapping):
        for date_value, count in source_by_date.items():
            if isinstance(date_value, str):
                target_by_date[date_value] = int(target_by_date.get(date_value, 0)) + _int_value(
                    count
                )


def _cash_holding_rule_id(*, security_id: str | None, source_name: str) -> str | None:
    code = security_id.upper() if security_id is not None else None
    if code in _CASH_EXACT_CODES:
        return "code_exact_cash"
    if code is not None and code.startswith("CASH"):
        return "code_prefix_cash"
    if code is not None and code.startswith(_CURRENCY_CODE_PREFIXES):
        return "code_prefix_currency"
    if security_id is None and _name_contains_cash_keyword(source_name):
        return "uncoded_cash_keyword"
    if _name_contains_cash_keyword(source_name):
        return "name_cash_keyword"
    return None


def _name_contains_cash_keyword(source_name: str) -> bool:
    normalized_name = source_name.strip()
    upper_name = normalized_name.upper()
    return any(
        keyword in upper_name if keyword.isascii() else keyword in normalized_name
        for keyword in _CASH_NAME_KEYWORDS
    )


def _is_cash_holding(*, security_id: str, source_name: str) -> bool:
    return _cash_holding_rule_id(
        security_id=security_id,
        source_name=source_name,
    ) is not None


def _source_quality_context_from_source_row(
    *,
    source_row: Mapping[str, Any],
    source_date: str,
    line_number: int,
) -> dict[str, JsonValue]:
    sample: dict[str, JsonValue] = {
        "source_date": source_date,
        "line_number": line_number,
    }
    for field in ("code", "fund_id"):
        if field in source_row:
            sample[field] = _json_sample_value(source_row[field])
    source_provider_id = _text_value(source_row.get("source_provider_id")) or _text_value(
        source_row.get("provider_id")
    )
    if source_provider_id is not None:
        sample["source_provider_id"] = source_provider_id
    return sample


def _source_quality_sample_from_source_row(
    *,
    source_row: Mapping[str, Any],
    source_date: str,
    line_number: int,
    reason: str,
) -> dict[str, JsonValue]:
    sample = _source_quality_context_from_source_row(
        source_row=source_row,
        source_date=source_date,
        line_number=line_number,
    )
    sample["reason"] = reason
    for field in ("eval_amount_krw", "weight_pct"):
        if field in source_row:
            sample[field] = _json_sample_value(source_row[field])
    return sample


def _source_quality_sample_from_holding(
    *,
    holding: _AggregateHolding,
    reason: str,
) -> dict[str, JsonValue]:
    sample = dict(holding.sample_context)
    sample["reason"] = reason
    if len(holding.line_numbers) > 1:
        sample["line_numbers"] = list(holding.line_numbers)
    if holding.row["market_value_krw"] is not None:
        sample["eval_amount_krw"] = holding.row["market_value_krw"]
    sample["weight_pct"] = holding.row["weight_percent"]
    if holding.weight_sum is not None and holding.row["weight_percent"] is not None:
        sample["derived_weight_percent"] = holding.row["weight_percent"]
    return sample


def _add_source_quality_sample(
    metadata: dict[str, Any],
    sample: dict[str, JsonValue],
) -> None:
    samples = metadata["source_quality_samples"]
    if len(samples) < _SOURCE_QUALITY_SAMPLE_LIMIT:
        samples.append(sample)


def _json_sample_value(value: Any) -> JsonValue:
    if value is None or isinstance(value, str | int | float | bool):
        return value
    return str(value)


def _unmapped_security_sample_observations(
    holdings: Iterable[_AggregateHolding],
) -> list[dict[str, str]]:
    observations: list[dict[str, str]] = []
    for holding in holdings:
        if (
            holding.skipped_reason is not None
            or holding.row["security_classification"] != "ticker_candidate"
            or holding.row["ticker"] is not None
        ):
            continue
        observations.append(
            {
                "security_id": str(holding.row["security_id"]),
                "name": str(holding.row["name"]),
                "etf_id": str(holding.row["etf_id"]),
                "as_of_date": str(holding.row["as_of_date"]),
            }
        )
    return observations


def _build_unmapped_security_samples(
    observations: Iterable[Mapping[str, str]],
) -> list[dict[str, JsonValue]]:
    grouped: dict[str, dict[str, Any]] = {}
    for observation in observations:
        security_id = observation["security_id"]
        item = grouped.setdefault(
            security_id,
            {
                "names": [],
                "etf_ids": set(),
                "as_of_dates": set(),
                "security_classifications": set(),
                "observed_row_count": 0,
            },
        )
        item["observed_row_count"] += 1
        if observation["name"] not in item["names"]:
            item["names"].append(observation["name"])
        item["etf_ids"].add(observation["etf_id"])
        item["as_of_dates"].add(observation["as_of_date"])
        security_classification = observation.get("security_classification")
        if security_classification is not None:
            item["security_classifications"].add(security_classification)

    samples: list[dict[str, JsonValue]] = []
    for security_id, item in grouped.items():
        names = item["names"]
        sample: dict[str, JsonValue] = {
            "security_id": security_id,
            "name": names[0],
            "observed_row_count": item["observed_row_count"],
            "observed_etf_count": len(item["etf_ids"]),
            "observed_date_count": len(item["as_of_dates"]),
            "name_alias_count": max(0, len(names) - 1),
        }
        if item["security_classifications"] == {"unknown"}:
            sample["security_classification"] = "unknown"
        samples.append(sample)
    samples.sort(
        key=lambda sample: (
            -int(sample["observed_row_count"]),
            -int(sample["observed_etf_count"]),
            str(sample["security_id"]),
        )
    )
    return samples[:_UNMAPPED_SECURITY_SAMPLE_LIMIT]


def _apply_security_name_canonicalization(
    holdings: Iterable[_AggregateHolding],
    security_names: Mapping[str, list[str]],
) -> None:
    canonical_by_security = {
        security_id: names[0] for security_id, names in security_names.items() if names
    }
    for holding in holdings:
        security_id = str(holding.row["security_id"])
        if not bool(holding.row["is_cash"]):
            holding.row["name"] = canonical_by_security.get(security_id, str(holding.row["name"]))


def _security_name_aliases(
    security_names: Mapping[str, list[str]],
) -> list[dict[str, JsonValue]]:
    aliases: list[dict[str, JsonValue]] = []
    for security_id, names in security_names.items():
        if len(names) <= 1:
            continue
        aliases.append(
            {
                "security_id": security_id,
                "canonical_name": names[0],
                "aliases": names[1:],
            }
        )
    aliases.sort(key=lambda item: str(item["security_id"]))
    return aliases


def _write_json(path: Path, payload: Mapping[str, JsonValue]) -> None:
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _write_jsonl(path: Path, rows: Iterable[Mapping[str, JsonValue]]) -> None:
    text = "".join(
        json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n" for row in rows
    )
    path.write_text(text, encoding="utf-8")
