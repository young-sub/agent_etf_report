from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from datetime import date
from typing import Literal

from agent_pack.models import JsonValue

type SecurityClassification = Literal[
    "ticker_candidate",
    "cash_like",
    "non_equity",
    "unknown",
]
type SecurityMasterStatus = Literal[
    "verified",
    "auto_verified",
    "proposed",
    "review_required",
    "unresolved",
    "conflict",
    "excluded",
]
type SecurityMasterConfidence = Literal["high", "medium", "low"]

SECURITY_CLASSIFICATIONS = frozenset(
    {"ticker_candidate", "cash_like", "non_equity", "unknown"}
)
SECURITY_MASTER_SCHEMA_VERSION = "agent_treport.security_master.v1"
SECURITY_MASTER_REVIEW_QUEUE_SCHEMA_VERSION = "agent_treport.security_master.review_queue.v1"
SECURITY_MASTER_IMPORT_RESULT_SCHEMA_VERSION = "agent_treport.security_master.import_result.v1"
SECURITY_MASTER_RESOLVE_RESULT_SCHEMA_VERSION = "agent_treport.security_master.resolve_result.v1"
SECURITY_RESOLUTION_EXPORT_SCHEMA_VERSION = "agent_treport.security_resolution_export.v1"
SECURITY_RESOLUTION_EXPORT_RESULT_SCHEMA_VERSION = (
    "agent_treport.security_resolution_export_result.v1"
)
SECURITY_MASTER_STATUSES = frozenset(
    {
        "verified",
        "auto_verified",
        "proposed",
        "review_required",
        "unresolved",
        "conflict",
        "excluded",
    }
)
SECURITY_MASTER_CONFIDENCES = frozenset({"high", "medium", "low"})
SECURITY_IDENTIFIER_TYPES = frozenset(
    {
        "isin",
        "bloomberg_equity_code",
        "krx_code",
        "ticker_like",
        "cash_like",
        "non_equity",
        "unknown",
    }
)
_EXPORTABLE_SECURITY_MASTER_STATUSES = {"verified", "auto_verified"}
_GLOBAL_ANALYTICAL_IDENTIFIER_TYPES = {
    "isin",
    "krx_code",
    "bloomberg_equity_code",
}

_CASH_EXACT_CODES = {"KRD010010001", "010010", "USDZZ0000001"}
_CURRENCY_CODE_PREFIXES = ("KRW", "USD", "EUR", "JPY")
_CASH_NAME_KEYWORDS = (
    "CASH",
    "DEPOSIT",
    "MMDA",
    "현금",
    "예금",
    "설정현금",
    "현금성자산",
    "예수금",
)
_SHORT_MATURITY_CASH_EQUIVALENT_KEYWORDS = (
    "MMF",
    "MONEY MARKET",
    "REPO",
    "REPURCHASE",
    "T-BILL",
    "TREASURY BILL",
    "SHORT TERM BOND",
    "SHORT-TERM BOND",
    "COMMERCIAL PAPER",
)
_NON_EQUITY_KEYWORDS = (
    "BOND",
    "NOTE",
    "BILL",
    "REPO",
    "MMF",
    "MONEY MARKET",
    "COMMERCIAL PAPER",
    "FUTURE",
    "OPTION",
    "WARRANT",
)
_FUTURES_MARKER_PATTERN = re.compile(r"(?<![A-Z0-9])FUT(?![A-Z0-9])")
_SHORT_MATURITY_MAX_DAYS = 92


class SecurityResolutionInputError(ValueError):
    """Raised when security-resolution inputs violate their JSON contract."""


class SecurityClassificationPolicy:
    def classify(
        self,
        *,
        security_id: str | None,
        name: str,
        as_of_date: str,
        maturity_date: str | None = None,
    ) -> SecurityClassification:
        code = security_id.strip().upper() if security_id is not None else None
        upper_name = name.strip().upper()
        if _is_cash_like_identity(code=code, upper_name=upper_name):
            return "cash_like"
        if _contains_any(upper_name, _SHORT_MATURITY_CASH_EQUIVALENT_KEYWORDS):
            if _has_short_maturity(as_of_date=as_of_date, maturity_date=maturity_date):
                return "cash_like"
            return "non_equity"
        if _FUTURES_MARKER_PATTERN.search(upper_name):
            return "non_equity"
        if _contains_any(upper_name, _NON_EQUITY_KEYWORDS):
            return "non_equity"
        return "ticker_candidate"


def validate_security_classification(value: object, *, label: str) -> SecurityClassification:
    if value not in SECURITY_CLASSIFICATIONS:
        raise ValueError(f"{label} must be one of {sorted(SECURITY_CLASSIFICATIONS)}")
    return value  # type: ignore[return-value]


def has_structural_ticker_resolution(
    *, security_id: str, security_classification: SecurityClassification
) -> bool:
    return (
        _structural_ticker_resolution(
            security_id=security_id,
            classification=security_classification,
        )
        is not None
    )


def analytical_identity_for_security(
    *, source_provider_id: str, security_id: str
) -> tuple[str, str]:
    identifier_type = _identifier_type(security_id)
    if identifier_type in _GLOBAL_ANALYTICAL_IDENTIFIER_TYPES:
        return security_id, "global_identifier"
    return f"provider={source_provider_id}|security={security_id}", "provider_scoped"


def validate_security_master_document(document: Mapping[str, JsonValue]) -> dict[str, JsonValue]:
    if document.get("schema_version") != SECURITY_MASTER_SCHEMA_VERSION:
        raise SecurityResolutionInputError("invalid security master schema")
    entries = document.get("entries")
    if not isinstance(entries, list):
        raise SecurityResolutionInputError("security master entries must be a list")
    seen_security_ids: set[str] = set()
    validated_entries: list[dict[str, JsonValue]] = []
    for index, item in enumerate(entries, 1):
        entry = _validate_security_master_entry(item, index=index)
        security_id = str(entry["security_id"])
        if security_id in seen_security_ids:
            raise SecurityResolutionInputError(
                f"duplicate security master security_id: {security_id}"
            )
        seen_security_ids.add(security_id)
        validated_entries.append(entry)
    return {
        "schema_version": SECURITY_MASTER_SCHEMA_VERSION,
        "entries": validated_entries,
    }


def import_security_master_seed_rows(
    *,
    stock_mapping_rows: Sequence[Mapping[str, str]],
    existing_master: Mapping[str, JsonValue] | None = None,
) -> tuple[dict[str, JsonValue], dict[str, JsonValue], dict[str, JsonValue]]:
    master = (
        validate_security_master_document(existing_master)
        if existing_master is not None
        else {"schema_version": SECURITY_MASTER_SCHEMA_VERSION, "entries": []}
    )
    entries_by_id = {
        str(entry["security_id"]): dict(entry)
        for entry in master["entries"]
        if isinstance(entry, Mapping)
    }
    imported_count = 0
    unchanged_count = 0
    conflict_items: list[dict[str, JsonValue]] = []

    for index, row in enumerate(stock_mapping_rows, 1):
        candidate = _seed_row_to_security_master_entry(row, index=index)
        security_id = str(candidate["security_id"])
        existing = entries_by_id.get(security_id)
        if existing is None:
            entries_by_id[security_id] = candidate
            imported_count += 1
            continue
        if existing.get("ticker") == candidate["ticker"]:
            unchanged_count += 1
            continue
        if existing.get("status") in _EXPORTABLE_SECURITY_MASTER_STATUSES:
            conflict_items.append(
                {
                    "security_id": security_id,
                    "reason": "seed_mapping_conflict",
                    "existing_ticker": existing.get("ticker"),
                    "candidate_ticker": candidate["ticker"],
                    "candidate_name": candidate["name"],
                    "candidate_exchange": candidate["exchange"],
                    "source": "stock_mapping_csv",
                }
            )
            continue
        entries_by_id[security_id] = candidate
        imported_count += 1

    merged_master: dict[str, JsonValue] = {
        "schema_version": SECURITY_MASTER_SCHEMA_VERSION,
        "entries": [entries_by_id[key] for key in sorted(entries_by_id)],
    }
    review_queue: dict[str, JsonValue] = {
        "schema_version": SECURITY_MASTER_REVIEW_QUEUE_SCHEMA_VERSION,
        "items": conflict_items,
    }
    summary: dict[str, JsonValue] = {
        "schema_version": SECURITY_MASTER_IMPORT_RESULT_SCHEMA_VERSION,
        "status": "succeeded",
        "imported_count": imported_count,
        "auto_verified_count": sum(
            1
            for entry in merged_master["entries"]
            if isinstance(entry, Mapping) and entry.get("status") == "auto_verified"
        ),
        "unchanged_count": unchanged_count,
        "conflict_count": len(conflict_items),
        "total_entry_count": len(entries_by_id),
    }
    return merged_master, review_queue, summary


def build_security_resolution_export(
    security_master: Mapping[str, JsonValue],
) -> tuple[dict[str, JsonValue], dict[str, JsonValue]]:
    master = validate_security_master_document(security_master)
    mappings: list[dict[str, JsonValue]] = []
    exclusions: list[dict[str, JsonValue]] = []
    suppressed_count = 0
    for entry in master["entries"]:
        if not isinstance(entry, Mapping):
            continue
        status = entry.get("status")
        classification = entry.get("security_classification")
        if (
            status in _EXPORTABLE_SECURITY_MASTER_STATUSES
            and classification == "ticker_candidate"
            and isinstance(entry.get("ticker"), str)
            and str(entry["ticker"]).strip()
        ):
            mappings.append(
                _with_optional_identity_fields(
                    entry,
                    {
                        "security_id": entry["security_id"],
                        "ticker": entry["ticker"],
                        "name": entry["name"],
                        "exchange": entry["exchange"],
                        "security_classification": classification,
                    },
                )
            )
            continue
        if status == "excluded" and classification in {"cash_like", "non_equity"}:
            exclusions.append(
                {
                    "security_id": entry["security_id"],
                    "name": entry["name"],
                    "security_classification": classification,
                    "reason": "excluded",
                }
            )
            continue
        suppressed_count += 1

    mappings.sort(key=lambda item: str(item["security_id"]))
    exclusions.sort(key=lambda item: str(item["security_id"]))
    export: dict[str, JsonValue] = {
        "schema_version": SECURITY_RESOLUTION_EXPORT_SCHEMA_VERSION,
        "mappings": mappings,
        "exclusions": exclusions,
    }
    summary: dict[str, JsonValue] = {
        "schema_version": SECURITY_RESOLUTION_EXPORT_RESULT_SCHEMA_VERSION,
        "status": "succeeded",
        "mapping_count": len(mappings),
        "exclusion_count": len(exclusions),
        "suppressed_count": suppressed_count,
    }
    return validate_security_resolution_export(export), summary


def resolve_security_master_observations(
    *,
    security_master: Mapping[str, JsonValue],
    observations: Sequence[Mapping[str, JsonValue]],
) -> tuple[dict[str, JsonValue], dict[str, JsonValue], dict[str, JsonValue]]:
    master = validate_security_master_document(security_master)
    entries_by_id = {
        str(entry["security_id"]): dict(entry)
        for entry in master["entries"]
        if isinstance(entry, Mapping)
    }
    grouped_observations = _group_security_observations(observations)
    review_items: list[dict[str, JsonValue]] = []
    excluded_count = 0
    unresolved_count = 0
    auto_verified_count = 0

    for security_id in sorted(grouped_observations):
        observation = grouped_observations[security_id]
        existing = entries_by_id.get(security_id)
        if existing is not None:
            candidate_ticker = observation.get("ticker")
            if (
                existing.get("status") in _EXPORTABLE_SECURITY_MASTER_STATUSES
                and isinstance(candidate_ticker, str)
                and candidate_ticker
                and candidate_ticker != existing.get("ticker")
            ):
                review_item = {
                    "security_id": security_id,
                    "reason": "holding_ticker_conflict",
                    "existing_ticker": existing.get("ticker"),
                    "candidate_ticker": candidate_ticker,
                    "candidate_name": observation["name"],
                    "source": "holdings_observation",
                }
                _apply_observation_source_provider(review_item, observation)
                review_items.append(review_item)
            if existing.get("status") != "unresolved":
                continue
            if candidate_ticker is not None:
                entries_by_id[security_id] = _observation_entry(
                    observation,
                    status="auto_verified",
                    confidence="medium",
                    ticker=str(candidate_ticker),
                )
                auto_verified_count += 1
                continue

        classification = str(observation["security_classification"])
        if classification in {"cash_like", "non_equity"}:
            entries_by_id[security_id] = _observation_entry(
                observation,
                status="excluded",
                confidence="high",
                ticker=None,
            )
            excluded_count += 1
            continue
        if classification == "ticker_candidate" and isinstance(observation.get("ticker"), str):
            entries_by_id[security_id] = _observation_entry(
                observation,
                status="auto_verified",
                confidence="medium",
                ticker=str(observation["ticker"]),
            )
            auto_verified_count += 1
            continue
        structural_resolution = _structural_ticker_resolution(
            security_id=security_id,
            classification=classification,
        )
        if structural_resolution is not None:
            entries_by_id[security_id] = _structural_observation_entry(
                observation,
                structural_resolution=structural_resolution,
            )
            auto_verified_count += 1
            continue

        entries_by_id[security_id] = _observation_entry(
            observation,
            status="unresolved",
            confidence="low",
            ticker=None,
        )
        unresolved_count += 1
        review_item = {
            "security_id": security_id,
            "reason": (
                "ticker_candidate_unresolved"
                if classification == "ticker_candidate"
                else "classification_unknown"
            ),
            "candidate_name": observation["name"],
            "source": "holdings_observation",
        }
        _apply_observation_source_provider(review_item, observation)
        review_items.append(review_item)

    resolved_master: dict[str, JsonValue] = {
        "schema_version": SECURITY_MASTER_SCHEMA_VERSION,
        "entries": [entries_by_id[key] for key in sorted(entries_by_id)],
    }
    review_queue: dict[str, JsonValue] = {
        "schema_version": SECURITY_MASTER_REVIEW_QUEUE_SCHEMA_VERSION,
        "items": review_items,
    }
    summary: dict[str, JsonValue] = {
        "schema_version": SECURITY_MASTER_RESOLVE_RESULT_SCHEMA_VERSION,
        "status": "succeeded",
        "observed_security_count": len(grouped_observations),
        "excluded_count": excluded_count,
        "auto_verified_count": auto_verified_count,
        "unresolved_count": unresolved_count,
        "conflict_count": sum(
            1 for item in review_items if item.get("reason") == "holding_ticker_conflict"
        ),
        "review_queue_count": len(review_items),
        "total_entry_count": len(entries_by_id),
    }
    return validate_security_master_document(resolved_master), review_queue, summary


def validate_security_resolution_export(
    document: Mapping[str, JsonValue],
) -> dict[str, JsonValue]:
    if document.get("schema_version") != SECURITY_RESOLUTION_EXPORT_SCHEMA_VERSION:
        raise SecurityResolutionInputError("invalid security resolution export schema")
    mappings = document.get("mappings")
    exclusions = document.get("exclusions")
    if not isinstance(mappings, list):
        raise SecurityResolutionInputError("security resolution mappings must be a list")
    if not isinstance(exclusions, list):
        raise SecurityResolutionInputError("security resolution exclusions must be a list")
    seen_security_ids: set[str] = set()
    validated_mappings = [
        _validate_security_resolution_mapping(item, index=index, seen=seen_security_ids)
        for index, item in enumerate(mappings, 1)
    ]
    validated_exclusions = [
        _validate_security_resolution_exclusion(item, index=index, seen=seen_security_ids)
        for index, item in enumerate(exclusions, 1)
    ]
    return {
        "schema_version": SECURITY_RESOLUTION_EXPORT_SCHEMA_VERSION,
        "mappings": validated_mappings,
        "exclusions": validated_exclusions,
    }


def _is_cash_like_identity(*, code: str | None, upper_name: str) -> bool:
    if code in _CASH_EXACT_CODES:
        return True
    if code is not None and code.startswith("CASH"):
        return True
    if code is not None and code.startswith(_CURRENCY_CODE_PREFIXES):
        return True
    return _contains_any(upper_name, _CASH_NAME_KEYWORDS)


def _has_short_maturity(*, as_of_date: str, maturity_date: str | None) -> bool:
    if maturity_date is None:
        return False
    as_of = _parse_date(as_of_date)
    maturity = _parse_date(maturity_date)
    if as_of is None or maturity is None:
        return False
    days_to_maturity = (maturity - as_of).days
    return 0 <= days_to_maturity <= _SHORT_MATURITY_MAX_DAYS


def _parse_date(value: str) -> date | None:
    text = value.strip()
    try:
        if len(text) == 8 and text.isdigit():
            return date.fromisoformat(f"{text[:4]}-{text[4:6]}-{text[6:]}")
        return date.fromisoformat(text)
    except ValueError:
        return None


def _contains_any(value: str, keywords: tuple[str, ...]) -> bool:
    return any(keyword in value for keyword in keywords)


def _validate_security_master_entry(item: JsonValue, *, index: int) -> dict[str, JsonValue]:
    if not isinstance(item, Mapping):
        raise SecurityResolutionInputError(
            f"security master entry must be an object: index={index}"
        )
    security_id = _required_text(item.get("security_id"), label=f"security_id index={index}")
    name = _required_text(item.get("name"), label=f"name index={index}")
    ticker = _optional_text(item.get("ticker"), label=f"ticker index={index}")
    exchange = _optional_text(item.get("exchange"), label=f"exchange index={index}")
    status = _validate_member(
        item.get("status"),
        allowed=SECURITY_MASTER_STATUSES,
        label=f"status index={index}",
    )
    confidence = _validate_member(
        item.get("confidence"),
        allowed=SECURITY_MASTER_CONFIDENCES,
        label=f"confidence index={index}",
    )
    try:
        security_classification = validate_security_classification(
            item.get("security_classification"),
            label=f"security_classification index={index}",
        )
    except ValueError as exc:
        raise SecurityResolutionInputError(str(exc)) from exc
    identifier_type = _validate_member(
        item.get("identifier_type"),
        allowed=SECURITY_IDENTIFIER_TYPES,
        label=f"identifier_type index={index}",
    )
    sources = item.get("sources")
    if not isinstance(sources, list):
        raise SecurityResolutionInputError(f"sources must be a list: index={index}")
    for source_index, source in enumerate(sources, 1):
        if not isinstance(source, Mapping) or _optional_text(
            source.get("source"), label=f"source index={index}.{source_index}"
        ) is None:
            raise SecurityResolutionInputError(
                f"sources entries must include source: index={index}.{source_index}"
            )
    return _with_optional_identity_fields(
        item,
        {
            "security_id": security_id,
            "name": name,
            "ticker": ticker,
            "exchange": exchange,
            "status": status,
            "confidence": confidence,
            "security_classification": security_classification,
            "identifier_type": identifier_type,
            "sources": [dict(source) for source in sources],
        },
    )


def _seed_row_to_security_master_entry(
    row: Mapping[str, str], *, index: int
) -> dict[str, JsonValue]:
    security_id = _required_text(row.get("stock_code"), label=f"stock_code index={index}")
    name = _required_text(row.get("stock_name"), label=f"stock_name index={index}")
    raw_ticker = _required_text(row.get("symbol"), label=f"symbol index={index}")
    exchange = _required_text(row.get("exchange"), label=f"exchange index={index}")
    updated_at = _required_text(row.get("updated_at"), label=f"updated_at index={index}")
    ticker = _display_ticker(security_id=security_id, raw_ticker=raw_ticker)
    return {
        "security_id": security_id,
        "name": name,
        "ticker": ticker,
        "exchange": exchange,
        "status": "auto_verified",
        "confidence": "high",
        "security_classification": "ticker_candidate",
        "identifier_type": _identifier_type(security_id),
        "sources": [
            {
                "source": "stock_mapping_csv",
                "ticker": ticker,
                "name": name,
                "exchange": exchange,
                "updated_at": updated_at,
            }
        ],
    }


def _validate_security_resolution_mapping(
    item: JsonValue, *, index: int, seen: set[str]
) -> dict[str, JsonValue]:
    if not isinstance(item, Mapping):
        raise SecurityResolutionInputError(
            f"security resolution mapping must be an object: index={index}"
        )
    security_id = _required_text(item.get("security_id"), label=f"mapping security_id {index}")
    if security_id in seen:
        raise SecurityResolutionInputError(
            f"duplicate security resolution security_id: {security_id}"
        )
    seen.add(security_id)
    classification = _resolution_classification(
        item.get("security_classification"),
        index=index,
        expected={"ticker_candidate"},
    )
    return _with_optional_identity_fields(
        item,
        {
            "security_id": security_id,
            "ticker": _required_text(item.get("ticker"), label=f"mapping ticker {index}"),
            "name": _required_text(item.get("name"), label=f"mapping name {index}"),
            "exchange": _optional_text(item.get("exchange"), label=f"mapping exchange {index}"),
            "security_classification": classification,
        },
        index=index,
    )


def _validate_security_resolution_exclusion(
    item: JsonValue, *, index: int, seen: set[str]
) -> dict[str, JsonValue]:
    if not isinstance(item, Mapping):
        raise SecurityResolutionInputError(
            f"security resolution exclusion must be an object: index={index}"
        )
    security_id = _required_text(item.get("security_id"), label=f"exclusion security_id {index}")
    if security_id in seen:
        raise SecurityResolutionInputError(
            f"duplicate security resolution security_id: {security_id}"
        )
    seen.add(security_id)
    classification = _resolution_classification(
        item.get("security_classification"),
        index=index,
        expected={"cash_like", "non_equity"},
    )
    return {
        "security_id": security_id,
        "name": _required_text(item.get("name"), label=f"exclusion name {index}"),
        "security_classification": classification,
        "reason": _required_text(item.get("reason"), label=f"exclusion reason {index}"),
    }


def _with_optional_identity_fields(
    source: Mapping[str, JsonValue],
    target: dict[str, JsonValue],
    *,
    index: int | None = None,
) -> dict[str, JsonValue]:
    labels = {
        "security_group_id": "security_group_id",
        "listing_key": "listing_key",
        "security_group_name": "security_group_name",
        "security_group_ticker": "security_group_ticker",
    }
    for field, label in labels.items():
        value = _optional_text(
            source.get(field),
            label=f"mapping {label} {index}" if index is not None else label,
        )
        if value is not None:
            target[field] = value
    return target


def _group_security_observations(
    observations: Sequence[Mapping[str, JsonValue]],
) -> dict[str, dict[str, JsonValue]]:
    grouped: dict[str, dict[str, JsonValue]] = {}
    for index, item in enumerate(observations, 1):
        security_id = _required_text(
            item.get("security_id"),
            label=f"observation security_id {index}",
        )
        name = _required_text(item.get("name"), label=f"observation name {index}")
        ticker = _optional_text(item.get("ticker"), label=f"observation ticker {index}")
        exchange = _optional_text(
            item.get("exchange"),
            label=f"observation exchange {index}",
        )
        try:
            classification = validate_security_classification(
                item.get("security_classification"),
                label=f"observation security_classification {index}",
            )
        except ValueError as exc:
            raise SecurityResolutionInputError(str(exc)) from exc
        existing = grouped.get(security_id)
        if existing is None:
            grouped[security_id] = {
                "security_id": security_id,
                "name": name,
                "ticker": ticker,
                "exchange": exchange,
                "security_classification": classification,
                "observed_row_count": 1,
            }
            _apply_observation_source_provider(grouped[security_id], item)
            continue
        existing["observed_row_count"] = int(existing["observed_row_count"]) + 1
        if existing.get("source_provider_id") is None:
            _apply_observation_source_provider(existing, item)
        if existing.get("ticker") is None and ticker is not None:
            existing["ticker"] = ticker
        if existing.get("exchange") is None and exchange is not None:
            existing["exchange"] = exchange
        if existing.get("security_classification") == "unknown" and classification != "unknown":
            existing["security_classification"] = classification
    return grouped


def _observation_entry(
    observation: Mapping[str, JsonValue],
    *,
    status: SecurityMasterStatus,
    confidence: SecurityMasterConfidence,
    ticker: str | None,
) -> dict[str, JsonValue]:
    security_id = str(observation["security_id"])
    classification = str(observation["security_classification"])
    source: dict[str, JsonValue] = {
        "source": "holdings_observation",
        "observed_row_count": observation["observed_row_count"],
    }
    _apply_observation_source_provider(source, observation)
    return {
        "security_id": security_id,
        "name": observation["name"],
        "ticker": ticker,
        "exchange": _optional_text(
            observation.get("exchange"),
            label=f"observation exchange {security_id}",
        ),
        "status": status,
        "confidence": confidence,
        "security_classification": classification,
        "identifier_type": _observation_identifier_type(security_id, classification),
        "sources": [source],
    }


def _apply_observation_source_provider(
    target: dict[str, JsonValue], observation: Mapping[str, JsonValue]
) -> None:
    source_provider_id = _optional_text(
        observation.get("source_provider_id"),
        label=f"observation source_provider_id {observation.get('security_id', '')}",
    )
    if source_provider_id is not None:
        target["source_provider_id"] = source_provider_id


def _structural_observation_entry(
    observation: Mapping[str, JsonValue],
    *,
    structural_resolution: tuple[str, str, str, str],
) -> dict[str, JsonValue]:
    ticker, exchange, identifier_type, rule = structural_resolution
    source: dict[str, JsonValue] = {
        "source": "holdings_observation",
        "observed_row_count": observation["observed_row_count"],
    }
    _apply_observation_source_provider(source, observation)
    return {
        "security_id": observation["security_id"],
        "name": observation["name"],
        "ticker": ticker,
        "exchange": exchange,
        "status": "auto_verified",
        "confidence": "medium",
        "security_classification": "ticker_candidate",
        "identifier_type": identifier_type,
        "sources": [
            source,
            {
                "source": "structural_rule",
                "rule": rule,
            },
        ],
    }


def _structural_ticker_resolution(
    *, security_id: str, classification: str
) -> tuple[str, str, str, str] | None:
    if classification != "ticker_candidate":
        return None
    normalized = " ".join(security_id.strip().split())
    if len(normalized) == 6 and normalized.isdigit():
        return normalized, "KRX", "krx_code", "krx_code_self_ticker"
    korean_isin_ticker = _parse_korean_isin_ticker(normalized)
    if korean_isin_ticker is not None:
        return korean_isin_ticker, "KRX", "isin", "korean_isin_display_ticker"
    bloomberg_resolution = _parse_bloomberg_equity_code(normalized)
    if bloomberg_resolution is not None:
        ticker, exchange = bloomberg_resolution
        return ticker, exchange, "bloomberg_equity_code", "bloomberg_equity_code"
    return None


def _parse_korean_isin_ticker(value: str) -> str | None:
    normalized = value.upper()
    if (
        len(normalized) == 12
        and normalized.startswith("KR")
        and normalized[3:9].isdigit()
    ):
        return normalized[3:9]
    return None


def _parse_bloomberg_equity_code(value: str) -> tuple[str, str] | None:
    parts = value.split()
    if len(parts) == 2:
        ticker, exchange = parts
    elif len(parts) == 3 and parts[2].upper() == "EQUITY":
        ticker, exchange = parts[0], parts[1]
    else:
        return None
    if not _is_bloomberg_ticker_token(ticker):
        return None
    if not (
        2 <= len(exchange) <= 4
        and exchange.isalnum()
        and any(character.isalpha() for character in exchange)
    ):
        return None
    return ticker.upper(), exchange.upper()


def _is_bloomberg_ticker_token(value: str) -> bool:
    return any(character.isalnum() for character in value) and all(
        character.isalnum() or character in {".", "/", "-"} for character in value
    )


def _observation_identifier_type(security_id: str, classification: str) -> str:
    if classification in {"cash_like", "non_equity"}:
        return classification
    return _identifier_type(security_id)


def _resolution_classification(
    value: object, *, index: int, expected: set[str]
) -> SecurityClassification:
    try:
        classification = validate_security_classification(
            value,
            label=f"security resolution security_classification index={index}",
        )
    except ValueError as exc:
        raise SecurityResolutionInputError(str(exc)) from exc
    if classification not in expected:
        raise SecurityResolutionInputError(
            f"security resolution classification is not valid for this section: index={index}"
        )
    return classification


def _required_text(value: object, *, label: str) -> str:
    text = _optional_text(value, label=label)
    if text is None:
        raise SecurityResolutionInputError(f"{label} must be a non-empty string")
    return text


def _optional_text(value: object, *, label: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise SecurityResolutionInputError(f"{label} must be a string")
    text = value.strip()
    return text or None


def _validate_member(value: object, *, allowed: frozenset[str], label: str) -> str:
    if value not in allowed:
        raise SecurityResolutionInputError(f"{label} must be one of {sorted(allowed)}")
    assert isinstance(value, str)
    return value


def _display_ticker(*, security_id: str, raw_ticker: str) -> str:
    if security_id.upper().startswith("KR"):
        ticker_head = raw_ticker.split(".", 1)[0]
        if len(ticker_head) == 6 and ticker_head.isdigit():
            return ticker_head
    return raw_ticker


def _identifier_type(security_id: str) -> str:
    normalized = security_id.strip().upper()
    if len(normalized) == 12 and normalized[:2].isalpha() and normalized[2:].isalnum():
        return "isin"
    if len(normalized) == 6 and normalized.isdigit():
        return "krx_code"
    if _parse_bloomberg_equity_code(normalized) is not None:
        return "bloomberg_equity_code"
    return "unknown"
