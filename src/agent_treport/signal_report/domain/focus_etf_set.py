from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from agent_pack.models import RuntimeModel

FOCUS_ETF_SET_SCHEMA_VERSION = "agent_treport.focus_etf_set.v1"

_ALLOWED_FIELDS = {"schema_version", "focus_etf_ids", "label", "notes"}


class FocusETFSetInputError(ValueError):
    """Raised when a FocusETFSet file violates the path-safe handoff contract."""


class FocusETFSet(RuntimeModel):
    schema_version: str = FOCUS_ETF_SET_SCHEMA_VERSION
    focus_etf_ids: tuple[str, ...]
    label: str | None = None
    notes: str | None = None


def load_focus_etf_set_file(path: str | Path) -> FocusETFSet:
    payload = _read_json_object(Path(path))
    return focus_etf_set_from_mapping(payload)


def focus_etf_set_from_mapping(payload: dict[str, Any]) -> FocusETFSet:
    unsupported = sorted(set(payload) - _ALLOWED_FIELDS)
    if unsupported:
        raise FocusETFSetInputError(
            "unsupported focus ETF set field: " + ", ".join(unsupported)
        )
    if payload.get("schema_version") != FOCUS_ETF_SET_SCHEMA_VERSION:
        raise FocusETFSetInputError("invalid focus ETF set schema")

    ids_value = payload.get("focus_etf_ids")
    if not isinstance(ids_value, list) or not ids_value:
        raise FocusETFSetInputError("focus_etf_ids must be a non-empty list")

    focus_etf_ids: list[str] = []
    seen: set[str] = set()
    for index, raw_id in enumerate(ids_value, 1):
        if not isinstance(raw_id, str) or not raw_id.strip():
            raise FocusETFSetInputError(
                f"focus_etf_id must be a non-empty string: index={index}"
            )
        etf_id = raw_id.strip()
        _ensure_path_safe_text(etf_id, label="focus_etf_id")
        if etf_id in seen:
            raise FocusETFSetInputError(f"duplicate focus_etf_id: {etf_id}")
        seen.add(etf_id)
        focus_etf_ids.append(etf_id)

    label = _optional_path_safe_text(payload.get("label"), "label")
    notes = _optional_path_safe_text(payload.get("notes"), "notes")
    return FocusETFSet(
        focus_etf_ids=tuple(focus_etf_ids),
        label=label,
        notes=notes,
    )


def _read_json_object(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise FocusETFSetInputError(f"invalid JSON input: {path}: {exc.msg}") from exc
    except OSError as exc:
        raise FocusETFSetInputError(f"focus ETF set file could not be read: {path}") from exc
    if not isinstance(payload, dict):
        raise FocusETFSetInputError("focus ETF set file must be a JSON object")
    return payload


def _optional_path_safe_text(value: object, label: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise FocusETFSetInputError(f"{label} must be a string")
    text = value.strip()
    if not text:
        return None
    _ensure_path_safe_text(text, label=label)
    return text


def _ensure_path_safe_text(value: str, *, label: str) -> None:
    if (
        "://" in value
        or "\\" in value
        or value.startswith("/")
        or "C:" in value
        or "Users\\" in value
    ):
        raise FocusETFSetInputError(f"{label} is not path-safe")
