from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from agent_pack.models import JsonValue

from agent_treport.signal_report.adapters.errors import SignalReportInputError

UNIVERSE_FIXTURE_SCHEMA_VERSION = "agent_treport.native_universe.fixture.v1"
UNIVERSE_STATE_SCHEMA_VERSION = "agent_treport.native_universe.state.v1"
UNIVERSE_SUMMARY_SCHEMA_VERSION = "agent_treport.native_universe.summary.v1"
UNIVERSE_STATE_FILENAME = "universe_state.json"
UNIVERSE_SUMMARY_FILENAME = "universe_summary.json"


class OperationalUniverseInputError(SignalReportInputError):
    """Raised when native ETF universe input violates the local contract."""


@dataclass(frozen=True)
class UniverseETFRecord:
    etf_id: str
    etf_name: str
    brand_id: str
    source_provider_id: str
    status: str = "active"


@dataclass(frozen=True)
class UniverseBrandRecord:
    brand_id: str
    brand_name: str
    source_provider_id: str
    status: str = "active"


def collect_universe_fixture(
    *,
    fixture_path: str | Path,
    dest_dir: str | Path,
    now: Callable[[], datetime] | None = None,
) -> dict[str, JsonValue]:
    fixture = _read_json_object(Path(fixture_path))
    if fixture.get("schema_version") != UNIVERSE_FIXTURE_SCHEMA_VERSION:
        raise OperationalUniverseInputError("invalid native universe fixture schema")
    if fixture.get("complete") is not True:
        raise OperationalUniverseInputError(
            "native universe fixture must be marked complete"
        )

    brands = _fixture_brands(fixture.get("brands", fixture.get("managers")))
    etfs = _fixture_etfs(fixture.get("etfs"), brands=brands)
    collected_at = _timestamp(now)
    destination = Path(dest_dir)
    state_path = destination / UNIVERSE_STATE_FILENAME
    previous_etfs, previous_brands = _read_existing_state(state_path)
    state_etfs = _next_state_etfs(etfs, previous_etfs=previous_etfs)
    state_brands = _next_state_brands(
        brands,
        active_brand_ids={etf.brand_id for etf in etfs.values()},
        previous_brands=previous_brands,
    )
    state = _state_document(
        updated_at=collected_at,
        etfs=state_etfs,
        brands=state_brands,
    )
    summary = _summary_document(
        collected_at=collected_at,
        etfs=etfs,
        brands=brands,
        state_etfs=state_etfs,
        state_brands=state_brands,
        previous_etfs=previous_etfs,
        previous_brands=previous_brands,
    )

    destination.mkdir(parents=True, exist_ok=True)
    _write_json(destination / UNIVERSE_SUMMARY_FILENAME, summary)
    _write_json(state_path, state)
    return summary


def load_active_universe_etfs(
    state_path: str | Path,
) -> dict[str, UniverseETFRecord]:
    path = Path(state_path)
    if not path.is_file():
        raise OperationalUniverseInputError("native universe state file not found")
    etfs, _ = _read_existing_state(path)
    active = {
        etf_id: record
        for etf_id, record in etfs.items()
        if record.status == "active"
    }
    if not active:
        raise OperationalUniverseInputError("native universe state has no active ETFs")
    return active


def _fixture_brands(value: JsonValue) -> dict[str, UniverseBrandRecord]:
    if not isinstance(value, list) or not value:
        raise OperationalUniverseInputError(
            "native universe fixture brands must be a non-empty list"
        )
    brands: dict[str, UniverseBrandRecord] = {}
    for index, item in enumerate(value, 1):
        if not isinstance(item, Mapping):
            raise OperationalUniverseInputError(
                f"native universe fixture brand must be an object: index={index}"
            )
        brand_id = _required_field(item, "brand_id", aliases=("manager_id",))
        brand_name = _required_field(item, "brand_name", aliases=("manager_name",))
        source_provider_id = _required_field(
            item,
            "source_provider_id",
            aliases=("provider_id",),
        )
        if brand_id in brands:
            raise OperationalUniverseInputError(
                f"duplicate native universe brand_id: {brand_id}"
            )
        brands[brand_id] = UniverseBrandRecord(
            brand_id=brand_id,
            brand_name=brand_name,
            source_provider_id=source_provider_id,
        )
    return brands


def _fixture_etfs(
    value: JsonValue,
    *,
    brands: Mapping[str, UniverseBrandRecord],
) -> dict[str, UniverseETFRecord]:
    if not isinstance(value, list) or not value:
        raise OperationalUniverseInputError(
            "native universe fixture etfs must be a non-empty list"
        )
    etfs: dict[str, UniverseETFRecord] = {}
    for index, item in enumerate(value, 1):
        if not isinstance(item, Mapping):
            raise OperationalUniverseInputError(
                f"native universe fixture ETF must be an object: index={index}"
            )
        etf_id = _required_text(item.get("etf_id"), "etf_id")
        etf_name = _required_text(item.get("etf_name"), "etf_name")
        brand_id = _required_field(item, "brand_id", aliases=("manager_id",))
        source_provider_id = _required_field(
            item,
            "source_provider_id",
            aliases=("provider_id",),
        )
        if brand_id not in brands:
            raise OperationalUniverseInputError(
                f"native universe ETF references unknown brand_id: {brand_id}"
            )
        if etf_id in etfs:
            raise OperationalUniverseInputError(
                f"duplicate native universe etf_id: {etf_id}"
            )
        etfs[etf_id] = UniverseETFRecord(
            etf_id=etf_id,
            etf_name=etf_name,
            brand_id=brand_id,
            source_provider_id=source_provider_id,
        )
    return etfs


def _state_document(
    *,
    updated_at: str,
    etfs: Mapping[str, UniverseETFRecord],
    brands: Mapping[str, UniverseBrandRecord],
) -> dict[str, JsonValue]:
    return {
        "schema_version": UNIVERSE_STATE_SCHEMA_VERSION,
        "collection_source_type": "fixture",
        "updated_at": updated_at,
        "etfs": [_etf_state_item(etfs[etf_id]) for etf_id in sorted(etfs)],
        "brands": [
            _brand_state_item(brands[brand_id])
            for brand_id in sorted(brands)
        ],
    }


def _summary_document(
    *,
    collected_at: str,
    etfs: Mapping[str, UniverseETFRecord],
    brands: Mapping[str, UniverseBrandRecord],
    state_etfs: Mapping[str, UniverseETFRecord],
    state_brands: Mapping[str, UniverseBrandRecord],
    previous_etfs: Mapping[str, UniverseETFRecord],
    previous_brands: Mapping[str, UniverseBrandRecord],
) -> dict[str, JsonValue]:
    etf_changes = _etf_changes(etfs, previous_etfs=previous_etfs)
    brand_changes = _brand_changes(
        brands,
        active_brand_ids={etf.brand_id for etf in etfs.values()},
        state_brands=state_brands,
        previous_brands=previous_brands,
    )
    return {
        "schema_version": UNIVERSE_SUMMARY_SCHEMA_VERSION,
        "collection_source_type": "fixture",
        "collected_at": collected_at,
        "state_output": {"state_path": UNIVERSE_STATE_FILENAME},
        "active_etf_count": _status_count(state_etfs, "active"),
        "removed_etf_count": _status_count(state_etfs, "removed"),
        "active_brand_count": _status_count(state_brands, "active"),
        "removed_brand_count": _status_count(state_brands, "removed"),
        "etf_change_counts": {
            "added": len(etf_changes["added"]),
            "changed": len(etf_changes["changed"]),
            "removed": len(etf_changes["removed"]),
            "unchanged": len(etf_changes["unchanged"]),
        },
        "brand_change_counts": {
            "added": len(brand_changes["added"]),
            "changed": len(brand_changes["changed"]),
            "removed": len(brand_changes["removed"]),
            "unchanged": len(brand_changes["unchanged"]),
        },
        "etf_changes": {
            "added": etf_changes["added"],
            "changed": etf_changes["changed"],
            "removed": etf_changes["removed"],
            "unchanged": etf_changes["unchanged"],
        },
        "brand_changes": {
            "added": brand_changes["added"],
            "changed": brand_changes["changed"],
            "removed": brand_changes["removed"],
            "unchanged": brand_changes["unchanged"],
        },
    }


def _next_state_etfs(
    etfs: Mapping[str, UniverseETFRecord],
    *,
    previous_etfs: Mapping[str, UniverseETFRecord],
) -> dict[str, UniverseETFRecord]:
    state = {
        etf_id: UniverseETFRecord(
            etf_id=record.etf_id,
            etf_name=record.etf_name,
            brand_id=record.brand_id,
            source_provider_id=record.source_provider_id,
            status="active",
        )
        for etf_id, record in etfs.items()
    }
    for etf_id, previous in previous_etfs.items():
        if etf_id in state:
            continue
        state[etf_id] = UniverseETFRecord(
            etf_id=previous.etf_id,
            etf_name=previous.etf_name,
            brand_id=previous.brand_id,
            source_provider_id=previous.source_provider_id,
            status="removed",
        )
    return state


def _next_state_brands(
    brands: Mapping[str, UniverseBrandRecord],
    *,
    active_brand_ids: set[str],
    previous_brands: Mapping[str, UniverseBrandRecord],
) -> dict[str, UniverseBrandRecord]:
    state: dict[str, UniverseBrandRecord] = {}
    for brand_id in set(brands) | set(previous_brands):
        source = brands.get(brand_id) or previous_brands[brand_id]
        status = "active" if brand_id in active_brand_ids else "removed"
        state[brand_id] = UniverseBrandRecord(
            brand_id=source.brand_id,
            brand_name=source.brand_name,
            source_provider_id=source.source_provider_id,
            status=status,
        )
    return state


def _etf_changes(
    etfs: Mapping[str, UniverseETFRecord],
    *,
    previous_etfs: Mapping[str, UniverseETFRecord],
) -> dict[str, list[dict[str, JsonValue]]]:
    changes: dict[str, list[dict[str, JsonValue]]] = {
        "added": [],
        "changed": [],
        "removed": [],
        "unchanged": [],
    }
    for etf_id in sorted(etfs):
        current = etfs[etf_id]
        previous = previous_etfs.get(etf_id)
        if previous is None:
            changes["added"].append(_etf_summary_item(current))
        elif _same_etf_tracked_fields(current, previous):
            changes["unchanged"].append(_etf_summary_item(current))
        else:
            item = _etf_summary_item(current)
            item["changed_fields"] = _changed_etf_fields(current, previous)
            changes["changed"].append(item)
    for etf_id in sorted(previous_etfs):
        previous = previous_etfs[etf_id]
        if etf_id not in etfs and previous.status == "active":
            changes["removed"].append(_etf_summary_item(previous))
    return changes


def _brand_changes(
    brands: Mapping[str, UniverseBrandRecord],
    *,
    active_brand_ids: set[str],
    state_brands: Mapping[str, UniverseBrandRecord],
    previous_brands: Mapping[str, UniverseBrandRecord],
) -> dict[str, list[dict[str, JsonValue]]]:
    changes: dict[str, list[dict[str, JsonValue]]] = {
        "added": [],
        "changed": [],
        "removed": [],
        "unchanged": [],
    }
    for brand_id in sorted(active_brand_ids):
        current = brands[brand_id]
        previous = previous_brands.get(brand_id)
        if previous is None:
            changes["added"].append(_brand_summary_item(current))
        elif _same_brand_tracked_fields(current, previous):
            changes["unchanged"].append(_brand_summary_item(current))
        else:
            item = _brand_summary_item(current)
            item["changed_fields"] = _changed_brand_fields(current, previous)
            changes["changed"].append(item)
    for brand_id in sorted(previous_brands):
        previous = previous_brands[brand_id]
        if brand_id not in active_brand_ids and previous.status == "active":
            changes["removed"].append(_brand_summary_item(state_brands[brand_id]))
    return changes


def _status_count(
    records: Mapping[str, UniverseETFRecord] | Mapping[str, UniverseBrandRecord],
    status: str,
) -> int:
    return sum(1 for record in records.values() if record.status == status)


def _same_etf_tracked_fields(
    current: UniverseETFRecord,
    previous: UniverseETFRecord,
) -> bool:
    return (
        current.brand_id == previous.brand_id
        and current.source_provider_id == previous.source_provider_id
        and previous.status == "active"
    )


def _same_brand_tracked_fields(
    current: UniverseBrandRecord,
    previous: UniverseBrandRecord,
) -> bool:
    return (
        current.source_provider_id == previous.source_provider_id
        and previous.status == "active"
    )


def _changed_etf_fields(
    current: UniverseETFRecord,
    previous: UniverseETFRecord,
) -> list[str]:
    fields: list[str] = []
    if current.brand_id != previous.brand_id:
        fields.append("brand_id")
    if current.source_provider_id != previous.source_provider_id:
        fields.append("source_provider_id")
    if previous.status != "active":
        fields.append("status")
    return fields


def _changed_brand_fields(
    current: UniverseBrandRecord,
    previous: UniverseBrandRecord,
) -> list[str]:
    fields: list[str] = []
    if current.source_provider_id != previous.source_provider_id:
        fields.append("source_provider_id")
    if previous.status != "active":
        fields.append("status")
    return fields


def _etf_state_item(record: UniverseETFRecord) -> dict[str, JsonValue]:
    item = _etf_summary_item(record)
    item["status"] = record.status
    return item


def _brand_state_item(record: UniverseBrandRecord) -> dict[str, JsonValue]:
    item = _brand_summary_item(record)
    item["status"] = record.status
    return item


def _etf_summary_item(record: UniverseETFRecord) -> dict[str, JsonValue]:
    return {
        "etf_id": record.etf_id,
        "etf_name": record.etf_name,
        "brand_id": record.brand_id,
        "source_provider_id": record.source_provider_id,
    }


def _brand_summary_item(record: UniverseBrandRecord) -> dict[str, JsonValue]:
    return {
        "brand_id": record.brand_id,
        "brand_name": record.brand_name,
        "source_provider_id": record.source_provider_id,
    }


def _required_text(value: object, label: str) -> str:
    if not isinstance(value, str):
        raise OperationalUniverseInputError(
            f"native universe fixture field is required: {label}"
        )
    text = value.strip()
    if not text:
        raise OperationalUniverseInputError(
            f"native universe fixture field is required: {label}"
        )
    _ensure_path_safe_summary_text(text, label=label)
    return text


def _required_field(
    item: Mapping[str, object],
    field: str,
    *,
    aliases: tuple[str, ...] = (),
) -> str:
    for candidate in (field, *aliases):
        if candidate in item:
            return _required_text(item.get(candidate), field)
    return _required_text(None, field)


def _ensure_path_safe_summary_text(value: str, *, label: str) -> None:
    if "://" in value or "\\" in value or value.startswith("/"):
        raise OperationalUniverseInputError(
            f"native universe fixture field is not path-safe for summary: {label}"
        )


def _timestamp(now: Callable[[], datetime] | None) -> str:
    current = now() if now is not None else datetime.now(UTC)
    if current.tzinfo is None:
        current = current.replace(tzinfo=UTC)
    return current.astimezone(UTC).isoformat()


def _read_existing_state(
    path: Path,
) -> tuple[dict[str, UniverseETFRecord], dict[str, UniverseBrandRecord]]:
    if not path.exists():
        return {}, {}
    state = _read_json_object(path)
    if state.get("schema_version") != UNIVERSE_STATE_SCHEMA_VERSION:
        raise OperationalUniverseInputError("invalid native universe state schema")
    etfs = _state_etfs(state.get("etfs"))
    brands = _state_brands(state.get("brands", state.get("managers")))
    return etfs, brands


def _state_etfs(value: JsonValue) -> dict[str, UniverseETFRecord]:
    if not isinstance(value, list):
        raise OperationalUniverseInputError("native universe state etfs must be a list")
    etfs: dict[str, UniverseETFRecord] = {}
    for index, item in enumerate(value, 1):
        if not isinstance(item, Mapping):
            raise OperationalUniverseInputError(
                f"native universe state ETF must be an object: index={index}"
            )
        etf_id = _required_text(item.get("etf_id"), "state.etf_id")
        if etf_id in etfs:
            raise OperationalUniverseInputError(
                f"duplicate native universe state etf_id: {etf_id}"
            )
        etfs[etf_id] = UniverseETFRecord(
            etf_id=etf_id,
            etf_name=_required_text(item.get("etf_name"), "state.etf_name"),
            brand_id=_required_field(
                item,
                "brand_id",
                aliases=("manager_id",),
            ),
            source_provider_id=_required_field(
                item,
                "source_provider_id",
                aliases=("provider_id",),
            ),
            status=_state_status(item.get("status"), "state.etf.status"),
        )
    return etfs


def _state_brands(value: JsonValue) -> dict[str, UniverseBrandRecord]:
    if not isinstance(value, list):
        raise OperationalUniverseInputError(
            "native universe state brands must be a list"
        )
    brands: dict[str, UniverseBrandRecord] = {}
    for index, item in enumerate(value, 1):
        if not isinstance(item, Mapping):
            raise OperationalUniverseInputError(
                f"native universe state brand must be an object: index={index}"
            )
        brand_id = _required_field(item, "brand_id", aliases=("manager_id",))
        if brand_id in brands:
            raise OperationalUniverseInputError(
                f"duplicate native universe state brand_id: {brand_id}"
            )
        brands[brand_id] = UniverseBrandRecord(
            brand_id=brand_id,
            brand_name=_required_field(
                item,
                "brand_name",
                aliases=("manager_name",),
            ),
            source_provider_id=_required_field(
                item,
                "source_provider_id",
                aliases=("provider_id",),
            ),
            status=_state_status(item.get("status"), "state.brand.status"),
        )
    return brands


def _state_status(value: object, label: str) -> str:
    text = _required_text(value, label)
    if text not in {"active", "removed"}:
        raise OperationalUniverseInputError(f"invalid native universe status: {label}")
    return text


def _read_json_object(path: Path) -> dict[str, JsonValue]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise OperationalUniverseInputError(
            f"invalid native universe JSON input: {path}: {exc.msg}"
        ) from exc
    except OSError as exc:
        raise OperationalUniverseInputError(
            f"native universe JSON input could not be read: {path}"
        ) from exc
    if not isinstance(data, dict):
        raise OperationalUniverseInputError(
            f"native universe JSON input must be an object: {path}"
        )
    return data


def _write_json(path: Path, payload: Mapping[str, JsonValue]) -> None:
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
