from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from agent_pack.models import JsonValue

from agent_treport.signal_report.adapters.operational_holdings import (
    HOLDINGS_HISTORY_MANIFEST_FILENAME,
    OperationalHoldingsInputError,
    _history_snapshot_fingerprint,
    _parse_iso_date,
    _read_history_snapshot_rows,
    _write_history_snapshot_rows,
)
from agent_treport.signal_report.adapters.operational_source_cache import (
    DEFAULT_OPERATIONAL_SOURCE_CACHE_ROOT,
    provider_operational_cache_path,
    provider_operational_cache_reference_path,
    registered_provider_operational_cache_layouts,
)

PROVIDER_HOLDINGS_RECONCILIATION_SCHEMA_VERSION = (
    "agent_treport.source_provider_operational_cache.holdings_history_reconciliation.v1"
)


@dataclass(frozen=True)
class ProviderExpectedSnapshot:
    source_provider_id: str
    etf_id: str
    observed_date: str


def reconcile_provider_holdings_histories(
    *,
    canonical_history_dir: str | Path,
    cache_root: str | Path = DEFAULT_OPERATIONAL_SOURCE_CACHE_ROOT,
    provider_ids: Iterable[str] | None = None,
    write_missing_provider_histories: bool = False,
    expected_snapshots: Iterable[ProviderExpectedSnapshot] = (),
    now: Callable[[], str] | None = None,
) -> dict[str, JsonValue]:
    canonical_history_path = Path(canonical_history_dir) / HOLDINGS_HISTORY_MANIFEST_FILENAME
    canonical_rows = _read_history_snapshot_rows(canonical_history_path)
    if not canonical_rows:
        raise OperationalHoldingsInputError("canonical holdings history is empty")

    root = Path(cache_root)
    if provider_ids is None:
        layouts = registered_provider_operational_cache_layouts(cache_root=root)
    else:
        layouts = registered_provider_operational_cache_layouts(
            cache_root=root,
            provider_ids=provider_ids,
        )
    provider_rows = _provider_snapshot_rows(canonical_rows)
    canonical_dates = _ordered_dates({date for _, date in canonical_rows})
    providers: list[dict[str, JsonValue]] = []
    missing_provider_history_count = 0
    mismatched_provider_history_count = 0
    written_provider_history_count = 0

    for layout in layouts:
        rows = provider_rows.get(layout.source_provider_id, {})
        history_path = provider_operational_cache_reference_path(
            provider_cache_root=layout.cache_root,
            relative_path=f"holdings-history/{HOLDINGS_HISTORY_MANIFEST_FILENAME}",
            label="holdings_history",
        )
        item = _provider_summary_item(
            source_provider_id=layout.source_provider_id,
            snapshot_rows=rows,
            canonical_dates=canonical_dates,
            history_path=history_path,
            root=root,
            write_missing_provider_histories=write_missing_provider_histories,
            now=now,
        )
        providers.append(item)
        if item["history_status"] == "missing":
            missing_provider_history_count += 1
        elif item["history_status"] == "mismatched":
            mismatched_provider_history_count += 1
        elif item["history_status"] == "written":
            written_provider_history_count += 1

    if mismatched_provider_history_count:
        status = "mismatched_provider_histories"
    elif missing_provider_history_count:
        status = "missing_provider_histories"
    else:
        status = "ready"

    selected_provider_rows = {
        layout.source_provider_id: provider_rows.get(layout.source_provider_id, {})
        for layout in layouts
    }
    all_rows = [row for rows in canonical_rows.values() for row in rows]
    return {
        "schema_version": PROVIDER_HOLDINGS_RECONCILIATION_SCHEMA_VERSION,
        "status": status,
        "canonical": {
            "manifest_path": canonical_history_path.as_posix(),
            "record_count": len(all_rows),
            "snapshot_count": len(canonical_rows),
            "etf_count": len({str(row["etf_id"]) for row in all_rows}),
            "dates": canonical_dates,
        },
        "provider_totals": _provider_totals(selected_provider_rows),
        "provider_count": len(providers),
        "missing_provider_history_count": missing_provider_history_count,
        "mismatched_provider_history_count": mismatched_provider_history_count,
        "written_provider_history_count": written_provider_history_count,
        "expected_snapshot_gaps": _expected_snapshot_gap_items(
            provider_rows=provider_rows,
            expected_snapshots=expected_snapshots,
            cache_root=root,
        ),
        "providers": providers,
    }


def _provider_summary_item(
    *,
    source_provider_id: str,
    snapshot_rows: Mapping[tuple[str, str], list[dict[str, JsonValue]]],
    canonical_dates: list[str],
    history_path: Path,
    root: Path,
    write_missing_provider_histories: bool,
    now: Callable[[], str] | None,
) -> dict[str, JsonValue]:
    record_count = sum(len(rows) for rows in snapshot_rows.values())
    dates = _ordered_dates({date for _, date in snapshot_rows})
    missing_canonical_dates = [date for date in canonical_dates if date not in set(dates)]
    item: dict[str, JsonValue] = {
        "source_provider_id": source_provider_id,
        "history_path": _display_path(history_path, root=root),
        "record_count": record_count,
        "snapshot_count": len(snapshot_rows),
        "etf_count": len({etf_id for etf_id, _ in snapshot_rows}),
        "dates": dates,
        "missing_canonical_dates": missing_canonical_dates,
        "missing_snapshot_count": 0,
        "extra_snapshot_count": 0,
        "changed_snapshot_count": 0,
    }
    if not snapshot_rows:
        item["history_status"] = "no_canonical_rows"
        return item
    if history_path.is_file():
        existing_rows = _read_history_snapshot_rows(history_path)
        missing, extra, changed = _history_mismatches(
            expected=snapshot_rows,
            actual=existing_rows,
        )
        item.update(
            {
                "history_status": "mismatched" if missing or extra or changed else "matched",
                "missing_snapshot_count": len(missing),
                "extra_snapshot_count": len(extra),
                "changed_snapshot_count": len(changed),
                "missing_snapshots": _snapshot_items(missing),
                "extra_snapshots": _snapshot_items(extra),
                "changed_snapshots": _snapshot_items(changed),
            }
        )
        return item
    if write_missing_provider_histories:
        _write_history_snapshot_rows(
            history_path=history_path,
            snapshot_rows=snapshot_rows,
            updated_at=_timestamp(now),
        )
        item["history_status"] = "written"
        return item
    item["history_status"] = "missing"
    return item


def _provider_snapshot_rows(
    snapshot_rows: Mapping[tuple[str, str], list[dict[str, JsonValue]]],
) -> dict[str, dict[tuple[str, str], list[dict[str, JsonValue]]]]:
    grouped: dict[str, dict[tuple[str, str], list[dict[str, JsonValue]]]] = {}
    for key, rows in snapshot_rows.items():
        provider_ids = {str(row.get("source_provider_id")) for row in rows}
        if len(provider_ids) != 1 or "None" in provider_ids:
            raise OperationalHoldingsInputError(
                "holdings history snapshot must contain one source_provider_id"
            )
        provider_id = next(iter(provider_ids))
        provider_operational_cache_path(cache_root=Path("."), source_provider_id=provider_id)
        grouped.setdefault(provider_id, {})[key] = [dict(row) for row in rows]
    return grouped


def _provider_totals(
    provider_rows: Mapping[str, Mapping[tuple[str, str], list[dict[str, JsonValue]]]],
) -> dict[str, JsonValue]:
    etf_ids: set[str] = set()
    record_count = 0
    snapshot_count = 0
    for rows_by_snapshot in provider_rows.values():
        snapshot_count += len(rows_by_snapshot)
        for etf_id, _ in rows_by_snapshot:
            etf_ids.add(etf_id)
        record_count += sum(len(rows) for rows in rows_by_snapshot.values())
    return {
        "record_count": record_count,
        "snapshot_count": snapshot_count,
        "etf_count": len(etf_ids),
    }


def _history_mismatches(
    *,
    expected: Mapping[tuple[str, str], list[dict[str, JsonValue]]],
    actual: Mapping[tuple[str, str], list[dict[str, JsonValue]]],
) -> tuple[list[tuple[str, str]], list[tuple[str, str]], list[tuple[str, str]]]:
    expected_keys = set(expected)
    actual_keys = set(actual)
    missing = _ordered_snapshot_keys(expected_keys - actual_keys)
    extra = _ordered_snapshot_keys(actual_keys - expected_keys)
    changed = _ordered_snapshot_keys(
        key
        for key in expected_keys & actual_keys
        if _history_snapshot_fingerprint(expected[key])
        != _history_snapshot_fingerprint(actual[key])
    )
    return missing, extra, changed


def _expected_snapshot_gap_items(
    *,
    provider_rows: Mapping[str, Mapping[tuple[str, str], list[dict[str, JsonValue]]]],
    expected_snapshots: Iterable[ProviderExpectedSnapshot],
    cache_root: Path,
) -> list[dict[str, JsonValue]]:
    items: list[dict[str, JsonValue]] = []
    for expected in expected_snapshots:
        provider_operational_cache_path(
            cache_root=cache_root,
            source_provider_id=expected.source_provider_id,
        )
        _ensure_cache_relative_identifier(
            expected.etf_id,
            label="expected snapshot etf_id",
        )
        _parse_iso_date(expected.observed_date)
        provider_history = provider_rows.get(expected.source_provider_id, {})
        key = (expected.etf_id, expected.observed_date)
        status = (
            "present_in_canonical_history"
            if key in provider_history
            else "missing_from_canonical_history"
        )
        items.append(
            {
                "source_provider_id": expected.source_provider_id,
                "etf_id": expected.etf_id,
                "observed_date": expected.observed_date,
                "status": status,
            }
        )
    return items


def _ensure_cache_relative_identifier(value: str, *, label: str) -> None:
    path = Path(value)
    if path.is_absolute() or path.drive:
        raise OperationalHoldingsInputError(f"{label} is not path-safe")
    if len(path.parts) != 1 or any(part in {"", ".", ".."} for part in path.parts):
        raise OperationalHoldingsInputError(f"{label} is not path-safe")


def _ordered_dates(values: Iterable[str]) -> list[str]:
    return sorted(values, key=_parse_iso_date, reverse=True)


def _ordered_snapshot_keys(values: Iterable[tuple[str, str]]) -> list[tuple[str, str]]:
    return sorted(values, key=lambda item: (_parse_iso_date(item[1]), item[0]), reverse=True)


def _snapshot_items(keys: Iterable[tuple[str, str]]) -> list[dict[str, JsonValue]]:
    return [
        {"etf_id": etf_id, "observed_date": observed_date}
        for etf_id, observed_date in keys
    ]


def _timestamp(now: Callable[[], str] | None) -> str:
    if now is not None:
        return now()
    return datetime.now(UTC).isoformat()


def _display_path(path: Path, *, root: Path) -> str:
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return path.as_posix()