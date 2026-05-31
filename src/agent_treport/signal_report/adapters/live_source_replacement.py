from __future__ import annotations

import random
import shutil
import time
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

from agent_pack.models import JsonValue

from agent_treport.signal_report.adapters.operational_holdings import (
    HOLDINGS_HISTORY_MANIFEST_FILENAME,
    OperationalHoldingsInputError,
    _expected_partition_record_count,
    _normalized_partition_path,
    _read_history_snapshot_rows,
    _read_json_object,
    _read_normalized_partition,
    _required_mapping,
    _validate_normalized_dates,
    _write_history_snapshot_rows,
)
from agent_treport.signal_report.adapters.operational_universe import (
    load_active_universe_etfs,
)
from agent_treport.signal_report.adapters.source_acquisition import (
    HoldingsFetchTarget,
    SourceAcquisitionInputError,
    SourceCatalogEntry,
    SourceProvider,
    _apply_source_fetch_result,
    _provider_query_date,
    _read_source_catalog_entries,
    _target_outcome_item,
)

REPRESENTATIVE_EQUIVALENCE_SCHEMA_VERSION = (
    "agent_treport.live_source.representative_equivalence.v1"
)
BASELINE_PLAN_SCHEMA_VERSION = "agent_treport.live_source.baseline_plan.v1"
BASELINE_BACKFILL_SCHEMA_VERSION = "agent_treport.live_source.baseline_backfill.v1"
RETENTION_SCHEMA_VERSION = "agent_treport.live_source.retention.v1"
DAILY_HEALTH_SCHEMA_VERSION = "agent_treport.live_source.daily_health.v1"
LIVE_REPLACEMENT_DEFAULT_TOLERANCES: dict[str, float] = {
    "weight_percent_abs": 0.01,
    "market_value_krw_abs": 1.0,
    "shares_abs": 0.000001,
}
DEFAULT_PACING_BASE_SECONDS = 1.2
DEFAULT_PACING_JITTER_MAX_SECONDS = 0.4
BLOCKED_RETRY_DELAYS_SECONDS = (120.0, 600.0)
PROVIDER_PACING_OVERRIDES: dict[str, tuple[float, float]] = {
    "rise": (2.0, 0.8),
    "sol": (2.0, 0.8),
}
_STOP_FAILURE_CLASSES = {
    "rate_limited",
    "blocked",
    "anti_bot",
    "credential_required",
}
DEFAULT_RETENTION_ROOTS = (
    "evidence",
    "artifacts",
    "daily-smoke-summaries",
    "daily-health",
)
_MISMATCH_COUNT_KEYS = (
    "missing_live_security_code",
    "missing_operational_security_code",
    "weight_mismatch",
    "market_value_mismatch",
    "shares_mismatch",
)


@dataclass(frozen=True)
class LiveBaselineProviderInput:
    provider: SourceProvider
    source_catalog_path: str | Path
    universe_state_path: str | Path
    representative_provider_etf_id: str
    representative_requested_date: str


def verify_representative_equivalence(
    *,
    provider: SourceProvider,
    source_catalog_path: str | Path,
    universe_state_path: str | Path,
    history_dir: str | Path,
    operational_manifest_path: str | Path,
    provider_etf_id: str,
    requested_date: str,
    now: Callable[[], datetime] | None = None,
    mismatch_sample_limit: int = 5,
) -> dict[str, JsonValue]:
    """Fetch one representative SourceProvider snapshot and compare it to operational data."""
    if mismatch_sample_limit <= 0:
        raise SourceAcquisitionInputError("mismatch sample limit must be positive")
    catalog_entries = _read_source_catalog_entries(
        Path(source_catalog_path),
        expected_source_provider_id=provider.source_provider_id,
    )
    entry = _selected_catalog_entry(catalog_entries, provider_etf_id=provider_etf_id)
    fetch_summary = _fetch_planned_requests(
        provider=provider,
        source_catalog_path=Path(source_catalog_path),
        history_dir=Path(history_dir),
        requests=[
            {
                "source_provider_id": provider.source_provider_id,
                "provider_etf_id": provider_etf_id,
                "etf_id": entry.etf_id,
                "requested_date": requested_date,
                "required_observed_date": requested_date,
                "window_position": "representative",
                "date_alignment": {},
            }
        ],
        sleep=None,
        jitter=None,
        now=now,
    )
    target_summary = _target_summary_for_etf(
        fetch_summary.get("target_outcomes"),
        etf_id=entry.etf_id,
        provider_etf_id=provider_etf_id,
    )
    observed_date = _optional_text(target_summary.get("observed_date"))
    live_rows: list[dict[str, JsonValue]] = []
    if observed_date is not None:
        live_rows = _read_history_snapshot_rows(
            Path(history_dir) / "holdings_history.json"
        ).get((entry.etf_id, observed_date), [])
    operational_rows = (
        _operational_rows_for_representative(
            manifest_path=Path(operational_manifest_path),
            source_provider_id=provider.source_provider_id,
            entry=entry,
            observed_date=observed_date,
        )
        if observed_date is not None
        else []
    )
    return _representative_equivalence_summary(
        source_provider_id=provider.source_provider_id,
        provider_etf_id=provider_etf_id,
        etf_id=entry.etf_id,
        observed_date=observed_date,
        live_rows=live_rows,
        operational_rows=operational_rows,
        fetch_outcome=_optional_text(target_summary.get("outcome")),
        mismatch_sample_limit=mismatch_sample_limit,
    )


def plan_live_baseline_snapshots(
    *,
    source_provider_id: str,
    source_catalog_path: str | Path,
    universe_state_path: str | Path,
    history_dir: str | Path,
    operational_manifest_path: str | Path,
    sample_limit: int = 5,
) -> dict[str, JsonValue]:
    if sample_limit <= 0:
        raise SourceAcquisitionInputError("sample limit must be positive")
    catalog_entries = _read_source_catalog_entries(
        Path(source_catalog_path),
        expected_source_provider_id=source_provider_id,
    )
    active_etf_ids = set(load_active_universe_etfs(universe_state_path))
    active_entries = [
        entry
        for entry in catalog_entries
        if entry.etf_id in active_etf_ids and entry.is_active_strategy_etf is True
    ]
    operational_dates = _operational_dates_by_provider_etf(
        manifest_path=Path(operational_manifest_path),
        source_provider_id=source_provider_id,
        entries=active_entries,
    )
    existing = _read_history_snapshot_rows(Path(history_dir) / "holdings_history.json")
    existing_dates = _history_dates_by_etf_id(existing)
    anchor_dates = sorted(
        {
            observed_date
            for observed_dates in operational_dates.values()
            for observed_date in observed_dates
        },
        key=_date_sort_key,
        reverse=True,
    )
    requests: list[dict[str, JsonValue]] = []
    window_gap_samples: list[dict[str, JsonValue]] = []
    required_snapshot_count = 0
    existing_snapshot_count = 0

    for entry in active_entries:
        available_dates = operational_dates.get(entry.provider_etf_id, [])
        history_dates = existing_dates.get(entry.etf_id, [])
        if len(available_dates) >= 2:
            latest_observed_date = available_dates[0]
            prior_observed_date = available_dates[1]
            window_positions = ("latest", "prior")
        elif len(history_dates) >= 2:
            latest_observed_date = history_dates[0]
            prior_observed_date = history_dates[1]
            window_positions = ("latest_discovery", "prior_discovery")
        elif len(history_dates) == 1:
            latest_observed_date = history_dates[0]
            prior_observed_date = _previous_business_day(latest_observed_date)
            window_positions = ("latest_discovery", "prior_discovery")
        elif anchor_dates:
            latest_observed_date = anchor_dates[0]
            prior_observed_date = _previous_business_day(latest_observed_date)
            window_positions = ("latest_discovery", "prior_discovery")
        else:
            window_gap_samples.append(
                {
                    "provider_etf_id": entry.provider_etf_id,
                    "etf_id": entry.etf_id,
                    "available_observed_date_count": len(available_dates),
                    "issue": "required_comparison_window_unavailable",
                }
            )
            continue
        date_alignment = _baseline_date_alignment(
            latest_observed_date=latest_observed_date,
            prior_observed_date=prior_observed_date,
        )
        for window_position, required_observed_date in (
            (window_positions[0], latest_observed_date),
            (window_positions[1], prior_observed_date),
        ):
            required_snapshot_count += 1
            if (entry.etf_id, required_observed_date) in existing:
                existing_snapshot_count += 1
                continue
            requests.append(
                {
                    "source_provider_id": source_provider_id,
                    "provider_etf_id": entry.provider_etf_id,
                    "etf_id": entry.etf_id,
                    "requested_date": required_observed_date,
                    "required_observed_date": required_observed_date,
                    "window_position": window_position,
                    "date_alignment": dict(date_alignment),
                }
            )

    missing_snapshot_count = len(requests)
    window_gap_count = len(window_gap_samples)
    return {
        "schema_version": BASELINE_PLAN_SCHEMA_VERSION,
        "source_provider_id": source_provider_id,
        "tracked_active_strategy_etf_count": len(active_entries),
        "required_snapshot_count": required_snapshot_count,
        "existing_snapshot_count": existing_snapshot_count,
        "missing_snapshot_count": missing_snapshot_count,
        "request_count": missing_snapshot_count,
        "ready_for_baseline_export": missing_snapshot_count == 0 and window_gap_count == 0,
        "requests": requests,
        "window_gap_count": window_gap_count,
        "window_gap_samples": window_gap_samples[:sample_limit],
    }


def run_live_baseline_backfill(
    *,
    provider_inputs: tuple[LiveBaselineProviderInput, ...],
    operational_manifest_path: str | Path,
    history_dir: str | Path,
    now: Callable[[], datetime] | None = None,
    sleep: Callable[[float], object] | None = None,
    jitter: Callable[[float], float] | None = None,
) -> dict[str, JsonValue]:
    if not provider_inputs:
        raise SourceAcquisitionInputError("at least one provider input is required")
    representative_summaries = [
        verify_representative_equivalence(
            provider=item.provider,
            source_catalog_path=item.source_catalog_path,
            universe_state_path=item.universe_state_path,
            history_dir=history_dir,
            operational_manifest_path=operational_manifest_path,
            provider_etf_id=item.representative_provider_etf_id,
            requested_date=item.representative_requested_date,
            now=now,
        )
        for item in provider_inputs
    ]
    representative_pass_count = sum(
        1 for summary in representative_summaries if summary.get("passed") is True
    )
    representative_fail_count = len(representative_summaries) - representative_pass_count
    if representative_fail_count:
        return {
            "schema_version": BASELINE_BACKFILL_SCHEMA_VERSION,
            "bulk_started": False,
            "bulk_completed": False,
            "representative_pass_count": representative_pass_count,
            "representative_fail_count": representative_fail_count,
            "representatives": representative_summaries,
            "provider_results": [],
            "aggregate_counts": _empty_backfill_aggregate_counts(),
        }

    provider_results: list[dict[str, JsonValue]] = []
    aggregate_counts = _empty_backfill_aggregate_counts()
    bulk_completed = True
    for item in provider_inputs:
        plan = plan_live_baseline_snapshots(
            source_provider_id=item.provider.source_provider_id,
            source_catalog_path=item.source_catalog_path,
            universe_state_path=item.universe_state_path,
            history_dir=history_dir,
            operational_manifest_path=operational_manifest_path,
        )
        result = _fetch_planned_requests(
            provider=item.provider,
            source_catalog_path=Path(item.source_catalog_path),
            history_dir=Path(history_dir),
            requests=plan.get("requests"),
            sleep=sleep,
            jitter=jitter,
            now=now,
        )
        result["plan_window_gap_count"] = plan["window_gap_count"]
        provider_results.append(result)
        _merge_backfill_aggregate_counts(aggregate_counts, result)
        if (
            result.get("stopped") is True
            or int(plan["window_gap_count"]) > 0
            or _provider_result_has_incomplete_targets(result)
        ):
            bulk_completed = False

    return {
        "schema_version": BASELINE_BACKFILL_SCHEMA_VERSION,
        "bulk_started": True,
        "bulk_completed": bulk_completed,
        "representative_pass_count": representative_pass_count,
        "representative_fail_count": representative_fail_count,
        "representatives": representative_summaries,
        "provider_results": provider_results,
        "aggregate_counts": aggregate_counts,
    }


def apply_live_source_rolling_retention(
    *,
    live_root: str | Path,
    keep_latest: int = 10,
    retention_roots: tuple[str, ...] = DEFAULT_RETENTION_ROOTS,
    protected_run_dir_names: tuple[str, ...] = (),
) -> dict[str, JsonValue]:
    if keep_latest <= 0:
        raise SourceAcquisitionInputError("keep_latest must be positive")
    root = Path(live_root)
    root.mkdir(parents=True, exist_ok=True)
    resolved_root = root.resolve()
    protected_names = set(protected_run_dir_names)
    root_summaries: list[dict[str, JsonValue]] = []
    for name in retention_roots:
        retention_root = root / name
        if not retention_root.is_dir():
            continue
        run_dirs = sorted(
            (child for child in retention_root.iterdir() if child.is_dir()),
            key=lambda child: child.name,
            reverse=True,
        )
        protected_dirs = [child for child in run_dirs if child.name in protected_names]
        unprotected_dirs = [
            child for child in run_dirs if child.name not in protected_names
        ]
        unprotected_keep_count = max(0, keep_latest - len(protected_dirs))
        to_prune = unprotected_dirs[unprotected_keep_count:]
        for directory in to_prune:
            resolved = directory.resolve()
            if resolved == resolved_root or resolved_root not in resolved.parents:
                raise SourceAcquisitionInputError("refusing to prune outside live source root")
            if directory.name in protected_names:
                raise SourceAcquisitionInputError("refusing to prune protected run")
            shutil.rmtree(directory)
        root_summary: dict[str, JsonValue] = {
            "name": name,
            "before_count": len(run_dirs),
            "after_count": len(run_dirs) - len(to_prune),
            "pruned_count": len(to_prune),
        }
        if protected_dirs:
            root_summary["protected_count"] = len(protected_dirs)
        root_summaries.append(root_summary)
    return {
        "schema_version": RETENTION_SCHEMA_VERSION,
        "keep_latest": keep_latest,
        "retention_roots": root_summaries,
    }


def summarize_live_collection_health(
    *,
    source_provider_id: str,
    source_catalog_path: str | Path,
    universe_state_path: str | Path,
    history_dir: str | Path,
    operational_manifest_path: str | Path,
    latest_source_summary: Mapping[str, JsonValue] | None = None,
    sample_limit: int = 5,
) -> dict[str, JsonValue]:
    if sample_limit <= 0:
        raise SourceAcquisitionInputError("sample limit must be positive")
    catalog_entries = _read_source_catalog_entries(
        Path(source_catalog_path),
        expected_source_provider_id=source_provider_id,
    )
    active_etf_ids = set(load_active_universe_etfs(universe_state_path))
    active_entries = [
        entry
        for entry in catalog_entries
        if entry.etf_id in active_etf_ids and entry.is_active_strategy_etf is True
    ]
    operational_dates = _operational_dates_by_provider_etf(
        manifest_path=Path(operational_manifest_path),
        source_provider_id=source_provider_id,
        entries=active_entries,
    )
    history = _read_history_snapshot_rows(
        Path(history_dir) / HOLDINGS_HISTORY_MANIFEST_FILENAME
    )
    history_dates = _history_dates_by_etf_id(history)
    plan = plan_live_baseline_snapshots(
        source_provider_id=source_provider_id,
        source_catalog_path=source_catalog_path,
        universe_state_path=universe_state_path,
        history_dir=history_dir,
        operational_manifest_path=operational_manifest_path,
        sample_limit=sample_limit,
    )
    missing_etf_ids = _unique_texts(
        str(request["etf_id"])
        for request in plan["requests"]
        if isinstance(request, Mapping)
    )
    window_gap_samples = [
        sample
        for sample in plan.get("window_gap_samples", [])
        if isinstance(sample, Mapping)
    ]
    window_gap_etf_ids = _unique_texts(
        str(sample["etf_id"])
        for sample in window_gap_samples
        if isinstance(sample.get("etf_id"), str)
    )
    current_up_to_date_count = 0
    stale_etf_ids: list[str] = []
    last_successful_dates: list[str] = []
    for entry in active_entries:
        available_dates = operational_dates.get(entry.provider_etf_id, [])
        entry_history_dates = history_dates.get(entry.etf_id, [])
        if len(available_dates) >= 2:
            latest_observed_date = available_dates[0]
            if (entry.etf_id, latest_observed_date) in history:
                current_up_to_date_count += 1
            elif entry_history_dates:
                stale_etf_ids.append(entry.etf_id)
        elif len(entry_history_dates) >= 2:
            current_up_to_date_count += 1
        elif entry_history_dates:
            stale_etf_ids.append(entry.etf_id)
        last_successful_dates.extend(entry_history_dates)
    failed_etf_ids = _failed_target_etf_ids(latest_source_summary)
    last_successful_observed_date = (
        max(last_successful_dates, key=_date_sort_key)
        if last_successful_dates
        else None
    )
    return {
        "schema_version": DAILY_HEALTH_SCHEMA_VERSION,
        "source_provider_id": source_provider_id,
        "tracked_active_strategy_etf_count": len(active_entries),
        "current_up_to_date_count": current_up_to_date_count,
        "missing_snapshot_count": int(plan["missing_snapshot_count"]),
        "failed_target_count": len(failed_etf_ids),
        "stale_target_count": len(stale_etf_ids),
        "window_gap_count": int(plan["window_gap_count"]),
        "next_backfill_target_count": int(plan["request_count"]),
        "last_successful_observed_date": last_successful_observed_date,
        "missing_snapshot_etf_id_samples": missing_etf_ids[:sample_limit],
        "failed_target_etf_id_samples": failed_etf_ids[:sample_limit],
        "stale_target_etf_id_samples": stale_etf_ids[:sample_limit],
        "window_gap_etf_id_samples": window_gap_etf_ids[:sample_limit],
        "next_backfill_etf_id_samples": missing_etf_ids[:sample_limit],
    }


def _selected_catalog_entry(
    entries: tuple[SourceCatalogEntry, ...],
    *,
    provider_etf_id: str,
) -> SourceCatalogEntry:
    for entry in entries:
        if entry.provider_etf_id == provider_etf_id:
            if entry.is_active_strategy_etf is not True:
                raise SourceAcquisitionInputError(
                    "representative ETF must be an ActiveStrategyETF"
                )
            return entry
    raise SourceAcquisitionInputError("representative provider ETF id not found")


def _target_summary_for_etf(
    value: JsonValue,
    *,
    etf_id: str,
    provider_etf_id: str,
) -> Mapping[str, JsonValue]:
    if not isinstance(value, list):
        return {}
    for item in value:
        if isinstance(item, Mapping) and item.get("etf_id") == etf_id:
            return item
        if isinstance(item, Mapping) and item.get("provider_etf_id") == provider_etf_id:
            return item
    return {}


def _operational_rows_for_representative(
    *,
    manifest_path: Path,
    source_provider_id: str,
    entry: SourceCatalogEntry,
    observed_date: str,
) -> list[dict[str, JsonValue]]:
    manifest = _read_json_object(manifest_path)
    if manifest.get("schema_version") != "agent_treport.operational_holdings.v1":
        raise OperationalHoldingsInputError("invalid normalized holdings export schema")
    if manifest.get("storage_format") != "normalized_partitioned_jsonl_v1":
        raise OperationalHoldingsInputError("invalid normalized holdings export storage format")
    dates = _validate_normalized_dates(manifest.get("dates"))
    if observed_date not in dates:
        return []
    partitions = _required_mapping(manifest, "partitions")
    partition_path = _normalized_partition_path(
        manifest_path=manifest_path,
        partition_specs=partitions,
        partition_date=observed_date,
    )
    rows = _read_normalized_partition(
        partition_path=partition_path,
        partition_date=observed_date,
        expected_record_count=_expected_partition_record_count(partitions, observed_date),
    )
    matching_etf_ids = {entry.etf_id, entry.provider_etf_id}
    return [
        row
        for row in rows
        if row.get("source_provider_id") == source_provider_id
        and row.get("etf_id") in matching_etf_ids
    ]


def _operational_dates_by_provider_etf(
    *,
    manifest_path: Path,
    source_provider_id: str,
    entries: list[SourceCatalogEntry],
) -> dict[str, list[str]]:
    manifest = _read_json_object(manifest_path)
    if manifest.get("schema_version") != "agent_treport.operational_holdings.v1":
        raise OperationalHoldingsInputError("invalid normalized holdings export schema")
    if manifest.get("storage_format") != "normalized_partitioned_jsonl_v1":
        raise OperationalHoldingsInputError("invalid normalized holdings export storage format")
    dates = _validate_normalized_dates(manifest.get("dates"))
    partitions = _required_mapping(manifest, "partitions")
    provider_by_matching_etf_id: dict[str, str] = {}
    for entry in entries:
        provider_by_matching_etf_id[entry.etf_id] = entry.provider_etf_id
        provider_by_matching_etf_id[entry.provider_etf_id] = entry.provider_etf_id
    found: dict[str, set[str]] = {entry.provider_etf_id: set() for entry in entries}
    for observed_date in dates:
        partition_path = _normalized_partition_path(
            manifest_path=manifest_path,
            partition_specs=partitions,
            partition_date=observed_date,
        )
        rows = _read_normalized_partition(
            partition_path=partition_path,
            partition_date=observed_date,
            expected_record_count=_expected_partition_record_count(partitions, observed_date),
        )
        for row in rows:
            if row.get("source_provider_id") != source_provider_id:
                continue
            provider_etf_id = provider_by_matching_etf_id.get(str(row.get("etf_id")))
            if provider_etf_id is not None:
                found[provider_etf_id].add(observed_date)
    return {
        provider_etf_id: sorted(observed_dates, key=_date_sort_key, reverse=True)
        for provider_etf_id, observed_dates in found.items()
    }


def _fetch_planned_requests(
    *,
    provider: SourceProvider,
    source_catalog_path: Path,
    history_dir: Path,
    requests: JsonValue,
    sleep: Callable[[float], object] | None,
    jitter: Callable[[float], float] | None,
    now: Callable[[], datetime] | None,
) -> dict[str, JsonValue]:
    if not isinstance(requests, list):
        raise SourceAcquisitionInputError("baseline plan requests must be a list")
    base_delay, jitter_max = _provider_pacing(provider.source_provider_id)
    sleep_fn = sleep or time.sleep
    jitter_fn = jitter or (lambda max_seconds: random.uniform(0.0, max_seconds))
    catalog_entries = _read_source_catalog_entries(
        source_catalog_path,
        expected_source_provider_id=provider.source_provider_id,
    )
    catalog_by_provider_etf_id = {
        entry.provider_etf_id: entry
        for entry in catalog_entries
    }
    history_path = history_dir / HOLDINGS_HISTORY_MANIFEST_FILENAME
    stored = _read_history_snapshot_rows(history_path)
    next_stored = {key: [dict(row) for row in rows] for key, rows in stored.items()}
    target_outcomes: list[dict[str, JsonValue]] = []
    written_row_count = 0
    written_snapshot_count = 0
    stopped = False
    stop_reason: str | None = None

    for index, request in enumerate(requests):
        if not isinstance(request, Mapping):
            raise SourceAcquisitionInputError("baseline plan request must be an object")
        if index > 0:
            sleep_fn(base_delay + jitter_fn(jitter_max))
        provider_etf_id = _required_request_text(request.get("provider_etf_id"), "provider_etf_id")
        requested_date = _required_request_text(request.get("requested_date"), "requested_date")
        entry = catalog_by_provider_etf_id.get(provider_etf_id)
        if entry is None:
            raise SourceAcquisitionInputError("baseline request provider ETF id not found")
        target = HoldingsFetchTarget(
            source_provider_id=provider.source_provider_id,
            provider_etf_id=provider_etf_id,
            etf_id=entry.etf_id,
            requested_date=requested_date,
            provider_query_date=_provider_query_date(requested_date),
        )
        retry_attempt_count = 0
        while True:
            result = provider.fetch_holdings(target)
            outcome, rows, failure_code_class = _apply_source_fetch_result(
                result=result,
                target=target,
                entry=entry,
                stored=stored,
                next_stored=next_stored,
                refresh_set=set(),
            )
            if (
                failure_code_class == "blocked"
                and retry_attempt_count < len(BLOCKED_RETRY_DELAYS_SECONDS)
            ):
                sleep_fn(BLOCKED_RETRY_DELAYS_SECONDS[retry_attempt_count])
                retry_attempt_count += 1
                _clear_provider_stop_state(provider)
                continue
            if retry_attempt_count:
                result = replace(
                    result,
                    retry_attempt_count=max(
                        result.retry_attempt_count,
                        retry_attempt_count,
                    ),
                )
            break
        row_count = len(rows)
        observed_date = str(rows[0]["as_of_date"]) if rows else result.observed_date
        target_outcomes.append(
            _target_outcome_item(
                target=target,
                entry=entry,
                result=result,
                outcome="fetched" if outcome == "refreshed" else outcome,
                observed_date=observed_date,
                row_count=row_count,
                failure_code_class=failure_code_class,
            )
        )
        if outcome in {"fetched", "refreshed"}:
            written_row_count += row_count
            written_snapshot_count += 1
        if outcome == "rate_limited" or failure_code_class in _STOP_FAILURE_CLASSES:
            stopped = True
            stop_reason = failure_code_class or outcome
            break

    if written_snapshot_count:
        _write_history_snapshot_rows(
            history_path=history_path,
            snapshot_rows=next_stored,
            updated_at=_timestamp(now),
        )
    aggregate_counts = _target_aggregate_counts(target_outcomes)
    return {
        "source_provider_id": provider.source_provider_id,
        "request_count": len(requests),
        "attempted_request_count": len(target_outcomes),
        "written_snapshot_count": written_snapshot_count,
        "row_count": written_row_count,
        "stopped": stopped,
        "stop_reason": stop_reason,
        "pacing": {
            "base_delay_seconds": base_delay,
            "jitter_max_seconds": jitter_max,
        },
        "aggregate_counts": aggregate_counts,
        "target_outcomes": target_outcomes,
    }


def _clear_provider_stop_state(provider: SourceProvider) -> None:
    blocked_hosts = getattr(provider, "_blocked_hosts", None)
    if isinstance(blocked_hosts, dict):
        blocked_hosts.clear()


def _provider_pacing(source_provider_id: str) -> tuple[float, float]:
    return PROVIDER_PACING_OVERRIDES.get(
        source_provider_id,
        (DEFAULT_PACING_BASE_SECONDS, DEFAULT_PACING_JITTER_MAX_SECONDS),
    )


def _target_aggregate_counts(
    target_outcomes: list[dict[str, JsonValue]],
) -> dict[str, JsonValue]:
    counts: dict[str, JsonValue] = {
        "target_count": len(target_outcomes),
        "fetched": 0,
        "skipped_existing": 0,
        "failed": 0,
        "rate_limited": 0,
        "unsupported": 0,
    }
    for item in target_outcomes:
        outcome = str(item["outcome"])
        if outcome in counts:
            counts[outcome] = int(counts[outcome]) + 1
    return counts


def _empty_backfill_aggregate_counts() -> dict[str, JsonValue]:
    return {
        "provider_count": 0,
        "request_count": 0,
        "attempted_request_count": 0,
        "written_snapshot_count": 0,
        "row_count": 0,
        "stopped_provider_count": 0,
    }


def _merge_backfill_aggregate_counts(
    aggregate_counts: dict[str, JsonValue],
    provider_result: Mapping[str, JsonValue],
) -> None:
    aggregate_counts["provider_count"] = int(aggregate_counts["provider_count"]) + 1
    for key in (
        "request_count",
        "attempted_request_count",
        "written_snapshot_count",
        "row_count",
    ):
        aggregate_counts[key] = int(aggregate_counts[key]) + int(provider_result[key])
    if provider_result.get("stopped") is True:
        aggregate_counts["stopped_provider_count"] = (
            int(aggregate_counts["stopped_provider_count"]) + 1
        )


def _provider_result_has_incomplete_targets(
    provider_result: Mapping[str, JsonValue],
) -> bool:
    aggregate_counts = provider_result.get("aggregate_counts")
    if not isinstance(aggregate_counts, Mapping):
        return True
    return any(
        int(aggregate_counts.get(key, 0)) > 0
        for key in ("failed", "rate_limited", "unsupported")
    )


def _required_request_text(value: JsonValue | None, label: str) -> str:
    if not isinstance(value, str) or not value:
        raise SourceAcquisitionInputError(f"baseline request field must be text: {label}")
    return value


def _failed_target_etf_ids(
    latest_source_summary: Mapping[str, JsonValue] | None,
) -> list[str]:
    if latest_source_summary is None:
        return []
    outcomes = latest_source_summary.get("target_outcomes")
    if not isinstance(outcomes, list):
        return []
    failed: list[str] = []
    for item in outcomes:
        if not isinstance(item, Mapping):
            continue
        if item.get("outcome") not in {"failed", "rate_limited", "unsupported"}:
            continue
        etf_id = item.get("etf_id")
        if isinstance(etf_id, str) and etf_id:
            failed.append(etf_id)
    return _unique_texts(failed)


def _unique_texts(values: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    unique: list[str] = []
    for value in values:
        if not isinstance(value, str) or value in seen:
            continue
        seen.add(value)
        unique.append(value)
    return unique


def _history_dates_by_etf_id(
    history: Mapping[tuple[str, str], list[dict[str, JsonValue]]],
) -> dict[str, list[str]]:
    dates_by_etf_id: dict[str, set[str]] = {}
    for etf_id, observed_date in history:
        dates_by_etf_id.setdefault(etf_id, set()).add(observed_date)
    return {
        etf_id: sorted(observed_dates, key=_date_sort_key, reverse=True)
        for etf_id, observed_dates in dates_by_etf_id.items()
    }


def _timestamp(now: Callable[[], datetime] | None) -> str:
    current = now() if now is not None else datetime.now(UTC)
    if current.tzinfo is None:
        current = current.replace(tzinfo=UTC)
    return current.astimezone(UTC).isoformat()


def _baseline_date_alignment(
    *,
    latest_observed_date: str,
    prior_observed_date: str,
) -> dict[str, JsonValue]:
    prior_business_date = _previous_business_day(latest_observed_date)
    status = (
        "exact_prior_business_date"
        if prior_observed_date == prior_business_date
        else "nearest_available_prior_observed_business_date"
    )
    return {
        "latest_observed_date": latest_observed_date,
        "prior_business_date": prior_business_date,
        "prior_observed_date": prior_observed_date,
        "status": status,
    }


def _previous_business_day(date_value: str) -> str:
    parsed = datetime.strptime(date_value, "%Y-%m-%d").date() - timedelta(days=1)
    while parsed.weekday() >= 5:
        parsed -= timedelta(days=1)
    return parsed.isoformat()


def _date_sort_key(value: str) -> datetime:
    return datetime.strptime(value, "%Y-%m-%d")


def _representative_equivalence_summary(
    *,
    source_provider_id: str,
    provider_etf_id: str,
    etf_id: str,
    observed_date: str | None,
    live_rows: list[dict[str, JsonValue]],
    operational_rows: list[dict[str, JsonValue]],
    fetch_outcome: str | None,
    mismatch_sample_limit: int,
) -> dict[str, JsonValue]:
    live_by_security = _rows_by_security_id(live_rows)
    operational_by_security = _rows_by_security_id(operational_rows)
    live_codes = set(live_by_security)
    operational_codes = set(operational_by_security)
    matched_codes = sorted(live_codes & operational_codes)
    mismatch_counts = {key: 0 for key in _MISMATCH_COUNT_KEYS}
    warning_counts = {"missing_shares": 0}
    mismatch_samples: list[dict[str, JsonValue]] = []
    warning_samples: list[dict[str, JsonValue]] = []

    for security_id in sorted(operational_codes - live_codes):
        mismatch_counts["missing_live_security_code"] += 1
        mismatch_samples.append(
            {
                "security_id": security_id,
                "field": "security_id",
                "issue": "missing_live_security_code",
            }
        )
    for security_id in sorted(live_codes - operational_codes):
        mismatch_counts["missing_operational_security_code"] += 1
        mismatch_samples.append(
            {
                "security_id": security_id,
                "field": "security_id",
                "issue": "missing_operational_security_code",
            }
        )
    for security_id in matched_codes:
        live = live_by_security[security_id]
        operational = operational_by_security[security_id]
        _compare_required_number(
            mismatch_counts=mismatch_counts,
            mismatch_samples=mismatch_samples,
            security_id=security_id,
            field="weight_percent",
            count_key="weight_mismatch",
            live_value=live.get("weight_percent"),
            operational_value=operational.get("weight_percent"),
            tolerance=LIVE_REPLACEMENT_DEFAULT_TOLERANCES["weight_percent_abs"],
        )
        _compare_required_number(
            mismatch_counts=mismatch_counts,
            mismatch_samples=mismatch_samples,
            security_id=security_id,
            field="market_value_krw",
            count_key="market_value_mismatch",
            live_value=live.get("market_value_krw"),
            operational_value=operational.get("market_value_krw"),
            tolerance=LIVE_REPLACEMENT_DEFAULT_TOLERANCES["market_value_krw_abs"],
        )
        _compare_optional_shares(
            mismatch_counts=mismatch_counts,
            warning_counts=warning_counts,
            mismatch_samples=mismatch_samples,
            warning_samples=warning_samples,
            security_id=security_id,
            live_value=live.get("shares"),
            operational_value=operational.get("shares"),
        )

    total_mismatches = sum(mismatch_counts.values())
    summary: dict[str, JsonValue] = {
        "schema_version": REPRESENTATIVE_EQUIVALENCE_SCHEMA_VERSION,
        "source_provider_id": source_provider_id,
        "provider_etf_id": provider_etf_id,
        "etf_id": etf_id,
        "observed_date": observed_date,
        "fetch_outcome": fetch_outcome,
        "passed": total_mismatches == 0 and fetch_outcome in {"fetched", "skipped_existing"},
        "live_row_count": len(live_rows),
        "operational_row_count": len(operational_rows),
        "matched_constituent_count": len(matched_codes),
        "mismatch_counts": mismatch_counts,
        "warning_counts": warning_counts,
        "tolerances": dict(LIVE_REPLACEMENT_DEFAULT_TOLERANCES),
        "mismatch_sample_count": len(mismatch_samples),
        "mismatch_samples": mismatch_samples[:mismatch_sample_limit],
        "warning_sample_count": len(warning_samples),
        "warning_samples": warning_samples[:mismatch_sample_limit],
    }
    return summary


def _rows_by_security_id(
    rows: list[dict[str, JsonValue]],
) -> dict[str, dict[str, JsonValue]]:
    return {str(row["security_id"]): row for row in rows}


def _compare_required_number(
    *,
    mismatch_counts: dict[str, int],
    mismatch_samples: list[dict[str, JsonValue]],
    security_id: str,
    field: str,
    count_key: str,
    live_value: JsonValue | None,
    operational_value: JsonValue | None,
    tolerance: float,
) -> None:
    live_number = _optional_float(live_value)
    operational_number = _optional_float(operational_value)
    if live_number is None or operational_number is None:
        mismatch_counts[count_key] += 1
        mismatch_samples.append(
            {
                "security_id": security_id,
                "field": field,
                "issue": "missing_required_value",
            }
        )
        return
    absolute_difference = abs(live_number - operational_number)
    if absolute_difference - tolerance > 1e-12:
        mismatch_counts[count_key] += 1
        mismatch_samples.append(
            {
                "security_id": security_id,
                "field": field,
                "issue": "outside_tolerance",
                "absolute_difference": round(absolute_difference, 6),
            }
        )


def _compare_optional_shares(
    *,
    mismatch_counts: dict[str, int],
    warning_counts: dict[str, int],
    mismatch_samples: list[dict[str, JsonValue]],
    warning_samples: list[dict[str, JsonValue]],
    security_id: str,
    live_value: JsonValue | None,
    operational_value: JsonValue | None,
) -> None:
    live_number = _optional_float(live_value)
    operational_number = _optional_float(operational_value)
    if live_number is None and operational_number is None:
        return
    if live_number is None or operational_number is None:
        warning_counts["missing_shares"] += 1
        warning_samples.append(
            {
                "security_id": security_id,
                "field": "shares",
                "issue": "missing_on_one_side",
            }
        )
        return
    absolute_difference = abs(live_number - operational_number)
    if absolute_difference - LIVE_REPLACEMENT_DEFAULT_TOLERANCES["shares_abs"] > 1e-12:
        mismatch_counts["shares_mismatch"] += 1
        mismatch_samples.append(
            {
                "security_id": security_id,
                "field": "shares",
                "issue": "outside_tolerance",
                "absolute_difference": round(absolute_difference, 6),
            }
        )


def _optional_float(value: JsonValue | None) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, int | float):
        return float(value)
    return None


def _optional_text(value: JsonValue | None) -> str | None:
    if isinstance(value, str) and value:
        return value
    return None
